"""Маршруты задач и справочника сроков сдачи отчётности."""
from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from engine.forms import ReportingDeadlineForm, TaskForm
from engine.models import Client, ReportingDeadline, Task, User, db
from services.tasks import generate_deadline_tasks, mark_overdue_tasks

bp = Blueprint('tasks', __name__)


def _task_filter(q):
    """ФТ-7: сотрудник видит только свои задачи."""
    if not current_user.is_admin:
        q = q.filter(Task.assignee_id == current_user.id)
    return q


@bp.route('/tasks')
@login_required
def list_tasks():
    mark_overdue_tasks()  # актуализация статусов при просмотре (ФТ-4)

    q = _task_filter(Task.query)
    status = request.args.get('status') or ''
    assignee_id = request.args.get('assignee', type=int)
    mine = request.args.get('mine') == '1'

    if status == 'open':
        # «Открытые»: новые + в работе + просроченные
        q = q.filter(Task.status.in_([Task.STATUS_NEW, Task.STATUS_IN_PROGRESS,
                                      Task.STATUS_OVERDUE]))
    elif status:
        q = q.filter(Task.status == status)
    if assignee_id:
        q = q.filter(Task.assignee_id == assignee_id)
    if mine:
        q = q.filter(Task.assignee_id == current_user.id)

    tasks = q.order_by(Task.due_date.asc().nullslast(),
                       Task.priority.asc()).all()
    users = User.query.order_by(User.fio).all()
    return render_template('tasks/list.html', tasks=tasks, users=users,
                           status=status, assignee_id=assignee_id, mine=mine)


@bp.route('/tasks/add', methods=['GET', 'POST'])
@bp.route('/tasks/<int:task_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_task(task_id=None):
    task = Task.query.get_or_404(task_id) if task_id else Task()
    if task_id:
        if not current_user.is_admin and task.assignee_id != current_user.id:
            abort(403)

    form = TaskForm(obj=task)
    form.client_id.choices = [(c.id, c.name) for c in
                              _visible_client_ids()]
    form.client_id.choices.insert(0, (0, '— без привязки —'))
    form.assignee_id.choices = [(u.id, u.fio) for u in
                                User.query.order_by(User.fio).all()]
    form.assignee_id.choices.insert(0, (0, '— не назначен —'))

    if request.method == 'GET':
        form.client_id.data = task.client_id or 0
        form.assignee_id.data = task.assignee_id or 0

    if form.validate_on_submit():
        task.title = form.title.data.strip()
        task.description = form.description.data or ''
        task.client_id = form.client_id.data or None
        task.assignee_id = form.assignee_id.data or None
        task.due_date = form.due_date.data
        task.priority = form.priority.data
        task.status = form.status.data
        db.session.add(task)
        db.session.commit()
        flash('Задача сохранена', 'success')
        return redirect(url_for('tasks.list_tasks'))

    return render_template('tasks/form.html', form=form, task=task)


def _visible_client_ids():
    q = Client.query
    if not current_user.is_admin:
        q = q.filter_by(responsible_id=current_user.id)
    return q.order_by(Client.name).all()


@bp.route('/tasks/<int:task_id>/status', methods=['POST'])
@login_required
def change_status(task_id):
    """Быстрая смена статуса из реестра."""
    task = Task.query.get_or_404(task_id)
    if not current_user.is_admin and task.assignee_id != current_user.id:
        abort(403)
    new_status = request.form.get('status', '')
    if new_status in ('new', 'in_progress', 'done', 'overdue'):
        task.status = new_status
        db.session.commit()
        flash(f'Статус задачи изменён на «{task.status_label}»', 'success')
    return redirect(request.referrer or url_for('tasks.list_tasks'))


@bp.route('/tasks/<int:task_id>/delete', methods=['POST'])
@login_required
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    if not current_user.is_admin and task.assignee_id != current_user.id:
        abort(403)
    db.session.delete(task)
    db.session.commit()
    flash('Задача удалена', 'success')
    return redirect(url_for('tasks.list_tasks'))


# ------------------------------------------------- справочник сроков (ФТ-4)
@bp.route('/deadlines')
@login_required
def list_deadlines():
    deadlines = ReportingDeadline.query.order_by(
        ReportingDeadline.periodicity, ReportingDeadline.code).all()
    upcoming = generate_deadline_tasks_preview()
    return render_template('deadlines/list.html',
                           deadlines=deadlines, upcoming=upcoming)


def generate_deadline_tasks_preview():
    """Ближайшие сроки в разрезе клиентов для визуального контроля (ФТ-4)."""
    from datetime import date, timedelta
    from flask import current_app

    today = date.today()
    lead = current_app.config['DEADLINE_LEAD_DAYS']
    result = []

    q = Client.query
    if not current_user.is_admin:
        q = q.filter_by(responsible_id=current_user.id)
    clients = q.filter_by(status='active').all()

    for d in ReportingDeadline.query.filter_by(is_enabled=True).all():
        # ближайший срок для формы
        best = None
        for m in range(-1, 14):
            y = today.year + (today.month + m - 1) // 12
            mo = (today.month + m - 1) % 12 + 1
            due = d.due_date_for_period(date(y, mo, 1))
            if today <= due <= today + timedelta(days=lead * 4):
                best = due
                break
        if best:
            overdue = best < today
            done_clients = {
                t.client_id for t in Task.query.filter_by(
                    reporting_deadline_id=d.id, due_date=best,
                    status=Task.STATUS_DONE).all()}
            result.append({
                'deadline': d, 'due': best, 'overdue': overdue,
                'clients_total': len(clients),
                'clients_done': len(done_clients),
            })
    return sorted(result, key=lambda x: x['due'])


@bp.route('/deadlines/generate', methods=['POST'])
@login_required
def generate_tasks_now():
    """Ручной запуск автогенерации задач по срокам (ФТ-3)."""
    from flask import current_app
    created = generate_deadline_tasks(current_app.config['DEADLINE_LEAD_DAYS'])
    flash(f'Создано задач: {len(created)}', 'success')
    return redirect(url_for('tasks.list_deadlines'))


@bp.route('/deadlines/add', methods=['GET', 'POST'])
@bp.route('/deadlines/<int:deadline_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_deadline(deadline_id=None):
    if not current_user.is_admin:
        abort(403)
    deadline = (ReportingDeadline.query.get_or_404(deadline_id)
                if deadline_id else ReportingDeadline())
    form = ReportingDeadlineForm(obj=deadline)
    if request.method == 'GET' and deadline_id is None:
        form.is_enabled.data = True
    if form.validate_on_submit():
        form.populate_obj(deadline)
        db.session.add(deadline)
        db.session.commit()
        flash('Вид отчётности сохранён', 'success')
        return redirect(url_for('tasks.list_deadlines'))
    return render_template('deadlines/form.html', form=form, deadline=deadline)
