"""Маршруты задач."""
from datetime import date

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import or_

from engine.dates import apply_created_at_range
from engine.forms import TaskForm
from engine.models import Client, Task, User, db

bp = Blueprint('tasks', __name__)

# Русские названия статусов для поиска по таблице
_STATUS_LABELS = {
    Task.STATUS_NEW: 'новая',
    Task.STATUS_IN_PROGRESS: 'в работе',
    Task.STATUS_DONE: 'выполнена',
}

# Сортируемые столбцы реестра: имя параметра -> колонка/выражение
SORTABLE_COLUMNS = {
    'title': Task.title,
    'client': Client.name,
    'assignee': User.fio,
    'due_date': Task.due_date,
    'priority': Task.priority,
    'status': Task.status,
    'created_at': Task.created_at,
}


def _task_filter(q):
    """ФТ-1: сотрудник видит только свои задачи."""
    if not current_user.is_admin:
        q = q.filter(Task.assignee_id == current_user.id)
    return q


@bp.route('/tasks')
@login_required
def list_tasks():
    # Клиент и исполнитель нужны и для поиска, и для сортировки по столбцам
    q = (_task_filter(Task.query)
         .outerjoin(Client, Task.client_id == Client.id)
         .outerjoin(User, Task.assignee_id == User.id))
    status = request.args.get('status') or ''
    assignee_id = request.args.get('assignee', type=int)
    mine = request.args.get('mine') == '1'
    overdue = request.args.get('overdue') == '1'

    search = (request.args.get('q') or '').strip()
    if search:
        like = f'%{search}%'
        lowered = search.lower()
        status_codes = [code for code, label in _STATUS_LABELS.items()
                        if lowered in label or lowered in code]
        conditions = [Task.title.ilike(like),
                      Task.description.ilike(like),
                      Client.name.ilike(like),
                      User.fio.ilike(like)]
        if status_codes:
            conditions.append(Task.status.in_(status_codes))
        q = q.filter(or_(*conditions))

    if status == 'open':
        # «Открытые»: новые + в работе
        q = q.filter(Task.status.in_([Task.STATUS_NEW, Task.STATUS_IN_PROGRESS]))
    elif status:
        q = q.filter(Task.status == status)
    if assignee_id:
        q = q.filter(Task.assignee_id == assignee_id)
    if mine:
        q = q.filter(Task.assignee_id == current_user.id)
    if overdue:
        # Просрочка — не статус, а признак: срок истёк, задача не выполнена
        q = q.filter(Task.status != Task.STATUS_DONE,
                     Task.due_date.isnot(None),
                     Task.due_date < date.today())

    date_from = request.args.get('date_from') or ''
    date_to = request.args.get('date_to') or ''
    q, date_from, date_to = apply_created_at_range(
        q, Task.created_at, date_from, date_to)

    # Сортировка по столбцам: 3 состояния — по возрастанию, по убыванию
    # и отключена (сортировка по умолчанию: сначала ближайший срок).
    sort = request.args.get('sort') or None
    direction = request.args.get('dir') or None
    if sort not in SORTABLE_COLUMNS:
        sort = None
        direction = None
    elif direction not in ('asc', 'desc'):
        direction = 'asc'

    if sort:
        order_col = SORTABLE_COLUMNS[sort]
        order_expr = (order_col.desc() if direction == 'desc'
                      else order_col.asc()).nullslast()
        order_by = (order_expr, Task.id.asc())
    else:
        order_by = (Task.due_date.asc().nullslast(), Task.priority.asc())

    page = request.args.get('page', 1, type=int)
    pagination = q.order_by(*order_by).paginate(
        page=page, per_page=20, error_out=False)
    users = User.query.order_by(User.fio).all()
    return render_template('tasks/list.html',
                           pagination=pagination,
                           users=users, status=status,
                           assignee_id=assignee_id, mine=mine,
                           overdue=overdue, search=search,
                           date_from=date_from, date_to=date_to,
                           sort=sort, direction=direction)


@bp.route('/tasks/<int:task_id>')
@login_required
def detail(task_id):
    """Карточка просмотра задачи."""
    task = Task.query.get_or_404(task_id)
    if not current_user.is_admin and task.assignee_id != current_user.id:
        abort(403)
    return render_template('tasks/detail.html', task=task)


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
        client_param = request.args.get('client', type=int)
        if task_id:
            form.client_id.data = task.client_id or 0
        elif client_param and any(cid == client_param
                                  for cid, _ in form.client_id.choices):
            form.client_id.data = client_param
        else:
            form.client_id.data = 0
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
    if new_status in ('new', 'in_progress', 'done'):
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


@bp.route('/tasks/delete-all', methods=['POST'])
@login_required
def delete_all_tasks():
    """Удаление всех задач — доступно только руководителю."""
    if not current_user.is_admin:
        abort(403)
    count = Task.query.count()
    if count:
        Task.query.delete()
        db.session.commit()
        flash(f'Удалены все задачи ({count})', 'success')
    else:
        flash('Задач для удаления нет', 'info')
    return redirect(url_for('tasks.list_tasks'))
