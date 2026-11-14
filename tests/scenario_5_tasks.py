"""Сценарий 5 (ФТ-5): задачи с привязкой к клиенту и исполнителю.

Создание задачи с привязкой к клиенту и исполнителю; изменение статуса;
проверка отображения в виджете «Мои задачи». Результат: задача
создаётся, статусы изменяются, виджет обновляется.
"""
import re
from datetime import date, timedelta

from engine.models import Task, db
from tests.base import EMAILS, Steps, create_scenario_app, login, seed_users

FT = 'ФТ-5'
TITLE = 'Создание задачи, изменение статуса, виджет «Мои задачи»'
DESCRIPTION = ('Создание задачи с привязкой к клиенту и исполнителю; '
               'изменение статуса; проверка отображения в виджете «Мои задачи». '
               'Результат: задача создаётся, статусы изменяются, '
               'виджет обновляется.')

TASK_TITLE = 'Подготовить декларацию по НДС (сценарий 3)'
CLIENT_NAME = 'ООО «Ромашка-Тест»'


def run():
    app = create_scenario_app()
    steps = Steps()
    users = seed_users(app)

    with app.app_context():
        from engine.models import Client
        db.session.add(Client(name=CLIENT_NAME, inn='7709999901',
                              status='active', responsible_id=users['admin']))
        db.session.commit()
        client_id = Client.query.filter_by(name=CLIENT_NAME).first().id

    web = app.test_client()
    login(web, EMAILS['admin'])

    # --- Шаг 1. Создание задачи через веб-форму -------------------------
    due = (date.today() + timedelta(days=5)).isoformat()
    resp = web.post('/tasks/add', data={
        'title': TASK_TITLE,
        'description': 'Проверочная задача сценария тестирования',
        'client_id': str(client_id),
        'assignee_id': str(users['admin']),
        'due_date': due,
        'priority': '1',
        'status': 'new',
    }, follow_redirects=True)
    with app.app_context():
        task = Task.query.filter_by(title=TASK_TITLE).first()
        task_id = task.id if task else None
    steps.check(resp.status_code == 200 and task is not None,
                'Задача создана через веб-форму',
                'задача сохранена и открылся реестр задач',
                f'HTTP {resp.status_code}, задача '
                f'{"создана" if task else "не создана"}')

    # --- Шаг 2. Привязка к клиенту и исполнителю -------------------------
    steps.check(
        task is not None and task.client_id == client_id
        and task.assignee_id == users['admin'] and task.status == 'new'
        and task.due_date == date.today() + timedelta(days=5),
        'Задача привязана к клиенту и исполнителю',
        'клиент и исполнитель сохранены, статус «новая», срок задан',
        (f'клиент {task.client_id}, исполнитель {task.assignee_id}, '
         f'статус {task.status}, срок {task.due_date}'
         if task else 'задача не найдена'))

    # --- Шаг 3. Задача в реестре задач ------------------------------------
    html = web.get('/tasks').get_data(as_text=True)
    steps.check(TASK_TITLE[:40] in html,
                'Задача отображается в реестре задач',
                'задача видна на странице «Задачи»',
                'задача в реестре' if TASK_TITLE[:40] in html
                else 'задачи нет в реестре')

    # --- Шаг 4. Смена статуса: новая → в работе ---------------------------
    resp = web.post(f'/tasks/{task_id}/status', data={'status': 'in_progress'},
                    follow_redirects=True)
    with app.app_context():
        task = db.session.get(Task, task_id)
    steps.check(resp.status_code == 200 and task.status == 'in_progress',
                'Статус изменён: новая → в работе',
                'статус задачи в базе — «в работе»',
                f'HTTP {resp.status_code}, статус {task.status}')

    # --- Шаг 5. Виджет «Мои задачи» ----------------------------------------
    html = web.get('/dashboard').get_data(as_text=True)
    widget = html.split('Мои задачи', 1)
    in_widget = (len(widget) > 1
                 and TASK_TITLE[:40] in widget[1][:3000])
    steps.check('Мои задачи' in html and in_widget,
                'Задача отображается в виджете «Мои задачи»',
                'открытая задача исполнителя видна в виджете на панели',
                'задача в виджете' if in_widget else 'задачи нет в виджете')

    # --- Шаг 6. Смена статуса: в работе → выполнена ------------------------
    resp = web.post(f'/tasks/{task_id}/status', data={'status': 'done'},
                    follow_redirects=True)
    with app.app_context():
        task = db.session.get(Task, task_id)
    steps.check(resp.status_code == 200 and task.status == 'done',
                'Статус изменён: в работе → выполнена',
                'статус задачи в базе — «выполнена»',
                f'HTTP {resp.status_code}, статус {task.status}')

    # --- Шаг 7. Виджет обновился -------------------------------------------
    html = web.get('/dashboard').get_data(as_text=True)
    widget = html.split('Мои задачи', 1)
    in_widget = (len(widget) > 1
                 and TASK_TITLE[:40] in widget[1][:3000])
    steps.check(not in_widget,
                'Виджет обновился после смены статуса',
                'выполненная задача исчезла из виджета «Мои задачи»',
                'виджет обновился' if not in_widget
                else 'выполненная задача всё ещё в виджете')

    # --- Шаг 8. Переход к созданию задачи из карточки клиента --------------
    card_html = web.get(f'/clients/{client_id}').get_data(as_text=True)
    form_html = web.get(f'/tasks/add?client={client_id}').get_data(as_text=True)
    selected = (re.search(rf'<option[^>]*selected[^>]*value="{client_id}"', form_html)
                or re.search(rf'<option[^>]*value="{client_id}"[^>]*selected', form_html))
    steps.check(f'/tasks/add?client={client_id}' in card_html and selected is not None,
                'Из карточки клиента открывается задача с выбранным клиентом',
                'кнопка «+ Задача» ведёт на создание задачи, клиент подставлен',
                'клиент выбран в форме задачи' if selected else 'клиент не выбран')

    conclusion = ('Задача создаётся, статусы изменяются, виджет «Мои задачи» '
                  'обновляется.' if steps.passed
                  else 'Работа с задачами некорректна — см. шаги.')
    return steps, conclusion
