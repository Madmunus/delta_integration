"""Реестр документов из «1С:Документооборот» (ФТ-4)."""
from flask import Blueprint, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import or_

from engine.models import Client, Contractor, Document

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

    page = request.args.get('page', 1, type=int)
    pagination = q.order_by(Document.doc_date.desc().nullslast(),
                            Document.id.desc()).paginate(
        page=page, per_page=20, error_out=False)
    return render_template('documents/list.html',
                           documents=pagination.items,
                           pagination=pagination,
                           search=search)
