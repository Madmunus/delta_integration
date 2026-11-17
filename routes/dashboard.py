"""Панель управления — сводка по всем реестрам системы.

Для каждого реестра на странице выводится блок из двух частей:
слева — первые 5 записей в порядке сортировки по умолчанию, справа —
сводная информация (количество записей по статусам и другим признакам).
"""
from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import case, func

from engine.models import Client, Contract, Contractor, Document, Task
from routes.contracts import DEFAULT_STATUS_ORDER as CONTRACT_STATUS_ORDER
from routes.documents import (DEFAULT_STATUS_ORDER as DOCUMENT_STATUS_ORDER,
                              DOCUMENT_STATUSES, _status_in)
from routes.tasks import DEFAULT_STATUS_ORDER as TASK_STATUS_ORDER

bp = Blueprint('dashboard', __name__)

# Порядок клиентов по умолчанию: сначала активные, затем архивные,
# внутри группы — по наименованию (как в реестре клиентов).
CLIENT_STATUS_ORDER = case((Client.status == 'active', 0), else_=1)


def _stat(label, value, tone='secondary', url=None, money=False):
    """Плитка сводки: подпись, значение, цвет и ссылка на реестр."""
    return {'label': label, 'value': value, 'tone': tone,
            'url': url, 'money': money}


@bp.route('/dashboard')
@login_required
def index():
    """Панель управления: блоки реестров клиентов, контрагентов, договоров,
    документов и задач."""
    today = date.today()
    month_ago = datetime.utcnow() - timedelta(days=30)
    is_admin = current_user.is_admin

    # --- Клиенты -----------------------------------------------------------
    clients_q = Client.query
    if not is_admin:
        clients_q = clients_q.filter(Client.responsible_id == current_user.id)
    clients_total = clients_q.count()
    clients_active = clients_q.filter(Client.status == 'active').count()
    clients_archived = clients_q.filter(Client.status == 'archived').count()
    clients_new = clients_q.filter(Client.created_at >= month_ago).count()
    clients_preview = (clients_q
                       .order_by(CLIENT_STATUS_ORDER, Client.name.asc())
                       .limit(5).all())
    client_stats = [
        _stat('Активные', clients_active, 'success',
              url_for('clients.list_clients', status='active')),
        _stat('Архивные', clients_archived, 'secondary',
              url_for('clients.list_clients', status='archived')),
    ]
    if is_admin:
        clients_unassigned = clients_q.filter(
            Client.responsible_id.is_(None)).count()
        client_stats.append(
            _stat('Без ответственного', clients_unassigned, 'warning',
                  url_for('clients.list_clients', responsible='none')))
    client_stats.append(_stat('Новые за 30 дней', clients_new, 'info'))

    # --- Контрагенты -------------------------------------------------------
    contractors_total = Contractor.query.count()
    contractors_active = Contractor.query.filter(
        Contractor.status == 'active').count()
    contractors_archived = Contractor.query.filter(
        Contractor.status == 'archived').count()
    contractors_new = Contractor.query.filter(
        Contractor.created_at >= month_ago).count()
    contractors_preview = (Contractor.query
                           .order_by(Contractor.full_name.asc())
                           .limit(5).all())
    contractor_stats = [
        _stat('Активные', contractors_active, 'success',
              url_for('contractors.list_contractors', status='active')),
        _stat('Архивные', contractors_archived, 'secondary',
              url_for('contractors.list_contractors', status='archived')),
        _stat('Новые за 30 дней', contractors_new, 'info'),
    ]

    # --- Договоры ----------------------------------------------------------
    contracts_q = Contract.query.join(Client)
    if not is_admin:
        contracts_q = contracts_q.filter(
            Client.responsible_id == current_user.id)
    contracts_total = contracts_q.count()
    contracts_draft = contracts_q.filter(Contract.status == 'draft').count()
    contracts_active = contracts_q.filter(Contract.status == 'active').count()
    contracts_closed = contracts_q.filter(Contract.status == 'closed').count()
    contracts_processed = contracts_q.filter(
        Contract.processed.is_(True)).count()
    contracts_active_amount = (contracts_q
                               .filter(Contract.status == 'active')
                               .with_entities(
                                   func.coalesce(func.sum(Contract.amount), 0))
                               .scalar())
    contracts_preview = (contracts_q
                         .outerjoin(
                             Contractor,
                             Contract.contractor_id == Contractor.id)
                         .order_by(CONTRACT_STATUS_ORDER,
                                   Contract.signed_date.desc().nullslast(),
                                   Contract.number.asc())
                         .limit(5).all())
    contract_stats = [
        _stat('Проекты', contracts_draft, 'warning',
              url_for('contracts.list_contracts', status='draft')),
        _stat('Действующие', contracts_active, 'success',
              url_for('contracts.list_contracts', status='active')),
        _stat('Завершённые', contracts_closed, 'secondary',
              url_for('contracts.list_contracts', status='closed')),
        _stat('Обработано', contracts_processed, 'info',
              url_for('contracts.list_contracts', processed='yes')),
        _stat('Сумма действующих', contracts_active_amount, 'primary',
              money=True),
    ]

    # --- Документы ---------------------------------------------------------
    documents_q = Document.query.join(Client)
    if not is_admin:
        documents_q = documents_q.filter(
            Client.responsible_id == current_user.id)
    documents_total = documents_q.count()
    document_counts = {label: documents_q.filter(_status_in(label)).count()
                       for label in DOCUMENT_STATUSES}
    documents_processed = documents_q.filter(
        Document.processed.is_(True)).count()
    documents_preview = (documents_q
                         .order_by(DOCUMENT_STATUS_ORDER,
                                   Document.doc_date.desc().nullslast(),
                                   Document.number.asc(),
                                   Document.id.desc())
                         .limit(5).all())
    document_stats = [
        _stat('Черновик', document_counts['Черновик'], 'secondary',
              url_for('documents.list_documents', status='Черновик')),
        _stat('Зарегистрирован', document_counts['Зарегистрирован'], 'primary',
              url_for('documents.list_documents', status='Зарегистрирован')),
        _stat('Проведён', document_counts['Проведён'], 'success',
              url_for('documents.list_documents', status='Проведён')),
        _stat('Отменён', document_counts['Отменён'], 'danger',
              url_for('documents.list_documents', status='Отменён')),
        _stat('Обработано', documents_processed, 'info',
              url_for('documents.list_documents', processed='yes')),
    ]

    # --- Задачи ------------------------------------------------------------
    tasks_q = Task.query
    if not is_admin:
        tasks_q = tasks_q.filter(Task.assignee_id == current_user.id)
    tasks_total = tasks_q.count()
    tasks_new = tasks_q.filter(Task.status == Task.STATUS_NEW).count()
    tasks_in_progress = tasks_q.filter(
        Task.status == Task.STATUS_IN_PROGRESS).count()
    tasks_done = tasks_q.filter(Task.status == Task.STATUS_DONE).count()
    tasks_overdue = tasks_q.filter(
        Task.status != Task.STATUS_DONE,
        Task.due_date.isnot(None),
        Task.due_date < today).count()
    tasks_preview = (tasks_q
                     .order_by(TASK_STATUS_ORDER,
                               Task.priority.asc().nullslast(),
                               Task.due_date.asc().nullslast(),
                               Task.id.asc())
                     .limit(5).all())
    task_stats = [
        _stat('Новые', tasks_new, 'secondary',
              url_for('tasks.list_tasks', status='new')),
        _stat('В работе', tasks_in_progress, 'primary',
              url_for('tasks.list_tasks', status='in_progress')),
        _stat('Выполнены', tasks_done, 'success',
              url_for('tasks.list_tasks', status='done')),
        _stat('Просрочено', tasks_overdue,
              'danger' if tasks_overdue else 'secondary',
              url_for('tasks.list_tasks', status='overdue')),
    ]

    return render_template(
        'dashboard/dashboard.html',
        clients_total=clients_total, clients_preview=clients_preview,
        client_stats=client_stats,
        contractors_total=contractors_total,
        contractors_preview=contractors_preview,
        contractor_stats=contractor_stats,
        contracts_total=contracts_total, contracts_preview=contracts_preview,
        contract_stats=contract_stats,
        documents_total=documents_total, documents_preview=documents_preview,
        document_stats=document_stats,
        tasks_total=tasks_total, tasks_preview=tasks_preview,
        task_stats=task_stats)
