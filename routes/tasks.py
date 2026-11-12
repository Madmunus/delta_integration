"""Маршруты задач."""
from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from engine.forms import TaskForm
from engine.models import Client, Task, User, db
from services.tasks import mark_overdue_tasks

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
    return render_template('tasks/list.html', tasks=tasks, tasks_total=len(tasks), users=users,
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
