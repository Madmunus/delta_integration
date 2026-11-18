"""Серверный учёт сессий пользователей.

Flask-Login хранит авторизацию в подписанных cookie. Чтобы показывать входы
и закрывать их, каждый успешный вход создаёт запись ``UserSession`` и
сохраняет её ``sid`` в cookie-сессии. Завершённые сессии не удаляются: у них
сбрасывается ``is_active_flag``, поэтому история входов сохраняется и может
использоваться, например, для связи с записями журнала действий.
"""
import secrets
from datetime import datetime

from flask import (current_app, flash, redirect, request, session, url_for)
from flask_login import current_user, logout_user

from engine.models import UserSession, db

# Ключ cookie-сессии, в котором лежит идентификатор записи UserSession
SID_KEY = 'delta_session_id'

# Не обновляем last_seen на каждый запрос: раз в минуту достаточно
TOUCH_INTERVAL_SECONDS = 60

# Ограничим длину User-Agent, чтобы не раздувать базу
USER_AGENT_LENGTH = 512


def _client_ip() -> str:
    """IP клиента: за прокси — первый адрес из X-Forwarded-For."""
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',', 1)[0].strip()[:64]
    return (request.remote_addr or '')[:64]


def _client_user_agent() -> str:
    return (request.headers.get('User-Agent') or '')[:USER_AGENT_LENGTH]


def _cutoff() -> datetime:
    """Граница, раньше которой сессия считается неактивной."""
    return datetime.utcnow() - current_app.permanent_session_lifetime


def start_session(user) -> UserSession:
    """Создать запись о новом входе и запомнить её sid в cookie-сессии."""
    sid = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    record = UserSession(
        sid=sid,
        user_id=user.id,
        ip_address=_client_ip(),
        user_agent=_client_user_agent(),
        is_active_flag=True,
        created_at=now,
        last_seen_at=now,
    )
    session[SID_KEY] = sid
    db.session.add(record)
    db.session.commit()
    return record


def current_sid() -> str:
    return session.get(SID_KEY) or ''


def _expire_current(message: str):
    """Разлогинить текущую сессию и увести на страницу входа."""
    logout_user()
    session.pop(SID_KEY, None)
    flash(message, 'warning')
    return redirect(url_for('auth.login'))


def enforce_session():
    """before_request: проверить, что текущая сессия ещё активна."""
    if request.path.startswith('/static/'):
        return None
    if not current_user.is_authenticated:
        return None

    sid = current_sid()
    record = (UserSession.query.filter_by(sid=sid, user_id=current_user.id).first()
              if sid else None)
    if record is None:
        return _expire_current('Сессия завершена. Войдите в систему снова.')

    if not record.is_active_flag:
        return _expire_current('Сессия завершена. Войдите в систему снова.')

    cutoff = _cutoff()
    if record.last_seen_at is None or record.last_seen_at < cutoff:
        record.is_active_flag = False
        db.session.commit()
        return _expire_current('Срок сессии истёк. Войдите в систему снова.')

    now = datetime.utcnow()
    ip = _client_ip()
    # User-Agent задаётся при входе: он описывает устройство сессии и не
    # должен перезаписываться каждым запросом.
    need_touch = (record.ip_address != ip
                  or (now - record.last_seen_at).total_seconds()
                  >= TOUCH_INTERVAL_SECONDS)
    if need_touch:
        record.ip_address = ip
        record.last_seen_at = now
        db.session.commit()
    return None


def terminate_current_session():
    """Завершить текущую сессию (сбросить флаг) и разлогинить пользователя."""
    sid = session.pop(SID_KEY, '')
    if sid and current_user.is_authenticated:
        UserSession.query.filter_by(sid=sid, user_id=current_user.id).update(
            {'is_active_flag': False}, synchronize_session=False)
        db.session.commit()
    logout_user()


def deactivate_expired(user_id=None) -> int:
    """Отметить неактивными сессии, в которых давно не было активности."""
    query = UserSession.query.filter(
        UserSession.is_active_flag.is_(True),
        UserSession.last_seen_at < _cutoff(),
    )
    if user_id is not None:
        query = query.filter(UserSession.user_id == user_id)
    updated = query.update({'is_active_flag': False},
                           synchronize_session=False)
    if updated:
        db.session.commit()
    return updated


def list_sessions(user_id) -> list:
    """Все сессии пользователя: сначала активные, затем завершённые."""
    deactivate_expired(user_id)
    return UserSession.query.filter_by(user_id=user_id).order_by(
        UserSession.is_active_flag.desc(),
        UserSession.last_seen_at.desc(),
    ).all()


def terminate_others(user_id, keep_sid) -> int:
    """Завершить все активные сессии пользователя, кроме текущей."""
    if not keep_sid:
        return 0
    updated = UserSession.query.filter(
        UserSession.user_id == user_id,
        UserSession.sid != keep_sid,
        UserSession.is_active_flag.is_(True),
    ).update({'is_active_flag': False}, synchronize_session=False)
    db.session.commit()
    return updated
