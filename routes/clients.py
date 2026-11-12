"""Маршруты реестра клиентов и карточки клиента."""
from datetime import datetime

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import case, or_

from engine.forms import ClientForm
from engine.models import Client, Contract, Document, Task, User, db
from services.sync import sync_contracts

bp = Blueprint('clients', __name__)


def visible_clients_query():
    """ФТ-7: руководитель видит всех, сотрудник — только своих клиентов."""
    q = Client.query
    if not current_user.is_admin:
        q = q.filter_by(responsible_id=current_user.id)
    return q


def _parse_query_date(value):
    """Дата из параметра запроса (ISO или ДД.ММ.ГГГГ); None при ошибке."""
    if not value:
        return None
    value = value.strip()
    for fmt in ('%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


# Сортируемые столбцы реестра: имя параметра -> колонка/выражение
SORTABLE_COLUMNS = {
    'name': Client.name,
    'inn': Client.inn,
    'kpp': Client.kpp,
    'status': Client.status,
    'responsible': User.fio,
    'date_added': Client.date_added,
}


@bp.route('/clients')
@login_required
def list_clients():
    q = visible_clients_query()

    # Поиск и фильтрация (ФТ-1)
    search = (request.args.get('q') or '').strip()
    status = request.args.get('status') or ''
    responsible = request.args.get('responsible') or ''
    date_from = request.args.get('date_from') or ''
    date_to = request.args.get('date_to') or ''
    if search:
        like = f'%{search}%'
        q = q.filter(or_(Client.name.ilike(like),
                         Client.inn.ilike(like),
                         Client.kpp.ilike(like),
                         Client.notes.ilike(like)))
    if status in ('active', 'archived'):
        q = q.filter_by(status=status)
    # Фильтр по ответственному — только для руководителей (ФТ-7)
    if responsible:
        if not current_user.is_admin:
            abort(403)
        if responsible == 'none':
            q = q.filter(Client.responsible_id.is_(None))
        elif responsible.isdigit():
            q = q.filter(Client.responsible_id == int(responsible))
    # Поиск по дате добавления (диапазон «с — по»)
    d_from = _parse_query_date(date_from)
    d_to = _parse_query_date(date_to)
    # Поля дат текстовые: показываем в привычном формате дд.мм.гггг
    date_from = d_from.strftime('%d.%m.%Y') if d_from else ''
    date_to = d_to.strftime('%d.%m.%Y') if d_to else ''
    if d_from:
        q = q.filter(Client.date_added >= d_from)
    if d_to:
        q = q.filter(Client.date_added <= d_to)

    # Сортировка по столбцам: 3 состояния — по возрастанию, по убыванию
    # и отключена (сортировка по умолчанию). По умолчанию: сначала активные,
    # затем архивные, внутри группы — по наименованию.
    sort = request.args.get('sort') or None
    direction = request.args.get('dir') or None
    if sort not in SORTABLE_COLUMNS:
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
    total = q.count()
    pagination = q.order_by(*order_by).paginate(
        page=page, per_page=20, error_out=False)
    responsibles = (User.query.order_by(User.fio).all()
                    if current_user.is_admin else [])
    return render_template('clients/list.html',
                           pagination=pagination, total=total, search=search,
                           status=status, responsibles=responsibles,
                           responsible=responsible,
                           date_from=date_from, date_to=date_to,
                           sort=sort, direction=direction)


@bp.route('/clients/<int:client_id>')
@login_required
def detail(client_id):
    client = Client.query.get_or_404(client_id)
    # ФТ-7: сотрудник не видит чужих клиентов
    if not current_user.is_admin and client.responsible_id != current_user.id:
        abort(403)
    contracts = client.contracts.order_by(Contract.signed_date.desc()).all()
    tasks = client.tasks.order_by(Task.due_date.asc().nullslast()).all()
    documents = client.documents.order_by(Document.doc_date.desc().nullslast()).all()
    return render_template('clients/detail.html', client=client,
                           contracts=contracts, tasks=tasks,
                           documents=documents)


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
        client.responsible_id = form.responsible_id.data or None
        client.notes = form.notes.data or ''
        client.odata_url = (form.odata_url.data or '').strip()
        client.odata_username = (form.odata_username.data or '').strip()
        if form.odata_password.data:
            client.odata_password = form.odata_password.data
        db.session.commit()
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
    stats = sync_contracts(client)
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
    flash(f'Клиент «{name}» и связанные данные удалены', 'success')
    return redirect(url_for('clients.list_clients'))
