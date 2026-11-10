"""Сводная отчётность для руководителя (ФТ-6) и панель управления."""
from collections import defaultdict
from datetime import date, timedelta

from flask import Blueprint, abort, render_template
from flask_login import current_user, login_required
from sqlalchemy import case, func

from models import Client, Contract, ReportingDeadline, Task, User

bp = Blueprint('reports', __name__)


@bp.route('/dashboard')
@login_required
def dashboard():
    """Панель управления с виджетами «Ближайшие сроки», «Мои задачи», «Клиенты»."""
    today = date.today()
    soon = today + timedelta(days=14)

    if current_user.is_admin:
        active_clients = Client.query.filter_by(status='active').count()
        archived_clients = Client.query.filter_by(status='archived').count()
        base_tasks = Task.query
    else:
        my = Client.query.filter_by(responsible_id=current_user.id)
        active_clients = my.filter_by(status='active').count()
        archived_clients = my.filter_by(status='archived').count()
        base_tasks = Task.query.filter(Task.client_id.in_(
            [c.id for c in my.with_entities(Client.id).all()]))

    open_tasks = base_tasks.filter(
        Task.status.in_([Task.STATUS_NEW, Task.STATUS_IN_PROGRESS,
                         Task.STATUS_OVERDUE])).all()
    overdue_count = sum(1 for t in open_tasks if t.due_date and t.due_date < today)
    upcoming = sorted(
        [t for t in open_tasks if t.due_date and today <= t.due_date <= soon],
        key=lambda t: t.due_date)[:5]
    # «Мои задачи» отображаются только руководителю
    if current_user.is_admin:
        my_tasks = sorted(
            [t for t in open_tasks if t.assignee_id == current_user.id
             and t.due_date], key=lambda t: t.due_date)[:5]
    else:
        my_tasks = []

    recent_clients = Client.query
    if not current_user.is_admin:
        recent_clients = recent_clients.filter_by(responsible_id=current_user.id)
    recent_clients = recent_clients.order_by(Client.date_added.desc()).limit(5).all()

    return render_template('dashboard.html',
                           active_clients=active_clients,
                           archived_clients=archived_clients,
                           open_tasks_count=len(open_tasks),
                           overdue_count=overdue_count,
                           upcoming=upcoming, my_tasks=my_tasks,
                           recent_clients=recent_clients, today=today)


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
    rows = (db_session().query(
                User.fio,
                func.count(Client.id),
                func.sum(case((Client.status == 'active', 1), else_=0)),
            ).outerjoin(Client, Client.responsible_id == User.id)
             .group_by(User.id, User.fio)
             .order_by(User.fio)).all()
    total = sum(r[1] for r in rows)
    active = sum(r[2] or 0 for r in rows)
    unassigned = Client.query.filter(Client.responsible_id.is_(None)).count()
    return render_template('reports/clients.html', rows=rows, total=total,
                           active=active, unassigned=unassigned)


def db_session():
    from models import db
    return db.session


@bp.route('/reports/tasks')
@login_required
def tasks_report():
    """Отчёт по задачам: выполненные, в работе, просроченные."""
    if not current_user.is_admin:
        abort(403)
    rows = (db_session().query(
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
