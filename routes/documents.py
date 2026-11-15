"""Реестр документов из «1С:Документооборот» (ФТ-4)."""
from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import or_

from engine.dates import apply_created_at_range
from engine.forms import DocumentForm
from engine.models import Client, Contractor, Document, db

bp = Blueprint('documents', __name__)


@bp.route('/documents')
@login_required
def list_documents():
    """Реестр документов с поиском по таблице.

    Сотрудник видит только документы клиентов, закреплённых за ним (ФТ-1).
    """
    q = Document.query.join(Client)
    if not current_user.is_admin:
        q = q.filter(Client.responsible_id == current_user.id)

    search = (request.args.get('q') or '').strip()
    if search:
        like = f'%{search}%'
        q = (q.outerjoin(Contractor, Document.contractor_id == Contractor.id)
             .filter(or_(Document.doc_type.ilike(like),
                         Document.number.ilike(like),
                         Document.status.ilike(like),
                         Client.name.ilike(like),
                         Contractor.full_name.ilike(like))))

    date_from = request.args.get('date_from') or ''
    date_to = request.args.get('date_to') or ''
    q, date_from, date_to = apply_created_at_range(
        q, Document.created_at, date_from, date_to)

    page = request.args.get('page', 1, type=int)
    pagination = q.order_by(Document.doc_date.desc().nullslast(),
                            Document.id.desc()).paginate(
        page=page, per_page=20, error_out=False)
    return render_template('documents/list.html',
                           documents=pagination.items,
                           pagination=pagination,
                           search=search,
                           date_from=date_from, date_to=date_to)


@bp.route('/documents/<int:document_id>')
@login_required
def detail(document_id):
    """Карточка документа: реквизиты, даты создания и обновления.

    Сотрудник видит только документы клиентов, закреплённых за ним (ФТ-1).
    """
    document = Document.query.get_or_404(document_id)
    if (not current_user.is_admin
            and document.client.responsible_id != current_user.id):
        abort(403)
    return render_template('documents/detail.html', document=document)


@bp.route('/documents/<int:document_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_document(document_id):
    """Карточка редактирования документа: вручную меняются только заметки."""
    document = Document.query.get_or_404(document_id)
    if (not current_user.is_admin
            and document.client.responsible_id != current_user.id):
        abort(403)
    form = DocumentForm(obj=document)
    if form.validate_on_submit():
        document.processed = form.processed.data
        document.notes = form.notes.data or ''
        db.session.commit()
        flash('Заметки документа сохранены', 'success')
        return redirect(url_for('documents.detail', document_id=document.id))
    return render_template('documents/form.html', document=document,
                           form=form)
