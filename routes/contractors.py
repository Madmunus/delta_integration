"""Справочник контрагентов из главной базы «1С:Предприятие»."""
from flask import (Blueprint, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import or_

from engine.dates import apply_created_at_range
from engine.forms import ContractorForm
from engine.models import Client, Contract, Contractor, Document, db

bp = Blueprint('contractors', __name__)


@bp.route('/contractors')
@login_required
def list_contractors():
    """Список контрагентов из Catalog_Контрагенты с поиском по таблице."""
    search = (request.args.get('q') or '').strip()
    q = Contractor.query
    if search:
        like = f'%{search}%'
        q = q.filter(or_(Contractor.full_name.ilike(like),
                         Contractor.inn.ilike(like),
                         Contractor.kpp.ilike(like),
                         Contractor.notes.ilike(like)))
    date_from = request.args.get('date_from') or ''
    date_to = request.args.get('date_to') or ''
    q, date_from, date_to = apply_created_at_range(
        q, Contractor.created_at, date_from, date_to)
    page = request.args.get('page', 1, type=int)
    pagination = q.order_by(Contractor.full_name).paginate(
        page=page, per_page=20, error_out=False)
    return render_template('contractors/list.html',
                           pagination=pagination,
                           search=search,
                           date_from=date_from, date_to=date_to)


@bp.route('/contractors/<int:contractor_id>')
@login_required
def detail(contractor_id):
    """Карточка контрагента: реквизиты, договоры и документы.

    Договоры и документы показываются только по клиентам, доступным
    текущему пользователю (ФТ-1).
    """
    contractor = Contractor.query.get_or_404(contractor_id)

    contracts_q = Contract.query.filter(
        Contract.contractor_id == contractor.id)
    documents_q = Document.query.filter(
        Document.contractor_id == contractor.id)
    if not current_user.is_admin:
        contracts_q = (contracts_q
                       .join(Client, Contract.client_id == Client.id)
                       .filter(Client.responsible_id == current_user.id))
        documents_q = (documents_q
                       .join(Client, Document.client_id == Client.id)
                       .filter(Client.responsible_id == current_user.id))

    contracts = contracts_q.order_by(Contract.signed_date.desc()).all()
    documents = (documents_q
                 .order_by(Document.doc_date.desc().nullslast()).all())
    return render_template('contractors/detail.html', contractor=contractor,
                           contracts=contracts, documents=documents)


@bp.route('/contractors/<int:contractor_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(contractor_id):
    """Карточка редактирования контрагента: вручную меняются только заметки."""
    contractor = Contractor.query.get_or_404(contractor_id)
    form = ContractorForm(obj=contractor)
    if form.validate_on_submit():
        contractor.notes = form.notes.data or ''
        db.session.commit()
        flash('Заметки контрагента сохранены', 'success')
        return redirect(url_for('contractors.detail', contractor_id=contractor.id))
    return render_template('contractors/form.html', contractor=contractor,
                           form=form)
