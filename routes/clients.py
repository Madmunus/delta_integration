"""Маршруты реестра клиентов и карточки клиента."""
from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import case, func, or_
from sqlalchemy.orm import joinedload, load_only

from engine import circuit_breaker
from engine.audit import (CLIENT_DELETE, CLIENT_SYNC_CONTRACT, CLIENT_UPDATE,
                          log_action)
from engine.dates import apply_date_range
from engine.forms import ClientForm
from engine.models import Client, Contract, Contractor, Document, Task, User, db
from engine.secret_storage import encrypt_secret
from engine.sync import sync_contracts

bp = Blueprint('clients', __name__)

# Сколько строк дочерних реестров показывать в карточке клиента
DETAIL_ROWS = 10


def visible_clients_query():
    """ФТ-1: руководитель видит всех, сотрудник — только своих клиентов."""
    q = Client.query
    if not current_user.is_admin:
        q = q.filter_by(responsible_id=current_user.id)
    return q


# Сортируемые столбцы реестра: имя параметра -> колонка/выражение
SORTABLE_COLUMNS = {
    'name': Client.name,
    'inn': Client.inn,
    'status': Client.status,
    'responsible': User.fio,
    'created_at': Client.created_at,
}


@bp.route('/clients')
@login_required
def list_clients():
    # Ответственный выводится в каждой строке реестра — грузим его сразу.
    # Ограничиваем и поля реестра, и поля пользователя: раньше в выборку
    # попадали и пароли 1С клиентов, и хеши пользователей.
    q = visible_clients_query().options(
        load_only(Client.id, Client.name, Client.inn, Client.status,
                  Client.notes, Client.created_at, Client.responsible_id),
        joinedload(Client.responsible).load_only(User.id, User.fio),
    )

    # Поиск и фильтрация (ФТ-2)
    search = (request.args.get('q') or '').strip()
    status = request.args.get('status') or ''
    responsible = request.args.get('responsible') or ''
    if search:
        like = f'%{search}%'
        q = q.filter(or_(Client.name.ilike(like),
                         Client.inn.ilike(like),
                         Client.kpp.ilike(like),
                         Client.notes.ilike(like)))
    if status in ('active', 'archived'):
        q = q.filter_by(status=status)
    # Фильтр по ответственному — только для руководителей (ФТ-1)
    if responsible:
        if not current_user.is_admin:
            abort(403)
        if responsible == 'none':
            q = q.filter(Client.responsible_id.is_(None))
        elif responsible.isdigit():
            q = q.filter(Client.responsible_id == int(responsible))
    # Поиск по дате создания (диапазон «с — по» в едином поле)
    q, date_range = apply_date_range(q, Client.created_at, request.args)

    # Сортировка по столбцам: 3 состояния — по возрастанию, по убыванию
    # и отключена (сортировка по умолчанию). По умолчанию: сначала активные,
    # затем архивные, внутри группы — по наименованию.
    sort = request.args.get('sort') or None
    direction = request.args.get('dir') or None
    # Сотруднику колонка «Ответственный» не показывается — не даём и сортировать по ней.
    if sort not in SORTABLE_COLUMNS or (sort == 'responsible'
                                        and not current_user.is_admin):
        sort = None
        direction = None
    elif direction not in ('asc', 'desc'):
        direction = 'asc'

    if sort:
        if sort == 'responsible':
            q = q.outerjoin(User, Client.responsible_id == User.id)
        order_col = SORTABLE_COLUMNS[sort]
        order_expr = (order_col.desc() if direction == 'desc'
                      else order_col.asc()).nullslast()
        order_by = (order_expr, Client.name.asc())
    else:
        # Активные (0) идут раньше архивных (1)
        status_order = case((Client.status == 'active', 0), else_=1)
        order_by = (status_order, Client.name.asc())

    page = request.args.get('page', 1, type=int)
    pagination = q.order_by(*order_by).paginate(
        page=page, per_page=20, error_out=False)
    responsibles = (User.query.order_by(User.fio).all()
                    if current_user.is_admin else [])
    return render_template('clients/list.html',
                           pagination=pagination,
                           search=search,
                           status=status, responsibles=responsibles,
                           responsible=responsible,
                           date_range=date_range,
                           sort=sort, direction=direction)


@bp.route('/clients/<int:client_id>')
@login_required
def detail(client_id):
    # Query.get() не применяет loader options, поэтому используем filter().
    client = (Client.query.options(joinedload(Client.responsible))
              .filter(Client.id == client_id).first_or_404())
    # ФТ-1: сотрудник не видит чужих клиентов
    if not current_user.is_admin and client.responsible_id != current_user.id:
        abort(403)
    # dynamic-связи нельзя загрузить joinedload, поэтому запрашиваем
    # дочерние реестры отдельно и сразу подтягиваем их связи.
    # В карточке показываем первые DETAIL_ROWS строк; полный список —
    # в соответствующем реестре со ссылкой на этого клиента.
    contracts_q = (Contract.query
                   .options(
                       load_only(Contract.id, Contract.number,
                                 Contract.signed_date, Contract.start_date,
                                 Contract.end_date, Contract.status,
                                 Contract.status_1c, Contract.client_id,
                                 Contract.contractor_id, Contract.amount,
                                 Contract.created_at),
                       joinedload(Contract.client).load_only(
                           Client.id, Client.name, Client.status),
                       joinedload(Contract.contractor).load_only(
                           Contractor.id, Contractor.full_name,
                           Contractor.external_ref),
                   )
                   .filter(Contract.client_id == client_id)
                   .order_by(Contract.signed_date.desc()))
    tasks_q = (Task.query
               .options(
                   load_only(Task.id, Task.title, Task.client_id,
                             Task.assignee_id, Task.due_date, Task.priority,
                             Task.status, Task.created_at,
                             Task.reporting_deadline_id),
                   joinedload(Task.client).load_only(Client.id, Client.name),
                   joinedload(Task.assignee).load_only(User.id, User.fio),
               )
               .filter(Task.client_id == client_id)
               .order_by(Task.due_date.asc().nullslast()))
    documents_q = (Document.query
                   .options(
                       load_only(Document.id, Document.doc_type,
                                 Document.number, Document.doc_date,
                                 Document.status, Document.client_id,
                                 Document.contractor_id,
                                 Document.external_url,
                                 Document.created_at),
                       joinedload(Document.client).load_only(
                           Client.id, Client.name),
                       joinedload(Document.contractor).load_only(
                           Contractor.id, Contractor.full_name,
                           Contractor.external_ref),
                   )
                   .filter(Document.client_id == client_id)
                   .order_by(Document.doc_date.desc().nullslast()))
    # Три счётчика — одним запросом: скалярные подзапросы не умножают
    # строки друг на друга, в отличие от CROSS JOIN.
    counts = db.session.query(
        db.select(func.count()).select_from(Contract)
        .where(Contract.client_id == client_id)
        .scalar_subquery().label('contracts_total'),
        db.select(func.count()).select_from(Task)
        .where(Task.client_id == client_id)
        .scalar_subquery().label('tasks_total'),
        db.select(func.count()).select_from(Document)
        .where(Document.client_id == client_id)
        .scalar_subquery().label('documents_total'),
    ).one()
    contracts_total = counts.contracts_total
    tasks_total = counts.tasks_total
    documents_total = counts.documents_total
    contracts = contracts_q.limit(DETAIL_ROWS).all()
    tasks = tasks_q.limit(DETAIL_ROWS).all()
    documents = documents_q.limit(DETAIL_ROWS).all()
    return render_template('clients/detail.html', client=client,
                           contracts=contracts, tasks=tasks,
                           documents=documents,
                           contracts_total=contracts_total,
                           tasks_total=tasks_total,
                           documents_total=documents_total,
                           detail_rows=DETAIL_ROWS)


@bp.route('/clients/<int:client_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(client_id):
    client = Client.query.get_or_404(client_id)
    if not current_user.is_admin and client.responsible_id != current_user.id:
        abort(403)
    form = ClientForm(obj=client)
    form.responsible_id.choices = [(u.id, u.fio) for u in
                                    User.query.order_by(User.fio).all()]
    form.responsible_id.choices.insert(0, (0, '— не назначен —'))
    if request.method == 'GET':
        form.responsible_id.data = client.responsible_id or 0
        form.odata_password.data = ''  # пароль не показываем
    if form.validate_on_submit():
        new_url = (form.odata_url.data or '').strip()
        new_username = (form.odata_username.data or '').strip()
        credentials_changed = (
            client.odata_url != new_url
            or client.odata_username != new_username
            or bool(form.odata_password.data))
        client.responsible_id = form.responsible_id.data or None
        client.notes = form.notes.data or ''
        client.odata_url = new_url
        client.odata_username = new_username
        if form.odata_password.data:
            client.odata_password = encrypt_secret(form.odata_password.data)
        if credentials_changed:
            # Новые реквизиты — старые ошибки и блокировка breaker неактуальны.
            circuit_breaker.reset(client.id)
        db.session.commit()
        log_action(CLIENT_UPDATE, f'Изменён клиент «{client.name}»')
        flash('Изменения сохранены', 'success')
        return redirect(url_for('clients.detail', client_id=client.id))
    return render_template('clients/form.html', form=form, client=client)


@bp.route('/clients/<int:client_id>/sync-contracts', methods=['POST'])
@login_required
def sync_client_contracts(client_id):
    """Загрузить договоры клиента из его базы «1С:Бухгалтерия»."""
    client = Client.query.get_or_404(client_id)
    if not current_user.is_admin and client.responsible_id != current_user.id:
        abort(403)
    force = request.args.get('force') == '1'
    stats = sync_contracts(client, force=force)
    log_action(CLIENT_SYNC_CONTRACT,
               f'Клиент «{client.name}»: создано {stats["created"]}, '
               f'обновлено {stats["updated"]}')
    for err in stats['errors'][:5]:
        flash(err, 'warning')
    if not stats['errors']:
        flash(f"Договоры клиента «{client.name}»: создано {stats['created']}, "
              f"обновлено {stats['updated']}", 'success')
    return redirect(url_for('clients.detail', client_id=client.id))


@bp.route('/clients/<int:client_id>/delete', methods=['POST'])
@login_required
def delete(client_id):
    """Удаление клиента вместе со связанными договорами, задачами, документами."""
    if not current_user.is_admin:
        abort(403)
    client = Client.query.get_or_404(client_id)
    name = client.name
    db.session.delete(client)  # cascade: contracts, tasks, documents
    db.session.commit()
    log_action(CLIENT_DELETE, f'Удалён клиент «{name}»')
    flash(f'Клиент «{name}» и связанные данные удалены', 'success')
    return redirect(url_for('clients.list_clients'))
