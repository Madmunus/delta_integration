"""Маршруты договоров на сопровождение (ФТ-5)."""
from flask import (Blueprint, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required

from engine.forms import ContractForm
from engine.models import Client, Contract, Contractor, db

bp = Blueprint('contracts', __name__)


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
    return render_template('contracts/list.html', contracts=contracts, contracts_total=len(contracts), status=status)


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
