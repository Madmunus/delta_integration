"""Ограничение неудачных попыток входа в систему.

Состояние хранится в таблице ``login_attempts``, поэтому лимит общий для
всех воркеров gunicorn и не сбрасывается при рестарте процесса. Ключ включает
IP и нормализованный email: распределённые по IP атаки не обходят блокировку
аккаунта, но разные аккаунты за одним NAT не мешают друг другу.
"""
from datetime import datetime, timedelta

from flask import current_app, has_app_context

from engine.config import Config
from engine.models import LoginAttempt, db


def _setting(name, default):
    if has_app_context():
        return current_app.config.get(name, default)
    return default


def _now():
    """Текущее UTC-время; отдельная функция позволяет подменить часы в тестах."""
    return datetime.utcnow()


def scope(ip, email):
    """Ключ учётной записи для given IP и email."""
    return f'{ip or "-"}|{(email or "").strip().lower()}'


def _window():
    return max(1, int(_setting(
        'LOGIN_FAILURE_WINDOW_SECONDS',
        Config.LOGIN_FAILURE_WINDOW_SECONDS)))


def _lockout():
    return max(1, int(_setting(
        'LOGIN_LOCKOUT_SECONDS', Config.LOGIN_LOCKOUT_SECONDS)))


def _threshold():
    return max(1, int(_setting(
        'LOGIN_FAILURE_THRESHOLD', Config.LOGIN_FAILURE_THRESHOLD)))


def _reset(state, now):
    state.failures = 0
    state.first_failed_at = now
    state.blocked_until = None


def check(key):
    """Вернуть ``(разрешён, секунд до разблокировки)`` для попытки входа."""
    state = db.session.get(LoginAttempt, key)
    if state is None:
        return True, 0
    now = _now()
    if state.blocked_until and state.blocked_until > now:
        left = int((state.blocked_until - now).total_seconds()) + 1
        return False, max(1, left)
    # Блокировка истекла или окно неудач прошло — начинаем новое окно.
    stale = (not state.first_failed_at
             or (now - state.first_failed_at).total_seconds() > _window())
    if state.blocked_until or stale:
        _reset(state, now)
        db.session.commit()
    return True, 0


def record_failure(key):
    """Учесть неудачу; при достижении порога открыть блокировку."""
    state = LoginAttempt.get_or_create(key)
    now = _now()
    if not state.first_failed_at \
            or (now - state.first_failed_at).total_seconds() > _window():
        _reset(state, now)
    state.failures = int(state.failures or 0) + 1
    state.last_failed_at = now
    if state.failures >= _threshold():
        state.blocked_until = now + timedelta(seconds=_lockout())
    db.session.commit()


def record_success(key):
    """Полностью сбросить историю после успешного входа."""
    state = db.session.get(LoginAttempt, key)
    if state is None:
        return
    db.session.delete(state)
    db.session.commit()
