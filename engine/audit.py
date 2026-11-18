"""Централизованное логирование действий пользователей.

Вызов ``log_action`` делает одну компактную запись в журнале ``UserAction``:
исполнитель (по умолчанию — текущий пользователь), код действия, подробности
и ID сессии (sid), в которой совершено действие. Записи читаются в карточке
пользователя через ``latest_actions``.

Логирование не должно ломать основное действие: при отсутствии request
(например, в фоновом задании) запись всё равно создаётся, если передан
пользователь, а ID сессии остаётся пустым.
"""
from flask import has_request_context
from flask_login import current_user

from engine.models import UserAction, db
from engine.user_sessions import current_sid

# Коды действий — те же, что перечислены в engine.models.ACTION_LABELS
LOGIN = 'login'
LOGIN_FAILED = 'login_failed'
LOGOUT = 'logout'
USER_CREATE = 'user_create'
USER_UPDATE = 'user_update'
USER_TOGGLE = 'user_toggle'
SESSIONS_TERMINATED = 'sessions_terminated'
CLIENT_UPDATE = 'client_update'
CLIENT_DELETE = 'client_delete'
CLIENT_SYNC_CONTRACT = 'client_sync_contracts'
CONTRACTOR_UPDATE = 'contractor_update'
CONTRACT_UPDATE = 'contract_update'
DOCUMENT_UPDATE = 'document_update'
TASK_CREATE = 'task_create'
TASK_UPDATE = 'task_update'
TASK_STATUS = 'task_status'
TASK_DELETE = 'task_delete'
TASKS_DELETE_ALL = 'tasks_delete_all'
DEADLINE_UPDATE = 'deadline_update'
DEADLINES_GENERATE = 'deadlines_generate'
SETTINGS_UPDATE = 'settings_update'
SYNC_CLIENTS = 'sync_clients'
SYNC_CONTRACTS = 'sync_contracts'
SYNC_DOCUMENTS = 'sync_documents'
SYNC_ERROR_RETRY = 'sync_error_retry'

# Сколько последних действий показывать в карточке пользователя
LATEST_LIMIT = 10

_DETAILS_LENGTH = 1000


def log_action(action, details='', user=None):
    """Записать действие в журнал и вернуть созданную запись.

    ``user`` можно передать явно (объект User или его id) — это нужно там,
    где текущий пользователь уже разлогинен или действие касается другого
    пользователя. По умолчанию используется current_user.
    """
    if user is None:
        if not has_request_context() or not current_user.is_authenticated:
            return None
        user = current_user

    user_id = getattr(user, 'id', user)
    if not user_id:
        return None

    record = UserAction(
        user_id=user_id,
        action=action,
        details=(details or '')[:_DETAILS_LENGTH],
        session_id=(current_sid() if has_request_context() else ''),
    )
    db.session.add(record)
    db.session.commit()
    return record


def latest_actions(user_id, limit=LATEST_LIMIT):
    """Последние действия пользователя: сначала самые свежие."""
    return (UserAction.query
            .filter_by(user_id=user_id)
            .order_by(UserAction.created_at.desc(), UserAction.id.desc())
            .limit(limit).all())
