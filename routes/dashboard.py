"""Панель управления — главная страница после входа."""
from datetime import date, timedelta

from flask import Blueprint, render_template
from flask_login import current_user, login_required

from engine.models import Client, Task

bp = Blueprint('dashboard', __name__)


@bp.route('/dashboard')
@login_required
def index():
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
    recent_clients = recent_clients.order_by(Client.created_at.desc()).limit(5).all()

    return render_template('dashboard.html',
                           active_clients=active_clients,
                           archived_clients=archived_clients,
                           open_tasks_count=len(open_tasks),
                           overdue_count=overdue_count,
                           upcoming=upcoming, my_tasks=my_tasks,
                           recent_clients=recent_clients, today=today)
