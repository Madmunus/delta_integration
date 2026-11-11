"""Клиент OData для взаимодействия с учётными базами «1С» и «1С:Документооборот».

Обёртка над requests: Basic Auth, таймауты, обработка ошибок, разбор JSON.
Пример стандартного URL: http://server/base/odata/standard.odata/
"""
import logging
from typing import Any, Dict, Iterable, List, Optional

import requests

logger = logging.getLogger(__name__)


class ODataError(Exception):
    """Ошибка обращения к OData-интерфейсу 1С."""


class ODataClient:
    """Инкапсулирует доступ к OData-интерфейсу конкретной учётной базы 1С."""

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
        """Универсальная выборка сущности с фильтрацией.

        Пример: client.query('Catalog_Контрагенты',
                             select=['Description', 'ИНН'],
                             filt="DeletionMark eq false")
        """
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
    def get_contractors(self) -> List[Dict[str, Any]]:
        """Справочник контрагентов/организаций: наименование, ИНН, КПП.

        Используется при синхронизации реестра клиентов (ФТ-1).
        В конфигурациях на БСП сущность обычно Catalog_Контрагенты;
        вариант Catalog_Организации — для учётной базы самой фирмы.
        """
        rows = self.query(
            'Catalog_Контрагенты',
            select=['Ref_Key', 'Description', 'ИНН', 'КПП', 'DeletionMark'],
            order_by='Description',
        )
        return [r for r in rows if not r.get('DeletionMark', False)]

    def get_documents(self, entity: str = 'Document_ПоступлениеТоваровУслуг',
                      date_from=None, date_to=None, contractor_key: str = None,
                      top: int = 500) -> List[Dict[str, Any]]:
        """Документы за период с фильтрацией по дате и контрагенту.

        Формирует $filter вида:
        Date ge datetime'2026-01-01T00:00:00' and Date le datetime'...'
        """
        conditions = []
        if date_from:
            conditions.append(f"Date ge datetime'{date_from.isoformat()}T00:00:00'")
        if date_to:
            conditions.append(f"Date le datetime'{date_to.isoformat()}T23:59:59'")
        if contractor_key:
            conditions.append(f"Контрагент_Key eq guid'{contractor_key}'")
        filt = ' and '.join(conditions) or None
        return self.query(
            entity,
            select=['Ref_Key', 'Number', 'Date', 'Контрагент_Key',
                    'СуммаДокумента', 'Статус'],
            filt=filt,
            order_by='Date desc',
            top=top,
        )

    def test_connection(self) -> bool:
        """Проверка доступности сервиса (для формы настройки баз)."""
        try:
            # Дешёвая проверка: корневой запрос к сервису с обработкой ошибок.
            self._session.get(self.base_url, timeout=self.timeout).raise_for_status()
            return True
        except requests.RequestException:
            return False

    def close(self):
        self._session.close()


def client_for_base(odata_base) -> ODataClient:
    """Фабрика: создать клиент для записи ODataBase из модели."""
    return ODataClient(
        base_url=odata_base.odata_url,
        username=odata_base.username,
        password=odata_base.password,
    )
