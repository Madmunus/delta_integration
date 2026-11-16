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

    # --- Шаг 3.1. Поиск, фильтр по дате и кнопки реестра ------------------
    filtered = web.get('/tasks', query_string={
        'q': 'Подготовить декларацию'}).get_data(as_text=True)
    unmatched = web.get('/tasks', query_string={
        'q': 'нет-такой-задачи'}).get_data(as_text=True)
    controls_ok = ('Поиск по таблице' in html
                   and 'name="q"' in html
                   and 'fa-xmark' in html
                   and 'name="date_from"' in html
                   and 'name="date_to"' in html
                   and 'title="Применить фильтр"' in html
                   and 'fa-magnifying-glass' in html
                   and 'title="Сбросить фильтр"' in html
                   and 'fa-arrow-rotate-left' in html
                   and 'title="Добавить задачу"' in html
                   and 'fa-plus' in html
                   and 'name="overdue"' in html
                   and 'Просроченные' in html
                   and 'value="overdue"' not in html
                   and 'Новая задача' not in html)
    steps.check(controls_ok,
                'В реестре задач есть поиск, фильтр по дате и кнопки действий',
                'поле «Поиск по таблице» с очисткой, фильтр «Дата создания: '
                'с / по», кнопки применения, сброса и синяя кнопка «+» '
                'вместо «Новая задача»',
                'элементы фильтрации и кнопки присутствуют'
                if controls_ok else 'элементы фильтрации или кнопки неверны')
    search_ok = (TASK_TITLE[:40] in filtered
                 and TASK_TITLE[:40] not in unmatched)
    steps.check(search_ok,
                'Поиск по таблице в реестре задач фильтрует записи',
                'поиск «Подготовить декларацию» находит задачу, '
                'несуществующий текст — ничего не находит',
                'поиск работает' if search_ok else 'поиск не фильтрует записи')
    today = date.today().strftime('%d.%m.%Y')
    date_filtered = web.get('/tasks', query_string={
        'date_from': today, 'date_to': today}).get_data(as_text=True)
    date_ok = TASK_TITLE[:40] in date_filtered
    steps.check(date_ok,
                'В реестре задач работает фильтр по дате создания',
                'задача, созданная сегодня, попадает в диапазон «с — по»',
                'фильтр по дате работает' if date_ok
                else 'задача не найдена по дате создания')

    # --- Шаг 3.2. Страница просмотра задачи -------------------------------
    detail_html = web.get(f'/tasks/{task_id}').get_data(as_text=True)
    detail_ok = (TASK_TITLE[:40] in detail_html
                 and CLIENT_NAME in detail_html
                 and 'Исполнитель' in detail_html
                 and 'Данные' in detail_html
                 and 'Редактировать' in detail_html
                 and f'href="/tasks/{task_id}/edit"' in detail_html)
    steps.check(detail_ok,
                'Открывается страница просмотра задачи',
                'карточка показывает клиента, исполнителя, срок, приоритет, '
                'статус, описание и даты; есть переход в редактирование',
                'страница просмотра заполнена' if detail_ok
                else 'данные или кнопка редактирования отсутствуют')

    # --- Шаг 3.3. Карточка редактирования задачи --------------------------
    edit_html = web.get(f'/tasks/{task_id}/edit').get_data(as_text=True)
    edit_ok = (TASK_TITLE[:40] in edit_html
               and 'Данные' in edit_html
               and 'Редактируемые поля' in edit_html
               and 'Просмотр' in edit_html
               and f'href="/tasks/{task_id}"' in edit_html
               and 'name="title"' in edit_html
               and 'Просрочена' not in edit_html
               and 'Отмена' not in edit_html)
    steps.check(edit_ok,
                'Карточка редактирования задачи оформлена как у других сущностей',
                'заголовок с кнопкой «Просмотр», блоки «Данные» и '
                '«Редактируемые поля», только кнопка сохранения',
                'карточка редактирования соответствует образцу' if edit_ok
                else 'структура карточки редактирования отличается')

    # --- Шаг 3.4. Переходы из реестра задач -------------------------------
    links_ok = (f'href="/tasks/{task_id}"' in html
                and f'href="/tasks/{task_id}/edit"' in html
                and 'fa-pencil' in html)
    steps.check(links_ok,
                'Из реестра открываются просмотр и редактирование задачи',
                'название задачи ведёт в карточку просмотра, карандаш — '
                'в карточку редактирования',
                'ссылки реестра настроены' if links_ok
                else 'ссылки реестра неверны')

    # --- Шаг 3.4.1. Сортировка по столбцам реестра ------------------------
    with app.app_context():
        db.session.add_all([
            Task(title='А-сортировка задачи', status=Task.STATUS_NEW,
                 assignee_id=users['admin']),
            Task(title='Я-сортировка задачи', status=Task.STATUS_NEW,
                 assignee_id=users['admin']),
        ])
        db.session.commit()
    sort_asc = web.get('/tasks', query_string={
        'sort': 'title', 'dir': 'asc'}).get_data(as_text=True)
    sort_desc = web.get('/tasks', query_string={
        'sort': 'title', 'dir': 'desc'}).get_data(as_text=True)
    anchor = TASK_TITLE[:20]
    sorting_ok = ('▲' in sort_asc and '▼' in sort_desc
                  and 'name="sort" value="title"' in sort_asc
                  and 'name="dir" value="asc"' in sort_asc
                  and sort_asc.find('А-сортировка') < sort_asc.find(anchor)
                  < sort_asc.find('Я-сортировка')
                  and sort_desc.find('Я-сортировка') < sort_desc.find(anchor)
                  < sort_desc.find('А-сортировка'))
    steps.check(sorting_ok,
                'В реестре задач работает сортировка по столбцам',
                'клик по заголовку сортирует по возрастанию (▲), повторный — '
                'по убыванию (▼); выбранная сортировка сохраняется в фильтре',
                'сортировка по названию работает' if sorting_ok
                else 'сортировка по столбцам не работает')
    with app.app_context():
        Task.query.filter(Task.title.in_(
            ['А-сортировка задачи', 'Я-сортировка задачи'])).delete(
                synchronize_session=False)
        db.session.commit()

    # --- Шаг 3.5. Кнопки движения по статусам ------------------------------
    arrows_ok = ('fa-arrow-left' in html
                 and 'fa-arrow-right' in html
                 and 'Отметить выполненной' not in html
                 and 'fa-check' not in html)
    steps.check(arrows_ok,
                'В реестре задачи двигаются по статусам стрелками',
                'вместо кнопки «Отметить выполненной» — кнопки с иконками '
                'fa-arrow-left и fa-arrow-right',
                'кнопки движения по статусам присутствуют' if arrows_ok
                else 'кнопки движения по статусам неверны')
    with app.app_context():
        task = db.session.get(Task, task_id)
        chain_ok = (task.status == 'new' and task.prev_status is None
                    and task.next_status == 'in_progress')
    steps.check(chain_ok,
                'Цепочка статусов задачи: новая → в работе → выполнена',
                'для новой задачи доступно движение только вперёд — «в работе»',
                'цепочка статусов настроена' if chain_ok
                else 'цепочка статусов неверна')

    # --- Шаг 4. Смена статуса: новая → в работе ---------------------------
    resp = web.post(f'/tasks/{task_id}/status', data={'status': 'in_progress'},
                    follow_redirects=True)
    with app.app_context():
        task = db.session.get(Task, task_id)
    steps.check(resp.status_code == 200 and task.status == 'in_progress',
                'Статус изменён: новая → в работе',
                'статус задачи в базе — «в работе»',
                f'HTTP {resp.status_code}, статус {task.status}')
    chain_mid_ok = (task.prev_status == 'new' and task.next_status == 'done')
    steps.check(chain_mid_ok,
                'Из статуса «в работе» доступны обе стрелки',
                'назад — «новая», вперёд — «выполнена»',
                'переходы в обе стороны доступны' if chain_mid_ok
                else 'переходы по статусам неверны')

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

    # --- Шаг 9. Бейдж статуса и кнопка удаления всех задач ------------------
    registry = web.get('/tasks').get_data(as_text=True)
    badge_ok = ('<span class="badge text-bg-success">выполнена</span>'
                in registry)
    delete_all_ok = ('title="Удалить все задачи"' in registry
                     and 'btn-danger' in registry
                     and 'fa-xmark' in registry
                     and 'formaction="/tasks/delete-all"' in registry)
    steps.check(badge_ok,
                'Статус задачи в реестре выводится цветным бейджем',
                'выполненная задача отмечена зелёным бейджем «выполнена»',
                'бейдж статуса отображается' if badge_ok
                else 'статус выводится без бейджа')
    with app.app_context():
        mapping_ok = (
            Task.STATUS_BADGES == {
                'new': 'text-bg-secondary',
                'in_progress': 'text-bg-primary',
                'done': 'text-bg-success',
            }
            and Task(status='new').status_badge_class == 'text-bg-secondary'
            and Task(status='in_progress').status_badge_class == 'text-bg-primary'
            and Task(status='done').status_badge_class == 'text-bg-success'
            and Task(status='unknown').status_badge_class == 'text-bg-dark')
    steps.check(mapping_ok,
                'Для статусов задачи задано соответствие цветных бейджей',
                '«новая» — серый, «в работе» — синий, «выполнена» — зелёный; '
                'для неизвестного значения — нейтральный тёмный',
                'соответствие статусов и цветов настроено' if mapping_ok
                else 'соответствие статусов и цветов неверно')
    with app.app_context():
        priority_mapping_ok = (
            Task.PRIORITY_BADGES == {
                1: 'text-bg-danger',
                2: 'text-bg-warning',
                3: 'text-bg-secondary',
            }
            and Task(priority=1).priority_badge_class == 'text-bg-danger'
            and Task(priority=2).priority_badge_class == 'text-bg-warning'
            and Task(priority=3).priority_badge_class == 'text-bg-secondary'
            and Task(priority=99).priority_badge_class == 'text-bg-dark')
    steps.check(priority_mapping_ok,
                'Для приоритетов задачи задано соответствие цветных бейджей',
                '«высокий» — красный, «средний» — жёлтый, «низкий» — серый; '
                'для неизвестного значения — нейтральный тёмный',
                'соответствие приоритетов и цветов настроено'
                if priority_mapping_ok
                else 'соответствие приоритетов и цветов неверно')
    priority_badge_ok = (
        '<span class="badge text-bg-danger">высокий</span>' in registry
        and '<span class="badge text-bg-danger">высокий</span>' in detail_html)
    steps.check(priority_badge_ok,
                'Приоритет задачи выводится цветным бейджем',
                'высокий приоритет отмечен красным бейджем в реестре '
                'и в карточке просмотра',
                'бейдж приоритета отображается' if priority_badge_ok
                else 'приоритет выводится без бейджа')
    steps.check(delete_all_ok,
                'В реестре есть кнопка удаления всех задач для руководителя',
                'после кнопки «+» расположена красная кнопка с крестиком',
                'кнопка удаления всех задач присутствует' if delete_all_ok
                else 'кнопка удаления всех задач отсутствует')

    # --- Шаг 10. Удаление всех задач доступно только руководителю ----------
    emp_web = app.test_client()
    login(emp_web, EMAILS['emp1'])
    emp_html = emp_web.get('/tasks').get_data(as_text=True)
    hidden_ok = 'title="Удалить все задачи"' not in emp_html
    steps.check(hidden_ok,
                'Кнопка удаления всех задач скрыта от сотрудника',
                'сотрудник не видит красную кнопку удаления всех задач',
                'кнопка скрыта' if hidden_ok else 'кнопка видна сотруднику')
    resp = web.post('/tasks/delete-all', follow_redirects=True)
    with app.app_context():
        left = Task.query.count()
    delete_ok = (resp.status_code == 200 and left == 0
                 and 'Удалены все задачи' in resp.get_data(as_text=True))
    steps.check(delete_ok,
                'Руководитель удаляет все задачи одной кнопкой',
                'после нажатия кнопки в базе не остаётся задач',
                f'HTTP {resp.status_code}, осталось задач: {left}')

    conclusion = ('Задача создаётся, статусы изменяются, виджет «Мои задачи» '
                  'обновляется.' if steps.passed
                  else 'Работа с задачами некорректна — см. шаги.')
    return steps, conclusion
