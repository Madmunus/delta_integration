"""Сводная отчётность для руководителя (ФТ-7)."""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from flask import Blueprint, abort, render_template
from flask_login import current_user, login_required
from sqlalchemy import and_, case, func
from sqlalchemy.orm import joinedload, load_only

from engine.models import (Client, Contract, Contractor, Document, Task, User,
                           db)

bp = Blueprint('reports', __name__)

# Статусы документов в порядке отображения (как в реестре документов).
# Статус из «1С» приходит свободным текстом, поэтому сравнение идёт
# по нормализованному значению: без пробелов по краям, в нижнем регистре,
# с заменой «ё» на «е».
DOCUMENT_STATUS_LABELS = ('Черновик', 'Зарегистрирован', 'Проведён', 'Отменён')
DOCUMENT_STATUS_KEYS = {
    label: label.lower().replace('ё', 'е') for label in DOCUMENT_STATUS_LABELS
}
DOCUMENT_STATUS_LABELS_BY_KEY = {
    key: label for label, key in DOCUMENT_STATUS_KEYS.items()
}


def _document_status_key(document):
    """Нормализованный статус документа для группировки и сортировки."""
    return (document.status or '').strip().lower().replace('ё', 'е')


def _document_status_counts(documents):
    """Количество документов по известным статусам."""
    counts = {label: 0 for label in DOCUMENT_STATUS_LABELS}
    for document in documents:
        label = DOCUMENT_STATUS_LABELS_BY_KEY.get(_document_status_key(document))
        if label is not None:
            counts[label] += 1
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
    # Сводные показатели считаем напрямую по реестру одним запросом:
    # в таблице ниже отражены только клиенты с назначенным ответственным
    total, active, unassigned = db.session.query(
        func.count(Client.id),
        func.coalesce(func.sum(case(
            (Client.status == 'active', 1), else_=0)), 0),
        func.coalesce(func.sum(case(
            (Client.responsible_id.is_(None), 1), else_=0)), 0),
    ).one()
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
                User.is_active_flag,
                func.sum(case((Task.status == 'done', 1), else_=0)),
                func.sum(case((Task.status.in_(
                    ['new', 'in_progress']), 1), else_=0)),
                func.sum(case((overdue_condition, 1), else_=0)),
            ).outerjoin(Task, Task.assignee_id == User.id)
             .group_by(User.id, User.fio, User.is_active_flag)
             # Сначала активные пользователи, затем отключённые; внутри
             # группы — по числу просроченных, в работе и выполненных
             # задач (по убыванию), при равенстве — по ФИО.
             .order_by(
                 User.is_active_flag.desc(),
                 func.sum(case((overdue_condition, 1), else_=0)).desc(),
                 func.sum(case((Task.status.in_(
                     ['new', 'in_progress']), 1), else_=0)).desc(),
                 func.sum(case((Task.status == 'done', 1), else_=0)).desc(),
                 User.fio.asc(),
             )).all()
    # Задачи каждого исполнителя — для раскрывающегося списка в таблице
    tasks_by_assignee = defaultdict(list)
    for task in (Task.query.options(joinedload(Task.client))
                 .order_by(Task.due_date.asc().nullslast(),
                           Task.title.asc()).all()):
        tasks_by_assignee[task.assignee_id].append(task)
    # Суммарные показатели для плиток над таблицей — одним запросом
    total, done, opened, overdue = db.session.query(
        func.count(Task.id),
        func.coalesce(func.sum(case(
            (Task.status == Task.STATUS_DONE, 1), else_=0)), 0),
        func.coalesce(func.sum(case(
            (Task.status.in_([Task.STATUS_NEW, Task.STATUS_IN_PROGRESS]), 1),
            else_=0)), 0),
        func.coalesce(func.sum(case((overdue_condition, 1), else_=0)), 0),
    ).one()
    totals = {
        'total': total,
        'done': done,
        'open': opened,
        'overdue': overdue,
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
    # Группы и строки сортируются ниже в Python: SQL-сортировка здесь
    # была бы лишней. Загружаем только поля, используемые в отчёте.
    contracts = (Contract.query
                 .options(
                     load_only(Contract.id, Contract.number,
                               Contract.signed_date, Contract.start_date,
                               Contract.end_date, Contract.status,
                               Contract.client_id, Contract.contractor_id,
                               Contract.amount, Contract.processed),
                     joinedload(Contract.client).load_only(
                         Client.id, Client.name, Client.status),
                     joinedload(Contract.contractor).load_only(
                         Contractor.id, Contractor.full_name,
                         Contractor.external_ref),
                 ).all())

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
        processed = 0
        for contract in items:
            counts[contract.status] = counts.get(contract.status, 0) + 1
            amount_total += contract.amount or 0
            if contract.status == 'active':
                amount_active += contract.amount or 0
            if contract.processed:
                processed += 1
        groups.append({
            'client': items[0].client,
            'contracts': items,
            'total': len(items),
            'counts': counts,
            'processed': processed,
            'unprocessed': len(items) - processed,
            'amount_active': amount_active,
            'amount_total': amount_total,
        })

    groups.sort(key=lambda g: (
        0 if g['client'].is_active else 1,
        -g['amount_active'],
        g['client'].name))

    totals = {
        'clients': len(groups),
        'contracts': sum(g['total'] for g in groups),
        'draft': sum(g['counts'].get('draft', 0) for g in groups),
        'active': sum(g['counts'].get('active', 0) for g in groups),
        'closed': sum(g['counts'].get('closed', 0) for g in groups),
        'processed': sum(g['processed'] for g in groups),
        'amount_active': sum((g['amount_active'] for g in groups), Decimal('0')),
        'amount_total': sum((g['amount_total'] for g in groups), Decimal('0')),
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
    documents = (Document.query
                 .options(
                     load_only(Document.id, Document.doc_type, Document.number,
                               Document.doc_date, Document.status,
                               Document.client_id, Document.contractor_id,
                               Document.processed),
                     joinedload(Document.client).load_only(
                         Client.id, Client.name, Client.status),
                     joinedload(Document.contractor).load_only(
                         Contractor.id, Contractor.full_name,
                         Contractor.external_ref),
                 ).all())

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

    totals = {
        'clients': len(groups),
        'documents': sum(g['total'] for g in groups),
        'draft': sum(g['counts']['Черновик'] for g in groups),
        'registered': sum(g['counts']['Зарегистрирован'] for g in groups),
        'conducted': sum(g['counts']['Проведён'] for g in groups),
        'cancelled': sum(g['counts']['Отменён'] for g in groups),
        'processed': sum(g['processed'] for g in groups),
    }
    return render_template('reports/documents.html', groups=groups,
                           totals=totals)
