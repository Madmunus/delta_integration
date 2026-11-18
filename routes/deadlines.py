"""Маршруты справочника сроков сдачи отчётности (ФТ-6)."""
from datetime import date, timedelta

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required

from engine.forms import ReportingDeadlineForm
from engine.models import Client, ReportingDeadline, Task, db
from engine.tasks import generate_deadline_tasks, next_due_date

bp = Blueprint('deadlines', __name__)


@bp.route('/deadlines')
@login_required
def list_deadlines():
    deadlines = ReportingDeadline.query.order_by(
        ReportingDeadline.periodicity, ReportingDeadline.code).all()
    preview = deadline_preview()
    return render_template('deadlines/list.html',
                           deadlines=deadlines,
                           upcoming=preview['rows'],
                           window_end=preview['window_end'],
                           lead_days=preview['lead_days'],
                           next_due=preview['next_due'])


def deadline_preview():
    """Сроки в окне автогенерации и ближайший срок за его пределами.

    В таблицу «Контроль ближайших сроков» попадают только формы, срок
    которых наступает не позднее today + DEADLINE_LEAD_DAYS — то есть
    именно те, по которым кнопка «Сформировать задачи» создаст задачи.
    Ближайший срок за пределами окна возвращается отдельно, чтобы
    показать подсказку, когда задачи появятся.
    """
    today = date.today()
    lead = current_app.config['DEADLINE_LEAD_DAYS']
    window_end = today + timedelta(days=lead)

    q = Client.query
    if not current_user.is_admin:
        q = q.filter_by(responsible_id=current_user.id)
    # Задача не может быть без исполнителя: клиенты без ответственного
    # не участвуют в автогенерации, поэтому не попадают и в предпросмотр
    active_clients = q.filter(Client.status == 'active',
                              Client.responsible_id.isnot(None)).all()
    clients_total = len(active_clients)
    active_ids = {c.id for c in active_clients}

    dues = {}
    for d in ReportingDeadline.query.filter_by(is_enabled=True).all():
        due = next_due_date(d, today)
        if due is not None:
            dues[d.id] = (d, due)

    # Один запрос на все формы вместо N запросов «выполнено ли уже».
    done_by_key = {}
    if dues:
        deadline_ids = list(dues)
        due_dates = {due for _, due in dues.values()}
        done_rows = (db.session.query(Task.reporting_deadline_id,
                                      Task.due_date, Task.client_id)
                     .filter(Task.reporting_deadline_id.in_(deadline_ids),
                             Task.due_date.in_(due_dates),
                             Task.status == Task.STATUS_DONE)
                     .all())
        for deadline_id, due, client_id in done_rows:
            done_by_key.setdefault((deadline_id, due), set()).add(client_id)

    rows = []
    next_due = None
    for d, due in dues.values():
        if due > window_end:
            if next_due is None or due < next_due['due']:
                next_due = {'deadline': d, 'due': due}
            continue
        done_clients = done_by_key.get((d.id, due), set()) & active_ids
        rows.append({
            'deadline': d, 'due': due, 'overdue': due < today,
            'clients_total': clients_total,
            'clients_done': len(done_clients),
        })
    return {
        'rows': sorted(rows, key=lambda x: x['due']),
        'window_end': window_end,
        'lead_days': lead,
        'next_due': next_due,
    }


@bp.route('/deadlines/generate', methods=['POST'])
@login_required
def generate_tasks_now():
    """Ручной запуск автогенерации задач по срокам (ФТ-5)."""
    lead = current_app.config['DEADLINE_LEAD_DAYS']
    created = generate_deadline_tasks(lead)
    if created:
        flash(f'Создано задач: {len(created)}', 'success')
        return redirect(url_for('deadlines.list_deadlines'))

    preview = deadline_preview()
    if preview['rows']:
        flash('Создано задач: 0 — по срокам в окне задачи уже сформированы',
              'info')
    elif preview['next_due']:
        nxt = preview['next_due']
        days = (nxt['due'] - date.today()).days
        flash(f'Создано задач: 0. Ближайший срок — '
              f'{nxt["due"]:%d.%m.%Y} ({nxt["deadline"].name}), '
              f'через {days} дн.; задачи создаются за {lead} дн. до срока.',
              'info')
    else:
        flash('Создано задач: 0 — в справочнике нет включённых видов '
              'отчётности', 'info')
    return redirect(url_for('deadlines.list_deadlines'))


@bp.route('/deadlines/add', methods=['GET', 'POST'])
@bp.route('/deadlines/<int:deadline_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_deadline(deadline_id=None):
    if not current_user.is_admin:
        abort(403)
    deadline = (ReportingDeadline.query.get_or_404(deadline_id)
                if deadline_id else ReportingDeadline())
    form = ReportingDeadlineForm(obj=deadline)
    if request.method == 'GET' and deadline_id is None:
        form.is_enabled.data = True
    if form.validate_on_submit():
        form.populate_obj(deadline)
        db.session.add(deadline)
        db.session.commit()
        flash('Вид отчётности сохранён', 'success')
        return redirect(url_for('deadlines.list_deadlines'))
    return render_template('deadlines/form.html', form=form, deadline=deadline)
