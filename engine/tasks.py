"""Планировщик задач: автогенерация задач по срокам сдачи отчётности.

На основе справочника ReportingDeadline и календаря система формирует
задачи для активных клиентов: за N дней до срока сдачи, с привязкой
к клиенту и ответственному сотруднику. Повторные задачи для того же
(клиент, форма, период) не создаются.
"""
import logging
from datetime import date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from flask import current_app

from engine.models import Client, ReportingDeadline, Task, db
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
        due = deadline.due_date_for_period(period)
        if due >= today:
            return due
        year, month = _shift_month(period.year, period.month, step)
        period = date(year, month, 1)
    return None


def generate_deadline_tasks(lead_days: int = 7) -> list:
    """Сформировать задачи по приближающимся срокам отчётности.

    Для каждого включённого вида отчётности и каждого активного клиента:
    если срок сдачи попадает в окно [today; today + lead_days] и задача
    ещё не создана — создаём. Возвращает список созданных задач.
    """
    today = date.today()
    created = []

    deadlines = ReportingDeadline.query.filter_by(is_enabled=True).all()
    clients = Client.query.filter_by(status='active').all()
    periods = current_periods(today)
    # Одним запросом получаем все уже созданные комбинации: раньше каждая
    # проверка дубликата делала отдельный SELECT (N+1).
    existing = set(Task.query.with_entities(
        Task.client_id, Task.reporting_deadline_id, Task.due_date).all())

    for deadline in deadlines:
        for period in periods:
            due = deadline.due_date_for_period(period)
            if not (today <= due <= today + timedelta(days=lead_days)):
                continue
            for client in clients:
                # Правило системы: задача не может быть без исполнителя,
                # поэтому клиента без ответственного пропускаем
                if not client.responsible_id:
                    logger.info('Клиент %s без ответственного: задача '
                                'по форме %s не создана',
                                client.name, deadline.name)
                    continue
                # Дедупликация: та же форма + тот же срок + тот же клиент
                key = (client.id, deadline.id, due)
                if key in existing:
                    continue
                existing.add(key)
                task = Task(
                    title=f'{deadline.name}: {client.name} (период {period.strftime("%m.%Y")})',
                    description=(
                        f'Автоматически созданная задача по сроку сдачи отчётности.\n'
                        f'Форма: {deadline.name} ({deadline.code})\n'
                        f'Периодичность: {deadline.periodicity_label}\n'
                        f'Отчётный период: {period.strftime("%m.%Y")}\n'
                        f'Срок сдачи: {due.strftime("%d.%m.%Y")}\n'
                        f'Категория налогоплательщика: {deadline.taxpayer_category}'
                    ),
                    client_id=client.id,
                    assignee_id=client.responsible_id,
                    due_date=due,
                    priority=1,
                    status=Task.STATUS_NEW,
                    reporting_deadline_id=deadline.id,
                )
                created.append(task)

    if created:
        db.session.add_all(created)
        db.session.commit()
    logger.info('Автогенерация задач: создано %d задач', len(created))
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
