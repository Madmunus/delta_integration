"""Сценарий 7 (ФТ-5): загрузка договоров клиента из «1С:Бухгалтерия».

Для клиента с собственными реквизитами подключения к «1С:Бухгалтерия»
загружается справочник Catalog_Договоры. Проверяются реквизиты договора,
привязка к контрагенту, статусы и отсутствие дублирования.
"""
from datetime import date
from decimal import Decimal

from engine.models import Client, Contract, Contractor, db
from services.sync import sync_contracts
from tests.base import (EMAILS, Steps, create_scenario_app, fake_1c,
                        login, seed_users)

FT = 'ФТ-5'
TITLE = 'Загрузка договоров клиента из «1С:Бухгалтерия»'
DESCRIPTION = ('Загрузка справочника Catalog_Договоры по индивидуальным '
               'реквизитам подключения клиента. Результат: договоры созданы, '
               'привязаны к контрагенту, статусы и суммы соответствуют 1С.')

CLIENT_NAME = 'ООО «Договор-Тест»'
CONTRACTOR_KEY = 'cccccccc-1111-1111-1111-111111111111'
CLIENT_KEY = 'dddddddd-1111-1111-1111-111111111111'

# Catalog_Договоры: Ref_Key, Контрагент_Key, Наименование, Дата,
# СрокДействия, Статус, Сумма, Description.
CONTRACTS = [
    {'Ref_Key': 'eeeeeeee-0001-0000-0000-000000000001',
     'Контрагент_Key': CONTRACTOR_KEY, 'Наименование': 'ДГ-001',
     'Дата': '2026-01-15T00:00:00', 'СрокДействия': '2026-12-31T00:00:00',
     'Статус': 'Действует', 'Сумма': 30000,
     'Description': 'Бухгалтерское сопровождение'},
    {'Ref_Key': 'eeeeeeee-0002-0000-0000-000000000002',
     'Контрагент_Key': CONTRACTOR_KEY, 'Наименование': 'ДГ-002',
     'Дата': '2025-02-01T00:00:00', 'СрокДействия': '2026-01-31T00:00:00',
     'Статус': 'Закрыт', 'Сумма': 18000,
     'Description': 'Сопровождение за 2025 год'},
    {'Ref_Key': 'eeeeeeee-0003-0000-0000-000000000003',
     'Контрагент_Key': 'unknown-contractor', 'Наименование': 'ДГ-003',
     'Дата': '2026-03-01T00:00:00', 'СрокДействия': '2027-02-28T00:00:00',
     'Статус': 'Проект', 'Сумма': 5000,
     'Description': 'Проект договора'},
]


def run():
    app = create_scenario_app()
    steps = Steps()
    seed_users(app)

    with app.app_context():
        client = Client(
            name=CLIENT_NAME, inn='7705555555', status='active',
            external_ref=CLIENT_KEY,
            odata_url='http://client-1c.local/odata/standard.odata/',
            odata_username='client-user', odata_password='client-secret')
        contractor = Contractor(
            external_ref=CONTRACTOR_KEY, full_name='ООО «Договор-Тест»',
            inn='7705555555', kpp='770501001', description='Контрагент')
        db.session.add_all([client, contractor])
        db.session.commit()
        client_id = client.id
        contractor_id = contractor.id

    # --- Шаг 1. Загрузка договоров по реквизитам клиента ---------------
    with app.app_context():
        client = db.session.get(Client, client_id)
        with fake_1c(contracts=CONTRACTS):
            stats = sync_contracts(client)
        total = Contract.query.count()
    steps.check(stats['created'] == 3 and total == 3,
                'Договоры загружены из Catalog_Договоры',
                'создано 3 договора',
                f"создано {stats['created']}, в базе {total}")

    # --- Шаг 2. Реквизиты договора и привязка к контрагенту -------------
    with app.app_context():
        c1 = Contract.query.filter_by(number='ДГ-001').first()
        c2 = Contract.query.filter_by(number='ДГ-002').first()
        c3 = Contract.query.filter_by(number='ДГ-003').first()
        c1_data = (c1.signed_date, c1.end_date, c1.amount, c1.description,
                   c1.contractor_id, c1.contractor_name) if c1 else None
        c1_status, c2_status, c3_status = (
            c1.status if c1 else None,
            c2.status if c2 else None,
            c3.status if c3 else None)
        c1_status_1c, c2_status_1c, c3_status_1c = (
            c1.status_1c if c1 else '—',
            c2.status_1c if c2 else '—',
            c3.status_1c if c3 else '—')
        c3_contractor_id = c3.contractor_id if c3 else '—'
    steps.check(c1_data is not None
                and c1_data[0] == date(2026, 1, 15)
                and c1_data[1] == date(2026, 12, 31)
                and c1_data[2] == Decimal('30000.00')
                and c1_data[3] == 'Бухгалтерское сопровождение'
                and c1_data[4] == contractor_id,
                'Реквизиты договора перенесены из 1С',
                'дата, срок действия, сумма, описание и контрагент совпадают',
                (f'ДГ-001: {c1_data[0]}–{c1_data[1]}, сумма {c1_data[2]}, '
                 f'контрагент {c1_data[5]}'
                 if c1_data else 'договор ДГ-001 не найден'))
    steps.check(c1_status == 'active' and c2_status == 'closed'
                and c3_status == 'draft' and c3_contractor_id is None,
                'Статусы договоров приведены к внутренним значениям',
                '«Действует» → active, «Закрыт» → closed, «Проект» → draft; '
                'неизвестный контрагент не привязывается',
                f'{c1_status_1c} → {c1_status}; '
                f'{c2_status_1c} → {c2_status}; '
                f'{c3_status_1c} → {c3_status}')

    # --- Шаг 3. Договоры видны в карточке клиента ----------------------
    web = app.test_client()
    login(web, EMAILS['admin'])
    html = web.get(f'/clients/{client_id}').get_data(as_text=True)
    steps.check('ДГ-001' in html and 'ДГ-002' in html and 'ДГ-003' in html,
                'Договоры отображаются в карточке клиента',
                'на вкладке «Договоры» — все три договора',
                'договоры отображаются' if 'ДГ-001' in html
                else 'договоры не отображаются')

    # --- Шаг 4. Повторная загрузка не создаёт дублей -------------------
    with app.app_context():
        client = db.session.get(Client, client_id)
        with fake_1c(contracts=CONTRACTS):
            stats = sync_contracts(client)
        total_after = Contract.query.count()
    steps.check(stats['created'] == 0 and stats['updated'] == 3
                and total_after == total,
                'Повторная загрузка обновляет договоры без дублей',
                'создано 0, обновлено 3, количество договоров не изменилось',
                f"создано {stats['created']}, обновлено {stats['updated']}, "
                f'в базе {total_after}')

    # --- Шаг 5. Без подключения договоры не загружаются ----------------
    with app.app_context():
        plain = Client(name='ООО «Без 1С»', inn='7706666666', status='active')
        db.session.add(plain)
        db.session.commit()
        plain_id = plain.id
        plain = db.session.get(Client, plain_id)
        stats = sync_contracts(plain)
    steps.check(stats['created'] == 0 and stats['errors'],
                'Клиент без подключения к 1С:Бухгалтерия обрабатывается корректно',
                'загрузка не выполняется, выводится предупреждение',
                f"создано {stats['created']}, ошибок {len(stats['errors'])}")

    conclusion = ('Договоры загружаются из «1С:Бухгалтерия» по реквизитам '
                  'клиента, дублирование отсутствует.'
                  if steps.passed else 'Загрузка договоров работает некорректно.')
    return steps, conclusion
