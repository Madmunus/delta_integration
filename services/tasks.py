"""Планировщик задач: автогенерация задач по срокам сдачи отчётности.

На основе справочника ReportingDeadline и календаря система формирует
задачи для активных клиентов: за N дней до срока сдачи, с привязкой
к клиенту и ответственному сотруднику. Повторные задачи для того же
(клиент, форма, период) не создаются.
"""
import logging
from datetime import date

from apscheduler.schedulers.background import BackgroundScheduler
from flask import current_app

from models import Client, ReportingDeadline, Task, db
from services.sync import sync_clients

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

    for deadline in deadlines:
        for period in periods:
            due = deadline.due_date_for_period(period)
            if not (today <= due <= _shift_days(today, lead_days)):
                continue
            for client in clients:
                # Дедупликация: та же форма + тот же период + тот же клиент
                exists = Task.query.filter_by(
                    client_id=client.id,
                    reporting_deadline_id=deadline.id,
                    due_date=due,
                ).first()
                if exists:
                    continue
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
                db.session.add(task)
                created.append(task)

    if created:
        db.session.commit()
    logger.info('Автогенерация задач: создано %d задач', len(created))
    return created


def _shift_days(d: date, days: int) -> date:
    from datetime import timedelta
    return d + timedelta(days=days)


def mark_overdue_tasks() -> int:
    """Перевод невыполненных задач с истёкшим сроком в статус «просрочена»."""
    today = date.today()
    overdue = Task.query.filter(
        Task.due_date < today,
        Task.status.in_([Task.STATUS_NEW, Task.STATUS_IN_PROGRESS]),
    ).all()
    for task in overdue:
        task.status = Task.STATUS_OVERDUE
    if overdue:
        db.session.commit()
    return len(overdue)


def init_scheduler(app):
    """Инициализация фонового планировщика (APScheduler)."""
    from functools import partial

    scheduler = BackgroundScheduler(daemon=True)

    scheduler.add_job(
        partial(_run_in_context, app, sync_clients),
        'interval',
        seconds=app.config['SYNC_INTERVAL_SECONDS'],
        id='sync_clients',
        name='Синхронизация клиентов из 1С',
    )
    scheduler.add_job(
        partial(_run_in_context, app, generate_deadline_tasks,
               app.config['DEADLINE_LEAD_DAYS']),
        'interval',
        seconds=app.config['DEADLINE_CHECK_INTERVAL'],
        id='generate_deadline_tasks',
        name='Формирование задач по срокам отчётности',
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
