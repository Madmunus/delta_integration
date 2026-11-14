"""Синхронизация данных из «1С:Предприятие» и «1С:Бухгалтерия».

Главная база (одна настройка OneCSetting):
  * Catalog_Организации → клиенты реестра;
  * Catalog_Контрагенты → справочник контрагентов.

Каждый клиент может иметь собственные реквизиты подключения к своей
базе «1С:Бухгалтерия»; по ним загружается справочник Catalog_Договоры.
Документы загружаются из «1С:Документооборот» по видам Document_*;
поле «Вид документа» вычисляется из имени сущности.
"""
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from engine.models import (Client, Contractor, Contract, Document,
                           OneCSetting, db)
from engine.odata_client import (DOCUMENT_ENTITIES, ODataError,
                                 client_for_client, client_for_settings,
                                 document_object_url)

logger = logging.getLogger(__name__)


def _clean(value) -> str:
    return str(value).strip() if value is not None else ''


def _is_deletion_mark(value) -> bool:
    """DeletionMark: 0/False — активен, 1/True — неактивен."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return _clean(value).lower() in ('1', 'true', 'да', 'yes')


def _parse_date(value):
    """Дата из OData (ISO, ISO с временем или ДД.ММ.ГГГГ)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _clean(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    try:
        return datetime.strptime(text, '%d.%m.%Y').date()
    except ValueError:
        return None


def _parse_amount(value) -> Decimal:
    if value is None or value == '':
        return Decimal('0')
    try:
        return Decimal(str(value).replace(',', '.'))
    except (InvalidOperation, ValueError):
        return Decimal('0')


def _map_contract_status(raw: str) -> str:
    """Привести «Статус» из 1С к внутренним draft/active/closed."""
    text = _clean(raw).lower()
    if not text:
        return 'draft'
    if any(word in text for word in ('действ', 'актив', 'подпис', 'active')):
        return 'active'
    if any(word in text for word in ('заверш', 'закрыт', 'расторг', 'closed')):
        return 'closed'
    return 'draft'


# --------------------------------------------------------------- организации
def _upsert_client(row: dict) -> str:
    """Создать или обновить клиента из Catalog_Организации."""
    ref_key = _clean(row.get('Ref_Key'))
    inn = _clean(row.get('ИНН'))
    name = _clean(row.get('Наименование')) or _clean(row.get('Description'))
    name = name or '(без наименования)'
    status = 'archived' if _is_deletion_mark(row.get('DeletionMark')) else 'active'

    client = None
    if ref_key:
        client = Client.query.filter_by(external_ref=ref_key).first()
    if client is None and inn:
        client = Client.query.filter_by(inn=inn).first()

    if client is None:
        client = Client(name=name, inn=inn or None, status=status,
                        external_ref=ref_key or None)
        db.session.add(client)
        action = 'created'
    else:
        client.name = name
        if inn:
            client.inn = inn
        client.status = status
        if ref_key:
            client.external_ref = ref_key
        action = 'updated'
    return action


# ----------------------------------------------------------------- контрагенты
def _upsert_contractor(row: dict) -> str:
    """Создать или обновить контрагента из Catalog_Контрагенты."""
    ref_key = _clean(row.get('Ref_Key'))
    full_name = (_clean(row.get('НаименованиеПолное'))
                 or _clean(row.get('Description'))
                 or '(без наименования)')
    inn = _clean(row.get('ИНН'))
    kpp = _clean(row.get('КПП'))
    description = _clean(row.get('Description'))

    contractor = None
    if ref_key:
        contractor = Contractor.query.filter_by(external_ref=ref_key).first()
    if contractor is None and inn:
        contractor = Contractor.query.filter_by(inn=inn).first()

    if contractor is None:
        contractor = Contractor(external_ref=ref_key or None)
        db.session.add(contractor)
        action = 'created'
    else:
        action = 'updated'

    contractor.full_name = full_name
    contractor.inn = inn or None
    contractor.kpp = kpp or None
    contractor.description = description
    return action


# ------------------------------------------------------------------ синхронизация
def sync_clients() -> dict:
    """Синхронизация клиентов и контрагентов из главной базы 1С.

    Вызывается планировщиком и вручную со страницы «1С База».
    Ручные атрибуты клиентов (ответственный, заметки, реквизиты
    подключения к «1С:Бухгалтерия») не перезаписываются.
    """
    stats = {'created': 0, 'updated': 0,
             'contractors_created': 0, 'contractors_updated': 0,
             'errors': []}
    setting = OneCSetting.get()
    if not setting.odata_url:
        stats['errors'].append('Не настроено подключение к базе 1С:Предприятие')
        return stats

    api = client_for_settings(setting)
    try:
        organizations = api.get_organizations()
        contractors = api.get_contractors()
    except ODataError as exc:
        msg = f'Ошибка подключения к 1С: {exc}'
        logger.warning(msg)
        stats['errors'].append(msg)
        return stats
    finally:
        api.close()

    for row in organizations:
        try:
            stats[_upsert_client(row)] += 1
        except Exception as exc:  # защита от битых строк
            logger.exception('Ошибка обработки организации: %r', row)
            stats['errors'].append(f'Организация: {exc}')

    for row in contractors:
        try:
            action = _upsert_contractor(row)
            key = 'contractors_created' if action == 'created' else 'contractors_updated'
            stats[key] += 1
        except Exception as exc:  # защита от битых строк
            logger.exception('Ошибка обработки контрагента: %r', row)
            stats['errors'].append(f'Контрагент: {exc}')

    setting.last_sync_at = datetime.utcnow()
    if setting not in db.session:
        db.session.add(setting)
    db.session.commit()
    logger.info('Синхронизация 1С завершена: %s', stats)
    return stats


# --------------------------------------------------------------------- договоры
def sync_contracts(client: Client) -> dict:
    """Загрузить договоры клиента из его базы «1С:Бухгалтерия»."""
    stats = {'created': 0, 'updated': 0, 'errors': []}
    if not client.odata_url:
        stats['errors'].append(
            f'У клиента «{client.name}» не настроено подключение к 1С:Бухгалтерия')
        return stats

    api = client_for_client(client)
    try:
        rows = api.get_contracts()
    except ODataError as exc:
        msg = f'Клиент «{client.name}»: {exc}'
        logger.warning('Ошибка загрузки договоров: %s', msg)
        stats['errors'].append(msg)
        return stats
    finally:
        api.close()

    for row in rows:
        try:
            ref_key = _clean(row.get('Ref_Key'))
            number = _clean(row.get('Наименование')) or '(без номера)'
            contractor_ref = _clean(row.get('Контрагент'))
            contractor = (Contractor.query.filter_by(external_ref=contractor_ref).first()
                          if contractor_ref else None)

            contract = (Contract.query.filter_by(external_ref=ref_key).first()
                        if ref_key else None)
            if contract is None:
                contract = Contract(number=number, client_id=client.id,
                                    external_ref=ref_key or None,
                                    processed=False)
                db.session.add(contract)
                action = 'created'
            else:
                action = 'updated'

            contract.client_id = client.id
            contract.number = number
            contract.signed_date = (_parse_date(row.get('Дата'))
                                    or contract.signed_date or date.today())
            contract.end_date = _parse_date(row.get('СрокДействия'))
            contract.status_1c = _clean(row.get('Статус'))
            contract.status = _map_contract_status(row.get('Статус'))
            contract.amount = _parse_amount(row.get('Сумма'))
            description = _clean(row.get('Description'))
            contract.description = description
            contract.contractor_id = contractor.id if contractor else None
            stats[action] += 1
        except Exception as exc:  # защита от битых строк
            logger.exception('Ошибка обработки договора: %r', row)
            stats['errors'].append(f'Договор: {exc}')

    db.session.commit()
    logger.info('Договоры клиента «%s»: %s', client.name, stats)
    return stats


def sync_all_contracts() -> dict:
    """Загрузить договоры по всем клиентам с настроенным подключением."""
    stats = {'clients': 0, 'created': 0, 'updated': 0, 'errors': []}
    clients = Client.query.filter(Client.odata_url != '').order_by(Client.name).all()
    for client in clients:
        stats['clients'] += 1
        result = sync_contracts(client)
        stats['created'] += result['created']
        stats['updated'] += result['updated']
        stats['errors'].extend(result['errors'])
    return stats


# --------------------------------------------------------------------- документы
def sync_documents(settings=None, entities=None, days: int = 30) -> int:
    """Загрузка документов из «1С:Документооборот» (ФТ-4).

    Опрашивает перечень видов документов (по умолчанию все сущности
    из DOCUMENT_ENTITIES) и сохраняет реквизиты: вид документа, номер,
    дату, статус, клиента и контрагента. «Вид документа» вычисляется
    из имени сущности 1С (без префикса Document_).

    Контрагент может быть не указан — тогда contractor_id = NULL.
    Документы с неизвестной организацией (клиентом) пропускаются.
    Возвращает количество обработанных записей.
    """
    setting = settings or OneCSetting.get()
    if not setting.odata_url:
        return 0

    entity_list = list(entities) if entities else list(DOCUMENT_ENTITIES)
    api = client_for_settings(setting)
    try:
        rows = api.get_documents(entities=entity_list, top=500)
    finally:
        api.close()

    processed = 0
    for row in rows:
        ref_key = row.get('Ref_Key')
        if not ref_key:
            continue

        # Привязка документа к клиенту по внешней ссылке клиента
        client_key = row.get('Организация') or ''
        client = (Client.query.filter_by(external_ref=client_key).first()
                  if client_key else None)
        if client is None:
            continue

        # Контрагент необязателен: пустое или неизвестное значение
        # сохраняется как отсутствие контрагента.
        contractor_key = row.get('Контрагент') or ''
        contractor = (Contractor.query.filter_by(external_ref=contractor_key).first()
                      if contractor_key else None)

        doc = Document.query.filter_by(external_ref=ref_key).first()
        if doc is None:
            doc = Document(external_ref=ref_key)
            db.session.add(doc)
        doc.client_id = client.id
        doc.contractor_id = contractor.id if contractor else None
        doc.doc_type = str(row.get('ВидДокумента') or '')
        doc.number = str(row.get('Номер') or row.get('Number') or '')
        doc.doc_date = _parse_date(row.get('Дата') or row.get('Date'))
        doc.status = str(row.get('Статус') or '')
        # Ссылка на объект в «1С:Документооборот»: явное поле ответа 1С,
        # адрес ресурса OData или deep link веб-клиента.
        external_url = _clean(
            row.get('СсылкаНаОбъект') or row.get('Ссылка')
            or row.get('URL') or row.get('Hyperlink'))
        if not external_url:
            external_url = document_object_url(
                setting.odata_url, f'Document_{doc.doc_type}', ref_key, row)
        doc.external_url = external_url or None
        db.session.commit()
        processed += 1
    return processed
