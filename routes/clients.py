"""Маршруты реестра клиентов, карточки клиента и договоров."""
from datetime import date, datetime

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import case, or_

from engine.forms import ClientForm, ContractForm, ODataBaseForm
from engine.models import Client, Contract, DocumentRef, ODataBase, Task, User, db
from services.sync import sync_clients

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
    pagination = q.order_by(*order_by).paginate(
        page=page, per_page=20, error_out=False)
    responsibles = (User.query.order_by(User.fio).all()
                    if current_user.is_admin else [])
    return render_template('clients/list.html',
                           pagination=pagination, search=search,
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
    documents = client.documents.order_by(DocumentRef.doc_date.desc().nullslast()).all()
    return render_template('clients/detail.html', client=client,
                           contracts=contracts, tasks=tasks,
                           documents=documents)


@bp.route('/clients/add', methods=['GET', 'POST'])
@login_required
def add():
    form = ClientForm()
    form.responsible_id.choices = [(u.id, u.fio) for u in
                                   User.query.order_by(User.fio).all()]
    form.responsible_id.choices.insert(0, (0, '— не назначен —'))
    if form.validate_on_submit():
        client = Client(
            name=form.name.data.strip(),
            inn=form.inn.data.strip() or None,
            kpp=form.kpp.data.strip() or None,
            status=form.status.data,
            responsible_id=form.responsible_id.data or None,
            notes=form.notes.data or '',
            date_added=date.today(),
        )
        db.session.add(client)
        db.session.commit()
        flash(f'Клиент «{client.name}» добавлен', 'success')
        return redirect(url_for('clients.detail', client_id=client.id))
    return render_template('clients/form.html', form=form, client=None)


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
    if form.validate_on_submit():
        client.name = form.name.data.strip()
        client.inn = form.inn.data.strip() or None
        client.kpp = form.kpp.data.strip() or None
        client.status = form.status.data
        client.responsible_id = form.responsible_id.data or None
        client.notes = form.notes.data or ''
        db.session.commit()
        flash('Изменения сохранены', 'success')
        return redirect(url_for('clients.detail', client_id=client.id))
    return render_template('clients/form.html', form=form, client=client)


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


# ------------------------------------------------------------- договоры ФТ-5
@bp.route('/contracts')
@login_required
def list_contracts():
    q = Contract.query.join(Client)
    if not current_user.is_admin:
        q = q.filter(Client.responsible_id == current_user.id)
    status = request.args.get('status') or ''
    if status:
        q = q.filter(Contract.status == status)
    contracts = q.order_by(Contract.signed_date.desc()).all()
    return render_template('contracts/list.html', contracts=contracts, status=status)


@bp.route('/contracts/add', methods=['GET', 'POST'])
@bp.route('/contracts/<int:contract_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_contract(contract_id=None):
    contract = Contract.query.get_or_404(contract_id) if contract_id else Contract()
    form = ContractForm(obj=contract)
    base_q = Client.query
    if not current_user.is_admin:
        base_q = base_q.filter_by(responsible_id=current_user.id)
    form.client_id.choices = [(c.id, c.name) for c in
                              base_q.order_by(Client.name).all()]
    if form.validate_on_submit():
        form.populate_obj(contract)
        db.session.add(contract)
        db.session.commit()
        flash('Договор сохранён', 'success')
        return redirect(url_for('clients.detail', client_id=contract.client_id))
    return render_template('contracts/form.html', form=form, contract=contract)


# ------------------------------------------- учётные базы 1С (админ, НФТ-3)
@bp.route('/bases')
@login_required
def list_bases():
    if not current_user.is_admin:
        abort(403)
    bases = ODataBase.query.order_by(ODataBase.name).all()
    return render_template('bases/list.html', bases=bases)


@bp.route('/bases/add', methods=['GET', 'POST'])
@bp.route('/bases/<int:base_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_base(base_id=None):
    if not current_user.is_admin:
        abort(403)
    base = ODataBase.query.get_or_404(base_id) if base_id else ODataBase()
    form = ODataBaseForm(obj=base)
    if form.validate_on_submit():
        form.populate_obj(base)
        db.session.add(base)
        db.session.commit()
        flash('Учётная база сохранена', 'success')
        return redirect(url_for('clients.list_bases'))
    return render_template('bases/form.html', form=form, base=base)


@bp.route('/bases/<int:base_id>/delete', methods=['POST'])
@login_required
def delete_base(base_id):
    if not current_user.is_admin:
        abort(403)
    base = ODataBase.query.get_or_404(base_id)
    db.session.delete(base)
    db.session.commit()
    flash('Учётная база удалена', 'success')
    return redirect(url_for('clients.list_bases'))


@bp.route('/sync/run', methods=['POST'])
@login_required
def run_sync():
    """Ручной запуск синхронизации клиентов из 1С (ФТ-1)."""
    if not current_user.is_admin:
        abort(403)
    stats = sync_clients()
    if stats['errors']:
        for err in stats['errors'][:5]:
            flash(err, 'warning')
    flash(f"Синхронизация завершена: создано {stats['created']}, "
          f"обновлено {stats['updated']}", 'success')
    return redirect(url_for('clients.list_bases'))
