"""Per-client circuit breaker для подключений к «1С:Бухгалтерия».

Состояние привязано к ``Client.id``, а не к URL или паролю: смена реквизитов
не должна «забывать» историю сбоев сама собой. При изменении реквизитов
вызывается :func:`reset`, а ручной retry может явно обойти breaker.
"""
from datetime import datetime, timedelta

from flask import current_app, has_app_context

from engine import odata_metrics
from engine.config import Config
from engine.models import ODataCircuitState, db
from engine.sync_support import sanitize_message


def _setting(name, default):
    if has_app_context():
        return current_app.config.get(name, default)
    return default


def check(client_id: int):
    """Вернуть ``(разрешён, причина)`` для очередной попытки синхронизации."""
    state = db.session.get(ODataCircuitState, int(client_id))
    if state is None or not state.open_until:
        return True, ''
    if state.open_until <= datetime.utcnow():
        # Время блокировки прошло: даём одну пробную попытку.
        return True, ''
    odata_metrics.increment('breaker_skips')
    return False, sanitize_message(
        f'Подключение клиента временно отключено после повторных ошибок 1С '
        f'(до {state.open_until:%d.%m.%Y %H:%M:%S}): {state.last_error}')


def record_success(client_id: int) -> None:
    """Сбросить счётчик ошибок после успешной загрузки."""
    state = db.session.get(ODataCircuitState, int(client_id))
    if state is None:
        return
    state.failures = 0
    state.open_until = None
    state.last_error = ''
    state.last_success_at = datetime.utcnow()


def record_failure(client_id: int, exc, permanent: bool = False) -> None:
    """Учесть ошибку и при необходимости открыть breaker."""
    state = ODataCircuitState.get_or_create(int(client_id))
    state.failures = int(state.failures or 0) + 1
    is_permanent = bool(permanent or getattr(exc, 'permanent', False))
    threshold = max(1, int(_setting(
        'ODATA_CIRCUIT_FAILURE_THRESHOLD',
        Config.ODATA_CIRCUIT_FAILURE_THRESHOLD)))
    seconds = int(_setting(
        'ODATA_CIRCUIT_PERMANENT_COOLDOWN_SECONDS'
        if is_permanent else 'ODATA_CIRCUIT_COOLDOWN_SECONDS',
        Config.ODATA_CIRCUIT_PERMANENT_COOLDOWN_SECONDS
        if is_permanent else Config.ODATA_CIRCUIT_COOLDOWN_SECONDS))
    if is_permanent or state.failures >= threshold:
        state.open_until = datetime.utcnow() + timedelta(seconds=max(1, seconds))
    state.last_error = sanitize_message(str(exc), 500)


def reset(client_id: int) -> None:
    """Полностью сбросить состояние (вызывается при смене реквизитов)."""
    state = db.session.get(ODataCircuitState, int(client_id))
    if state is not None:
        db.session.delete(state)
