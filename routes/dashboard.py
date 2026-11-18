"""Панель управления — сводка по всем реестрам системы.

Для каждого реестра на странице выводится блок из двух частей:
слева — первые 5 записей в порядке сортировки по умолчанию, справа —
сводная информация (количество записей по статусам и другим признакам).
"""
from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import case, func
from sqlalchemy.orm import contains_eager, joinedload, load_only

from engine.models import Client, Contract, Contractor, Document, Task, User
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
    client_agg = clients_q.with_entities(
        func.count().label('total'),
        func.count(case((Client.status == 'active', 1))).label('active'),
        func.count(case((Client.status == 'archived', 1))).label('archived'),
        func.count(case((Client.created_at >= month_ago, 1))).label('new'),
        func.count(case((Client.responsible_id.is_(None), 1))).label('unassigned'),
    ).one()
    clients_total = client_agg.total
    clients_active = client_agg.active
    clients_archived = client_agg.archived
    clients_new = client_agg.new
    clients_preview = (clients_q
                       .options(
                           load_only(Client.id, Client.name, Client.inn,
                                     Client.status, Client.responsible_id),
                           joinedload(Client.responsible).load_only(
                               User.id, User.fio),
                       )
                       .order_by(CLIENT_STATUS_ORDER, Client.name.asc())
                       .limit(5).all())
    client_stats = [
        _stat('Активные', clients_active, 'success',
              url_for('clients.list_clients', status='active')),
        _stat('Архивные', clients_archived, 'secondary',
              url_for('clients.list_clients', status='archived')),
    ]
    if is_admin:
        client_stats.append(
            _stat('Без ответственного', client_agg.unassigned, 'warning',
                  url_for('clients.list_clients', responsible='none')))
    client_stats.append(_stat('Новые за 30 дней', clients_new, 'info'))

    # --- Контрагенты -------------------------------------------------------
    contractor_agg = Contractor.query.with_entities(
        func.count().label('total'),
        func.count(case((Contractor.status == 'active', 1))).label('active'),
        func.count(case((Contractor.status == 'archived', 1))).label('archived'),
        func.count(case((Contractor.created_at >= month_ago, 1))).label('new'),
    ).one()
    contractors_total = contractor_agg.total
    contractors_active = contractor_agg.active
    contractors_archived = contractor_agg.archived
    contractors_new = contractor_agg.new
    contractors_preview = (Contractor.query
                           .options(load_only(Contractor.id,
                                              Contractor.full_name,
                                              Contractor.inn,
                                              Contractor.status,
                                              Contractor.created_at))
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
    contract_agg = contracts_q.with_entities(
        func.count().label('total'),
        func.count(case((Contract.status == 'draft', 1))).label('draft'),
        func.count(case((Contract.status == 'active', 1))).label('active'),
        func.count(case((Contract.status == 'closed', 1))).label('closed'),
        func.count(case((Contract.processed.is_(True), 1))).label('processed'),
        func.coalesce(func.sum(case(
            (Contract.status == 'active', Contract.amount), else_=0)), 0
        ).label('active_amount'),
    ).one()
    contracts_total = contract_agg.total
    contracts_draft = contract_agg.draft
    contracts_active = contract_agg.active
    contracts_closed = contract_agg.closed
    contracts_processed = contract_agg.processed
    contracts_active_amount = contract_agg.active_amount
    contracts_preview = (contracts_q
                         .outerjoin(
                             Contractor,
                             Contract.contractor_id == Contractor.id)
                         .options(
                             contains_eager(Contract.client).load_only(
                                 Client.id, Client.name, Client.status),
                             contains_eager(Contract.contractor).load_only(
                                 Contractor.id, Contractor.full_name),
                             load_only(Contract.id, Contract.number,
                                       Contract.signed_date, Contract.status,
                                       Contract.status_1c,
                                       Contract.client_id,
                                       Contract.contractor_id),
                         )
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
    status_labels = list(DOCUMENT_STATUSES)
    document_agg = documents_q.with_entities(
        func.count().label('total'),
        *[func.count(case((_status_in(label), 1))).label(f'status_{i}')
          for i, label in enumerate(status_labels)],
        func.count(case((Document.processed.is_(True), 1))).label('processed'),
    ).one()
    documents_total = document_agg.total
    document_counts = {label: getattr(document_agg, f'status_{i}')
                       for i, label in enumerate(status_labels)}
    documents_processed = document_agg.processed
    documents_preview = (documents_q
                         .options(load_only(Document.id, Document.doc_type,
                                            Document.number,
                                            Document.doc_date,
                                            Document.status))
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
    task_agg = tasks_q.with_entities(
        func.count().label('total'),
        func.count(case((Task.status == Task.STATUS_NEW, 1))).label('new'),
        func.count(case((Task.status == Task.STATUS_IN_PROGRESS, 1))).label('in_progress'),
        func.count(case((Task.status == Task.STATUS_DONE, 1))).label('done'),
        func.count(case((
            (Task.status != Task.STATUS_DONE)
            & Task.due_date.isnot(None)
            & (Task.due_date < today), 1))).label('overdue'),
    ).one()
    tasks_total = task_agg.total
    tasks_new = task_agg.new
    tasks_in_progress = task_agg.in_progress
    tasks_done = task_agg.done
    tasks_overdue = task_agg.overdue
    tasks_preview = (tasks_q
                     .options(load_only(Task.id, Task.title,
                                        Task.due_date, Task.priority,
                                        Task.status))
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
