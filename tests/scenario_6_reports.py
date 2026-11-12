"""Сценарий 5 (ФТ-6): формирование отчётов и проверка агрегатов.

Формирование отчёта по клиентской базе и по задачам; проверка
корректности агрегированных показателей. Результат: отчёты
формируются, показатели соответствуют данным.
"""
import re
from datetime import date, timedelta

from engine.models import Client, Task, db
from tests.base import (EMAILS, Steps, create_scenario_app, login,
                        seed_users)

FT = 'ФТ-6'
TITLE = 'Формирование отчётов по клиентской базе и по задачам'
DESCRIPTION = ('Формирование отчёта по клиентской базе и по задачам; '
               'проверка корректности агрегированных показателей. '
               'Результат: отчёты формируются, показатели соответствуют данным.')

FIO = {
    'admin': 'Админов Аркадий Петрович',
    'emp1': 'Сотрудников Сергей Иванович',
    'emp2': 'Работникова Ольга Львовна',
}

# Датасет: 5 клиентов (4 активных, 1 архивный, 1 без ответственного)
CLIENTS = [
    # (наименование, ИНН, статус, ответственный)
    ('ООО «Ромашка-Т»', '7707777701', 'active', 'emp1'),
    ('ООО «Лютик-Т»', '7707777702', 'active', 'emp1'),
    ('ООО «Василёк-Т»', '7707777703', 'active', 'emp2'),
    ('ООО «Лопух-Т»', '7707777704', 'archived', 'emp2'),
    ('ООО «Незабудка-Т»', '7707777705', 'active', None),
]

# Датасет: 6 задач с известными исполнителями и статусами
TASKS = [
    # (клиент, исполнитель, статус)
    ('ООО «Ромашка-Т»', 'emp1', 'done'),
    ('ООО «Лютик-Т»', 'emp1', 'in_progress'),
    ('ООО «Ромашка-Т»', 'emp1', 'overdue'),
    ('ООО «Василёк-Т»', 'emp2', 'done'),
    ('ООО «Василёк-Т»', 'emp2', 'new'),
    ('ООО «Ромашка-Т»', None, 'overdue'),
]

# Разметка шаблонов отчётов (для извлечения показателей со страницы)
CARDS_RE = re.compile(r'<div class="display-6">(\d+)</div>')
CLIENT_ROWS_RE = re.compile(
    r'<td>([^<]+)</td>\s*<td class="text-end">(\d+)</td>\s*'
    r'<td class="text-end">(\d+)</td>')
TASK_ROWS_RE = re.compile(
    r'<td>([^<]+)</td>\s*<td class="text-end text-success">(\d+)</td>\s*'
    r'<td class="text-end">(\d+)</td>\s*<td class="text-end[^"]*">(\d+)</td>\s*'
    r'<td class="text-end">(\d+)</td>')
NO_ASSIGNEE_RE = re.compile(
    r'без исполнителя</em></td>\s*<td class="text-end">(\d+)</td>\s*'
    r'<td class="text-end">(\d+)</td>\s*<td class="text-end">(\d+)</td>')


def _clients_of(responsible):
    """Ожидаемое число клиентов сотрудника: (всего, активных)."""
    own = [c for c in CLIENTS if c[3] == responsible]
    return len(own), sum(1 for c in own if c[2] == 'active')


def _task_counts(assignee):
    """Ожидаемое число задач исполнителя: (выполнено, в работе, просрочено)."""
    done = sum(1 for _, a, s in TASKS if a == assignee and s == 'done')
    work = sum(1 for _, a, s in TASKS
               if a == assignee and s in ('new', 'in_progress'))
    over = sum(1 for _, a, s in TASKS if a == assignee and s == 'overdue')
    return done, work, over


def run():
    app = create_scenario_app()
    steps = Steps()
    users = seed_users(app)

    with app.app_context():
        client_ids = {}
        for name, inn, status, resp in CLIENTS:
            db.session.add(Client(
                name=name, inn=inn, status=status,
                responsible_id=users[resp] if resp else None))
        db.session.flush()
        for name, _, _, _ in CLIENTS:
            client_ids[name] = Client.query.filter_by(name=name).first().id
        for client_name, assignee, status in TASKS:
            db.session.add(Task(
                title=f'Задача для {client_name}',
                client_id=client_ids[client_name],
                assignee_id=users[assignee] if assignee else None,
                due_date=date.today() + timedelta(days=3),
                status=status))
        db.session.commit()

    steps.note('Подготовлен контрольный датасет',
               f'{len(CLIENTS)} клиентов ({sum(1 for c in CLIENTS if c[2] == "active")} '
               f'активных), {len(TASKS)} задач, 3 пользователя')

    web = app.test_client()
    login(web, EMAILS['admin'])

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

    rows = {fio.strip(): (int(a), int(b))
            for fio, a, b in CLIENT_ROWS_RE.findall(html)}
    exp_rows = {FIO['emp1']: _clients_of('emp1'), FIO['emp2']: _clients_of('emp2')}
    ok = all(rows.get(fio) == val for fio, val in exp_rows.items())
    steps.check(ok,
                'Распределение клиентов по ответственным',
                f'{FIO["emp1"]} — {_clients_of("emp1")[0]} кл. '
                f'({_clients_of("emp1")[1]} активных), '
                f'{FIO["emp2"]} — {_clients_of("emp2")[0]} кл. '
                f'({_clients_of("emp2")[1]} активных)',
                '; '.join(f'{fio}: {rows.get(fio)}' for fio in exp_rows))

    # --- Отчёт по задачам --------------------------------------------------
    resp = web.get('/reports/tasks')
    html = resp.get_data(as_text=True)
    steps.check(resp.status_code == 200 and 'Отчёт по задачам' in html,
                'Отчёт по задачам формируется',
                'страница отчёта открывается руководителю',
                f'HTTP {resp.status_code}')

    rows = {fio.strip(): tuple(int(v) for v in nums)
            for fio, *nums in TASK_ROWS_RE.findall(html)}
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

    m = NO_ASSIGNEE_RE.search(html)
    got = tuple(int(v) for v in m.groups()) if m else None
    exp = _task_counts(None)
    steps.check(got == exp,
                'Задачи без исполнителя учтены отдельной строкой',
                f'выполнено {exp[0]}, в работе {exp[1]}, просрочено {exp[2]}',
                f'строка «без исполнителя»: {got}')

    # --- Отчёты доступны только руководителю --------------------------------
    emp = app.test_client()
    login(emp, EMAILS['emp1'])
    resp = emp.get('/reports/clients')
    steps.check(resp.status_code == 403,
                'Отчёты доступны только руководителю',
                'сотруднику страница отчёта запрещена (HTTP 403)',
                f'HTTP {resp.status_code}')

    conclusion = ('Отчёты формируются, агрегированные показатели соответствуют '
                  'данным.' if steps.passed
                  else 'Отчёты формируются некорректно — см. шаги.')
    return steps, conclusion
