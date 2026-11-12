"""Настройка подключения к базе 1С:Предприятие и синхронизация."""
from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from engine.forms import OneCSettingForm
from engine.models import Client, Contractor, OneCSetting, db
from services.sync import sync_all_contracts, sync_clients

bp = Blueprint('bases', __name__)


def _admin_required():
    if not current_user.is_admin:
        abort(403)


@bp.route('/bases', methods=['GET', 'POST'])
@login_required
def settings():
    """Единственная настройка подключения к главной базе 1С:Предприятие."""
    _admin_required()
    setting = OneCSetting.get()
    form = OneCSettingForm(obj=setting)
    if request.method == 'GET':
        form.password.data = ''  # пароль не показываем
    if form.validate_on_submit():
        setting.odata_url = (form.odata_url.data or '').strip()
        setting.username = (form.username.data or '').strip()
        # Пустой пароль означает «оставить прежний»
        if form.password.data:
            setting.password = form.password.data
        db.session.add(setting)
        db.session.commit()
        flash('Настройка подключения к базе 1С:Предприятие сохранена', 'success')
        return redirect(url_for('bases.settings'))

    return render_template(
        'bases/list.html',
        form=form,
        setting=setting,
        contractors_total=Contractor.query.count(),
        clients_total=Client.query.count(),
        clients_with_odata=Client.query.filter(Client.odata_url != '').count(),
    )


@bp.route('/bases/sync', methods=['POST'])
@login_required
def run_sync():
    """Ручная синхронизация клиентов и контрагентов из главной базы 1С."""
    _admin_required()
    stats = sync_clients()
    for err in stats['errors'][:5]:
        flash(err, 'warning')
    if not stats['errors']:
        flash(
            f"Синхронизация завершена: клиентов создано {stats['created']}, "
            f"обновлено {stats['updated']}; контрагентов создано "
            f"{stats['contractors_created']}, обновлено "
            f"{stats['contractors_updated']}",
            'success')
    return redirect(url_for('bases.settings'))


@bp.route('/sync/run', methods=['POST'])
@login_required
def run_sync_legacy():
    """Совместимый адрес ручной синхронизации."""
    return run_sync()


@bp.route('/bases/sync-contracts', methods=['POST'])
@login_required
def run_contracts_sync():
    """Ручная загрузка договоров по всем клиентам с настроенным подключением."""
    _admin_required()
    stats = sync_all_contracts()
    for err in stats['errors'][:5]:
        flash(err, 'warning')
    if stats['clients'] == 0:
        flash('Нет клиентов с настроенным подключением к 1С:Бухгалтерия', 'warning')
    else:
        flash(
            f"Договоры: обработано клиентов {stats['clients']}, "
            f"создано {stats['created']}, обновлено {stats['updated']}",
            'success')
    return redirect(url_for('bases.settings'))
