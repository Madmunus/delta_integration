"""Маршруты договоров на сопровождение (ФТ-3)."""
from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import or_

from engine.dates import apply_created_at_range
from engine.forms import ContractForm
from engine.models import Client, Contract, Contractor, db

bp = Blueprint('contracts', __name__)

CONTRACT_STATUSES = ('draft', 'active', 'closed')

# Сортируемые столбцы реестра: имя параметра -> колонка/выражение
SORTABLE_COLUMNS = {
    'number': Contract.number,
    'signed_date': Contract.signed_date,
    'client': Client.name,
    'contractor': Contractor.full_name,
    'amount': Contract.amount,
    'start_date': Contract.start_date,
    'status': Contract.status,
    'created_at': Contract.created_at,
}


def _get_visible_contract(contract_id):
    """Договор, доступный текущему пользователю (сотрудник — только свои)."""
    contract = Contract.query.get_or_404(contract_id)
    if not current_user.is_admin and contract.client.responsible_id != current_user.id:
        abort(403)
    return contract


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

    date_from = request.args.get('date_from') or ''
    date_to = request.args.get('date_to') or ''
    q, date_from, date_to = apply_created_at_range(
        q, Contract.created_at, date_from, date_to)

    # Сортировка по столбцам: 3 состояния — по возрастанию, по убыванию
    # и отключена (сортировка по умолчанию: сначала новые договоры).
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
        order_by = (Contract.signed_date.desc(),)

    page = request.args.get('page', 1, type=int)
    pagination = q.order_by(*order_by).paginate(
        page=page, per_page=20, error_out=False)
    return render_template('contracts/list.html',
                           pagination=pagination,
                           search=search, status=status,
                           date_from=date_from, date_to=date_to,
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
