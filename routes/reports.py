"""Сводная отчётность для руководителя (ФТ-7)."""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from flask import Blueprint, abort, render_template
from flask_login import current_user, login_required
from sqlalchemy import and_, case, func
from sqlalchemy.orm import joinedload

from engine.models import Client, Contract, Document, Task, User, db

bp = Blueprint('reports', __name__)

# Статусы документов в порядке отображения (как в реестре документов).
# Статус из «1С» приходит свободным текстом, поэтому сравнение идёт
# по нормализованному значению: без пробелов по краям, в нижнем регистре,
# с заменой «ё» на «е».
DOCUMENT_STATUS_LABELS = ('Черновик', 'Зарегистрирован', 'Проведён', 'Отменён')
DOCUMENT_STATUS_KEYS = {
    label: label.lower().replace('ё', 'е') for label in DOCUMENT_STATUS_LABELS
}


def _document_status_key(document):
    """Нормализованный статус документа для группировки и сортировки."""
    return (document.status or '').strip().lower().replace('ё', 'е')


def _document_status_counts(documents):
    """Количество документов по известным статусам."""
    counts = {label: 0 for label in DOCUMENT_STATUS_LABELS}
    for document in documents:
        key = _document_status_key(document)
        for label, expected in DOCUMENT_STATUS_KEYS.items():
            if key == expected:
                counts[label] += 1
                break
    return counts


@bp.route('/reports')
@login_required
def index():
    """Список доступных отчётов."""
    if not current_user.is_admin:
        abort(403, 'Отчёты доступны только руководителю')
    return render_template('reports/index.html')


@bp.route('/reports/clients')
@login_required
def clients_report():
    """Отчёт по клиентской базе: активные, распределение по ответственным."""
    if not current_user.is_admin:
        abort(403)
    active_clients = func.sum(case((Client.status == 'active', 1), else_=0))
    rows = (db.session.query(
                User.id,
                User.fio,
                User.is_active_flag,
                func.count(Client.id),
                active_clients,
            ).outerjoin(Client, Client.responsible_id == User.id)
             .group_by(User.id, User.fio, User.is_active_flag)
             # Сначала активные пользователи, затем отключённые; внутри
             # группы — по числу активных клиентов (по убыванию),
             # при равенстве — по ФИО сотрудника.
             .order_by(User.is_active_flag.desc(),
                       active_clients.desc(),
                       User.fio.asc())).all()
    # Клиенты каждого ответственного — для раскрывающегося списка в таблице
    clients_by_user = defaultdict(list)
    for client in Client.query.order_by(Client.name).all():
        clients_by_user[client.responsible_id].append(client)
    # Сводные показатели считаем напрямую по реестру: в таблице ниже
    # отражены только клиенты с назначенным ответственным
    total = Client.query.count()
    active = Client.query.filter_by(status='active').count()
    unassigned = Client.query.filter(Client.responsible_id.is_(None)).count()
    return render_template('reports/clients.html', rows=rows, total=total,
                           active=active, unassigned=unassigned,
                           clients_by_user=clients_by_user)


@bp.route('/reports/tasks')
@login_required
def tasks_report():
    """Отчёт по задачам: выполненные, в работе, просроченные по сроку."""
    if not current_user.is_admin:
        abort(403)
    today = date.today()
    overdue_condition = and_(Task.status != 'done',
                             Task.due_date.isnot(None),
                             Task.due_date < today)
    rows = (db.session.query(
                User.id,
                User.fio,
                func.sum(case((Task.status == 'done', 1), else_=0)),
                func.sum(case((Task.status.in_(
                    ['new', 'in_progress']), 1), else_=0)),
                func.sum(case((overdue_condition, 1), else_=0)),
            ).outerjoin(Task, Task.assignee_id == User.id)
             .group_by(User.id, User.fio)
             .order_by(User.fio)).all()
    # Задачи каждого исполнителя — для раскрывающегося списка в таблице
    tasks_by_assignee = defaultdict(list)
    for task in (Task.query.options(joinedload(Task.client))
                 .order_by(Task.due_date.asc().nullslast(),
                           Task.title.asc()).all()):
        tasks_by_assignee[task.assignee_id].append(task)
    # Суммарные показатели для плиток над таблицей
    totals = {
        'total': Task.query.count(),
        'done': Task.query.filter_by(status=Task.STATUS_DONE).count(),
        'open': Task.query.filter(Task.status.in_(
            [Task.STATUS_NEW, Task.STATUS_IN_PROGRESS])).count(),
        'overdue': Task.query.filter(overdue_condition).count(),
    }
    return render_template('reports/tasks.html', rows=rows, totals=totals,
                           tasks_by_assignee=tasks_by_assignee)


@bp.route('/reports/contracts')
@login_required
def contracts_report():
    """Отчёт по договорам и поступлениям в разрезе клиентов.

    Договоры группируются по клиенту: количество по статусам и суммы.
    Сначала идут активные клиенты (по сумме действующих договоров
    по убыванию, при равенстве — по наименованию), затем архивные.
    """
    if not current_user.is_admin:
        abort(403)
    status_order = {'draft': 0, 'active': 1, 'closed': 2}
    contracts = (Contract.query.join(Client)
                 .options(joinedload(Contract.client),
                          joinedload(Contract.contractor))
                 .order_by(Client.name.asc(), Contract.status.asc(),
                           Contract.signed_date.desc())
                 .all())

    grouped = defaultdict(list)
    for contract in contracts:
        grouped[contract.client_id].append(contract)

    groups = []
    for items in grouped.values():
        # Внутри клиента: проект → действующий → завершённый,
        # затем более новые договоры, при равенстве — по номеру.
        items.sort(key=lambda c: (
            status_order.get(c.status, 3),
            -(c.signed_date.toordinal()) if c.signed_date else 0,
            c.number))
        counts = {status: 0 for status in status_order}
        amount_active = Decimal('0')
        amount_total = Decimal('0')
        for contract in items:
            counts[contract.status] = counts.get(contract.status, 0) + 1
            amount_total += contract.amount or 0
            if contract.status == 'active':
                amount_active += contract.amount or 0
        groups.append({
            'client': items[0].client,
            'contracts': items,
            'total': len(items),
            'counts': counts,
            'amount_active': amount_active,
            'amount_total': amount_total,
        })

    groups.sort(key=lambda g: (
        0 if g['client'].is_active else 1,
        -g['amount_active'],
        g['client'].name))

    totals = {
        'clients': len(groups),
        'contracts': len(contracts),
        'draft': sum(1 for c in contracts if c.status == 'draft'),
        'active': sum(1 for c in contracts if c.status == 'active'),
        'closed': sum(1 for c in contracts if c.status == 'closed'),
        'amount_active': sum((c.amount or 0 for c in contracts
                              if c.status == 'active'), Decimal('0')),
        'amount_total': sum((c.amount or 0 for c in contracts),
                            Decimal('0')),
    }
    return render_template('reports/contracts.html', groups=groups,
                           totals=totals)


@bp.route('/reports/documents')
@login_required
def documents_report():
    """Отчёт по документам в разрезе клиентов.

    Документы группируются по клиенту: количество по статусам и признаку
    «Обработан». Сначала идут активные клиенты (по числу документов
    по убыванию, при равенстве — по наименованию), затем архивные.
    """
    if not current_user.is_admin:
        abort(403)
    status_order = {key: index
                    for index, key in enumerate(DOCUMENT_STATUS_KEYS.values())}
    documents = (Document.query.join(Client)
                 .options(joinedload(Document.client),
                          joinedload(Document.contractor))
                 .order_by(Client.name.asc(), Document.doc_date.desc())
                 .all())

    grouped = defaultdict(list)
    for document in documents:
        grouped[document.client_id].append(document)

    groups = []
    for items in grouped.values():
        # Внутри клиента: черновик → зарегистрирован → проведён → отменён,
        # затем более новые документы, при равенстве — по номеру.
        items.sort(key=lambda d: (
            status_order.get(_document_status_key(d), len(status_order)),
            -(d.doc_date.toordinal()) if d.doc_date else 0,
            d.number or ''))
        counts = _document_status_counts(items)
        processed = sum(1 for document in items if document.processed)
        groups.append({
            'client': items[0].client,
            'documents': items,
            'total': len(items),
            'counts': counts,
            'processed': processed,
            'unprocessed': len(items) - processed,
        })

    groups.sort(key=lambda g: (
        0 if g['client'].is_active else 1,
        -g['total'],
        g['client'].name))

    counts = _document_status_counts(documents)
    totals = {
        'clients': len(groups),
        'documents': len(documents),
        'draft': counts['Черновик'],
        'registered': counts['Зарегистрирован'],
        'conducted': counts['Проведён'],
        'cancelled': counts['Отменён'],
        'processed': sum(1 for document in documents if document.processed),
    }
    return render_template('reports/documents.html', groups=groups,
                           totals=totals)
