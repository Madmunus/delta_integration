"""Реестр документов из «1С:Документооборот» (ФТ-4)."""
from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import case, func, or_
from sqlalchemy.orm import contains_eager, joinedload

from engine.dates import apply_date_range
from engine.forms import DocumentForm
from engine.models import Client, Contractor, Document, db

bp = Blueprint('documents', __name__)

# Статусы для фильтра реестра (отображаются как в справочнике 1С)
DOCUMENT_STATUSES = ('Черновик', 'Зарегистрирован', 'Проведён', 'Отменён')

# Значения фильтра по внутреннему признаку «Обработан»
PROCESSED_VALUES = ('yes', 'no')


def _visible_clients():
    """Клиенты, доступные текущему пользователю (сотрудник — только свои)."""
    q = Client.query
    if not current_user.is_admin:
        q = q.filter(Client.responsible_id == current_user.id)
    return q.order_by(Client.name).all()

# Сортируемые столбцы реестра: имя параметра -> колонка/выражение
SORTABLE_COLUMNS = {
    'doc_type': Document.doc_type,
    'number': Document.number,
    'doc_date': Document.doc_date,
    'client': Client.name,
    'contractor': Contractor.full_name,
    'status': Document.status,
    'processed': Document.processed,
    'created_at': Document.created_at,
}

# Статус в «1С» приходит свободным текстом. SQLite не приводит кириллицу
# к нижнему регистру, поэтому сравниваем с типовыми вариантами регистра,
# игнорируя пробелы по краям и различие «ё»/«е».
_DOC_STATUS_KEY = func.replace(func.trim(Document.status), 'ё', 'е')


def _status_in(value):
    value = value.replace('ё', 'е')
    return _DOC_STATUS_KEY.in_((value, value.lower(), value.upper()))


# Порядок статусов по умолчанию:
# Черновик → Зарегистрирован → Проведен → Отменен → прочие
DEFAULT_STATUS_ORDER = case(
    (_status_in('Черновик'), 0),
    (_status_in('Зарегистрирован'), 1),
    (_status_in('Проведен'), 2),
    (_status_in('Отменен'), 3),
    else_=4,
)


@bp.route('/documents')
@login_required
def list_documents():
    """Реестр документов с поиском по таблице.

    Сотрудник видит только документы клиентов, закреплённых за ним (ФТ-1).
    """
    # Контрагент нужен и для поиска, и для сортировки по столбцу
    q = (Document.query.join(Client)
         .outerjoin(Contractor, Document.contractor_id == Contractor.id)
         .options(contains_eager(Document.client),
                  contains_eager(Document.contractor)))
    if not current_user.is_admin:
        q = q.filter(Client.responsible_id == current_user.id)

    search = (request.args.get('q') or '').strip()
    if search:
        like = f'%{search}%'
        q = q.filter(or_(Document.doc_type.ilike(like),
                         Document.number.ilike(like),
                         Document.status.ilike(like),
                         Client.name.ilike(like),
                         Contractor.full_name.ilike(like)))

    status = request.args.get('status') or ''
    if status not in DOCUMENT_STATUSES:
        status = ''
    if status:
        q = q.filter(_status_in(status))

    # Фильтр по внутреннему признаку «Обработан» (Да / Нет)
    processed = request.args.get('processed') or ''
    if processed not in PROCESSED_VALUES:
        processed = ''
    if processed == 'yes':
        q = q.filter(Document.processed.is_(True))
    elif processed == 'no':
        q = q.filter(Document.processed.is_(False))

    # Фильтр по дате документа (диапазон «с — по» в едином поле)
    q, date_range = apply_date_range(q, Document.doc_date, request.args)

    # Фильтры по клиенту и контрагенту (идут после фильтра по дате)
    client_id = request.args.get('client', type=int)
    contractor_id = request.args.get('contractor', type=int)
    if client_id:
        q = q.filter(Document.client_id == client_id)
    if contractor_id:
        q = q.filter(Document.contractor_id == contractor_id)

    # Сортировка по столбцам: 3 состояния — по возрастанию, по убыванию
    # и отключена. По умолчанию: сначала черновики, затем зарегистрированные,
    # проведённые и отменённые; внутри статуса — новые документы, затем по номеру.
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
        order_by = (order_expr, Document.id.desc())
    else:
        order_by = (DEFAULT_STATUS_ORDER.asc(),
                    Document.doc_date.desc().nullslast(),
                    Document.number.asc(),
                    Document.id.desc())

    page = request.args.get('page', 1, type=int)
    pagination = q.order_by(*order_by).paginate(
        page=page, per_page=20, error_out=False)
    return render_template('documents/list.html',
                           documents=pagination.items,
                           pagination=pagination,
                           search=search, status=status,
                           processed=processed,
                           client_id=client_id, contractor_id=contractor_id,
                           clients=_visible_clients(),
                           contractors=Contractor.query.order_by(
                               Contractor.full_name).all(),
                           statuses=DOCUMENT_STATUSES,
                           date_range=date_range,
                           sort=sort, direction=direction)


@bp.route('/documents/<int:document_id>')
@login_required
def detail(document_id):
    """Карточка документа: реквизиты, даты создания и обновления.

    Сотрудник видит только документы клиентов, закреплённых за ним (ФТ-1).
    """
    document = (Document.query
                .options(joinedload(Document.client),
                         joinedload(Document.contractor))
                .filter(Document.id == document_id).first_or_404())
    if (not current_user.is_admin
            and document.client.responsible_id != current_user.id):
        abort(403)
    return render_template('documents/detail.html', document=document)


@bp.route('/documents/<int:document_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_document(document_id):
    """Карточка редактирования документа: вручную меняются только заметки."""
    document = (Document.query
                .options(joinedload(Document.client),
                         joinedload(Document.contractor))
                .filter(Document.id == document_id).first_or_404())
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
