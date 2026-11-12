"""Сводная отчётность для руководителя (ФТ-6)."""
from collections import defaultdict
from datetime import date

from flask import Blueprint, abort, render_template
from flask_login import current_user, login_required
from sqlalchemy import case, func

from engine.models import Client, Contract, ReportingDeadline, Task, User, db

bp = Blueprint('reports', __name__)


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
    rows = (db.session.query(
                User.fio,
                func.count(Client.id),
                func.sum(case((Client.status == 'active', 1), else_=0)),
            ).outerjoin(Client, Client.responsible_id == User.id)
             .group_by(User.id, User.fio)
             .order_by(User.fio)).all()
    # Сводные показатели считаем напрямую по реестру: в таблице ниже
    # отражены только клиенты с назначенным ответственным
    total = Client.query.count()
    active = Client.query.filter_by(status='active').count()
    unassigned = Client.query.filter(Client.responsible_id.is_(None)).count()
    return render_template('reports/clients.html', rows=rows, total=total,
                           active=active, unassigned=unassigned)


@bp.route('/reports/tasks')
@login_required
def tasks_report():
    """Отчёт по задачам: выполненные, в работе, просроченные."""
    if not current_user.is_admin:
        abort(403)
    rows = (db.session.query(
                User.fio,
                func.sum(case((Task.status == 'done', 1), else_=0)),
                func.sum(case((Task.status.in_(
                    ['new', 'in_progress']), 1), else_=0)),
                func.sum(case((Task.status == 'overdue', 1), else_=0)),
            ).outerjoin(Task, Task.assignee_id == User.id)
             .group_by(User.id, User.fio)
             .order_by(User.fio)).all()
    # Плюс задачи без исполнителя
    unassigned = Task.query.filter(Task.assignee_id.is_(None))
    no_assignee = (
        unassigned.filter_by(status='done').count(),
        unassigned.filter(Task.status.in_(['new', 'in_progress'])).count(),
        unassigned.filter_by(status='overdue').count(),
    )
    return render_template('reports/tasks.html', rows=rows, no_assignee=no_assignee)


@bp.route('/reports/contracts')
@login_required
def contracts_report():
    """Отчёт по договорам и поступлениям."""
    if not current_user.is_admin:
        abort(403)
    contracts = Contract.query.order_by(Contract.status, Contract.end_date).all()
    by_status = defaultdict(lambda: {'count': 0, 'amount': 0})
    for c in contracts:
        d = by_status[c.status]
        d['count'] += 1
        d['amount'] += float(c.amount or 0)
    total_amount = sum(float(c.amount or 0) for c in contracts
                       if c.status == 'active')
    return render_template('reports/contracts.html', contracts=contracts,
                           by_status=dict(by_status), total_amount=total_amount)


@bp.route('/reports/deadlines')
@login_required
def deadlines_report():
    """Контроль сроков отчётности в разрезе клиентов (ФТ-4)."""
    if not current_user.is_admin:
        abort(403)
    today = date.today()
    deadlines = ReportingDeadline.query.filter_by(is_enabled=True).all()
    clients = Client.query.filter_by(status='active').all()

    grid = []
    for d in deadlines:
        best = None
        for m in range(0, 15):
            y = today.year + (today.month + m - 1) // 12
            mo = (today.month + m - 1) % 12 + 1
            due = d.due_date_for_period(date(y, mo, 1))
            if due >= today:
                best = due
                break
        if best is None:
            continue
        done = {t.client_id for t in Task.query.filter_by(
            reporting_deadline_id=d.id, due_date=best,
            status=Task.STATUS_DONE).all()}
        grid.append({
            'deadline': d, 'due': best,
            'days_left': (best - today).days,
            'done': len(done), 'total': len(clients),
        })
    grid.sort(key=lambda g: g['due'])
    return render_template('reports/deadlines.html', grid=grid, today=today)
