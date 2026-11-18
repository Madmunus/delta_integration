"""Клиент OData для взаимодействия с «1С:Предприятие» и «1С:Бухгалтерия».

Обёртка над requests: Basic Auth, таймауты, обработка ошибок, разбор JSON.
Пример стандартного URL: http://server/base/odata/standard.odata/
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote, urljoin

import requests
from flask import current_app, has_app_context

from engine.config import Config

logger = logging.getLogger(__name__)

# Размер страницы при полной выборке справочников из 1С
QUERY_PAGE_SIZE = Config.ODATA_PAGE_SIZE


def _config_value(name: str, default):
    """Значение OData-настройки с учётом контекста приложения."""
    if has_app_context():
        return current_app.config.get(name, default)
    return default


def configured_timeout() -> int:
    """Таймаут OData из конфигурации приложения (для фоновых заданий)."""
    return int(_config_value('ODATA_TIMEOUT', Config.ODATA_TIMEOUT))


def configured_page_size() -> int:
    """Размер страницы OData из конфигурации приложения."""
    return max(1, int(_config_value('ODATA_PAGE_SIZE', Config.ODATA_PAGE_SIZE)))


def configured_max_workers() -> int:
    """Число потоков для параллельной загрузки разных сущностей."""
    return max(1, int(_config_value('ODATA_MAX_WORKERS',
                                    Config.ODATA_MAX_WORKERS)))

# Виды документов «1С:Документооборот». «Вид документа» в приложении —
# это имя сущности без префикса Document_ (например,
# Document_РеализацияТоваровУслуг → «РеализацияТоваровУслуг»).
DOCUMENT_ENTITIES = [
    'Document_РеализацияТоваровУслуг',
    'Document_РеализацияТоваровИУслуг',
    'Document_СчетНаОплатуПокупателю',
    'Document_СчетПокупателю',
    'Document_СчетНаОплатуКлиенту',
    'Document_ПоступлениеТоваровУслуг',
    'Document_ПоступлениеТоваровИУслуг',
    'Document_ПриобретениеТоваровУслуг',
    'Document_СчетНаОплатуПоставщика',
    'Document_ПоступлениеНаРасчетныйСчет',
    'Document_ПоступлениеБезналичныхДенежныхСредств',
    'Document_СписаниеСРасчетногоСчета',
    'Document_СписаниеБезналичныхДенежныхСредств',
    'Document_ПлатежноеПоручение',
    'Document_ПриходныйКассовыйОрдер',
    'Document_РасходныйКассовыйОрдер',
    'Document_ОплатаПлатежнойКартой',
    'Document_СчетФактураВыданный',
    'Document_ОтчетКомитентуОПродажах',
    'Document_СчетФактураПолученный',
    'Document_АктОбОказанииПроизводственныхУслуг',
    'Document_ВозвратТоваровОтПокупателя',
    'Document_ВозвратТоваровОтКлиента',
    'Document_ВозвратТоваровПоставщику',
    'Document_ПеремещениеТоваров',
    'Document_ОприходованиеТоваров',
    'Document_СписаниеТоваров',
    'Document_ИнвентаризацияТоваровНаСкладе',
]


def document_type(entity: str) -> str:
    """Вид документа — имя сущности 1С без префикса Document_."""
    prefix = 'Document_'
    return entity[len(prefix):] if entity.startswith(prefix) else entity


def document_object_url(base_url: str, entity: str, ref_key: str,
                        row: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Ссылка на объект документа в «1С:Документооборот».

    Приоритет источников:
      1. явное поле ответа OData (``Ссылка``, ``СсылкаНаОбъект``, ``URL``);
      2. ``__metadata.uri`` — канонический адрес ресурса OData;
      3. ссылка веб-клиента 1С (``.../e1cib/data/Документ.<Вид>?ref=<GUID>``),
         если базовый URL содержит ``/odata/``;
      4. адрес ресурса OData ``<сущность>(guid'<GUID>')``.
    """
    row = row or {}
    for key in ('Ссылка', 'СсылкаНаОбъект', 'URL', 'Hyperlink', 'Uri'):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    metadata = row.get('__metadata')
    if isinstance(metadata, dict):
        uri = metadata.get('uri')
        if isinstance(uri, str) and uri.strip():
            return urljoin(base_url.rstrip('/') + '/', uri)

    ref = str(ref_key).strip() if ref_key else ''
    if not ref:
        return None

    base = base_url.rstrip('/') + '/'
    marker = '/odata/'
    if marker in base:
        # Веб-клиент 1С: http://server/base/e1cib/data/Документ.<Вид>?ref=<GUID>
        web_root = base.split(marker, 1)[0] + '/'
        name = quote(document_type(entity))
        return f'{web_root}e1cib/data/Документ.{name}?ref={ref}'
    return f"{base}{entity}(guid'{ref}')"


class ODataError(Exception):
    """Ошибка обращения к OData-интерфейсу 1С."""


class ODataClient:
    """Инкапсулирует доступ к OData-интерфейсу конкретной базы 1С."""

    def __init__(self, base_url: str, username: str, password: str,
                 timeout: Optional[int] = None, verify_ssl: bool = True,
                 config_overrides: Optional[Dict[str, Any]] = None):
        # Нормализуем URL: добавляем завершающий слэш
        self.base_url = base_url.rstrip('/') + '/'
        self.timeout = timeout if timeout is not None else configured_timeout()
        # Сохраняем реквизиты: дочерние клиенты для параллельных запросов
        # должны использовать отдельные requests.Session, но те же ключи.
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        # ThreadPoolExecutor не наследует контекст Flask, поэтому значения
        # настроек фиксируются в родителе и передаются дочерним клиентам.
        self._config_overrides = dict(config_overrides or {})
        self._etag_cache: Dict[str, Dict[str, Any]] = {}
        self._session = requests.Session()
        self._session.auth = (username, password)
        self._session.headers.update({'Accept': 'application/json'})
        self._session.verify = verify_ssl

    def _setting(self, name: str, default):
        """Настройка клиента с учётом переопределений и контекста Flask."""
        if name in self._config_overrides:
            return self._config_overrides[name]
        return _config_value(name, default)

    # ------------------------------------------------------------------ core
    def _get(self, entity: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """GET-запрос к сущности с retry/backoff и поддержкой ETag."""
        url = f'{self.base_url}{entity}'
        # Ключ кэша должен включать параметры: разные страницы и фильтры
        # не должны делить один и тот же ETag.
        cache_key = f'{url}|{sorted((params or {}).items())!r}'
        cached = self._etag_cache.get(cache_key)
        headers = {}
        if cached and cached.get('etag'):
            headers['If-None-Match'] = cached['etag']

        retries = max(0, int(self._setting(
            'ODATA_MAX_RETRIES', Config.ODATA_MAX_RETRIES)))
        backoff = max(0.0, float(self._setting(
            'ODATA_RETRY_BACKOFF', Config.ODATA_RETRY_BACKOFF)))

        for attempt in range(retries + 1):
            try:
                resp = self._session.get(
                    url, params=params, timeout=self.timeout,
                    headers=headers or None)
            except requests.Timeout as exc:
                if attempt >= retries:
                    raise ODataError(
                        f'Таймаут запроса к {url} ({self.timeout} с)') from exc
                time.sleep(backoff * (2 ** attempt))
                continue
            except requests.RequestException as exc:
                if attempt >= retries:
                    raise ODataError(
                        f'Ошибка соединения с {url}: {exc}') from exc
                time.sleep(backoff * (2 ** attempt))
                continue

            if resp.status_code == 304 and cached:
                return cached['payload']

            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= retries:
                    raise ODataError(
                        f'HTTP {resp.status_code} от {url}: {resp.text[:300]}')
                retry_after = getattr(resp, 'headers', {}).get('Retry-After')
                try:
                    delay = (float(retry_after) if retry_after
                             else backoff * (2 ** attempt))
                except (TypeError, ValueError):
                    delay = backoff * (2 ** attempt)
                time.sleep(max(delay, backoff))
                continue

            if resp.status_code >= 400:
                raise ODataError(
                    f'HTTP {resp.status_code} от {url}: {resp.text[:300]}')

            try:
                payload = resp.json()
            except ValueError as exc:
                raise ODataError(f'Некорректный JSON от {url}') from exc

            etag = getattr(resp, 'headers', {}).get('ETag')
            if etag:
                self._etag_cache[cache_key] = {'etag': etag, 'payload': payload}
            else:
                self._etag_cache.pop(cache_key, None)
            return payload

        raise ODataError(f'Не удалось выполнить запрос к {url}')

    def query(self, entity: str, select: Optional[Iterable[str]] = None,
              filt: Optional[str] = None, order_by: Optional[str] = None,
              top: Optional[int] = None, skip: int = 0) -> List[Dict[str, Any]]:
        """Универсальная выборка сущности с фильтрацией."""
        params: Dict[str, Any] = {'$skip': skip}
        if select:
            params['$select'] = ','.join(select)
        if filt:
            params['$filter'] = filt
        if order_by:
            params['$orderby'] = order_by
        if top is not None:
            params['$top'] = top
        data = self._get(entity, params)
        return data.get('value', [])

    def query_all(self, entity: str, select: Optional[Iterable[str]] = None,
                  filt: Optional[str] = None, order_by: Optional[str] = None,
                  page_size: int = QUERY_PAGE_SIZE) -> List[Dict[str, Any]]:
        """Получить все строки сущности, перебирая страницы ``$top/$skip``.

        Дополнительно отсекаются повторные ``Ref_Key``: некоторые 1С-конфигурации
        при сдвинутой постраничной выборке могут вернуть уже отданную строку.
        """
        rows: List[Dict[str, Any]] = []
        seen = set()
        skip = 0
        size = max(1, int(page_size or self._setting(
            'ODATA_PAGE_SIZE', Config.ODATA_PAGE_SIZE)))
        max_pages = max(1, int(self._setting(
            'ODATA_MAX_PAGES', Config.ODATA_MAX_PAGES)))
        for _ in range(max_pages):
            page = self.query(entity, select=select, filt=filt,
                              order_by=order_by, top=size, skip=skip)
            if not page:
                break
            # Без Ref_Key нельзя надёжно дедуплицировать страницы —
            # возвращаем то, что получили, и не рискуем зациклиться.
            keyed = any(row.get('Ref_Key') is not None for row in page)
            if not keyed:
                logger.warning(
                    'OData %s: страница без Ref_Key (skip=%s), '
                    'дедупликация недоступна', entity, skip)
            new = []
            for row in page:
                key = row.get('Ref_Key')
                if keyed and key is not None:
                    if key in seen:
                        continue
                    seen.add(key)
                new.append(row)
            rows.extend(new)
            # Сервер может вернуть меньше строк, чем $top, но при этом
            # проигнорировать $skip — увеличение skip на len(page) тогда
            # приведёт к дублированию. Проверяем фактический сдвиг.
            if len(page) < size or not new:
                break
            next_skip = skip + len(page)
            if next_skip <= skip:
                break
            skip = next_skip
        else:
            logger.warning(
                'OData %s: достигнут предел %s страниц', entity, max_pages)
        return rows

    # ------------------------------------------------------- типовые запросы
    def get_organizations(self) -> List[Dict[str, Any]]:
        """Клиенты: справочник Catalog_Организации главной базы 1С.

        Поля: Ref_Key, Наименование, ИНН, DeletionMark
        (0 — активный, 1 — неактивный).
        """
        return self.query_all(
            'Catalog_Организации',
            select=['Ref_Key', 'Наименование', 'ИНН', 'DeletionMark'],
            order_by='Наименование',
        )

    def get_contractors(self) -> List[Dict[str, Any]]:
        """Контрагенты: справочник Catalog_Контрагенты главной базы 1С.

        Поля: Ref_Key, НаименованиеПолное, ИНН, КПП, DeletionMark
        (0 — активный, 1 — неактивный).
        """
        return self.query_all(
            'Catalog_Контрагенты',
            select=['Ref_Key', 'НаименованиеПолное', 'ИНН', 'КПП',
                    'DeletionMark'],
            order_by='НаименованиеПолное',
        )

    def get_contracts(self) -> List[Dict[str, Any]]:
        """Договоры: справочник Catalog_Договоры базы «1С:Бухгалтерия» клиента.

        Поля: Ref_Key, Контрагент, Наименование, Дата, СрокДействия,
        Статус, Сумма, Description.
        """
        return self.query_all(
            'Catalog_Договоры',
            select=['Ref_Key', 'Контрагент', 'Наименование', 'Дата',
                    'СрокДействия', 'Статус', 'Сумма', 'Description'],
            order_by='Дата desc',
        )

    @staticmethod
    def document_filt(date_from=None, date_to=None, client_key: str = None,
                      contractor_key: str = None) -> Optional[str]:
        """Фильтр OData для выборки документов по дате и контрагентам."""
        conditions = []
        if date_from:
            conditions.append(
                f"Date ge datetime'{date_from.isoformat()}T00:00:00'")
        if date_to:
            conditions.append(
                f"Date le datetime'{date_to.isoformat()}T23:59:59'")
        if client_key:
            conditions.append(f"Организация eq guid'{client_key}'")
        if contractor_key:
            conditions.append(f"Контрагент eq guid'{contractor_key}'")
        return ' and '.join(conditions) or None

    def _load_documents(self, entity: str, filt: Optional[str],
                        top: int, client: 'ODataClient') -> List[Dict[str, Any]]:
        """Загрузить один вид документов указанным клиентом."""
        rows = client.query_all(
            entity,
            select=['Ref_Key', 'Number', 'Date', 'Организация',
                    'Контрагент', 'Статус'],
            filt=filt,
            order_by='Date desc',
            page_size=top,
        )
        doc_type = document_type(entity)
        items = []
        for row in rows:
            item = dict(row)
            item['ВидДокумента'] = doc_type
            item['СсылкаНаОбъект'] = document_object_url(
                self.base_url, entity, row.get('Ref_Key'), row)
            items.append(item)
        return items

    def get_documents(self, entity: str = None,
                      date_from=None, date_to=None, client_key: str = None,
                      contractor_key: str = None, top: int = 500,
                      entities: Iterable[str] = None) -> List[Dict[str, Any]]:
        """Документы из «1С:Документооборот».

        Если ``entity`` или ``entities`` не заданы, опрашиваются все виды
        документов из :data:`DOCUMENT_ENTITIES` — параллельно, отдельным
        ``requests.Session`` на поток. Порядок сущностей в результате
        сохраняется таким же, как в исходном списке.

        По каждому документу 1С возвращает поля: Ref_Key, Организация,
        Контрагент (может быть пустым), Номер (Number), Дата (Date), Статус.
        В каждую строку добавляется вычисленное поле ``ВидДокумента`` —
        имя сущности без префикса ``Document_``.
        """
        if entity:
            entity_list = [entity]
        elif entities:
            entity_list = list(entities)
        else:
            entity_list = list(DOCUMENT_ENTITIES)

        filt = self.document_filt(
            date_from=date_from, date_to=date_to,
            client_key=client_key, contractor_key=contractor_key)
        workers = min(
            max(1, int(self._setting('ODATA_MAX_WORKERS',
                                     Config.ODATA_MAX_WORKERS))),
            len(entity_list))

        if workers <= 1 or len(entity_list) <= 1:
            return self._load_documents(entity_list[0], filt, top, self) \
                if entity_list else []

        # У каждого потока свой Session: requests.Session не потокобезопасен.
        by_index: Dict[int, List[Dict[str, Any]]] = {}
        children: List[ODataClient] = []
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {}
                for index, ent in enumerate(entity_list):
                    child = ODataClient(
                        base_url=self.base_url, username=self.username,
                        password=self.password, timeout=self.timeout,
                        verify_ssl=self.verify_ssl,
                        config_overrides=self._config_overrides)
                    children.append(child)
                    futures[pool.submit(
                        self._load_documents, ent, filt, top, child)] = index
                for future in as_completed(futures):
                    # Ошибка любой сущности остаётся фатальной, как и в
                    # последовательном варианте.
                    by_index[futures[future]] = future.result()
        finally:
            # with завершает все futures до выхода, поэтому сессии можно
            # закрывать даже при ошибке одной из сущностей.
            for child in children:
                child.close()

        documents: List[Dict[str, Any]] = []
        for index in range(len(entity_list)):
            documents.extend(by_index.get(index, []))
        return documents

    def test_connection(self) -> bool:
        """Проверка доступности сервиса (для формы настройки)."""
        try:
            # Дешёвая проверка: корневой запрос к сервису с обработкой ошибок.
            self._session.get(self.base_url, timeout=self.timeout).raise_for_status()
            return True
        except requests.RequestException:
            return False

    def close(self):
        self._session.close()


def client_for_settings(settings) -> ODataClient:
    """Фабрика: клиент OData по настройке главной базы 1С."""
    return ODataClient(
        base_url=settings.odata_url,
        username=settings.username,
        password=settings.password,
    )


def client_for_client(client) -> ODataClient:
    """Фабрика: клиент OData по настройкам «1С:Бухгалтерия» клиента."""
    return ODataClient(
        base_url=client.odata_url,
        username=client.odata_username,
        password=client.odata_password,
    )
