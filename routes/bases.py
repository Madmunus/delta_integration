"""Настройка подключения к базе 1С:Предприятие и синхронизация."""
from datetime import datetime

from flask import (Blueprint, abort, flash, jsonify, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import func

from engine import odata_metrics
from engine.audit import (SETTINGS_UPDATE, SYNC_CLIENTS, SYNC_CONTRACTS,
                          SYNC_DOCUMENTS, SYNC_ERROR_RETRY, log_action)
from engine.forms import OneCSettingForm
from engine.models import (Client, Contractor, Document, OneCSetting,
                           SyncError, db)
from engine.secret_storage import encrypt_secret
from engine.odata_client import ODataError
from engine.sync import (sync_all_contracts, sync_clients, sync_contracts,
                         sync_documents)
from engine.sync_support import record_sync_error, sanitize_message

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
            setting.password = encrypt_secret(form.password.data)
        db.session.add(setting)
        db.session.commit()
        log_action(SETTINGS_UPDATE, 'Настройки подключения к 1С изменены')
        flash('Настройка подключения к базе 1С:Предприятие сохранена', 'success')
        return redirect(url_for('bases.settings'))

    # Четыре счётчика — одним запросом (скалярные подзапросы не
    # умножают строки друг на друга, в отличие от CROSS JOIN).
    counts = db.session.query(
        db.select(func.count()).select_from(Contractor)
        .scalar_subquery().label('contractors_total'),
        db.select(func.count()).select_from(Client)
        .scalar_subquery().label('clients_total'),
        db.select(func.count()).select_from(Document)
        .scalar_subquery().label('documents_total'),
        db.select(func.count()).select_from(Client)
        .where(Client.odata_url != '')
        .scalar_subquery().label('clients_with_odata'),
    ).one()
    pending_errors = (SyncError.query
                      .filter_by(status=SyncError.STATUS_PENDING)
                      .order_by(SyncError.created_at.desc())
                      .limit(20).all())
    pending_total = (SyncError.query
                     .filter_by(status=SyncError.STATUS_PENDING)
                     .count())
    return render_template(
        'bases/list.html',
        form=form,
        setting=setting,
        contractors_total=counts.contractors_total,
        clients_total=counts.clients_total,
        documents_total=counts.documents_total,
        clients_with_odata=counts.clients_with_odata,
        sync_errors=pending_errors,
        sync_errors_total=pending_total,
        odata_metrics=odata_metrics.snapshot(),
    )


@bp.route('/bases/sync', methods=['POST'])
@login_required
def run_sync():
    """Ручная синхронизация клиентов и контрагентов из главной базы 1С."""
    _admin_required()
    stats = sync_clients()
    log_action(SYNC_CLIENTS,
               f'Клиентов создано {stats["created"]}, обновлено '
               f'{stats["updated"]}')
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


@bp.route('/bases/sync-contracts', methods=['POST'])
@login_required
def run_contracts_sync():
    """Ручная загрузка договоров по всем клиентам с настроенным подключением."""
    _admin_required()
    stats = sync_all_contracts()
    log_action(SYNC_CONTRACTS,
               f'Обработано клиентов {stats["clients"]}, договоров создано '
               f'{stats["created"]}, обновлено {stats["updated"]}')
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


@bp.route('/bases/sync-documents', methods=['POST'])
@login_required
def run_documents_sync():
    """Ручная загрузка документов из «1С:Документооборот»."""
    _admin_required()
    try:
        processed = sync_documents()
        log_action(SYNC_DOCUMENTS, f'Обработано записей: {processed}')
    except ODataError as exc:
        flash(f'Ошибка загрузки документов: {exc}', 'warning')
    else:
        if processed:
            flash(f'Документы: обработано {processed} записей', 'success')
        else:
            flash('Документы не загружены: проверьте настройку подключения к 1С',
                  'warning')
    return redirect(url_for('bases.settings'))


def _retry_sync_error(item: SyncError):
    """Повторить область, в которой произошла ошибка.

    Сами sync-функции при успехе помечают очередь устранённой, а при
    неудаче — увеличивают attempts. Поэтому после вызова достаточно
    обновить запись из базы, чтобы понять результат.
    """
    if not item.is_pending:
        return True, item.message

    client = None
    if item.client_id:
        client = db.session.get(Client, item.client_id)
        if client is None:
            item.status = SyncError.STATUS_RESOLVED
            item.resolved_at = datetime.utcnow()
            db.session.commit()
            return False, 'Клиент, для которого была ошибка, удалён'

    try:
        if item.scope == 'clients':
            sync_clients()
        elif item.scope == 'contracts':
            if client is None:
                raise ValueError('Для ошибки договоров не указан клиент')
            # Ручной повтор должен один раз проигнорировать открытый breaker.
            sync_contracts(client, force=True)
        elif item.scope == 'documents':
            sync_documents()
        else:
            raise ValueError(f'Неизвестная область синхронизации: {item.scope}')
    except Exception as exc:  # страховка: sync-функции ловят свои ошибки сами
        record_sync_error(item.scope,
                          f'Ошибка повтора: {sanitize_message(exc)}',
                          client=client, entity=item.entity,
                          external_ref=item.external_ref)
        db.session.commit()

    db.session.refresh(item)
    return item.status == SyncError.STATUS_RESOLVED, item.message


@bp.route('/bases/sync-errors/<int:error_id>/retry', methods=['POST'])
@login_required
def retry_sync_error(error_id):
    """Повторить одну ошибку синхронизации из очереди."""
    _admin_required()
    item = SyncError.query.get_or_404(error_id)
    if not item.is_pending:
        flash('Эта ошибка уже устранена', 'info')
        return redirect(url_for('bases.settings'))
    ok, message = _retry_sync_error(item)
    log_action(SYNC_ERROR_RETRY,
               f'Повтор ошибки #{item.id} ({item.scope}): '
               f'{"успешно" if ok else "без успеха"}')
    if ok:
        flash(f'Ошибка #{item.id} устранена', 'success')
    else:
        flash(f'Повтор не помог: {message}', 'warning')
    return redirect(url_for('bases.settings'))


@bp.route('/bases/sync-errors/retry-all', methods=['POST'])
@login_required
def retry_all_sync_errors():
    """Повторить все активные ошибки синхронизации."""
    _admin_required()
    items = (SyncError.query
             .filter_by(status=SyncError.STATUS_PENDING)
             .order_by(SyncError.created_at).all())
    success = failed = 0
    for item in items:
        if not item.is_pending:
            continue
        ok, _message = _retry_sync_error(item)
        if ok:
            success += 1
        else:
            failed += 1
    log_action(SYNC_ERROR_RETRY,
               f'Массовый повтор: успешно {success}, без успеха {failed}')
    if failed:
        flash(f'Повторено ошибок: успешно {success}, без успеха {failed}',
              'warning')
    else:
        flash(f'Ошибок устранено: {success}', 'success')
    return redirect(url_for('bases.settings'))


@bp.route('/bases/odata-metrics')
@login_required
def odata_metrics_view():
    """Метрики OData текущего процесса (только для администратора)."""
    _admin_required()
    return jsonify(odata_metrics.snapshot())
