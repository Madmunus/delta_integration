"""Маршруты реестра клиентов, карточки клиента и договоров."""
from datetime import date

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import or_

from forms import ClientForm, ContractForm, ODataBaseForm
from models import Client, Contract, DocumentRef, ODataBase, Task, User, db
from services.sync import sync_clients

bp = Blueprint('clients', __name__)


def visible_clients_query():
    """ФТ-7: руководитель видит всех, сотрудник — только своих клиентов."""
    q = Client.query
    if not current_user.is_admin:
        q = q.filter_by(responsible_id=current_user.id)
    return q


@bp.route('/clients')
@login_required
def list_clients():
    q = visible_clients_query()

    # Поиск и фильтрация (ФТ-1)
    search = (request.args.get('q') or '').strip()
    status = request.args.get('status') or ''
    if search:
        like = f'%{search}%'
        q = q.filter(or_(Client.name.ilike(like),
                         Client.inn.ilike(like),
                         Client.notes.ilike(like)))
    if status in ('active', 'archived'):
        q = q.filter_by(status=status)

    page = request.args.get('page', 1, type=int)
    pagination = q.order_by(Client.name).paginate(
        page=page, per_page=20, error_out=False)
    return render_template('clients/list.html',
                            pagination=pagination, search=search, status=status)


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
