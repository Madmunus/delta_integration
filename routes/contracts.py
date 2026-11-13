"""Маршруты договоров на сопровождение (ФТ-5)."""
from flask import (Blueprint, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import or_

from engine.forms import ContractForm
from engine.models import Client, Contract, Contractor, db

bp = Blueprint('contracts', __name__)

CONTRACT_STATUSES = ('draft', 'active', 'closed')


@bp.route('/contracts')
@login_required
def list_contracts():
    q = Contract.query.join(Client)
    if not current_user.is_admin:
        q = q.filter(Client.responsible_id == current_user.id)

    search = (request.args.get('q') or '').strip()
    if search:
        like = f'%{search}%'
        q = (q.outerjoin(Contractor, Contract.contractor_id == Contractor.id)
             .filter(or_(Contract.number.ilike(like),
                         Client.name.ilike(like),
                         Contractor.full_name.ilike(like),
                         Contract.description.ilike(like),
                         Contract.status_1c.ilike(like))))

    status = request.args.get('status') or ''
    if status not in CONTRACT_STATUSES:
        status = ''
    if status:
        q = q.filter(Contract.status == status)

    page = request.args.get('page', 1, type=int)
    pagination = q.order_by(Contract.signed_date.desc()).paginate(
        page=page, per_page=20, error_out=False)
    return render_template('contracts/list.html',
                           pagination=pagination,
                           search=search, status=status)


@bp.route('/contracts/<int:contract_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_contract(contract_id):
    contract = Contract.query.get_or_404(contract_id)
    form = ContractForm(obj=contract)
    base_q = Client.query
    if not current_user.is_admin:
        base_q = base_q.filter_by(responsible_id=current_user.id)
    form.client_id.choices = [(c.id, c.name) for c in
                              base_q.order_by(Client.name).all()]
    form.contractor_id.choices = [(0, '— не выбран —')] + [
        (c.id, c.display_name)
        for c in Contractor.query.order_by(Contractor.full_name).all()
    ]
    if request.method == 'GET':
        form.contractor_id.data = contract.contractor_id or 0
    if form.validate_on_submit():
        form.populate_obj(contract)
        contract.contractor_id = form.contractor_id.data or None
        db.session.add(contract)
        db.session.commit()
        flash('Договор сохранён', 'success')
        return redirect(url_for('clients.detail', client_id=contract.client_id))
    return render_template('contracts/form.html', form=form, contract=contract)
