"""Клиент OData для взаимодействия с «1С:Предприятие» и «1С:Бухгалтерия».

Обёртка над requests: Basic Auth, таймауты, обработка ошибок, разбор JSON.
Пример стандартного URL: http://server/base/odata/standard.odata/
"""
import logging
from typing import Any, Dict, Iterable, List, Optional

import requests

logger = logging.getLogger(__name__)

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


class ODataError(Exception):
    """Ошибка обращения к OData-интерфейсу 1С."""


class ODataClient:
    """Инкапсулирует доступ к OData-интерфейсу конкретной базы 1С."""

    def __init__(self, base_url: str, username: str, password: str,
                 timeout: int = 15, verify_ssl: bool = True):
        # Нормализуем URL: добавляем завершающий слэш
        self.base_url = base_url.rstrip('/') + '/'
        self.timeout = timeout
        self._session = requests.Session()
        self._session.auth = (username, password)
        self._session.headers.update({'Accept': 'application/json'})
        self._session.verify = verify_ssl

    # ------------------------------------------------------------------ core
    def _get(self, entity: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """GET-запрос к сущности с обработкой ошибок и таймаутов."""
        url = f'{self.base_url}{entity}'
        try:
            resp = self._session.get(url, params=params, timeout=self.timeout)
        except requests.Timeout:
            raise ODataError(f'Таймаут запроса к {url} ({self.timeout} с)')
        except requests.RequestException as exc:
            raise ODataError(f'Ошибка соединения с {url}: {exc}') from exc

        if resp.status_code >= 400:
            raise ODataError(
                f'HTTP {resp.status_code} от {url}: {resp.text[:300]}')

        try:
            return resp.json()
        except ValueError as exc:
            raise ODataError(f'Некорректный JSON от {url}') from exc

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

    # ------------------------------------------------------- типовые запросы
    def get_organizations(self) -> List[Dict[str, Any]]:
        """Клиенты: справочник Catalog_Организации главной базы 1С.

        Поля: Ref_Key, Наименование, ИНН, DeletionMark
        (0 — активный, 1 — неактивный).
        """
        return self.query(
            'Catalog_Организации',
            select=['Ref_Key', 'Наименование', 'ИНН', 'DeletionMark'],
            order_by='Наименование',
        )

    def get_contractors(self) -> List[Dict[str, Any]]:
        """Контрагенты: справочник Catalog_Контрагенты главной базы 1С.

        Поля: Ref_Key, НаименованиеПолное, ИНН, КПП, Description.
        """
        return self.query(
            'Catalog_Контрагенты',
            select=['Ref_Key', 'НаименованиеПолное', 'ИНН', 'КПП', 'Description'],
            order_by='НаименованиеПолное',
        )

    def get_contracts(self) -> List[Dict[str, Any]]:
        """Договоры: справочник Catalog_Договоры базы «1С:Бухгалтерия» клиента.

        Поля: Ref_Key, Контрагент, Наименование, Дата, СрокДействия,
        Статус, Сумма, Description.
        """
        return self.query(
            'Catalog_Договоры',
            select=['Ref_Key', 'Контрагент', 'Наименование', 'Дата',
                    'СрокДействия', 'Статус', 'Сумма', 'Description'],
            order_by='Дата desc',
        )

    def get_documents(self, entity: str = None,
                      date_from=None, date_to=None, client_key: str = None,
                      contractor_key: str = None, top: int = 500,
                      entities: Iterable[str] = None) -> List[Dict[str, Any]]:
        """Документы из «1С:Документооборот».

        Если ``entity`` или ``entities`` не заданы, опрашиваются все виды
        документов из :data:`DOCUMENT_ENTITIES`. По каждому документу 1С
        возвращает поля: Ref_Key, Организация, Контрагент (может быть
        пустым), Номер (Number), Дата (Date), Статус.

        В каждую строку добавляется вычисленное поле ``ВидДокумента`` —
        имя сущности без префикса ``Document_``.
        """
        if entity:
            entity_list = [entity]
        elif entities:
            entity_list = list(entities)
        else:
            entity_list = list(DOCUMENT_ENTITIES)

        documents: List[Dict[str, Any]] = []
        for ent in entity_list:
            conditions = []
            if date_from:
                conditions.append(f"Date ge datetime'{date_from.isoformat()}T00:00:00'")
            if date_to:
                conditions.append(f"Date le datetime'{date_to.isoformat()}T23:59:59'")
            if client_key:
                conditions.append(f"Организация eq guid'{client_key}'")
            if contractor_key:
                conditions.append(f"Контрагент eq guid'{contractor_key}'")
            filt = ' and '.join(conditions) or None

            rows = self.query(
                ent,
                select=['Ref_Key', 'Number', 'Date', 'Организация',
                        'Контрагент', 'Статус'],
                filt=filt,
                order_by='Date desc',
                top=top,
            )
            doc_type = document_type(ent)
            for row in rows:
                item = dict(row)
                item['ВидДокумента'] = doc_type
                documents.append(item)
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
