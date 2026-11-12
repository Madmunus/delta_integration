"""Маршруты справочника сроков сдачи отчётности (ФТ-4)."""
from datetime import date, timedelta

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required

from engine.forms import ReportingDeadlineForm
from engine.models import Client, ReportingDeadline, Task, db
from services.tasks import generate_deadline_tasks

bp = Blueprint('deadlines', __name__)


@bp.route('/deadlines')
@login_required
def list_deadlines():
    deadlines = ReportingDeadline.query.order_by(
        ReportingDeadline.periodicity, ReportingDeadline.code).all()
    upcoming = generate_deadline_tasks_preview()
    return render_template('deadlines/list.html',
                           deadlines=deadlines, upcoming=upcoming)


def generate_deadline_tasks_preview():
    """Ближайшие сроки в разрезе клиентов для визуального контроля (ФТ-4)."""
    today = date.today()
    lead = current_app.config['DEADLINE_LEAD_DAYS']
    result = []

    q = Client.query
    if not current_user.is_admin:
        q = q.filter_by(responsible_id=current_user.id)
    clients = q.filter_by(status='active').all()

    for d in ReportingDeadline.query.filter_by(is_enabled=True).all():
        # ближайший срок для формы
        best = None
        for m in range(-1, 14):
            y = today.year + (today.month + m - 1) // 12
            mo = (today.month + m - 1) % 12 + 1
            due = d.due_date_for_period(date(y, mo, 1))
            if today <= due <= today + timedelta(days=lead * 4):
                best = due
                break
        if best:
            overdue = best < today
            done_clients = {
                t.client_id for t in Task.query.filter_by(
                    reporting_deadline_id=d.id, due_date=best,
                    status=Task.STATUS_DONE).all()}
            result.append({
                'deadline': d, 'due': best, 'overdue': overdue,
                'clients_total': len(clients),
                'clients_done': len(done_clients),
            })
    return sorted(result, key=lambda x: x['due'])


@bp.route('/deadlines/generate', methods=['POST'])
@login_required
def generate_tasks_now():
    """Ручной запуск автогенерации задач по срокам (ФТ-3)."""
    created = generate_deadline_tasks(current_app.config['DEADLINE_LEAD_DAYS'])
    flash(f'Создано задач: {len(created)}', 'success')
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
