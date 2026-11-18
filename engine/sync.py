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
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from engine import circuit_breaker
from engine.models import (Client, Contractor, Contract, Document,
                           OneCSetting, close_archived_client_contracts, db)
from engine.odata_client import (DOCUMENT_ENTITIES, ODataError,
                                 client_for_client, client_for_settings,
                                 document_object_url, incremental_options)
from engine.secret_storage import SecretDecryptionError
from engine.sync_support import (advance_watermark, incremental_since,
                                 record_sync_error, resolve_sync_errors,
                                 sanitize_message)

logger = logging.getLogger(__name__)


def _incremental():
    """Возвращает параметры инкрементальной выборки или отключённый набор."""
    enabled, change_field, deletion_field, _overlap = incremental_options()
    if enabled and not change_field:
        logger.warning(
            'Инкрементальная синхронизация включена без поля изменений — '
            'используется полная выборка')
        enabled = False
    return enabled, change_field or None, deletion_field or None


def _sync_error(scope, message, exc=None, client=None, entity='',
                external_ref=''):
    """Записать ошибку в очередь и лог, не меняя текст stats['errors']."""
    text = sanitize_message(message)
    logger.warning('Ошибка синхронизации [%s]: %s', scope, text,
                   exc_info=exc is not None)
    return record_sync_error(scope, text, client=client, entity=entity,
                             external_ref=external_ref)


def _commit_with_error(scope, stats, message, client=None):
    """Зафиксировать изменения; при сбое коммита сохранить ошибку в очереди."""
    try:
        db.session.commit()
    except Exception as exc:  # сессия после неудачного коммита «испорчена»
        db.session.rollback()
        _sync_error(scope, message, exc=exc, client=client)
        db.session.commit()
        stats['errors'].append(sanitize_message(message))


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


def _index_by(rows, field: str) -> dict:
    """Индекс ORM-объектов по непустому полю (одна выборка вместо N)."""
    return {getattr(row, field): row for row in rows if getattr(row, field)}


def _index_by_refs(model, refs, chunk_size: int = 500) -> dict:
    """Индекс только по интересующим external_ref (порциями по 500).

    Полная выборка справочника при каждом импорте документов лишняя:
    из 1С за раз приходит ограниченный набор ссылок. Порции нужны,
    чтобы не упираться в лимит SQL-параметров SQLite/PostgreSQL.
    """
    values = sorted({_clean(ref) for ref in refs if _clean(ref)})
    index = {}
    for start in range(0, len(values), chunk_size):
        chunk = values[start:start + chunk_size]
        for row in model.query.filter(model.external_ref.in_(chunk)).all():
            index[row.external_ref] = row
    return index


# --------------------------------------------------------------- организации
def _upsert_client(row: dict, by_ref: dict, by_inn: dict,
                   deletion_field: str = 'DeletionMark') -> str:
    """Создать или обновить клиента из Catalog_Организации."""
    ref_key = _clean(row.get('Ref_Key'))
    inn = _clean(row.get('ИНН'))
    name = _clean(row.get('Наименование')) or _clean(row.get('Description'))
    name = name or '(без наименования)'
    status = ('archived' if _is_deletion_mark(row.get(deletion_field))
              else 'active')

    client = (by_ref.get(ref_key) if ref_key else None)
    if client is None and inn:
        client = by_inn.get(inn)

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
    # Обновляем индексы: повторные Ref_Key/ИНН в ответе 1С должны
    # попадать в ту же запись, а не создавать дубликаты.
    if ref_key:
        by_ref[ref_key] = client
    if inn:
        by_inn[inn] = client
    return action


# ----------------------------------------------------------------- контрагенты
def _upsert_contractor(row: dict, by_ref: dict, by_inn: dict,
                       deletion_field: str = 'DeletionMark') -> str:
    """Создать или обновить контрагента из Catalog_Контрагенты.

    Статус (``Contractor.status``) вычисляется из ``DeletionMark`` так же,
    как у клиентов: 1 — «архивный», иначе — «активный».
    Заметки (``Contractor.notes``) — внутреннее поле пользователя:
    синхронизация их не читает и не перезаписывает.
    """
    ref_key = _clean(row.get('Ref_Key'))
    full_name = (_clean(row.get('НаименованиеПолное'))
                 or '(без наименования)')
    inn = _clean(row.get('ИНН'))
    kpp = _clean(row.get('КПП'))
    status = ('archived' if _is_deletion_mark(row.get(deletion_field))
              else 'active')

    contractor = (by_ref.get(ref_key) if ref_key else None)
    if contractor is None and inn:
        contractor = by_inn.get(inn)

    if contractor is None:
        contractor = Contractor(external_ref=ref_key or None)
        db.session.add(contractor)
        action = 'created'
    else:
        action = 'updated'

    contractor.full_name = full_name
    contractor.inn = inn or None
    contractor.kpp = kpp or None
    contractor.status = status
    if ref_key:
        by_ref[ref_key] = contractor
    if inn:
        by_inn[inn] = contractor
    return action


# ------------------------------------------------------------------ синхронизация
def sync_clients() -> dict:
    """Синхронизация клиентов и контрагентов из главной базы 1С.

    Вызывается планировщиком и вручную со страницы «1С База».
    Ручные атрибуты клиентов (ответственный, заметки, реквизиты
    подключения к «1С:Бухгалтерия») не перезаписываются.
    """
    stats: dict[str, Any] = {'created': 0, 'updated': 0,
                             'contractors_created': 0, 'contractors_updated': 0,
                             'contracts_closed': 0, 'errors': []}
    setting = OneCSetting.get()
    if not setting.odata_url:
        msg = 'Не настроено подключение к базе 1С:Предприятие'
        stats['errors'].append(msg)
        _sync_error('clients', msg)
        _commit_with_error('clients', stats,
                           'Не удалось сохранить ошибку синхронизации клиентов')
        return stats

    incremental, change_field, deletion_field = _incremental()
    since_clients = incremental_since('clients') if incremental else None
    since_contractors = incremental_since('contractors') if incremental else None
    started = datetime.utcnow()
    api = None
    try:
        api = client_for_settings(setting)
        organizations = api.get_organizations(
            since=since_clients, change_field=change_field,
            deletion_field=deletion_field)
        contractors = api.get_contractors(
            since=since_contractors, change_field=change_field,
            deletion_field=deletion_field)
    except SecretDecryptionError as exc:
        msg = f'Не удалось расшифровать пароль подключения к 1С: {exc}'
        stats['errors'].append(msg)
        _sync_error('clients', msg, exc=exc)
        _commit_with_error('clients', stats,
                           'Не удалось сохранить ошибку синхронизации клиентов')
        return stats
    except ODataError as exc:
        msg = f'Ошибка подключения к 1С: {exc}'
        logger.warning(msg)
        stats['errors'].append(msg)
        _sync_error('clients', msg, exc=exc)
        _commit_with_error('clients', stats,
                           'Не удалось сохранить ошибку синхронизации клиентов')
        return stats
    except Exception as exc:
        msg = f'Неожиданная ошибка подключения к 1С: {sanitize_message(exc)}'
        logger.exception(msg)
        stats['errors'].append(msg)
        _sync_error('clients', msg, exc=exc)
        _commit_with_error('clients', stats,
                           'Не удалось сохранить ошибку синхронизации клиентов')
        return stats
    finally:
        if api is not None:
            api.close()

    all_clients = Client.query.all()
    all_contractors = Contractor.query.all()
    clients_by_ref = _index_by(all_clients, 'external_ref')
    clients_by_inn = _index_by(all_clients, 'inn')
    contractors_by_ref = _index_by(all_contractors, 'external_ref')
    contractors_by_inn = _index_by(all_contractors, 'inn')

    for row in organizations:
        try:
            action = _upsert_client(
                row, clients_by_ref, clients_by_inn,
                deletion_field=deletion_field or 'DeletionMark')
            stats[action] += 1
        except Exception as exc:  # защита от битых строк
            logger.exception('Ошибка обработки организации: %r', row)
            msg = f'Организация: {exc}'
            stats['errors'].append(msg)
            _sync_error('clients', msg, exc=exc, entity='organization',
                        external_ref=_clean(row.get('Ref_Key')))

    for row in contractors:
        try:
            action = _upsert_contractor(
                row, contractors_by_ref, contractors_by_inn,
                deletion_field=deletion_field or 'DeletionMark')
            key = 'contractors_created' if action == 'created' else 'contractors_updated'
            stats[key] += 1
        except Exception as exc:  # защита от битых строк
            logger.exception('Ошибка обработки контрагента: %r', row)
            msg = f'Контрагент: {exc}'
            stats['errors'].append(msg)
            _sync_error('clients', msg, exc=exc, entity='contractor',
                        external_ref=_clean(row.get('Ref_Key')))

    # Правило системы: у архивного клиента не может быть проектов
    # и действующих договоров. Закрываем их после обновления статусов.
    stats['contracts_closed'] = close_archived_client_contracts()

    setting.last_sync_at = datetime.utcnow()
    if setting not in db.session:
        db.session.add(setting)
    if not stats['errors']:
        if incremental:
            advance_watermark('clients', organizations, change_field,
                              fallback=started)
            advance_watermark('contractors', contractors, change_field,
                              fallback=started)
        resolve_sync_errors('clients', 0)
        resolve_sync_errors('contractors', 0)
    _commit_with_error('clients', stats,
                       'Не удалось сохранить результат синхронизации клиентов')
    logger.info('Синхронизация 1С завершена: %s', stats)
    return stats


# --------------------------------------------------------------------- договоры
def sync_contracts(client: Client, contracts_by_ref=None,
                   contractors_by_ref=None, force: bool = False) -> dict:
    """Загрузить договоры клиента из его базы «1С:Бухгалтерия».

    Индексы договоров и контрагентов можно передать из
    :func:`sync_all_contracts`, чтобы не строить их заново для каждого
    клиента. Индекс договоров обновляется при создании новых записей.
    ``force=True`` используется ручным retry и обходит circuit breaker.
    """
    stats: dict[str, Any] = {'created': 0, 'updated': 0, 'errors': []}
    if not client.odata_url:
        msg = (f'У клиента «{client.name}» не настроено '
               'подключение к 1С:Бухгалтерия')
        stats['errors'].append(msg)
        _sync_error('contracts', msg, client=client)
        _commit_with_error('contracts', stats,
                           'Не удалось сохранить ошибку загрузки договоров',
                           client=client)
        return stats

    if not force:
        allowed, reason = circuit_breaker.check(client.id)
        if not allowed:
            stats['errors'].append(reason)
            _sync_error('contracts', reason, client=client)
            _commit_with_error('contracts', stats,
                               'Не удалось сохранить ошибку загрузки договоров',
                               client=client)
            return stats

    incremental, change_field, deletion_field = _incremental()
    since = (incremental_since('contracts', client.id)
             if incremental else None)
    started = datetime.utcnow()
    api = None
    try:
        api = client_for_client(client)
        rows = api.get_contracts(
            since=since, change_field=change_field,
            deletion_field=deletion_field)
    except SecretDecryptionError as exc:
        msg = f'Клиент «{client.name}»: {exc}'
        stats['errors'].append(msg)
        circuit_breaker.record_failure(client.id, exc, permanent=True)
        _sync_error('contracts', msg, exc=exc, client=client)
        _commit_with_error('contracts', stats,
                           'Не удалось сохранить ошибку загрузки договоров',
                           client=client)
        return stats
    except ODataError as exc:
        msg = f'Клиент «{client.name}»: {exc}'
        logger.warning('Ошибка загрузки договоров: %s', msg)
        stats['errors'].append(msg)
        circuit_breaker.record_failure(client.id, exc)
        _sync_error('contracts', msg, exc=exc, client=client)
        _commit_with_error('contracts', stats,
                           'Не удалось сохранить ошибку загрузки договоров',
                           client=client)
        return stats
    except Exception as exc:
        msg = (f'Клиент «{client.name}»: неожиданная ошибка 1С: '
               f'{sanitize_message(exc)}')
        logger.exception(msg)
        stats['errors'].append(msg)
        circuit_breaker.record_failure(client.id, exc)
        _sync_error('contracts', msg, exc=exc, client=client)
        _commit_with_error('contracts', stats,
                           'Не удалось сохранить ошибку загрузки договоров',
                           client=client)
        return stats
    finally:
        if api is not None:
            api.close()

    if contracts_by_ref is None:
        contracts_by_ref = _index_by(Contract.query.all(), 'external_ref')
    if contractors_by_ref is None:
        contractors_by_ref = _index_by(Contractor.query.all(), 'external_ref')

    for row in rows:
        ref_key = ''
        try:
            ref_key = _clean(row.get('Ref_Key'))
            number = _clean(row.get('Наименование')) or '(без номера)'
            contractor_ref = _clean(row.get('Контрагент'))
            contractor = contractors_by_ref.get(contractor_ref) if contractor_ref else None

            contract = contracts_by_ref.get(ref_key) if ref_key else None
            if contract is None:
                contract = Contract(number=number, client_id=client.id,
                                    external_ref=ref_key or None,
                                    processed=False)
                db.session.add(contract)
                action = 'created'
            else:
                action = 'updated'
            if ref_key:
                contracts_by_ref[ref_key] = contract

            contract.client_id = client.id
            contract.number = number
            contract.signed_date = (_parse_date(row.get('Дата'))
                                    or contract.signed_date or date.today())
            contract.end_date = _parse_date(row.get('СрокДействия'))
            contract.status_1c = _clean(row.get('Статус'))
            status = _map_contract_status(contract.status_1c)
            # У архивного клиента не может быть проектов и действующих
            # договоров: из 1С они загружаются как завершённые.
            if client.status != 'active' and status in Contract.OPEN_STATUSES:
                status = 'closed'
            # Tombstone из инкрементальной выборки также должен закрывать договор:
            # иначе удалённый в 1С объект останется действующим в реестре.
            if (deletion_field
                    and _is_deletion_mark(row.get(deletion_field))
                    and status in Contract.OPEN_STATUSES):
                status = 'closed'
            contract.status = status
            contract.amount = _parse_amount(row.get('Сумма'))
            description = _clean(row.get('Description'))
            contract.description = description
            contract.contractor_id = contractor.id if contractor else None
            stats[action] += 1
        except Exception as exc:  # защита от битых строк
            logger.exception('Ошибка обработки договора: %r', row)
            msg = f'Договор: {exc}'
            stats['errors'].append(msg)
            _sync_error('contracts', msg, exc=exc, client=client,
                        entity='contract', external_ref=ref_key)

    # Даже если в ответе 1С договора не было, у архивного клиента
    # не должно остаться открытых договоров.
    if client.status != 'active':
        client.close_open_contracts()

    if not stats['errors']:
        circuit_breaker.record_success(client.id)
        resolve_sync_errors('contracts', client.id)
        if incremental:
            advance_watermark('contracts', rows, change_field,
                              client_id=client.id, fallback=started)
    _commit_with_error('contracts', stats,
                       'Не удалось сохранить результат загрузки договоров',
                       client=client)
    logger.info('Договоры клиента «%s»: %s', client.name, stats)
    return stats


def sync_all_contracts(force: bool = False) -> dict:
    """Загрузить договоры по всем клиентам с настроенным подключением."""
    stats: dict[str, Any] = {'clients': 0, 'created': 0, 'updated': 0,
                             'errors': []}
    clients = Client.query.filter(Client.odata_url != '').order_by(Client.name).all()
    # Строим индексы один раз на всю операцию: раньше каждый клиент
    # выбирал все договоры и всех контрагентов заново.
    contracts_by_ref = _index_by(Contract.query.all(), 'external_ref')
    contractors_by_ref = _index_by(Contractor.query.all(), 'external_ref')
    for client in clients:
        stats['clients'] += 1
        result = sync_contracts(client, contracts_by_ref, contractors_by_ref,
                                force=force)
        stats['created'] += result['created']
        stats['updated'] += result['updated']
        stats['errors'].extend(result['errors'])
    return stats


# --------------------------------------------------------------------- документы
# Размер пакета при сохранении документов: коммит каждые N записей
# уменьшает число транзакций, но сохраняет прогресс при больших выборках.
_DOCUMENTS_COMMIT_BATCH = 100


def sync_documents(settings=None, entities=None, days: int = 30) -> int:
    """Загрузка документов из «1С:Документооборот» (ФТ-4).

    Опрашивает перечень видов документов (по умолчанию все сущности
    из DOCUMENT_ENTITIES) и сохраняет реквизиты: вид документа, номер,
    дату, статус, клиента и контрагента. «Вид документа» вычисляется
    из имени сущности 1С (без префикса Document_).

    Контрагент может быть не указан — тогда contractor_id = NULL.
    Документы с неизвестной организацией (клиентом) пропускаются.
    По умолчанию запрашиваются документы за ``days`` дней (30).
    Возвращается количество обработанных записей — сигнатура сохраняется
    для совместимости со всеми вызывающими.
    """
    stats: dict[str, Any] = {'errors': []}
    setting = settings or OneCSetting.get()
    if not setting.odata_url:
        msg = 'Не настроено подключение к базе 1С:Документооборот'
        stats['errors'].append(msg)
        _sync_error('documents', msg)
        _commit_with_error('documents', stats,
                           'Не удалось сохранить ошибку загрузки документов')
        return 0

    entity_list = list(entities) if entities else list(DOCUMENT_ENTITIES)
    date_from = (date.today() - timedelta(days=int(days))
                 if days is not None else None)
    incremental, change_field, deletion_field = _incremental()
    since = incremental_since('documents') if incremental else None
    started = datetime.utcnow()
    api = None
    rows = []
    try:
        api = client_for_settings(setting)
        rows = api.get_documents(
            entities=entity_list, top=500, date_from=date_from,
            change_field=change_field, since=since,
            deletion_field=deletion_field)
    except SecretDecryptionError as exc:
        msg = f'Не удалось расшифровать пароль подключения к 1С: {exc}'
        stats['errors'].append(msg)
        _sync_error('documents', msg, exc=exc)
        _commit_with_error('documents', stats,
                           'Не удалось сохранить ошибку загрузки документов')
        return 0
    except ODataError as exc:
        msg = f'Ошибка загрузки документов: {exc}'
        logger.warning(msg)
        stats['errors'].append(msg)
        _sync_error('documents', msg, exc=exc)
        _commit_with_error('documents', stats,
                           'Не удалось сохранить ошибку загрузки документов')
        return 0
    except Exception as exc:
        msg = f'Неожиданная ошибка загрузки документов: {sanitize_message(exc)}'
        logger.exception(msg)
        stats['errors'].append(msg)
        _sync_error('documents', msg, exc=exc)
        _commit_with_error('documents', stats,
                           'Не удалось сохранить ошибку загрузки документов')
        return 0
    finally:
        if api is not None:
            api.close()

    # Выбираем только те справочники и документы, ссылки на которые
    # реально пришли из 1С, вместо полных SELECT по всем таблицам.
    doc_refs = [row.get('Ref_Key') for row in rows]
    client_refs = [row.get('Организация') for row in rows]
    contractor_refs = [row.get('Контрагент') for row in rows]
    clients_by_ref = _index_by_refs(Client, client_refs)
    contractors_by_ref = _index_by_refs(Contractor, contractor_refs)
    docs_by_ref = _index_by_refs(Document, doc_refs)

    processed = 0
    for row in rows:
        ref_key = row.get('Ref_Key')
        try:
            if not ref_key:
                continue

            # Привязка документа к клиенту по внешней ссылке клиента
            client_key = row.get('Организация') or ''
            client = clients_by_ref.get(client_key) if client_key else None
            if client is None:
                continue

            # Контрагент необязателен: пустое или неизвестное значение
            # сохраняется как отсутствие контрагента.
            contractor_key = row.get('Контрагент') or ''
            contractor = (contractors_by_ref.get(contractor_key)
                          if contractor_key else None)

            doc = docs_by_ref.get(ref_key)
            if doc is None:
                doc = Document(external_ref=ref_key, processed=False)
                db.session.add(doc)
                docs_by_ref[ref_key] = doc
            doc.client_id = client.id
            doc.contractor_id = contractor.id if contractor else None
            doc.doc_type = str(row.get('ВидДокумента') or '')
            doc.number = str(row.get('Номер') or row.get('Number') or '')
            doc.doc_date = _parse_date(row.get('Дата') or row.get('Date'))
            doc.status = str(row.get('Статус') or '')
            # Удалённый в 1С документ не должен выглядеть активным.
            if deletion_field and _is_deletion_mark(row.get(deletion_field)):
                doc.status = 'отменен'
            # Ссылка на объект в «1С:Документооборот»: явное поле ответа 1С,
            # адрес ресурса OData или deep link веб-клиента.
            external_url: Optional[str] = _clean(
                row.get('СсылкаНаОбъект') or row.get('Ссылка')
                or row.get('URL') or row.get('Hyperlink'))
            if not external_url:
                external_url = document_object_url(
                    setting.odata_url, f'Document_{doc.doc_type}', ref_key, row)
            doc.external_url = external_url or None
            processed += 1
            if processed % _DOCUMENTS_COMMIT_BATCH == 0:
                _commit_documents_batch(stats)
        except Exception as exc:  # защита от битых строк
            logger.exception('Ошибка обработки документа: %r', row)
            msg = f'Документ: {exc}'
            stats['errors'].append(msg)
            _sync_error('documents', msg, exc=exc, entity='document',
                        external_ref=_clean(ref_key))

    if not stats['errors']:
        if incremental:
            advance_watermark('documents', rows, change_field,
                              client_id=0, fallback=started)
        resolve_sync_errors('documents', 0)
    _commit_with_error('documents', stats,
                       'Не удалось сохранить результат загрузки документов')
    return processed


def _commit_documents_batch(stats):
    """Промежуточный коммит загрузки документов с защитой сессии."""
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        msg = f'Не удалось сохранить пакет документов: {sanitize_message(exc)}'
        stats['errors'].append(msg)
        _sync_error('documents', msg, exc=exc)
