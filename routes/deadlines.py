"""Маршруты справочника сроков сдачи отчётности (ФТ-6)."""
from datetime import date, timedelta

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required

from engine.audit import DEADLINE_UPDATE, DEADLINES_GENERATE, log_action
from engine.forms import DeadlineOverrideForm, ReportingDeadlineForm
from engine.models import (Client, DeadlineNotification, DeadlineOverride,
                           ReportingDeadline, Task, db)
from engine.tasks import generate_deadline_tasks, next_due_date

bp = Blueprint('deadlines', __name__)


@bp.route('/deadlines')
@login_required
def list_deadlines():
    deadlines = ReportingDeadline.query.order_by(
        ReportingDeadline.periodicity, ReportingDeadline.code).all()
    preview = deadline_preview()
    unread = DeadlineNotification.query.filter_by(
        user_id=current_user.id, is_read=False).count()
    return render_template('deadlines/list.html',
                           deadlines=deadlines,
                           upcoming=preview['rows'],
                           window_end=preview['window_end'],
                           lead_days=preview['lead_days'],
                           next_due=preview['next_due'],
                           unread_notifications=unread)


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
    log_action(DEADLINES_GENERATE, f'Создано задач: {len(created)}')
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
        log_action(DEADLINE_UPDATE,
                   f'Изменён вид отчётности «{deadline.name}»')
        flash('Вид отчётности сохранён', 'success')
        return redirect(url_for('deadlines.list_deadlines'))
    return render_template('deadlines/form.html', form=form, deadline=deadline)


# ----------------------------------------------------------------------------
# Календарь сдачи на месяц / квартал и ручная корректировка сроков
# ----------------------------------------------------------------------------
def _calendar_range(view: str, year: int, month: int):
    """Первый и последний день выбранного месяца или квартала."""
    if view == 'quarter':
        first_month = (month - 1) // 3 * 3 + 1
        last_month = first_month + 2
    else:
        first_month = last_month = month
    start = date(year, first_month, 1)
    y, m = (year + 1, 1) if last_month == 12 else (year, last_month + 1)
    end = date(y, m, 1) - timedelta(days=1)
    return start, end


def _period_candidates(start: date, end: date):
    """Отчётные периоды, сроки по которым могут попасть в диапазон."""
    y, m = start.year - 1, start.month
    result = []
    for _ in range(24):
        result.append(date(y, m, 1))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return result


@bp.route('/deadlines/calendar')
@login_required
def calendar():
    """Календарь сдачи: месяц или квартал в разрезе клиентов и форм."""
    today = date.today()
    view = request.args.get('view', 'month')
    if view not in ('month', 'quarter'):
        view = 'month'
    try:
        year = int(request.args.get('year', today.year))
        month = int(request.args.get('month', today.month))
    except (TypeError, ValueError):
        year, month = today.year, today.month
    year = min(max(year, 1900), 9999)
    month = min(max(month, 1), 12)
    start, end = _calendar_range(view, year, month)

    q = Client.query.filter(Client.status == 'active',
                            Client.responsible_id.isnot(None))
    if not current_user.is_admin:
        q = q.filter_by(responsible_id=current_user.id)
    clients = q.order_by(Client.name).all()
    deadlines = (ReportingDeadline.query.filter_by(is_enabled=True)
                 .order_by(ReportingDeadline.priority,
                           ReportingDeadline.code).all())
    all_overrides = DeadlineOverride.query.order_by(
        DeadlineOverride.due_date).all()
    overrides = {(o.client_id, o.deadline_id, o.period): o.due_date
                 for o in all_overrides}
    periods = _period_candidates(start, end)
    base_cache = {}
    rows = []
    for deadline in deadlines:
        cells = []
        for client in clients:
            dates = []
            for period in periods:
                cache_key = (deadline.id, period)
                if cache_key not in base_cache:
                    base_cache[cache_key] = deadline.effective_due_date(period)
                due = overrides.get(
                    (client.id, deadline.id, period), base_cache[cache_key])
                if start <= due <= end:
                    dates.append((period, due))
            cells.append({'client': client,
                          'dates': sorted(set(dates)),
                          'overridden': any(
                              (client.id, deadline.id, period) in overrides
                              for period, _ in dates)})
        rows.append({'deadline': deadline, 'cells': cells,
                     'dates_total': sum(len(cell['dates']) for cell in cells)})

    step = 3 if view == 'quarter' else 1
    index = year * 12 + month - 1
    prev_index, next_index = index - step, index + step
    prev_view = {'year': prev_index // 12, 'month': prev_index % 12 + 1,
                 'view': view}
    next_view = {'year': next_index // 12, 'month': next_index % 12 + 1,
                 'view': view}
    return render_template('deadlines/calendar.html',
                           view=view, year=year, month=month,
                           start=start, end=end, rows=rows,
                           clients=clients, deadlines=deadlines,
                           prev_view=prev_view, next_view=next_view,
                           override_items=all_overrides,
                           calendar_shift=current_app.config['CALENDAR_SHIFT'])


@bp.route('/deadlines/calendar/override', methods=['GET', 'POST'])
@login_required
def edit_override():
    """Ручная корректировка срока для пары «клиент + форма + период»."""
    if not current_user.is_admin:
        abort(403)
    form = DeadlineOverrideForm()
    form.client_id.choices = [
        (c.id, c.name) for c in
        Client.query.order_by(Client.name).all()]
    form.deadline_id.choices = [
        (d.id, d.name) for d in
        ReportingDeadline.query.order_by(ReportingDeadline.code).all()]
    view = request.values.get('view', 'month')
    if view not in ('month', 'quarter'):
        view = 'month'

    if request.method == 'GET':
        for field, key in (('client_id', 'client_id'),
                           ('deadline_id', 'deadline_id')):
            try:
                form[field].data = int(request.args.get(key, 0)) or None
            except (TypeError, ValueError):
                form[field].data = None
        raw_period = request.args.get('period', '')
        try:
            form.period.data = date.fromisoformat(raw_period)
        except (TypeError, ValueError):
            form.period.data = None
        if form.period.data and form.deadline_id.data:
            deadline = db.session.get(ReportingDeadline, form.deadline_id.data)
            if deadline:
                form.due_date.data = deadline.effective_due_date(
                    form.period.data)

    if form.validate_on_submit():
        override = DeadlineOverride.query.filter_by(
            client_id=form.client_id.data,
            deadline_id=form.deadline_id.data,
            period=form.period.data).first()
        if override is None:
            override = DeadlineOverride(
                client_id=form.client_id.data,
                deadline_id=form.deadline_id.data,
                period=form.period.data)
            db.session.add(override)
        override.due_date = form.due_date.data
        override.note = form.note.data or ''
        db.session.commit()
        log_action(DEADLINE_UPDATE,
                   f'Скорректирован срок формы «{override.deadline_id}» '
                   f'на {override.due_date:%d.%m.%Y}')
        flash('Срок сдачи скорректирован', 'success')
        return redirect(url_for('deadlines.calendar', view=view,
                                year=form.period.data.year,
                                month=form.period.data.month))
    return render_template('deadlines/override_form.html', form=form,
                           view=view)


@bp.route('/deadlines/calendar/override/<int:override_id>/delete',
          methods=['POST'])
@login_required
def delete_override(override_id):
    """Отменить ручную корректировку и вернуть расчётный срок."""
    if not current_user.is_admin:
        abort(403)
    override = DeadlineOverride.query.get_or_404(override_id)
    period = override.period
    db.session.delete(override)
    db.session.commit()
    log_action(DEADLINE_UPDATE, 'Корректировка срока сдачи отменена')
    flash('Ручная корректировка сброшена', 'success')
    view = request.form.get('view', 'month')
    if view not in ('month', 'quarter'):
        view = 'month'
    return redirect(url_for('deadlines.calendar', view=view,
                            year=period.year, month=period.month))


# ----------------------------------------------------------------------------
# Уведомления за N дней до сдачи
# ----------------------------------------------------------------------------
@bp.route('/deadlines/notifications', methods=['GET', 'POST'])
@login_required
def notifications():
    """Уведомления текущего пользователя о приближающихся сроках."""
    if request.method == 'POST':
        query = DeadlineNotification.query.filter_by(
            user_id=current_user.id, is_read=False)
        if request.form.get('mark_all') != '1':
            ids = []
            for raw in request.form.getlist('notification_id'):
                try:
                    ids.append(int(raw))
                except (TypeError, ValueError):
                    continue
            query = query.filter(DeadlineNotification.id.in_(ids))
        changed = 0
        for item in query.all():
            item.is_read = True
            changed += 1
        if changed:
            db.session.commit()
        flash(f'Отмечено прочитанными: {changed}', 'success')
        return redirect(url_for('deadlines.notifications'))

    items = (DeadlineNotification.query
             .filter_by(user_id=current_user.id)
             .order_by(DeadlineNotification.is_read,
                       DeadlineNotification.due_date,
                       DeadlineNotification.id).all())
    unread = sum(1 for item in items if not item.is_read)
    return render_template('deadlines/notifications.html',
                           items=items, unread=unread,
                           lead_days=current_app.config[
                               'NOTIFICATION_LEAD_DAYS'])
