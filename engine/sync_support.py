"""Общие помощники надёжности синхронизации с 1С.

Здесь нет сетевого кода: модуль хранит очередь ошибок и водяные знаки,
а также очищает тексты исключений перед сохранением в базу.
"""
import logging
import re
from datetime import date, datetime, timedelta

from flask import current_app, has_app_context

from engine import odata_metrics
from engine.config import Config
from engine.models import SyncError, SyncState, db

logger = logging.getLogger(__name__)

_SECRET_IN_URL = re.compile(r'(?i)(https?://)[^/@\s]+@')
_SECRET_IN_QUERY = re.compile(
    r'(?i)([?&](?:password|pwd|pass|token|access_token|key)=)[^&\s]+')


def _setting(name, default):
    if has_app_context():
        return current_app.config.get(name, default)
    return default


def sanitize_message(text, limit: int = 1000) -> str:
    """Убрать возможные реквизиты из текста ошибки и ограничить длину."""
    value = str(text or '')
    value = _SECRET_IN_URL.sub(r'\1***@', value)
    value = _SECRET_IN_QUERY.sub(r'\1***', value)
    return value[:limit]


def record_sync_error(scope: str, message: str, client=None, entity: str = '',
                      external_ref: str = '') -> SyncError:
    """Создать или обновить активную ошибку синхронизации.

    Повтор той же ошибки увеличивает счётчик попыток вместо создания
    сотен почти одинаковых строк. Функция не делает commit — вызывающий
    фиксирует изменения вместе с результатом синхронизации.
    """
    client_id = client.id if client is not None else None
    text = sanitize_message(message)
    item = SyncError.query.filter_by(
        scope=scope, client_id=client_id, entity=entity or '',
        external_ref=external_ref or '', status=SyncError.STATUS_PENDING,
    ).first()
    if item is None:
        delay = max(0, int(_setting(
            'SYNC_ERROR_RETRY_DELAY_SECONDS',
            Config.SYNC_ERROR_RETRY_DELAY_SECONDS)))
        item = SyncError(
            scope=scope, client_id=client_id, entity=entity or '',
            external_ref=external_ref or '', message=text, attempts=1,
            status=SyncError.STATUS_PENDING,
            next_retry_at=datetime.utcnow() + timedelta(seconds=delay))
        db.session.add(item)
    else:
        item.attempts = min(999999, int(item.attempts or 0) + 1)
        item.message = text
        item.updated_at = datetime.utcnow()
    odata_metrics.increment('sync_errors')
    return item


def resolve_sync_errors(scope: str, client_id=None) -> int:
    """Отметить активные ошибки области как устранённые."""
    query = SyncError.query.filter_by(status=SyncError.STATUS_PENDING,
                                      scope=scope)
    if client_id is not None:
        query = query.filter_by(client_id=int(client_id))
    items = query.all()
    now = datetime.utcnow()
    for item in items:
        item.status = SyncError.STATUS_RESOLVED
        item.resolved_at = now
    return len(items)


def parse_change_datetime(value):
    """Привести значение ``ДатаИзменения`` из 1С к naive UTC datetime."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value or '').strip()
    if not text:
        return None
    if text[:1].isdigit() and len(text) >= 10:
        try:
            return datetime.fromisoformat(
                text.replace('Z', '+00:00')[:19])
        except ValueError:
            pass
    for fmt in ('%d.%m.%Y %H:%M:%S', '%d.%m.%Y %H:%M', '%d.%m.%Y'):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def read_watermark(scope: str, client_id: int = 0):
    """Последняя успешно обработанная дата изменения (или None)."""
    item = SyncState.get(scope, client_id)
    return parse_change_datetime(item.watermark) if item else None


def incremental_since(scope: str, client_id: int = 0):
    """Начало окна выборки с учётом перекрытия для рассинхрона часов."""
    watermark = read_watermark(scope, client_id)
    if watermark is None:
        return None
    overlap = max(0, int(_setting(
        'ODATA_INCREMENTAL_OVERLAP_MINUTES',
        Config.ODATA_INCREMENTAL_OVERLAP_MINUTES)))
    return watermark - timedelta(minutes=overlap)


def advance_watermark(scope: str, rows, change_field: str,
                      client_id: int = 0, fallback=None) -> None:
    """Сдвинуть водяной знак только после полностью успешного прохода.

    За значение берётся максимум из полученных строк: часы 1С и сервера
    могут не совпадать. Если поле изменения отсутствует, используется
    время запуска — повтор всё равно перекроет окно.
    """
    candidates = [parse_change_datetime(row.get(change_field)) for row in rows]
    candidates = [item for item in candidates if item is not None]
    value = max(candidates) if candidates else (fallback or datetime.utcnow())
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day)
    previous = read_watermark(scope, client_id)
    if previous is not None and value <= previous:
        return
    item = SyncState.get(scope, client_id)
    if item is None:
        item = SyncState(scope=scope, client_id=int(client_id))
        db.session.add(item)
    item.watermark = value.isoformat(timespec='seconds')
    item.updated_at = datetime.utcnow()
