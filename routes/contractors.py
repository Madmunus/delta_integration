"""Справочник контрагентов из главной базы «1С:Предприятие»."""
from flask import (Blueprint, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload, load_only

from engine.audit import CONTRACTOR_UPDATE, log_action
from engine.dates import apply_date_range
from engine.forms import ContractorForm
from engine.models import Client, Contract, Contractor, Document, db

bp = Blueprint('contractors', __name__)

# Сколько строк дочерних реестров показывать в карточке контрагента
DETAIL_ROWS = 10

# Сортируемые столбцы реестра: имя параметра -> колонка/выражение
SORTABLE_COLUMNS = {
    'name': Contractor.full_name,
    'inn': Contractor.inn,
    'status': Contractor.status,
    'notes': Contractor.notes,
    'created_at': Contractor.created_at,
}


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
    # Фильтр по активности записи: активные / архивные
    status = request.args.get('status') or ''
    if status not in ('active', 'archived'):
        status = ''
    if status:
        q = q.filter(Contractor.status == status)
    q, date_range = apply_date_range(q, Contractor.created_at, request.args)

    # Сортировка по столбцам: 3 состояния — по возрастанию, по убыванию
    # и отключена (сортировка по умолчанию: по наименованию).
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
        order_by = (order_expr, Contractor.full_name.asc())
    else:
        order_by = (Contractor.full_name.asc(),)

    page = request.args.get('page', 1, type=int)
    pagination = q.order_by(*order_by).paginate(
        page=page, per_page=20, error_out=False)
    return render_template('contractors/list.html',
                           pagination=pagination,
                           search=search,
                           status=status,
                           date_range=date_range,
                           sort=sort, direction=direction)


@bp.route('/contractors/<int:contractor_id>')
@login_required
def detail(contractor_id):
    """Карточка контрагента: реквизиты, договоры и документы.

    Договоры и документы показываются только по клиентам, доступным
    текущему пользователю (ФТ-1).
    """
    contractor = (Contractor.query
                  .filter(Contractor.id == contractor_id).first_or_404())

    contracts_q = (Contract.query
                   .options(
                       load_only(Contract.id, Contract.number,
                                 Contract.signed_date, Contract.start_date,
                                 Contract.end_date, Contract.status,
                                 Contract.status_1c, Contract.amount,
                                 Contract.client_id, Contract.created_at),
                       joinedload(Contract.client).load_only(
                           Client.id, Client.name, Client.status),
                   )
                   .filter(Contract.contractor_id == contractor.id))
    documents_q = (Document.query
                   .options(
                       load_only(Document.id, Document.doc_type,
                                 Document.number, Document.doc_date,
                                 Document.status, Document.client_id,
                                 Document.external_url,
                                 Document.created_at),
                       joinedload(Document.client).load_only(
                           Client.id, Client.name),
                   )
                   .filter(Document.contractor_id == contractor.id))
    if not current_user.is_admin:
        contracts_q = (contracts_q
                       .join(Client, Contract.client_id == Client.id)
                       .filter(Client.responsible_id == current_user.id))
        documents_q = (documents_q
                       .join(Client, Document.client_id == Client.id)
                       .filter(Client.responsible_id == current_user.id))

    contracts_q = contracts_q.order_by(Contract.signed_date.desc())
    documents_q = documents_q.order_by(Document.doc_date.desc().nullslast())
    # Оба счётчика — одним запросом (скалярные подзапросы не умножают
    # строки друг на друга, в отличие от CROSS JOIN).
    contract_count = (db.select(func.count()).select_from(Contract)
                      .where(Contract.contractor_id == contractor.id))
    document_count = (db.select(func.count()).select_from(Document)
                      .where(Document.contractor_id == contractor.id))
    if not current_user.is_admin:
        contract_count = (contract_count
                          .join(Client, Contract.client_id == Client.id)
                          .where(Client.responsible_id == current_user.id))
        document_count = (document_count
                          .join(Client, Document.client_id == Client.id)
                          .where(Client.responsible_id == current_user.id))
    counts = db.session.query(
        contract_count.scalar_subquery().label('contracts_total'),
        document_count.scalar_subquery().label('documents_total'),
    ).one()
    contracts_total = counts.contracts_total
    documents_total = counts.documents_total
    contracts = contracts_q.limit(DETAIL_ROWS).all()
    documents = documents_q.limit(DETAIL_ROWS).all()
    return render_template('contractors/detail.html', contractor=contractor,
                           contracts=contracts, documents=documents,
                           contracts_total=contracts_total,
                           documents_total=documents_total,
                           detail_rows=DETAIL_ROWS)


@bp.route('/contractors/<int:contractor_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(contractor_id):
    """Карточка редактирования контрагента: вручную меняются только заметки."""
    contractor = Contractor.query.get_or_404(contractor_id)
    form = ContractorForm(obj=contractor)
    if form.validate_on_submit():
        contractor.notes = form.notes.data or ''
        db.session.commit()
        log_action(CONTRACTOR_UPDATE,
                   f'Изменён контрагент «{contractor.display_name}»')
        flash('Заметки контрагента сохранены', 'success')
        return redirect(url_for('contractors.detail', contractor_id=contractor.id))
    return render_template('contractors/form.html', contractor=contractor,
                           form=form)
