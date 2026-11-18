"""Регрессия числа SQL-запросов на ключевых экранах (ФТ: производительность).

Сценарий считает только SELECT/WITH на стороне базы и ловит возврат N+1
или случайную потерю `joinedload`/скалярных подзапросов. Пороги оставляют
небольшой запас против текущего числа запросов, но не маскируют регрессию.
"""
from datetime import date, timedelta
from sqlalchemy import event

from engine.models import Client, Contract, Contractor, Document, Task, db
from tests.base import EMAILS, Steps, create_scenario_app, login, seed_users

FT = 'Производительность'
TITLE = 'Регрессия числа SQL-запросов на ключевых экранах'
DESCRIPTION = (
    'Проверяет, что реестры, отчёты, карточки и страница 1С не делают '
    'лишних SELECT-запросов после оптимизаций.'
)

# (путь, роль, бюджет SELECT). Роль None — неавторизованный запрос.
BUDGETS = [
    ('/login', None, 1),
    ('/dashboard', 'admin', 14),
    ('/clients', 'admin', 6),
    ('/clients', 'emp1', 5),
    ('/clients/1', 'admin', 9),
    ('/contracts', 'admin', 8),
    ('/documents', 'admin', 8),
    ('/tasks', 'admin', 8),
    ('/users', 'admin', 4),
    ('/reports/clients', 'admin', 5),
    ('/reports/tasks', 'admin', 5),
    ('/reports/contracts', 'admin', 5),
    ('/reports/documents', 'admin', 5),
    ('/bases', 'admin', 7),
]


def _measure(app, client, path):
    """Вернуть (HTTP-код, число SELECT/WITH) для одного GET-запроса."""
    counter = {'select': 0}

    def before_cursor_execute(conn, cursor, statement, parameters,
                              context, executemany):
        if statement.lstrip().upper().startswith(('SELECT', 'WITH')):
            counter['select'] += 1

    with app.app_context():
        event.listen(db.engine, 'before_cursor_execute',
                     before_cursor_execute)
        try:
            response = client.get(path)
        finally:
            event.remove(db.engine, 'before_cursor_execute',
                         before_cursor_execute)
    return response.status_code, counter['select']


def _seed_data(app, users):
    with app.app_context():
        client = Client(name='Бюджетный клиент', inn='7700000000',
                        status='active', responsible_id=users['emp1'])
        contractor = Contractor(full_name='Бюджетный контрагент',
                                external_ref='budget-contractor')
        db.session.add_all([client, contractor])
        db.session.flush()

        db.session.add_all([
            Contract(number='БЮД-1', client_id=client.id,
                     contractor_id=contractor.id, status='active',
                     signed_date=date.today() - timedelta(days=1)),
            Document(client_id=client.id, contractor_id=contractor.id,
                     doc_type='Счет', number='СЧ-БЮД-1',
                     doc_date=date.today() - timedelta(days=1),
                     status='Проведён'),
            Task(title='Бюджетная задача', assignee_id=users['emp1'],
                 client_id=client.id, status='new',
                 due_date=date.today() + timedelta(days=1)),
        ])
        db.session.commit()


def run():
    app = create_scenario_app()
    steps = Steps()
    users = seed_users(app)
    _seed_data(app, users)

    sessions = {'admin': app.test_client(), 'emp1': app.test_client()}
    login(sessions['admin'], EMAILS['admin'])
    login(sessions['emp1'], EMAILS['emp1'])
    anonymous = app.test_client()

    for path, role, budget in BUDGETS:
        web = anonymous if role is None else sessions[role]
        # Warm-up снимает разовые SELECT-ы и UPDATE сессии из before_request.
        web.get(path)
        status, count = _measure(app, web, path)
        steps.check(
            status == 200 and count <= budget,
            f'{path} ({role or "anonymous"}) — не более {budget} SELECT',
            f'HTTP 200, SELECT ≤ {budget}',
            f'HTTP {status}, SELECT {count}',
        )

    conclusion = ('Ключевые экраны укладываются в бюджет SQL-запросов.'
                  if steps.passed else
                  'Обнаружен рост числа SQL-запросов — см. детали шагов.')
    return steps, conclusion
