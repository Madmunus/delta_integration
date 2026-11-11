"""Синхронизация справочных данных из учётных баз «1С».

Для каждой зарегистрированной базы выполняется запрос справочника
контрагентов, полученные записи сопоставляются с существующими клиентами
по ИНН (или Ref_Key) — обновляются либо создаются новые.
"""
import logging
from datetime import datetime

from engine.models import Client, DocumentRef, ODataBase, db
from services.odata_client import ODataError, client_for_base

logger = logging.getLogger(__name__)


def _upsert_client(base: ODataBase, row: dict) -> str:
    """Создать или обновить клиента из строки OData. Возвращает действие."""
    inn = (row.get('ИНН') or '').strip()
    ref_key = row.get('Ref_Key') or ''
    name = (row.get('Description') or '').strip() or '(без наименования)'
    kpp = (row.get('КПП') or '').strip()

    client = None
    # Сопоставление: сначала по внешней ссылке, затем по ИНН
    if ref_key:
        client = Client.query.filter_by(external_ref=ref_key).first()
    if client is None and inn:
        client = Client.query.filter_by(inn=inn).first()

    if client is None:
        client = Client(name=name, inn=inn or None, kpp=kpp or None,
                        odata_base_id=base.id, external_ref=ref_key or None)
        db.session.add(client)
        action = 'created'
    else:
        client.name = name
        client.kpp = kpp or client.kpp
        client.odata_base_id = base.id
        if ref_key:
            client.external_ref = ref_key
        action = 'updated'

    # Реквизиты ИНН/КПП не затираем пустыми значениями из 1С
    if not client.inn and inn:
        client.inn = inn
    return action


def sync_clients() -> dict:
    """Синхронизация реестра клиентов из всех включённых учётных баз.

    Вызывается планировщиком (ежечасно) и вручную со страницы баз.
    Ручные атрибуты (статус, ответственный, заметки) не перезаписываются.
    """
    stats = {'bases': 0, 'created': 0, 'updated': 0, 'errors': []}
    for base in ODataBase.query.filter_by(is_enabled=True).all():
        stats['bases'] += 1
        try:
            client_api = client_for_base(base)
            rows = client_api.get_contractors()
            client_api.close()
        except ODataError as exc:
            msg = f'База «{base.name}»: {exc}'
            logger.warning('Ошибка синхронизации: %s', msg)
            stats['errors'].append(msg)
            continue

        for row in rows:
            try:
                action = _upsert_client(base, row)
                stats[action] += 1
            except Exception as exc:  # защита от битых строк
                logger.exception('Ошибка обработки контрагента: %r', row)
                stats['errors'].append(f'База «{base.name}»: {exc}')

        base.last_sync_at = datetime.utcnow()

    db.session.commit()
    logger.info('Синхронизация завершена: %s', stats)
    return stats


def sync_documents(base: ODataBase, entity: str = 'Document_ВходящееПисьмо',
                   days: int = 30) -> int:
    """Загрузка входящих документов из «1С:Документооборот» (ФТ-2).

    Обновляет таблицу DocumentRef: реквизиты документа (тип, номер, дата,
    сумма, статус) и привязка к клиенту по внешней ссылке контрагента
    (Ref_Key). Документы с нераспознанным контрагентом пропускаются.
    Возвращает количество обработанных записей.
    """
    client_api = client_for_base(base)
    try:
        rows = client_api.get_documents(entity=entity, top=500)
    finally:
        client_api.close()

    processed = 0
    for row in rows:
        ref_key = row.get('Ref_Key')
        if not ref_key:
            continue

        # Привязка документа к клиенту по внешней ссылке контрагента
        contractor_key = row.get('Контрагент_Key') or ''
        client = (Client.query.filter_by(external_ref=contractor_key).first()
                  if contractor_key else None)
        if client is None:
            continue

        doc = DocumentRef.query.filter_by(external_ref=ref_key).first()
        if doc is None:
            doc = DocumentRef(external_ref=ref_key)
            db.session.add(doc)
        doc.client_id = client.id
        doc.doc_type = entity.replace('Document_', '')
        doc.number = str(row.get('Number') or '')
        raw_date = row.get('Date')
        if isinstance(raw_date, str) and len(raw_date) >= 10:
            try:
                from datetime import date as _date
                doc.doc_date = _date.fromisoformat(raw_date[:10])
            except ValueError:
                pass
        doc.status = str(row.get('Статус') or '')
        amount = row.get('СуммаДокумента')
        try:
            doc.amount = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            doc.amount = None
        db.session.commit()
        processed += 1
    return processed
