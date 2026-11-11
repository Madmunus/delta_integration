"""Сценарий 4 (ФТ-4): формирование задач по справочнику сроков.

Формирование задач по справочнику сроков для клиента с указанной
категорией; проверка появления задач с корректными сроками.
Результат: задачи сформированы.
"""
from datetime import date, timedelta

from engine.models import Client, ReportingDeadline, Task, db
from tests.base import (EMAILS, Steps, create_scenario_app, login,
                        seed_users)

FT = 'ФТ-4'
TITLE = 'Формирование задач по справочнику сроков отчётности'
DESCRIPTION = ('Формирование задач по справочнику сроков для клиента '
               'с указанной категорией; проверка появления задач '
               'с корректными сроками. Результат: задачи сформированы.')

ACTIVE_NAME = 'ООО «Гамма-Тест»'
ARCHIVED_NAME = 'ООО «Дельта-Архив-Тест»'


def _prev_month(d: date) -> date:
    """Первый день месяца, предшествующего месяцу даты d."""
    return date(d.year, d.month - 1, 1) if d.month > 1 else date(d.year - 1, 12, 1)


def run():
    app = create_scenario_app()
    steps = Steps()
    users = seed_users(app)

    today = date.today()
    due1 = today + timedelta(days=2)   # срок по форме «СЗВ-М (сценарий 4)»
    due2 = today + timedelta(days=4)   # срок по форме «Уведомление по ЕНС»

    with app.app_context():
        db.session.add(Client(name=ACTIVE_NAME, inn='7705555501',
                               status='active', responsible_id=users['emp1']))
        db.session.add(Client(name=ARCHIVED_NAME, inn='7705555502',
                              status='archived', responsible_id=users['emp1']))
        # Ежемесячные формы с категорией налогоплательщика «общая».
        # due_day подобран так, что срок сдачи попадает в окно
        # автогенерации [сегодня; сегодня + 7 дней].
        db.session.add(ReportingDeadline(
            name='СЗВ-М (сценарий 4)', code='szv-m-sc4',
            periodicity='monthly', due_day=due1.day, period_offset=0,
            taxpayer_category='общая', is_enabled=True))
        db.session.add(ReportingDeadline(
            name='Уведомление по ЕНС (сценарий 4)', code='ens-sc4',
            periodicity='monthly', due_day=due2.day, period_offset=0,
            taxpayer_category='общая', is_enabled=True))
        db.session.commit()
        active_client = Client.query.filter_by(name=ACTIVE_NAME).first()
        archived_client = Client.query.filter_by(name=ARCHIVED_NAME).first()

    # --- Шаг 1. Запуск автогенерации из веб-интерфейса -------------------
    web = app.test_client()
    login(web, EMAILS['admin'])
    resp = web.post('/deadlines/generate', follow_redirects=True)
    steps.check(resp.status_code == 200,
                'Запуск формирования задач по срокам',
                'команда выполнена, открыт справочник сроков',
                f'HTTP {resp.status_code}')

    # --- Шаг 2. Задачи сформированы ----------------------------------------
    with app.app_context():
        tasks = Task.query.all()
        by_deadline = {}
        for t in tasks:
            by_deadline.setdefault(t.reporting_deadline_id, []).append(t)
    steps.check(len(tasks) == 2 and len(by_deadline) == 2,
                'Задачи сформированы по обеим формам справочника',
                'по одной задаче на каждую форму для активного клиента',
                f'создано задач: {len(tasks)}')

    # --- Шаг 3. Привязка к клиенту, исполнителю и категории ---------------
    ok = all(
        t.client_id == active_client.id
        and t.assignee_id == users['emp1']
        and t.status == Task.STATUS_NEW
        and t.reporting_deadline_id is not None
        and 'Категория налогоплательщика: общая' in t.description
        for t in tasks)
    steps.check(ok,
                'Задачи привязаны к клиенту с категорией из справочника',
                'клиент и ответственный сотрудник из карточки клиента; '
                'категория налогоплательщика перенесена в задачу',
                f'{[t.title[:45] for t in tasks]}')

    # --- Шаг 4. Сроки задач соответствуют справочнику ----------------------
    due_dates = {t.due_date for t in tasks}
    forms_ok = any('СЗВ-М' in t.title for t in tasks) and \
        any('ЕНС' in t.title for t in tasks)
    steps.check(due_dates == {due1, due2} and forms_ok,
                'Сроки задач соответствуют расчёту по справочнику',
                f'сроки: {due1.strftime("%d.%m.%Y")} (СЗВ-М) и '
                f'{due2.strftime("%d.%m.%Y")} (ЕНС)',
                f'сроки задач: {sorted(d.strftime("%d.%m.%Y") for d in due_dates)}')

    # --- Шаг 5. Архивные клиенты не обрабатываются -------------------------
    with app.app_context():
        archived_tasks = Task.query.filter_by(
            client_id=archived_client.id).count()
    steps.check(archived_tasks == 0,
                'Для архивных клиентов задачи не создаются',
                'по архивному клиенту задач нет',
                f'задач у архивного клиента: {archived_tasks}')

    # --- Шаг 6. Повторный запуск не дублирует задачи ------------------------
    web.post('/deadlines/generate', follow_redirects=True)
    with app.app_context():
        total = Task.query.count()
    steps.check(total == 2,
                'Повторный запуск не создаёт дублей',
                'для пары «форма + период + клиент» задача создаётся один раз',
                f'всего задач: {total}')

    conclusion = 'Задачи сформированы по справочнику сроков с корректными сроками.'\
        if steps.passed else 'Формирование задач работает некорректно — см. шаги.'
    return steps, conclusion
