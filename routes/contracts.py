"""Маршруты договоров на сопровождение (ФТ-3)."""
from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import case, or_

from engine.dates import apply_date_range
from engine.forms import ContractForm
from engine.models import Client, Contract, Contractor, db

bp = Blueprint('contracts', __name__)

CONTRACT_STATUSES = ('draft', 'active', 'closed')

# Значения фильтра по внутреннему признаку «Обработан»
PROCESSED_VALUES = ('yes', 'no')

# Порядок статусов по умолчанию: Проект → Действует → Закрыт
DEFAULT_STATUS_ORDER = case(
    (Contract.status == 'draft', 0),
    (Contract.status == 'active', 1),
    (Contract.status == 'closed', 2),
    else_=3,
)

# Сортируемые столбцы реестра: имя параметра -> колонка/выражение
SORTABLE_COLUMNS = {
    'number': Contract.number,
    'signed_date': Contract.signed_date,
    'client': Client.name,
    'contractor': Contractor.full_name,
    'amount': Contract.amount,
    'start_date': Contract.start_date,
    'status': Contract.status,
    'processed': Contract.processed,
    'created_at': Contract.created_at,
}


def _get_visible_contract(contract_id):
    """Договор, доступный текущему пользователю (сотрудник — только свои)."""
    contract = Contract.query.get_or_404(contract_id)
    if not current_user.is_admin and contract.client.responsible_id != current_user.id:
        abort(403)
    return contract


def _visible_clients():
    """Клиенты, доступные текущему пользователю (сотрудник — только свои)."""
    q = Client.query
    if not current_user.is_admin:
        q = q.filter(Client.responsible_id == current_user.id)
    return q.order_by(Client.name).all()


@bp.route('/contracts')
@login_required
def list_contracts():
    # Контрагент нужен и для поиска, и для сортировки по столбцу
    q = (Contract.query.join(Client)
         .outerjoin(Contractor, Contract.contractor_id == Contractor.id))
    if not current_user.is_admin:
        q = q.filter(Client.responsible_id == current_user.id)

    search = (request.args.get('q') or '').strip()
    if search:
        like = f'%{search}%'
        q = q.filter(or_(Contract.number.ilike(like),
                         Client.name.ilike(like),
                         Contractor.full_name.ilike(like),
                         Contract.description.ilike(like),
                         Contract.status_1c.ilike(like)))

    status = request.args.get('status') or ''
    if status not in CONTRACT_STATUSES:
        status = ''
    if status:
        q = q.filter(Contract.status == status)

    # Фильтр по внутреннему признаку «Обработан» (Да / Нет)
    processed = request.args.get('processed') or ''
    if processed not in PROCESSED_VALUES:
        processed = ''
    if processed == 'yes':
        q = q.filter(Contract.processed.is_(True))
    elif processed == 'no':
        q = q.filter(Contract.processed.is_(False))

    # Фильтр по дате договора (диапазон «с — по» в едином поле)
    q, date_range = apply_date_range(q, Contract.signed_date, request.args)

    # Фильтры по клиенту и контрагенту (идут после фильтра по дате)
    client_id = request.args.get('client', type=int)
    contractor_id = request.args.get('contractor', type=int)
    if client_id:
        q = q.filter(Contract.client_id == client_id)
    if contractor_id:
        q = q.filter(Contract.contractor_id == contractor_id)

    # Сортировка по столбцам: 3 состояния — по возрастанию, по убыванию
    # и отключена. По умолчанию: сначала проекты, затем действующие
    # и завершённые; внутри статуса — новые договоры, затем по номеру.
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
        order_by = (order_expr, Contract.id.desc())
    else:
        order_by = (DEFAULT_STATUS_ORDER.asc(),
                    Contract.signed_date.desc().nullslast(),
                    Contract.number.asc())

    page = request.args.get('page', 1, type=int)
    pagination = q.order_by(*order_by).paginate(
        page=page, per_page=20, error_out=False)
    clients = _visible_clients()
    contractors = Contractor.query.order_by(Contractor.full_name).all()
    return render_template('contracts/list.html',
                           pagination=pagination,
                           search=search, status=status,
                           processed=processed,
                           client_id=client_id, contractor_id=contractor_id,
                           clients=clients, contractors=contractors,
                           date_range=date_range,
                           sort=sort, direction=direction)


@bp.route('/contracts/<int:contract_id>')
@login_required
def detail(contract_id):
    contract = _get_visible_contract(contract_id)
    return render_template('contracts/detail.html', contract=contract)


@bp.route('/contracts/<int:contract_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_contract(contract_id):
    contract = _get_visible_contract(contract_id)
    form = ContractForm(obj=contract)
    if form.validate_on_submit():
        contract.processed = form.processed.data
        contract.notes = form.notes.data or ''
        db.session.add(contract)
        db.session.commit()
        flash('Договор сохранён', 'success')
        return redirect(url_for('contracts.detail', contract_id=contract.id))
    return render_template('contracts/form.html', form=form, contract=contract)
