"""Сценарий 7 (ФТ-7): формирование отчётов и проверка агрегатов.

Формирование отчётов по клиентской базе, по задачам, по договорам
и по документам (в разрезе клиентов); проверка корректности
агрегированных показателей.
Результат: отчёты формируются, показатели соответствуют данным.
"""
import csv
import io
import re
import zipfile
from datetime import date, timedelta
from html import unescape
from urllib.parse import quote

from engine.models import Client, Contract, Document, Task, User, db
from tests.base import (EMAILS, TEST_PASSWORD, Steps, create_scenario_app,
                        login, seed_users)

FT = 'ФТ-7'
TITLE = ('Формирование отчётов по клиентской базе, задачам, договорам '
         'и документам')
DESCRIPTION = ('Формирование отчётов по клиентской базе, по задачам, по '
               'договорам и по документам в разрезе клиентов; проверка '
               'корректности агрегированных показателей. Результат: отчёты '
               'формируются, показатели соответствуют данным.')

FIO = {
    'admin': 'Админов Аркадий Петрович',
    'emp1': 'Сотрудников Сергей Иванович',
    'emp2': 'Работникова Ольга Львовна',
}

# Отключённые сотрудники: в отчёте должны идти после всех активных,
# даже если у них больше активных клиентов. При равенстве показателей
# внутри группы — по ФИО.
INACTIVE_FIO = 'Уволенова Вера Петровна'
INACTIVE_FIO_2 = 'Алексеева Нина Сергеевна'

# Датасет: 9 клиентов (8 активных, 1 архивный, 1 без ответственного),
# включая клиентов отключённых сотрудников
CLIENTS = [
    # (наименование, ИНН, статус, ответственный)
    ('ООО «Ромашка-Т»', '7707777701', 'active', 'emp1'),
    ('ООО «Лютик-Т»', '7707777702', 'active', 'emp1'),
    ('ООО «Василёк-Т»', '7707777703', 'active', 'emp2'),
    ('ООО «Лопух-Т»', '7707777704', 'archived', 'emp2'),
    ('ООО «Незабудка-Т»', '7707777705', 'active', None),
    ('ООО «Клевер-Т»', '7707777706', 'active', 'emp3'),
    ('ООО «Мятлик-Т»', '7707777707', 'active', 'emp3'),
    ('ООО «Пырей-Т»', '7707777708', 'active', 'emp4'),
    ('ООО «Тимофеевка-Т»', '7707777709', 'active', 'emp4'),
]

# Датасет: 6 задач с известными исполнителями, статусами и сроками
TASKS = [
    # (клиент, исполнитель, статус, смещение срока в днях от сегодня)
    ('ООО «Ромашка-Т»', 'emp1', 'done', 3),
    ('ООО «Лютик-Т»', 'emp1', 'in_progress', 3),
    ('ООО «Ромашка-Т»', 'emp1', 'in_progress', -2),  # просрочена по сроку
    ('ООО «Василёк-Т»', 'emp2', 'done', -5),         # выполнена — не просрочена
    ('ООО «Василёк-Т»', 'emp2', 'new', 3),
    ('ООО «Ромашка-Т»', 'emp1', 'new', -1),          # просрочена по сроку
]

# Датасет: 9 договоров у 6 клиентов, включая архивного клиента;
# у архивного клиента только завершённый договор; признак «Обработан» —
# внутренний, часть договоров обработана
CONTRACTS = [
    # (клиент, номер, статус, сумма, смещение даты подписания в днях,
    #  обработан)
    ('ООО «Ромашка-Т»', 'Д-001', 'active', 1000, -30, True),
    ('ООО «Ромашка-Т»', 'Д-002', 'active', 500, -20, False),
    ('ООО «Ромашка-Т»', 'Д-003', 'closed', 300, -400, True),
    ('ООО «Лютик-Т»', 'Д-004', 'draft', 100, -5, False),
    ('ООО «Василёк-Т»', 'Д-005', 'active', 700, -60, True),
    ('ООО «Василёк-Т»', 'Д-006', 'closed', 200, -300, False),
    ('ООО «Лопух-Т»', 'Д-007', 'closed', 50, -10, False),
    ('ООО «Незабудка-Т»', 'Д-008', 'closed', 10, -200, True),
    ('ООО «Пырей-Т»', 'Д-009', 'active', 900, -90, False),
]

# Датасет: 5 документов у 4 клиентов, включая архивного клиента;
# статусы приходят свободным текстом, признак «Обработан» — внутренний
DOCUMENTS = [
    # (клиент, вид документа, номер, статус, смещение даты в днях, обработан)
    ('ООО «Ромашка-Т»', 'РеализацияТоваровУслуг', 'ДОК-001',
     'Проведён', -10, True),
    ('ООО «Ромашка-Т»', 'СчетНаОплатуПокупателю', 'ДОК-002',
     'Зарегистрирован', -5, False),
    ('ООО «Лютик-Т»', 'ПлатежноеПоручение', 'ДОК-003',
     'Черновик', -3, False),
    ('ООО «Василёк-Т»', 'РеализацияТоваровУслуг', 'ДОК-004',
     'Отменён', -20, True),
    ('ООО «Лопух-Т»', 'АктВыполненныхРабот', 'ДОК-005',
     'Проведён', -15, False),
]

# Разметка шаблонов отчётов (для извлечения показателей со страницы)
CARDS_RE = re.compile(r'<div class="display-6[^"]*">(\d+)</div>')
CLIENT_ROWS_RE = re.compile(
    r'<td><a href="/users/\d+/edit">([^<]+)</a></td>\s*'
    r'<td>\s*<span class="badge [^"]*">(активен|отключён)</span>\s*</td>\s*'
    r'<td class="text-end"><a href="[^"]*">(\d+)</a></td>\s*'
    r'<td class="text-end"><a href="[^"]*">(\d+)</a></td>')
TASK_ROWS_RE = re.compile(
    r'<td><a href="/users/\d+/edit">([^<]+)</a></td>\s*'
    r'<td>\s*<span class="badge [^"]*">(активен|отключён)</span>\s*</td>\s*'
    r'<td class="text-end text-success"><a\s+href="[^"]*">(\d+)</a></td>\s*'
    r'<td class="text-end"><a\s+href="[^"]*">(\d+)</a></td>\s*'
    r'<td class="text-end[^"]*"><a\s+href="[^"]*">(\d+)</a></td>\s*'
    r'<td class="text-end"><a\s+href="[^"]*">(\d+)</a></td>')

# Отчёт по договорам: плитки с числами и строки групп по клиентам
CONTRACT_CARD_RE = re.compile(
    r'<div class="display-6[^"]*">(\d+)</div>\s*'
    r'<div class="text-muted">([^<]+)</div>')


def _card_values(html):
    """Значения плиток в порядке вывода, независимо от текста подписей."""
    return tuple(int(value) for value, _label
                 in CONTRACT_CARD_RE.findall(html))


CONTRACT_GROUP_RE = re.compile(
    r'<td>\s*<a href="/clients/(\d+)">([^<]+)</a>'
    r'(?:\s*<span class="badge[^"]*">[^<]+</span>)?\s*</td>\s*'
    r'<td class="text-end"><a\s+href="/contracts\?client=\d+">(\d+)</a></td>\s*'
    r'<td class="text-end"><a\s+href="/contracts\?client=\d+&amp;status=draft">(\d+)</a></td>\s*'
    r'<td class="text-end"><a\s+href="/contracts\?client=\d+&amp;status=active">(\d+)</a></td>\s*'
    r'<td class="text-end"><a\s+href="/contracts\?client=\d+&amp;status=closed">(\d+)</a></td>\s*'
    r'<td class="text-end text-nowrap"><a\s+href="/contracts\?client=\d+&amp;processed=yes">(\d+)</a></td>\s*'
    r'<td class="text-end text-nowrap"><a\s+href="/contracts\?client=\d+&amp;processed=no">(\d+)</a></td>\s*'
    r'<td class="text-end text-nowrap">([^<]+)</td>\s*'
    r'<td class="text-end text-nowrap">([^<]+)</td>')

# Отчёт по документам: строки групп по клиентам
DOCUMENT_GROUP_RE = re.compile(
    r'<td>\s*<a href="/clients/(\d+)">([^<]+)</a>'
    r'(?:\s*<span class="badge[^"]*">[^<]+</span>)?\s*</td>\s*'
    r'<td class="text-end"><a\s+href="[^"]*">(\d+)</a></td>\s*'
    r'<td class="text-end"><a\s+href="[^"]*">(\d+)</a></td>\s*'
    r'<td class="text-end"><a\s+href="[^"]*">(\d+)</a></td>\s*'
    r'<td class="text-end"><a\s+href="[^"]*">(\d+)</a></td>\s*'
    r'<td class="text-end"><a\s+href="[^"]*">(\d+)</a></td>\s*'
    r'<td class="text-end text-nowrap"><a\s+href="[^"]*">(\d+)</a></td>\s*'
    r'<td class="text-end text-nowrap"><a\s+href="[^"]*">(\d+)</a></td>')


def _clients_of(responsible):
    """Ожидаемое число клиентов сотрудника: (всего, активных)."""
    own = [c for c in CLIENTS if c[3] == responsible]
    return len(own), sum(1 for c in own if c[2] == 'active')


def _task_counts(assignee):
    """Ожидаемое число задач исполнителя: (выполнено, в работе, просрочено).

    Просрочка — признак срока: задача не выполнена и её срок уже прошёл.
    """
    own = [t for t in TASKS if t[1] == assignee]
    done = sum(1 for _, _, status, _ in own if status == 'done')
    work = sum(1 for _, _, status, _ in own
               if status in ('new', 'in_progress'))
    over = sum(1 for _, _, status, days in own
               if status != 'done' and days < 0)
    return done, work, over


def _contract_stats(client_name):
    """Ожидаемые показатели клиента: (всего, проект, действ., заверш.,
    обработано, не обработано, сумма действующих, сумма всех)."""
    own = [c for c in CONTRACTS if c[0] == client_name]
    counts = {status: sum(1 for c in own if c[2] == status)
              for status in ('draft', 'active', 'closed')}
    processed = sum(1 for c in own if c[5])
    amount_active = sum(c[3] for c in own if c[2] == 'active')
    amount_total = sum(c[3] for c in own)
    return (len(own), counts['draft'], counts['active'], counts['closed'],
            processed, len(own) - processed,
            amount_active, amount_total)


def _document_stats(client_name):
    """Ожидаемые показатели клиента: (всего, черновик, зарегистрирован,
    проведён, отменён, обработано, не обработано)."""
    own = [d for d in DOCUMENTS if d[0] == client_name]
    statuses = ('Черновик', 'Зарегистрирован', 'Проведён', 'Отменён')
    counts = [sum(1 for d in own if d[3] == status) for status in statuses]
    processed = sum(1 for d in own if d[5])
    return (len(own), *counts, processed, len(own) - processed)


def _to_number(text):
    """Сумма со страницы («1 500,00») в число."""
    return float(text.replace('\xa0', '').replace(' ', '').replace(',', '.'))


def _card_hrefs(html):
    """Ссылки со всех плиток отчёта (в порядке вывода)."""
    return re.findall(
        r'<a class="card delta-report-card[^"]*"\s+href="([^"]+)"', html)


def _csv_rows(response):
    """Строки CSV-ответа без BOM."""
    text = response.get_data(as_text=True).lstrip('\ufeff')
    return list(csv.reader(io.StringIO(text), delimiter=';'))


def run():
    app = create_scenario_app()
    steps = Steps()
    users = seed_users(app)

    with app.app_context():
        for email, fio in (('emp3@test.local', INACTIVE_FIO),
                           ('emp4@test.local', INACTIVE_FIO_2)):
            inactive = User(email=email, fio=fio, role='employee',
                            is_active_flag=False)
            inactive.set_password(TEST_PASSWORD)
            db.session.add(inactive)
        db.session.flush()
        users['emp3'] = User.query.filter_by(email='emp3@test.local').first().id
        users['emp4'] = User.query.filter_by(email='emp4@test.local').first().id
        client_ids = {}
        for name, inn, status, resp in CLIENTS:
            db.session.add(Client(
                name=name, inn=inn, status=status,
                responsible_id=users[resp] if resp else None))
        db.session.flush()
        for name, _, _, _ in CLIENTS:
            client_ids[name] = Client.query.filter_by(name=name).first().id
        for client_name, assignee, status, due_offset in TASKS:
            db.session.add(Task(
                title=f'Задача для {client_name}',
                client_id=client_ids[client_name],
                assignee_id=users[assignee] if assignee else None,
                due_date=date.today() + timedelta(days=due_offset),
                status=status))
        for (client_name, number, status, amount, day_offset,
             processed) in CONTRACTS:
            db.session.add(Contract(
                number=number,
                client_id=client_ids[client_name],
                signed_date=date.today() + timedelta(days=day_offset),
                amount=amount,
                status=status,
                processed=processed))
        for client_name, doc_type, number, status, day_offset, processed in DOCUMENTS:
            db.session.add(Document(
                doc_type=doc_type,
                number=number,
                client_id=client_ids[client_name],
                doc_date=date.today() + timedelta(days=day_offset),
                status=status,
                processed=processed))
        db.session.commit()

    steps.note('Подготовлен контрольный датасет',
               f'{len(CLIENTS)} клиентов ({sum(1 for c in CLIENTS if c[2] == "active")} '
               f'активных), {len(TASKS)} задач, {len(CONTRACTS)} договоров '
               f'у {len({c[0] for c in CONTRACTS})} клиентов, '
               f'{len(DOCUMENTS)} документов у {len({d[0] for d in DOCUMENTS})} '
               f'клиентов, 5 пользователей (двое отключены)')

    web = app.test_client()
    login(web, EMAILS['admin'])

    # --- Страница «Отчёты»: весь блок — ссылка на отчёт --------------------
    resp = web.get('/reports')
    html = resp.get_data(as_text=True)
    index_ok = (resp.status_code == 200
                and 'Открыть' not in html
                and all(f'href="{href}"' in html for href in (
                    '/reports/clients', '/reports/contracts',
                    '/reports/documents', '/reports/tasks'))
                and html.count('delta-report-card') == 4)
    steps.check(index_ok,
                'На странице «Отчёты» блоки являются ссылками',
                'нет кнопки «Открыть»; каждый из 4 блоков целиком ведёт '
                'на страницу соответствующего отчёта',
                'блоки-ссылки оформлены корректно' if index_ok
                else 'кнопка «Открыть» осталась или ссылки отсутствуют')

    # --- Отчёт по клиентской базе ----------------------------------------
    resp = web.get('/reports/clients')
    html = resp.get_data(as_text=True)
    steps.check(resp.status_code == 200 and 'Отчёт по клиентской базе' in html,
                'Отчёт по клиентской базе формируется',
                'страница отчёта открывается руководителю',
                f'HTTP {resp.status_code}')

    cards = CARDS_RE.findall(html)
    exp_total = len(CLIENTS)
    exp_active = sum(1 for c in CLIENTS if c[2] == 'active')
    exp_unassigned = sum(1 for c in CLIENTS if c[3] is None)
    got = tuple(int(v) for v in cards[:3])
    steps.check(got == (exp_active, exp_total, exp_unassigned),
                'Сводные показатели отчёта по клиентской базе',
                f'активных {exp_active}, всего {exp_total}, '
                f'без ответственного {exp_unassigned}',
                f'на странице: активных {got[0]}, всего {got[1]}, '
                f'без ответственного {got[2]}')

    parsed = CLIENT_ROWS_RE.findall(html)
    rows = {fio.strip(): (int(a), int(b))
            for fio, status, a, b in parsed}
    exp_rows = {FIO['emp1']: _clients_of('emp1'), FIO['emp2']: _clients_of('emp2')}
    ok = all(rows.get(fio) == val for fio, val in exp_rows.items())
    steps.check(ok,
                'Распределение клиентов по ответственным',
                f'{FIO["emp1"]} — {_clients_of("emp1")[0]} кл. '
                f'({_clients_of("emp1")[1]} активных), '
                f'{FIO["emp2"]} — {_clients_of("emp2")[0]} кл. '
                f'({_clients_of("emp2")[1]} активных)',
                '; '.join(f'{fio}: {rows.get(fio)}' for fio in exp_rows))

    # --- Активность пользователей и порядок строк -------------------------
    statuses = {fio.strip(): status for fio, status, _, _ in parsed}
    row_order = [fio.strip() for fio, *_ in parsed]
    exp_order = [FIO['emp1'], FIO['emp2'], FIO['admin'],
                 INACTIVE_FIO_2, INACTIVE_FIO]
    steps.check(
        row_order == exp_order
        and statuses.get(INACTIVE_FIO) == 'отключён'
        and statuses.get(INACTIVE_FIO_2) == 'отключён'
        and statuses.get(FIO['emp1']) == 'активен',
        'Сортировка: активные пользователи, затем по активным клиентам',
        'сначала активные по убыванию числа активных клиентов, '
        'отключённые — в конце; при равенстве — по ФИО',
        f'порядок: {row_order}; статусы: {statuses}')

    # --- Раскрывающийся список клиентов сотрудника -------------------------
    toggles = html.count('delta-report-toggle')
    detail_ok = (
        f'id="responsible-clients-{users["emp1"]}"' in html
        and f'id="responsible-clients-{users["admin"]}"' in html
        and all(f'data-bs-target="#responsible-clients-{uid}"' in html
                for uid in users.values()))
    names_ok = all(name in html for name, _, _, resp in CLIENTS
                   if resp == 'emp1')
    steps.check(toggles == len(parsed) and detail_ok and names_ok,
                'Клиенты сотрудника раскрываются отдельным списком',
                f'{len(parsed)} раскрывающихся блоков; в блоке сотрудника — '
                'его клиенты со ссылками на карточки',
                f'кнопок: {toggles}, цели: '
                f'{"все" if detail_ok else "не все"}, клиенты: '
                f'{"все" if names_ok else "не все"}')

    # --- Ссылки в плитках и таблице ---------------------------------------
    links_ok = all(href in html for href in (
        'href="/clients?status=active"',
        'href="/clients"',
        'href="/clients?responsible=none"',
        f'href="/users/{users["emp1"]}/edit"',
        f'href="/clients?responsible={users["emp1"]}"',
        f'href="/clients?responsible={users["emp1"]}&amp;status=active"',
    ))
    steps.check(links_ok,
                'Ссылки отчёта ведут в реестр и карточки',
                'плитки — в реестр с фильтром, ФИО — в карточку '
                'пользователя, числа — в реестр по ответственному',
                'все ссылки найдены' if links_ok
                else 'часть ссылок отсутствует')

    # --- Отчёт по задачам --------------------------------------------------
    resp = web.get('/reports/tasks')
    html = resp.get_data(as_text=True)
    steps.check(resp.status_code == 200 and 'Отчёт по задачам' in html,
                'Отчёт по задачам формируется',
                'страница отчёта открывается руководителю',
                f'HTTP {resp.status_code}')

    exp_cards = (
        len(TASKS),
        sum(1 for t in TASKS if t[2] == 'done'),
        sum(1 for t in TASKS if t[2] in ('new', 'in_progress')),
        sum(1 for t in TASKS if t[2] != 'done' and t[3] < 0),
    )
    task_cards = tuple(int(v) for v in CARDS_RE.findall(html)[:4])
    steps.check(task_cards == exp_cards,
                'Сводные плитки отчёта по задачам',
                f'всего {exp_cards[0]}, выполнено {exp_cards[1]}, '
                f'в работе {exp_cards[2]}, просрочено {exp_cards[3]}',
                f'на странице: {task_cards}')

    steps.check('без исполнителя' not in html,
                'В отчёте по задачам нет строки «без исполнителя»',
                'все задачи отнесены к конкретным исполнителям',
                'строка «без исполнителя» отсутствует'
                if 'без исполнителя' not in html
                else 'строка «без исполнителя» осталась')

    parsed_tasks = TASK_ROWS_RE.findall(html)
    rows = {fio.strip(): tuple(int(v) for v in nums)
            for fio, _activity, *nums in parsed_tasks}
    activity = {fio.strip(): label for fio, label, *_ in parsed_tasks}
    exp_rows = {FIO['emp1']: _task_counts('emp1'), FIO['emp2']: _task_counts('emp2')}
    ok = all(rows.get(fio)[:3] == val for fio, val in exp_rows.items())
    steps.check(ok,
                'Агрегированные показатели отчёта по задачам',
                f'{FIO["emp1"]}: выполнено {_task_counts("emp1")[0]}, '
                f'в работе {_task_counts("emp1")[1]}, '
                f'просрочено {_task_counts("emp1")[2]}; '
                f'{FIO["emp2"]}: выполнено {_task_counts("emp2")[0]}, '
                f'в работе {_task_counts("emp2")[1]}, '
                f'просрочено {_task_counts("emp2")[2]}',
                '; '.join(f'{fio}: {rows.get(fio)}' for fio in exp_rows))

    order = [fio.strip() for fio, *_ in parsed_tasks]
    exp_order = [FIO['emp1'], FIO['emp2'], FIO['admin'],
                 INACTIVE_FIO_2, INACTIVE_FIO]
    inactive_ok = (activity.get(FIO['admin']) == 'активен'
                   and activity.get(FIO['emp1']) == 'активен'
                   and activity.get(FIO['emp2']) == 'активен'
                   and activity.get(INACTIVE_FIO) == 'отключён'
                   and activity.get(INACTIVE_FIO_2) == 'отключён'
                   and html.count('<tr class="table-secondary">') == 2)
    steps.check(order == exp_order and inactive_ok,
                'Сортировка и отметка активности в отчёте по задачам',
                'сначала активные пользователи, затем отключённые; внутри '
                'группы — «Просрочено», «В работе», «Выполнено» по убыванию, '
                'при равенстве — по ФИО; отключённые выделены строкой '
                'и бейджем «отключён»',
                f'порядок: {order}; активность: {activity}')

    # --- Раскрывающийся список задач исполнителя ---------------------------
    toggles = html.count('delta-report-toggle')
    detail_ok = (
        f'id="assignee-tasks-{users["emp1"]}"' in html
        and all(f'data-bs-target="#assignee-tasks-{uid}"' in html
                for uid in users.values()))
    titles_ok = all(f'Задача для {client_name}' in html
                    for client_name, assignee, _, _ in TASKS
                    if assignee == 'emp1')
    steps.check(toggles == len(TASK_ROWS_RE.findall(html))
                and detail_ok and titles_ok,
                'Задачи исполнителя раскрываются отдельным списком',
                'кнопка в каждой строке; в блоке — задачи со сроком, '
                'приоритетом и статусом',
                f'кнопок: {toggles}, цели: '
                f'{"все" if detail_ok else "не все"}, задачи: '
                f'{"все" if titles_ok else "не все"}')

    # --- Ссылки в плитках и таблице ---------------------------------------
    links_ok = all(href in html for href in (
        'href="/tasks"',
        'href="/tasks?status=done"',
        'href="/tasks?status=open"',
        'href="/tasks?status=overdue"',
        f'href="/users/{users["emp1"]}/edit"',
        f'href="/tasks?assignee={users["emp1"]}&amp;status=done"',
        f'href="/tasks?assignee={users["emp1"]}&amp;status=open"',
        f'href="/tasks?assignee={users["emp1"]}&amp;status=overdue"',
        f'href="/tasks?assignee={users["emp1"]}"',
    ))
    steps.check(links_ok,
                'Ссылки отчёта по задачам ведут в реестр и карточки',
                'плитки — в реестр с фильтром, ФИО — в карточку '
                'пользователя, числа — в реестр по исполнителю',
                'все ссылки найдены' if links_ok
                else 'часть ссылок отсутствует')

    # --- Отчёт по договорам и поступлениям (группировка по клиентам) ------
    resp = web.get('/reports/contracts')
    html = resp.get_data(as_text=True)
    steps.check(resp.status_code == 200
                and 'Отчёт по договорам' in html,
                'Отчёт по договорам и поступлениям формируется',
                'страница отчёта открывается руководителю',
                f'HTTP {resp.status_code}')

    card_values = _card_values(html)
    exp_active = sum(1 for c in CONTRACTS if c[2] == 'active')
    exp_closed = sum(1 for c in CONTRACTS if c[2] == 'closed')
    exp_draft = sum(1 for c in CONTRACTS if c[2] == 'draft')
    exp_clients = len({c[0] for c in CONTRACTS})
    exp_processed = sum(1 for c in CONTRACTS if c[5])
    exp_cards = (exp_clients, len(CONTRACTS), exp_draft, exp_active,
                 exp_closed, exp_processed)
    steps.check(card_values == exp_cards,
                'Сводные плитки отчёта по договорам',
                f'клиентов {exp_clients}, всего {len(CONTRACTS)}, '
                f'проектов {exp_draft}, действующих {exp_active}, '
                f'завершённых {exp_closed}, обработано {exp_processed}',
                f'на странице: {card_values}')

    parsed = CONTRACT_GROUP_RE.findall(html)
    order = []
    groups = {}
    for (_, name, total, draft, active, closed, processed, unprocessed,
         amt_active, amt_total) in parsed:
        name = name.strip()
        order.append(name)
        groups[name] = (int(total), int(draft), int(active), int(closed),
                        int(processed), int(unprocessed),
                        _to_number(amt_active), _to_number(amt_total))
    exp_groups = {name: _contract_stats(name)
                  for name in {c[0] for c in CONTRACTS}}
    ok = groups == exp_groups
    steps.check(ok, 'Договоры сгруппированы по клиентам',
                'для каждого клиента — всего, по статусам, обработано, '
                'не обработано и суммы действующих и всех договоров',
                '; '.join(f'{n}: {groups.get(n)}' for n in exp_groups)
                if ok else f'получено: {groups}')

    client_status = {name: status for name, _, status, _ in CLIENTS}
    with_contracts = {c[0] for c in CONTRACTS}

    archived_open = {
        name: (groups[name][1], groups[name][2])
        for name in with_contracts if client_status[name] != 'active'
    }
    steps.check(
        all(draft == 0 and active == 0
            for draft, active in archived_open.values()),
        'У архивных клиентов нет проектов и действующих договоров',
        'в отчёте у архивных клиентов проектов 0 и действующих 0',
        f'архивные клиенты: {archived_open}')

    exp_order = (
        sorted([n for n in with_contracts if client_status[n] == 'active'],
               key=lambda n: (-_contract_stats(n)[6], n))
        + sorted([n for n in with_contracts if client_status[n] != 'active'],
                 key=lambda n: (-_contract_stats(n)[6], n)))
    steps.check(order == exp_order,
                'Порядок групп: активные клиенты, затем архивные',
                'сначала активные по сумме действующих договоров '
                '(по убыванию), при равенстве — по наименованию; '
                'архивные — в конце',
                f'порядок: {order}')

    links_ok = all(href in html for href in (
        'href="/contracts"',
        'href="/contracts?status=active"',
        'href="/contracts?status=closed"',
        'href="/contracts?status=draft"',
        'href="/contracts?processed=yes"',
        f'href="/clients/{client_ids["ООО «Ромашка-Т»"]}"',
        f'href="/contracts?client={client_ids["ООО «Ромашка-Т»"]}"',
        f'href="/contracts?client={client_ids["ООО «Ромашка-Т»"]}'
        f'&amp;status=active"',
        f'href="/contracts?client={client_ids["ООО «Ромашка-Т»"]}'
        f'&amp;processed=yes"',
        f'href="/contracts?client={client_ids["ООО «Ромашка-Т»"]}'
        f'&amp;processed=no"',
    ))
    steps.check(links_ok,
                'Ссылки отчёта по договорам ведут в реестр и карточки',
                'плитки — в реестр по статусу, клиент — в карточку, '
                'числа — в реестр по клиенту, статусу и признаку '
                '«Обработан»',
                'все ссылки найдены' if links_ok
                else 'часть ссылок отсутствует')

    collapses = html.count('delta-report-toggle')
    targets_ok = all(f'id="client-contracts-{client_ids[name]}"' in html
                     for name in with_contracts)
    numbers_ok = all(number in html for _, number, *_ in CONTRACTS)
    steps.check(collapses == exp_clients and targets_ok and numbers_ok,
                'Договоры клиента раскрываются отдельным списком',
                f'{exp_clients} раскрывающихся блоков с номерами всех '
                f'{len(CONTRACTS)} договоров',
                f'блоков: {collapses}, цели: '
                f'{"все" if targets_ok else "не все"}, номера: '
                f'{"все" if numbers_ok else "не все"}')

    resp = web.get(f'/contracts?client={client_ids["ООО «Ромашка-Т»"]}'
                   f'&status=active')
    page = resp.get_data(as_text=True)
    m = re.search(r'Договоры\s*<span class="text-muted">\((\d+)\)</span>', page)
    got = int(m.group(1)) if m else None
    exp = sum(1 for c in CONTRACTS
              if c[0] == 'ООО «Ромашка-Т»' and c[2] == 'active')
    steps.check(resp.status_code == 200 and got == exp,
                'Фильтр реестра по клиенту и статусу из отчёта',
                f'в реестре {exp} действующих договора клиента',
                f'HTTP {resp.status_code}, найдено: {got}')

    resp = web.get(f'/contracts?client={client_ids["ООО «Ромашка-Т»"]}'
                   f'&processed=no')
    page = resp.get_data(as_text=True)
    m = re.search(r'Договоры\s*<span class="text-muted">\((\d+)\)</span>', page)
    got = int(m.group(1)) if m else None
    exp = sum(1 for c in CONTRACTS
              if c[0] == 'ООО «Ромашка-Т»' and not c[5])
    steps.check(resp.status_code == 200 and got == exp,
                'Фильтр реестра по клиенту и «Обработан» из отчёта',
                f'в реестре {exp} необработанный договор клиента',
                f'HTTP {resp.status_code}, найдено: {got}')

    # --- Отчёт по документам (группировка по клиентам) ---------------------
    resp = web.get('/reports/documents')
    html = resp.get_data(as_text=True)
    steps.check(resp.status_code == 200 and 'Отчёт по документам' in html,
                'Отчёт по документам формируется',
                'страница отчёта открывается руководителю',
                f'HTTP {resp.status_code}')

    card_values = _card_values(html)
    exp_doc_cards = (
        len({d[0] for d in DOCUMENTS}),
        len(DOCUMENTS),
        sum(1 for d in DOCUMENTS if d[3] == 'Черновик'),
        sum(1 for d in DOCUMENTS if d[3] == 'Зарегистрирован'),
        sum(1 for d in DOCUMENTS if d[3] == 'Проведён'),
        sum(1 for d in DOCUMENTS if d[3] == 'Отменён'),
        sum(1 for d in DOCUMENTS if d[5]),
    )
    steps.check(card_values == exp_doc_cards,
                'Сводные плитки отчёта по документам',
                f'клиентов {exp_doc_cards[0]}, всего {exp_doc_cards[1]}, '
                f'черновиков {exp_doc_cards[2]}, '
                f'зарегистрировано {exp_doc_cards[3]}, '
                f'проведено {exp_doc_cards[4]}, '
                f'отменено {exp_doc_cards[5]}, '
                f'обработано {exp_doc_cards[6]}',
                f'на странице: {card_values}')

    parsed = DOCUMENT_GROUP_RE.findall(html)
    order = []
    groups = {}
    for (_, name, total, draft, registered, conducted, cancelled,
         processed, unprocessed) in parsed:
        name = name.strip()
        order.append(name)
        groups[name] = (int(total), int(draft), int(registered),
                        int(conducted), int(cancelled), int(processed),
                        int(unprocessed))
    exp_groups = {name: _document_stats(name)
                  for name in {d[0] for d in DOCUMENTS}}
    steps.check(groups == exp_groups,
                'Документы сгруппированы по клиентам',
                'для каждого клиента — всего, по статусам, обработано '
                'и не обработано',
                '; '.join(f'{n}: {groups.get(n)}' for n in exp_groups)
                if groups == exp_groups else f'получено: {groups}')

    doc_clients = {d[0] for d in DOCUMENTS}
    exp_order = (
        sorted([n for n in doc_clients if client_status[n] == 'active'],
               key=lambda n: (-_document_stats(n)[0], n))
        + sorted([n for n in doc_clients if client_status[n] != 'active'],
                 key=lambda n: (-_document_stats(n)[0], n)))
    steps.check(order == exp_order,
                'Порядок групп отчёта по документам: активные, затем архивные',
                'сначала активные по числу документов (по убыванию), '
                'при равенстве — по наименованию; архивные — в конце',
                f'порядок: {order}')

    cid = client_ids['ООО «Ромашка-Т»']
    links_ok = all(href in html for href in (
        'href="/documents"',
        f'href="/documents?status={quote("Проведён")}"',
        'href="/documents?processed=yes"',
        f'href="/clients/{cid}"',
        f'href="/documents?client={cid}"',
        f'href="/documents?client={cid}'
        f'&amp;status={quote("Проведён")}"',
        f'href="/documents?client={cid}&amp;processed=yes"',
        f'href="/documents?client={cid}&amp;processed=no"',
    ))
    steps.check(links_ok,
                'Ссылки отчёта по документам ведут в реестр и карточки',
                'плитки — в реестр с фильтром, клиент — в карточку, '
                'числа — в реестр по клиенту, статусу и признаку «Обработан»',
                'все ссылки найдены' if links_ok
                else 'часть ссылок отсутствует')

    collapses = html.count('delta-report-toggle')
    targets_ok = all(f'id="client-documents-{client_ids[name]}"' in html
                     for name in doc_clients)
    numbers_ok = all(d[2] in html for d in DOCUMENTS)
    steps.check(collapses == len(doc_clients) and targets_ok and numbers_ok,
                'Документы клиента раскрываются отдельным списком',
                f'{len(doc_clients)} раскрывающихся блоков с номерами всех '
                f'{len(DOCUMENTS)} документов',
                f'блоков: {collapses}, цели: '
                f'{"все" if targets_ok else "не все"}, номера: '
                f'{"все" if numbers_ok else "не все"}')

    resp = web.get(f'/documents?client={cid}&processed=no')
    page = resp.get_data(as_text=True)
    m = re.search(r'Документы\s*<span class="text-muted">\((\d+)\)</span>',
                  page)
    got = int(m.group(1)) if m else None
    exp = sum(1 for d in DOCUMENTS
              if d[0] == 'ООО «Ромашка-Т»' and not d[5])
    steps.check(resp.status_code == 200 and got == exp,
                'Фильтр реестра по клиенту и «Обработан» из отчёта',
                f'в реестре {exp} необработанный документ клиента',
                f'HTTP {resp.status_code}, найдено: {got}')

    # --- Общий фильтр дат во всех отчётах ----------------------------------
    page = web.get('/reports/clients').get_data(as_text=True)
    steps.check(page.count('data-range-preset=') == 3
                and 'data-range-preset="year"' in page
                and 'data-range-preset="quarter"' in page
                and 'data-range-preset="month"' in page
                and 'title="Текущий год"' in page
                and 'title="Текущий квартал"' in page
                and 'title="Текущий месяц"' in page
                and 'fa-calendar' in page
                and 'fa-calendar-day' in page
                and 'fa-calendar-days' in page
                and '>Год<' not in page
                and '>Квартал<' not in page
                and '>Месяц<' not in page,
                'В отчётах есть быстрые иконки Год/Квартал/Месяц',
                'рядом с фильтром дат расположены три иконки с подсказками',
                f'кнопок: {page.count("data-range-preset=")}')

    old_range = '01.01.2000 — 31.12.2000'
    range_start = date.today() - timedelta(days=6)
    recent_range = (f'{range_start:%d.%m.%Y} — '
                    f'{date.today():%d.%m.%Y}')
    previous_range = (f'{range_start - timedelta(days=7):%d.%m.%Y} — '
                      f'{range_start - timedelta(days=1):%d.%m.%Y}')
    card_counts = {'clients': 3, 'tasks': 4, 'contracts': 6, 'documents': 7}
    filtered_pages = {}
    for report_name in card_counts:
        resp = web.get(f'/reports/{report_name}',
                       query_string={'date_range': old_range})
        filtered_pages[report_name] = (resp.status_code,
                                       resp.get_data(as_text=True))
    zero_ok = all(
        status == 200
        and all(int(value) == 0
                for value in CARDS_RE.findall(page)[:card_counts[report_name]])
        for report_name, (status, page) in filtered_pages.items())
    empty_ok = ('Договоров нет' in filtered_pages['contracts'][1]
                and 'Документов нет' in filtered_pages['documents'][1])
    users_kept = all(
        page.count('delta-report-toggle') == len(users)
        for report_name in ('clients', 'tasks')
        for _status, page in [filtered_pages[report_name]])
    steps.check(zero_ok and empty_ok and users_kept,
                'Общий фильтр дат применён во всех отчётах',
                'диапазон без данных обнуляет плитки и группы всех отчётов, '
                'но строки пользователей остаются',
                f'плитки: {"все нулевые" if zero_ok else "есть ненулевые"}, '
                f'пустые группы: {empty_ok}, пользователи: {users_kept}')

    # --- Сравнение периодов и графики --------------------------------------
    resp = web.get('/reports/contracts', query_string={
        'date_range': recent_range, 'compare': 'previous'})
    page = resp.get_data(as_text=True)
    comparison_ok = (resp.status_code == 200
                     and 'Сравнение периодов' in page
                     and (f'Текущий: <span class="fw-semibold">'
                          f'{recent_range}</span>') in page
                     and (f'Предыдущий: <span class="fw-semibold">'
                          f'{previous_range}</span>') in page
                     and ('Предыдущий период той же длительности '
                          'сразу перед текущим.') in page
                     and page.count('delta-compare-item') == 4
                     and page.count('delta-compare-row-label') == 8
                     and 'delta-chart-bar-previous' in page
                     and page.count('delta-chart-legend') >= 1
                     and page.count('delta-chart-value-current') >= 4
                     and page.count('delta-chart-value-previous') >= 4
                     and f'Текущий · {recent_range}' in page
                     and f'Предыдущий · {previous_range}' in page
                     and 'без изменений' in page)
    steps.check(comparison_ok,
                'Сравнение периодов показывает обе даты и легенду',
                'показаны текущий и предыдущий диапазоны, подписи столбцов '
                'графика и 4 показателя с изменением',
                f'HTTP {resp.status_code}, блоков: {page.count("delta-compare-item")}, '
                f'легенд: {page.count("delta-chart-legend")}')

    # Выбор сравнения не должен сбрасываться, даже если без двух границ
    # периода сравнение пока недоступно.
    resp = web.get('/reports/contracts', query_string={'compare': 'previous'})
    page = resp.get_data(as_text=True)
    selected = re.search(r'<option value="previous"[^>]*\bselected\b', page)
    persisted_ok = (resp.status_code == 200 and selected is not None
                    and 'Для сравнения укажите обе границы периода.' in page
                    and 'Сравнение периодов' not in page)
    steps.check(persisted_ok,
                'Выбор сравнения сохраняется в select',
                'после применения фильтра без обеих границ выбранное значение '
                'остаётся, а подсказка объясняет, почему сравнение не показано',
                'выбранное значение сохраняется' if persisted_ok
                else 'значение select сбросилось')

    charts_ok = True
    for report_name in card_counts:
        resp = web.get(f'/reports/{report_name}',
                       query_string={'date_range': recent_range})
        page = resp.get_data(as_text=True)
        charts_ok = (charts_ok and resp.status_code == 200
                     and 'delta-chart' in page and 'delta-chart-col' in page
                     and 'delta-chart-value-current' in page)
    steps.check(charts_ok,
                'Графики динамики строятся во всех отчётах',
                'каждый отчёт содержит столбчатый график по месяцам',
                'графики есть' if charts_ok else 'часть графиков отсутствует')

    # --- Плитки ведут сразу в отфильтрованный реестр -----------------------
    card_links = {}
    for report_name in card_counts:
        resp = web.get(f'/reports/{report_name}',
                       query_string={'date_range': recent_range})
        page = resp.get_data(as_text=True)
        card_links[report_name] = _card_hrefs(page)
    links_ok = all(hrefs and all('date_range=' in href for href in hrefs)
                   for hrefs in card_links.values())
    steps.check(links_ok,
                'Из плиток отчёта переход идёт в отфильтрованный реестр',
                'у каждой плитки всех отчётов ссылка несёт выбранный период',
                '; '.join(f'{name}: {len(hrefs)}'
                          for name, hrefs in card_links.items()))

    draft_href = next(href for href in card_links['contracts']
                      if 'status=draft' in href)
    resp = web.get(unescape(draft_href))
    page = resp.get_data(as_text=True)
    match = re.search(r'Договоры\s*<span class="text-muted">\((\d+)\)</span>',
                      page)
    got = int(match.group(1)) if match else None
    steps.check(resp.status_code == 200 and got == 1,
                'Переход из плитки сохраняет фильтр дат в реестре',
                'в реестре договоров один проект за выбранный период',
                f'HTTP {resp.status_code}, найдено: {got}, ссылка: {draft_href}')

    # --- Экспорт отчётов ----------------------------------------------------
    export_ok = True
    for report_name in card_counts:
        resp = web.get(f'/reports/export/{report_name}/csv',
                       query_string={'date_range': old_range})
        export_ok = (export_ok and resp.status_code == 200
                     and len(_csv_rows(resp)) == 1)
    resp = web.get('/reports/export/contracts/csv',
                   query_string={'date_range': recent_range})
    rows = _csv_rows(resp)
    export_ok = (export_ok and resp.status_code == 200 and len(rows) == 2
                 and rows[0][1] == 'Номер'
                 and any('Д-004' in row for row in rows[1:]))
    resp = web.get('/reports/export/contracts/xlsx',
                   query_string={'date_range': recent_range})
    with zipfile.ZipFile(io.BytesIO(resp.data)) as archive:
        sheet = archive.read('xl/worksheets/sheet1.xml').decode('utf-8')
    disposition = resp.headers.get('Content-Disposition', '')
    # Имя файла в HTTP-заголовке должно быть ASCII: неэкранированная
    # кириллица ломает отдачу файла через werkzeug/dev-сервер.
    export_ok = (export_ok and resp.status_code == 200
                 and sheet.count('<row ') == 2 and 'Д-004' in sheet
                 and 'filename*=UTF-8' in disposition
                 and disposition.isascii())
    steps.check(export_ok,
                'Экспорт отчётов в CSV и Excel учитывает фильтр дат',
                'CSV с BOM и «;», XLSX — корректный zip с листом отчёта; '
                'пустой период даёт только заголовок',
                'экспорт работает' if export_ok else 'экспорт некорректен')

    # --- Отчёты доступны только руководителю --------------------------------
    emp = app.test_client()
    login(emp, EMAILS['emp1'])
    denied = [path for path in ('/reports/clients', '/reports/tasks',
                                '/reports/contracts', '/reports/documents',
                                '/reports/export/contracts/csv',
                                '/reports/export/contracts/xlsx')
              if emp.get(path).status_code != 403]
    steps.check(not denied,
                'Отчёты доступны только руководителю',
                'сотруднику страницы отчётов запрещены (HTTP 403)',
                f'доступны сотруднику: {denied}' if denied
                else 'все страницы отчётов вернули HTTP 403')

    conclusion = ('Отчёты формируются, агрегированные показатели соответствуют '
                  'данным.' if steps.passed
                  else 'Отчёты формируются некорректно — см. шаги.')
    return steps, conclusion
