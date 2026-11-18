"""Сценарий 6 (ФТ-6): формирование задач и контроль сроков.

Формирование задач по справочнику сроков для клиента с указанной
категорией; проверка появления задач с корректными сроками и
предпросмотра ближайших сроков. Результат: задачи сформированы,
сроки рассчитаны корректно.
"""
from datetime import date, timedelta
import re

from engine.calendar import adjust_due_date
from engine.models import (Client, DeadlineNotification, DeadlineOverride,
                           ReportingDeadline, Task, db)
from engine.tasks import (generate_deadline_notifications,
                          generate_deadline_tasks, init_scheduler,
                          next_due_date)
from tests.base import (EMAILS, Steps, create_scenario_app, login,
                        seed_users)

FT = 'ФТ-6'
TITLE = 'Формирование задач и контроль сроков отчётности'
DESCRIPTION = ('Формирование задач по справочнику сроков для клиента '
               'с указанной категорией; проверка появления задач '
               'с корректными сроками и предпросмотра ближайших сроков. '
               'Результат: задачи сформированы, сроки рассчитаны корректно.')

ACTIVE_NAME = 'ООО «Гамма-Тест»'
ARCHIVED_NAME = 'ООО «Дельта-Архив-Тест»'
NO_RESP_NAME = 'ООО «Без-Ответственного-Тест»'


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
        # Активный клиент без ответственного: задача не может быть
        # без исполнителя, поэтому по нему задача не создаётся
        db.session.add(Client(name=NO_RESP_NAME, inn='7705555503',
                              status='active', responsible_id=None))
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

    # --- Шаг 1. Предпросмотр и запуск автогенерации ----------------------
    web = app.test_client()
    login(web, EMAILS['admin'])

    page = web.get('/deadlines').get_data(as_text=True)
    preview = page.split('Контроль ближайших', 1)[1].split(
        'Виды отчётности', 1)[0]
    steps.check(
        'Задачи формируются за 7 дн.' in preview
        and due1.strftime('%d.%m.%Y') in preview
        and due2.strftime('%d.%m.%Y') in preview,
        'Предпросмотр показывает формы в окне генерации',
        'обе формы, их сроки и подсказка про окно 7 дней',
        f'СЗВ-М: {"СЗВ-М" in preview}, ЕНС: {"ЕНС" in preview}')

    resp = web.post('/deadlines/generate', follow_redirects=True)
    steps.check(resp.status_code == 200,
                'Запуск формирования задач по срокам',
                'команда выполнена, открыт справочник сроков',
                f'HTTP {resp.status_code}')

    flash_text = re.sub(r'\s+', ' ', re.sub(
        r'<[^>]+>', ' ', resp.get_data(as_text=True)))
    steps.check('Создано задач: 2' in flash_text,
                'Сообщение после запуска показывает число задач',
                'Создано задач: 2',
                re.findall(r'Создано задач: \d+[^.]*', flash_text))

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

    # --- Шаг 5.1. Клиент без ответственного не получает задачу -------------
    with app.app_context():
        no_resp_client = Client.query.filter_by(name=NO_RESP_NAME).first()
        no_resp_tasks = Task.query.filter_by(
            client_id=no_resp_client.id).count()
        no_empty_assignee = Task.query.filter(
            Task.assignee_id.is_(None)).count()
    steps.check(no_resp_tasks == 0 and no_empty_assignee == 0,
                'Клиент без ответственного не получает задачу без исполнителя',
                'для активного клиента без ответственного задача не создаётся, '
                'задач без исполнителя нет',
                f'задач у клиента без ответственного: {no_resp_tasks}, '
                f'задач без исполнителя: {no_empty_assignee}')

    # --- Шаг 6. Повторный запуск не дублирует задачи ------------------------
    web.post('/deadlines/generate', follow_redirects=True)
    with app.app_context():
        total = Task.query.count()
    steps.check(total == 2,
                'Повторный запуск не создаёт дублей',
                'для пары «форма + период + клиент» задача создаётся один раз',
                f'всего задач: {total}')

    # --- Шаг 7. Ближайший срок учитывает предыдущий период ------------------
    fixed_monthly = next_due_date(
        ReportingDeadline(periodicity='monthly', due_day=25,
                          period_offset=0), date(2026, 11, 17))
    fixed_quarterly = next_due_date(
        ReportingDeadline(periodicity='quarterly', due_day=25,
                          period_offset=1), date(2026, 11, 17))
    fixed_monthly_shift = next_due_date(
        ReportingDeadline(periodicity='monthly', due_day=25,
                          period_offset=6), date(2026, 11, 17))
    fixed_quarterly_shift = next_due_date(
        ReportingDeadline(periodicity='quarterly', due_day=25,
                          period_offset=6), date(2026, 11, 17))
    steps.check(fixed_monthly == date(2026, 11, 25)
                and fixed_quarterly == date(2026, 11, 25)
                and fixed_monthly_shift == date(2026, 11, 25)
                and fixed_quarterly_shift == date(2027, 1, 25),
                'Ближайший срок учитывает предыдущий отчётный период',
                'для 17.11.2026: ежемесячная и квартальная — 25.11.2026; '
                'со смещением 6 мес. — 25.11.2026 и 25.01.2027',
                f'ежемесячная: {fixed_monthly}, квартальная: '
                f'{fixed_quarterly}, ежемесячная +6: '
                f'{fixed_monthly_shift}, квартальная +6: '
                f'{fixed_quarterly_shift}')

    # --- Шаг 8. Отдельная страница отчёта «Контроль сроков» удалена --------
    resp = web.get('/reports/deadlines')
    reports_page = web.get('/reports').get_data(as_text=True)
    link_present = 'Контроль сроков отчётности' in reports_page
    steps.check(resp.status_code == 404 and not link_present,
                'Страница «Контроль сроков сдачи отчётности» удалена',
                'страница недоступна (HTTP 404), в разделе «Отчёты» её нет',
                f'HTTP {resp.status_code}, ссылка в разделе отчётов: '
                f'{"есть" if link_present else "нет"}')

    # --- Шаг 9. Календарь переносит сроки с выходных и праздников -----------
    saturday = date(2026, 11, 21)
    holiday = date(2026, 1, 1)
    calendar_ok = (
        adjust_due_date(saturday, (), 'forward') == date(2026, 11, 23)
        and adjust_due_date(saturday, (), 'backward') == date(2026, 11, 20)
        and adjust_due_date(saturday, (), 'none') == saturday
        and adjust_due_date(holiday, (), 'forward') == date(2026, 1, 9))
    with app.app_context():
        app.config['CALENDAR_SHIFT'] = 'forward'
        shifted = ReportingDeadline(
            periodicity='monthly', due_day=20, period_offset=0
        ).effective_due_date(date(2026, 11, 1))
        app.config['CALENDAR_SHIFT'] = 'none'
    steps.check(calendar_ok and shifted == date(2026, 12, 21),
                'Календарь учитывает выходные и праздники',
                'суббота переносится на понедельник, 1 января — на 9-е; '
                'месячная форма со сроком 20.12 переходит на 21.12',
                f'суббота → {adjust_due_date(saturday, (), "forward")}, '
                f'праздник → {adjust_due_date(holiday, (), "forward")}, '
                f'форма → {shifted}')

    # --- Шаг 10. Приоритет клиента и формы влияет на задачу -----------------
    fixed_today = date(2026, 11, 18)
    with app.app_context():
        high_client = Client(
            name='ООО «Приоритетный-Тест»', inn='7705555504', status='active',
            responsible_id=users['emp1'], priority=1)
        low_form = ReportingDeadline(
            name='Форма низкого приоритета', code='low-prio-sc4',
            periodicity='monthly', due_day=20, period_offset=0,
            priority=3, is_enabled=True)
        db.session.add_all([high_client, low_form])
        db.session.commit()
        high_id, low_form_id = high_client.id, low_form.id
        created = generate_deadline_tasks(7, today=fixed_today)
        priority_task = next(
            (t for t in created
             if t.client_id == high_id and t.reporting_deadline_id == low_form_id),
            None)
    priority_ok = (priority_task is not None
                   and priority_task.priority == 1
                   and 'клиент — высокий, форма — низкий'
                   in priority_task.description)
    steps.check(priority_ok,
                'Приоритет задачи определяется клиентом и формой',
                'у клиента с высоким приоритетом и формы с низким '
                'приоритетом создаётся задача высокого приоритета',
                f'приоритет: {priority_task.priority if priority_task else "нет задачи"}')

    # --- Шаг 11. Ручная правка срока не создаёт дубли -----------------------
    first_deadline_id = next(iter(by_deadline))
    with app.app_context():
        editable = Task.query.filter_by(
            client_id=active_client.id,
            reporting_deadline_id=first_deadline_id).first()
        editable.due_date = editable.due_date + timedelta(days=1)
        db.session.commit()
        total_before = Task.query.count()
        generate_deadline_tasks(7)
        total_after = Task.query.count()
    steps.check(total_after == total_before,
                'Ручная корректировка срока не ломает дедупликацию',
                'после изменения срока у существующей задачи повторная '
                'генерация не создаёт дубль',
                f'задач до: {total_before}, после: {total_after}')

    # --- Шаг 12. Корректировка меняет срок в автогенерации ------------------
    with app.app_context():
        override_form = ReportingDeadline(
            name='Форма с ручным сроком', code='override-sc4',
            periodicity='monthly', due_day=1, period_offset=0,
            is_enabled=True)
        db.session.add(override_form)
        db.session.commit()
        db.session.add(DeadlineOverride(
            client_id=active_client.id, deadline_id=override_form.id,
            period=date(2026, 10, 1), due_date=date(2026, 11, 24),
            note='тестовая корректировка'))
        db.session.commit()
        created = generate_deadline_tasks(7, today=fixed_today)
        overridden_task = next(
            (t for t in created
             if t.client_id == active_client.id
             and t.reporting_deadline_id == override_form.id), None)
    steps.check(overridden_task is not None
                and overridden_task.due_date == date(2026, 11, 24),
                'Ручная корректировка срока попадает в задачу',
                'форма со сроком 01.11 переходит в окно генерации '
                'скорректированной датой 24.11',
                f'срок задачи: '
                f'{overridden_task.due_date if overridden_task else "нет задачи"}')

    # --- Шаг 13. Календарь сдачи на месяц и квартал -------------------------
    month_page = web.get('/deadlines/calendar', query_string={
        'view': 'month', 'year': 2026, 'month': 11})
    month_html = month_page.get_data(as_text=True)
    quarter_html = web.get('/deadlines/calendar', query_string={
        'view': 'quarter', 'year': 2026, 'month': 11}).get_data(as_text=True)
    steps.check(month_page.status_code == 200
                and 'Календарь сдачи отчётности' in month_html
                and 'СЗВ-М (сценарий 4)' in month_html
                and 'Квартал' in quarter_html,
                'Календарь сдачи доступен в режиме месяца и квартала',
                'страница открывается и показывает формы клиента '
                'в выбранном периоде',
                f'месяц: HTTP {month_page.status_code}, '
                f'квартал: {"есть" if "Квартал" in quarter_html else "нет"}')

    # --- Шаг 14. Уведомления за N дней до сдачи -----------------------------
    with app.app_context():
        soon = Task(title='Уведомление: тестовая задача',
                    assignee_id=users['emp1'], client_id=active_client.id,
                    status=Task.STATUS_NEW,
                    due_date=date.today() + timedelta(days=3))
        db.session.add(soon)
        db.session.commit()
        soon_id = soon.id
        first_batch = generate_deadline_notifications(
            lead_days=(3, 1), today=date.today())
        repeat_batch = generate_deadline_notifications(
            lead_days=(3, 1), today=date.today())
        has_ours = any(n.task_id == soon_id and n.days_before == 3
                       for n in first_batch)
    emp_web = app.test_client()
    login(emp_web, EMAILS['emp1'])
    notifications_html = emp_web.get('/deadlines/notifications').get_data(
        as_text=True)
    marked = emp_web.post('/deadlines/notifications',
                          data={'mark_all': '1'},
                          follow_redirects=True)
    with app.app_context():
        unread_after = DeadlineNotification.query.filter_by(
            user_id=users['emp1'], is_read=False).count()
    steps.check(has_ours and not repeat_batch
                and 'Уведомление: тестовая задача' in notifications_html
                and 'Отмечено прочитанными' in marked.get_data(as_text=True)
                and unread_after == 0,
                'Уведомления создаются за N дней и отмечаются прочитанными',
                'уведомление за 3 дня создано один раз, отображается '
                'исполнителю и сбрасывается кнопкой «Отметить все»',
                f'создано: {len(first_batch)}, повтор: {len(repeat_batch)}, '
                f'непрочитано после: {unread_after}')

    # --- Шаг 15. Задание уведомлений зарегистрировано в планировщике --------
    scheduler = init_scheduler(app)
    job_ids = {job.id for job in scheduler.get_jobs()}
    scheduler.shutdown(wait=False)
    steps.check('generate_deadline_notifications' in job_ids,
                'Уведомления подключены к планировщику',
                'в планировщике есть задание generate_deadline_notifications',
                f'задания: {sorted(job_ids)}')

    conclusion = ('Задачи формируются по справочнику сроков, сроки '
                  'рассчитываются корректно.'
                  if steps.passed
                  else 'Формирование задач по срокам работает некорректно — '
                       'см. шаги.')
    return steps, conclusion
