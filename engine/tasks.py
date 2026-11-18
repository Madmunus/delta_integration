"""Планировщик задач: автогенерация задач и уведомлений по срокам отчётности.

На основе справочника ReportingDeadline и календаря система формирует
задачи для активных клиентов: за N дней до срока сдачи, с привязкой
к клиенту и ответственному сотруднику. Повторные задачи для того же
(клиент, форма, период) не создаются. Отдельное задание готовит уведомления
за настраиваемое число дней до сдачи.
"""
import logging
from datetime import date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import and_, or_

from engine.models import (Client, DeadlineNotification, DeadlineOverride,
                           ReportingDeadline, Task, db)
from engine.sync import sync_clients, sync_documents

logger = logging.getLogger(__name__)


def current_periods(today: date):
    """Отчётные периоды, актуальные на дату: возвращает список дат-первых-дней.

    Для каждой периодичности берём период, чей срок сдачи попадает
    в окно [сегодня; сегодня + lead_days].
    """
    # Перебираем периоды за последние 14 месяцев, чтобы покрыть
    # ежемесячные, квартальные и годовые формы с любым смещением.
    periods = []
    y, m = today.year - 1, today.month
    for _ in range(15):
        periods.append(date(y, m, 1))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return periods


def _shift_month(year: int, month: int, delta: int):
    """Смещение месяца с переносом года; возвращает (год, месяц)."""
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _first_period_for_due(deadline, today: date) -> date:
    """Отчётный период, срок по которому не раньше месяца ``today``.

    Вместо перебора 42 периодов суммируем смещения: по периодичности
    и ``period_offset`` сразу вычисляем месяц периода, чей срок сдачи
    попадает в нужный месяц. Точный день проверяет вызывающая функция.
    """
    target = today.year * 12 + today.month - 1
    offset = int(deadline.period_offset or 0)
    if deadline.periodicity == 'monthly':
        index = target - 1 - offset
    elif deadline.periodicity == 'quarterly':
        # Конец квартала — месяцы 3, 6, 9, 12 (индексы 2, 5, 8, 11).
        index = target - 1 - offset
        index += (2 - index % 12) % 3
        year, month = index // 12, index % 12 + 1
        return date(year, month - 2, 1)
    else:  # annual
        year = (target - 12 - offset) // 12 + 1
        return date(year, 1, 1)
    year, month = index // 12, index % 12 + 1
    return date(year, month, 1)


def next_due_date(deadline, today: date, months_back: int = 18,
                  months_ahead: int = 24):
    """Ближайший срок сдачи формы начиная с today.

    Старт берётся из :func:`_first_period_for_due`; далее проверяется
    лишь несколько следующих периодов (на случай, когда ``due_day``
    в целевом месяце раньше сегодня). Параметры ``months_back`` /
    ``months_ahead`` сохранены для совместимости вызовов.
    """
    step = {'monthly': 1, 'quarterly': 3,
            'annual': 12}.get(deadline.periodicity, 1)
    period = _first_period_for_due(deadline, today)
    # Кандидат уже рассчитан на нужный месяц: достаточно пары шагов
    # вперёд, чтобы перейти через день сдачи внутри месяца.
    for _ in range(4):
        due = deadline.effective_due_date(period)
        if due >= today:
            return due
        year, month = _shift_month(period.year, period.month, step)
        period = date(year, month, 1)
    return None


def generate_deadline_tasks(lead_days: int = 7,
                            today: date | None = None) -> list:
    """Сформировать задачи по приближающимся срокам отчётности.

    Для каждого включённого вида отчётности и каждого активного клиента:
    если срок сдачи (с учётом календаря и ручной корректировки) попадает
    в окно [today; today + lead_days] и задача ещё не создана — создаём.
    Дедупликация идёт по отчётному периоду, поэтому ручная правка срока
    у уже созданной задачи не приведёт к появлению дубля.
    """
    today = today or date.today()
    created = []

    deadlines = ReportingDeadline.query.filter_by(is_enabled=True).all()
    clients = Client.query.filter_by(status='active').all()
    periods = current_periods(today)
    deadline_ids = [deadline.id for deadline in deadlines]
    window_end = today + timedelta(days=lead_days)
    # Ручные корректировки загружаем одним запросом — без N+1 внутри цикла.
    overrides = {(o.client_id, o.deadline_id, o.period): o.due_date
                 for o in DeadlineOverride.query.all()}
    # Одним запросом получаем все уже созданные комбинации. Для старых задач
    # без reporting_period ключом остаётся их текущий due_date.
    rows = (Task.query.with_entities(
        Task.client_id, Task.reporting_deadline_id,
        Task.reporting_period, Task.due_date)
        .filter(Task.reporting_deadline_id.in_(deadline_ids),
                or_(Task.reporting_period.in_(periods),
                    and_(Task.reporting_period.is_(None),
                         Task.due_date.isnot(None),
                         Task.due_date >= today,
                         Task.due_date <= window_end))).all())
    existing = {(cid, did, period or due) for cid, did, period, due in rows}

    for deadline in deadlines:
        for period in periods:
            base_due = deadline.effective_due_date(period)
            for client in clients:
                # Правило системы: задача не может быть без исполнителя,
                # поэтому клиента без ответственного пропускаем
                if not client.responsible_id:
                    logger.info('Клиент %s без ответственного: задача '
                                'по форме %s не создана',
                                client.name, deadline.name)
                    continue
                due = overrides.get((client.id, deadline.id, period), base_due)
                if not (today <= due <= window_end):
                    continue
                # Дедупликация: та же форма + тот же период + тот же клиент
                key = (client.id, deadline.id, period)
                if key in existing:
                    continue
                existing.add(key)
                priority = min(int(client.priority or 2),
                               int(deadline.priority or 2))
                priority = max(1, min(3, priority))
                task = Task(
                    title=f'{deadline.name}: {client.name} (период {period.strftime("%m.%Y")})',
                    description=(
                        f'Автоматически созданная задача по сроку сдачи отчётности.\n'
                        f'Форма: {deadline.name} ({deadline.code})\n'
                        f'Периодичность: {deadline.periodicity_label}\n'
                        f'Отчётный период: {period.strftime("%m.%Y")}\n'
                        f'Срок сдачи: {due.strftime("%d.%m.%Y")}\n'
                        f'Категория налогоплательщика: {deadline.taxpayer_category}\n'
                        f'Приоритет: клиент — {client.priority_label}, '
                        f'форма — {deadline.priority_label}'
                    ),
                    client_id=client.id,
                    assignee_id=client.responsible_id,
                    due_date=due,
                    reporting_period=period,
                    priority=priority,
                    status=Task.STATUS_NEW,
                    reporting_deadline_id=deadline.id,
                )
                created.append(task)

    if created:
        db.session.add_all(created)
        db.session.commit()
    logger.info('Автогенерация задач: создано %d задач', len(created))
    return created


def generate_deadline_notifications(lead_days=None,
                                    today: date | None = None) -> list:
    """Создать уведомления о сроках, наступающих через N дней.

    Значения N берутся из ``NOTIFICATION_LEAD_DAYS`` (например, 3 и 1).
    Уведомление адресуется исполнителю задачи и создаётся один раз для
    комбинации «пользователь + задача + дней до сдачи».
    """
    if lead_days is None:
        from flask import current_app, has_app_context
        if has_app_context():
            lead_days = current_app.config.get(
                'NOTIFICATION_LEAD_DAYS', (3, 1))
        else:
            lead_days = (3, 1)
    today = today or date.today()
    targets = {}
    for days in lead_days:
        try:
            days = int(days)
        except (TypeError, ValueError):
            continue
        if days >= 0:
            targets[today + timedelta(days=days)] = days
    if not targets:
        return []

    existing = set(DeadlineNotification.query.with_entities(
        DeadlineNotification.user_id, DeadlineNotification.task_id,
        DeadlineNotification.days_before).all())
    # Уведомление создаём для любой невыполненной задачи со сроком сдачи,
    # а не только для задач, сгенерированных из справочника сроков: срок
    # может быть задан и вручную. Ссылка на форму в уведомлении необязательна.
    tasks = (Task.query
             .filter(Task.due_date.in_(list(targets)),
                     Task.status != Task.STATUS_DONE).all())
    created = []
    for task in tasks:
        days = targets[task.due_date]
        key = (task.assignee_id, task.id, days)
        if key in existing:
            continue
        existing.add(key)
        created.append(DeadlineNotification(
            user_id=task.assignee_id,
            task_id=task.id,
            reporting_deadline_id=task.reporting_deadline_id,
            days_before=days,
            due_date=task.due_date,
        ))

    if created:
        db.session.add_all(created)
        db.session.commit()
    logger.info('Уведомлений о сроках создано: %d', len(created))
    return created


def init_scheduler(app):
    """Инициализация фонового планировщика (APScheduler)."""
    from functools import partial

    scheduler = BackgroundScheduler(daemon=True)
    # Долгая синхронизация не должна перекрывать саму себя, а пропущенные
    # запуски (например, после сна сервера) сливаются в один.
    job_options = dict(
        max_instances=1,
        coalesce=True,
        misfire_grace_time=app.config['SCHEDULER_MISFIRE_GRACE_SECONDS'],
    )

    scheduler.add_job(
        partial(_run_in_context, app, sync_clients),
        'interval',
        seconds=app.config['SYNC_INTERVAL_SECONDS'],
        id='sync_clients',
        name='Синхронизация клиентов из 1С',
        **job_options,
    )
    scheduler.add_job(
        partial(_run_in_context, app, generate_deadline_tasks,
                app.config['DEADLINE_LEAD_DAYS']),
        'interval',
        seconds=app.config['DEADLINE_CHECK_INTERVAL'],
        id='generate_deadline_tasks',
        name='Формирование задач по срокам отчётности',
        **job_options,
    )
    scheduler.add_job(
        partial(_run_in_context, app, generate_deadline_notifications,
                app.config['NOTIFICATION_LEAD_DAYS']),
        'interval',
        seconds=app.config['DEADLINE_CHECK_INTERVAL'],
        id='generate_deadline_notifications',
        name='Уведомления о сроках отчётности',
        **job_options,
    )
    scheduler.add_job(
        partial(_run_in_context, app, sync_documents),
        'interval',
        seconds=app.config['DOCUMENTS_SYNC_INTERVAL'],
        id='sync_documents',
        name='Загрузка документов из 1С:Документооборот',
        **job_options,
    )
    scheduler.start()
    logger.info('Планировщик запущен')
    return scheduler


def _run_in_context(app, fn, *args):
    """Выполнить функцию в контексте приложения (для фоновых заданий)."""
    with app.app_context():
        try:
            fn(*args)
        except Exception:
            logger.exception('Ошибка фонового задания %s', fn.__name__)
