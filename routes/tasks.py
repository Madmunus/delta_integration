"""Маршруты задач."""
from datetime import date

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import case, or_
from sqlalchemy.orm import contains_eager, joinedload, load_only

from engine.audit import (TASK_CREATE, TASK_DELETE, TASK_STATUS, TASK_UPDATE,
                          TASKS_DELETE_ALL, log_action)
from engine.dates import apply_date_range
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


# Порядок статусов по умолчанию: Новая → В работе → Выполнена
DEFAULT_STATUS_ORDER = case(
    (Task.status == Task.STATUS_NEW, 0),
    (Task.status == Task.STATUS_IN_PROGRESS, 1),
    (Task.status == Task.STATUS_DONE, 2),
    else_=3,
)

# Приоритеты для фильтра: код -> подпись
PRIORITY_LABELS = {1: 'Высокий', 2: 'Средний', 3: 'Низкий'}


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
         .outerjoin(User, Task.assignee_id == User.id)
         .options(
             contains_eager(Task.client).load_only(Client.id, Client.name),
             contains_eager(Task.assignee).load_only(User.id, User.fio),
             load_only(Task.id, Task.title, Task.client_id,
                       Task.assignee_id, Task.due_date, Task.priority,
                       Task.status, Task.created_at,
                       Task.reporting_deadline_id),
         ))
    status = (request.args.get('status') or '').strip()
    # Старый параметр «Просроченные» поддерживается как псевдостатус
    if not status and request.args.get('overdue') == '1':
        status = 'overdue'

    assignee_param = (request.args.get('assignee') or '').strip()
    # Старый параметр «Только мои» поддерживается как вариант исполнителя
    if not assignee_param and request.args.get('mine') == '1':
        assignee_param = 'mine'
    mine = assignee_param == 'mine'
    assignee_id = None
    if assignee_param and not mine:
        try:
            assignee_id = int(assignee_param)
        except ValueError:
            assignee_id = None

    client_id = request.args.get('client', type=int)
    priority = request.args.get('priority', type=int)

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
    elif status == 'overdue':
        # Просрочка — не статус, а признак: срок истёк, задача не выполнена
        q = q.filter(Task.status != Task.STATUS_DONE,
                     Task.due_date.isnot(None),
                     Task.due_date < date.today())
    elif status:
        q = q.filter(Task.status == status)
    if assignee_id:
        q = q.filter(Task.assignee_id == assignee_id)
    if client_id:
        q = q.filter(Task.client_id == client_id)
    if priority in PRIORITY_LABELS:
        q = q.filter(Task.priority == priority)
    if mine:
        q = q.filter(Task.assignee_id == current_user.id)

    # Фильтр по сроку (диапазон «с — по» в едином поле)
    q, date_range = apply_date_range(q, Task.due_date, request.args)

    # Сортировка по столбцам: 3 состояния — по возрастанию, по убыванию
    # и отключена. По умолчанию: статус (Новая → В работе → Выполнена),
    # затем приоритет (Высокий → Средний → Низкий), затем срок по возрастанию.
    sort = request.args.get('sort') or None
    direction = request.args.get('dir') or None
    # Сотруднику колонка «Исполнитель» не показывается — не даём и сортировать по ней.
    if sort not in SORTABLE_COLUMNS or (sort == 'assignee'
                                        and not current_user.is_admin):
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
        order_by = (DEFAULT_STATUS_ORDER.asc(),
                    Task.priority.asc().nullslast(),
                    Task.due_date.asc().nullslast(),
                    Task.id.asc())

    page = request.args.get('page', 1, type=int)
    pagination = q.order_by(*order_by).paginate(
        page=page, per_page=20, error_out=False)
    users = User.query.order_by(User.fio).all()
    clients = _visible_client_ids()
    return render_template('tasks/list.html',
                           pagination=pagination,
                           users=users, clients=clients, status=status,
                           assignee_id=assignee_id, client_id=client_id,
                           priority=priority, priorities=PRIORITY_LABELS,
                           mine=mine,
                           search=search,
                           date_range=date_range,
                           sort=sort, direction=direction)


@bp.route('/tasks/<int:task_id>')
@login_required
def detail(task_id):
    """Карточка просмотра задачи."""
    task = (Task.query
            .options(joinedload(Task.client), joinedload(Task.assignee))
            .filter(Task.id == task_id).first_or_404())
    if not current_user.is_admin and task.assignee_id != current_user.id:
        abort(403)
    return render_template('tasks/detail.html', task=task)


@bp.route('/tasks/add', methods=['GET', 'POST'])
@bp.route('/tasks/<int:task_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_task(task_id=None):
    task = ((Task.query
             .options(joinedload(Task.client), joinedload(Task.assignee))
             .filter(Task.id == task_id).first_or_404())
            if task_id else Task())
    if task_id:
        if not current_user.is_admin and task.assignee_id != current_user.id:
            abort(403)

    form = TaskForm(obj=task)
    form.client_id.choices = [(c.id, c.name) for c in
                              _visible_client_ids()]
    form.client_id.choices.insert(0, (0, '— без привязки —'))
    form.assignee_id.choices = [(u.id, u.fio) for u in
                                User.query.order_by(User.fio).all()]

    if request.method == 'GET':
        client_param = request.args.get('client', type=int)
        if task_id:
            form.client_id.data = task.client_id or 0
        elif client_param and any(cid == client_param
                                  for cid, _ in form.client_id.choices):
            form.client_id.data = client_param
        else:
            form.client_id.data = 0
        form.assignee_id.data = task.assignee_id or current_user.id

    if form.validate_on_submit():
        is_new = task.id is None
        task.title = form.title.data.strip()
        task.description = form.description.data or ''
        task.client_id = form.client_id.data or None
        task.assignee_id = form.assignee_id.data
        task.due_date = form.due_date.data
        task.priority = form.priority.data
        task.status = form.status.data
        db.session.add(task)
        db.session.commit()
        log_action(TASK_CREATE if is_new else TASK_UPDATE,
                   f'Задача «{task.title}» '
                   f'{"создана" if is_new else "изменена"}')
        flash('Задача сохранена', 'success')
        return redirect(url_for('tasks.list_tasks'))

    return render_template('tasks/form.html', form=form, task=task)


def _visible_client_ids():
    q = Client.query
    if not current_user.is_admin:
        q = q.filter_by(responsible_id=current_user.id)
    return q.options(load_only(Client.id, Client.name)).order_by(Client.name).all()


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
        log_action(TASK_STATUS,
                   f'Задача «{task.title}»: {task.status_label}')
        flash(f'Статус задачи изменён на «{task.status_label}»', 'success')
    return redirect(request.referrer or url_for('tasks.list_tasks'))


@bp.route('/tasks/<int:task_id>/delete', methods=['POST'])
@login_required
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    if not current_user.is_admin and task.assignee_id != current_user.id:
        abort(403)
    title = task.title
    db.session.delete(task)
    db.session.commit()
    log_action(TASK_DELETE, f'Удалена задача «{title}»')
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
        log_action(TASKS_DELETE_ALL, f'Удалено задач: {count}')
        flash(f'Удалены все задачи ({count})', 'success')
    else:
        flash('Задач для удаления нет', 'info')
    return redirect(url_for('tasks.list_tasks'))
