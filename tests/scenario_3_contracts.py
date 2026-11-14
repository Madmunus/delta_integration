"""Сценарий 3 (ФТ-3): загрузка договоров клиента из «1С:Бухгалтерия».

Для клиента с собственными реквизитами подключения к «1С:Бухгалтерия»
загружается справочник Catalog_Договоры. Проверяются реквизиты договора,
привязка к контрагенту, статусы и отсутствие дублирования.
"""
from datetime import date, timedelta
from decimal import Decimal

from engine.models import Client, Contract, Contractor, db
from engine.sync import sync_contracts
from tests.base import (EMAILS, Steps, create_scenario_app, fake_1c,
                        login, seed_users)

FT = 'ФТ-3'
TITLE = 'Загрузка договоров клиента из «1С:Бухгалтерия»'
DESCRIPTION = ('Загрузка справочника Catalog_Договоры по индивидуальным '
               'реквизитам подключения клиента. Результат: договоры созданы, '
               'привязаны к контрагенту, статусы и суммы соответствуют 1С.')

CLIENT_NAME = 'ООО «Договор-Тест»'
CONTRACTOR_KEY = 'cccccccc-1111-1111-1111-111111111111'
CLIENT_KEY = 'dddddddd-1111-1111-1111-111111111111'

# Catalog_Договоры: Ref_Key, Контрагент, Наименование, Дата,
# СрокДействия, Статус, Сумма, Description.
CONTRACTS = [
    {'Ref_Key': 'eeeeeeee-0001-0000-0000-000000000001',
     'Контрагент': CONTRACTOR_KEY, 'Наименование': 'ДГ-001',
     'Дата': '2026-01-15T00:00:00', 'СрокДействия': '2026-12-31T00:00:00',
     'Статус': 'Действует', 'Сумма': 30000,
     'Description': 'Бухгалтерское сопровождение'},
    {'Ref_Key': 'eeeeeeee-0002-0000-0000-000000000002',
     'Контрагент': CONTRACTOR_KEY, 'Наименование': 'ДГ-002',
     'Дата': '2025-02-01T00:00:00', 'СрокДействия': '2026-01-31T00:00:00',
     'Статус': 'Закрыт', 'Сумма': 18000,
     'Description': 'Сопровождение за 2025 год'},
    {'Ref_Key': 'eeeeeeee-0003-0000-0000-000000000003',
     'Контрагент': 'unknown-contractor', 'Наименование': 'ДГ-003',
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
        c1_id = c1.id if c1 else None
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

    # --- Шаг 2.1. Внутреннее поле «Обработан» при импорте ---------------
    with app.app_context():
        processed_values = [c.processed for c in
                            Contract.query.order_by(Contract.id).all()]
    steps.check(processed_values
                and all(value is False for value in processed_values),
                'При импорте договоров поле «Обработан» равно 0',
                'у всех загруженных из 1С договоров признак «Обработан» = Нет',
                f'значения: {processed_values}')

    # --- Шаг 3. Договоры видны в карточке клиента ----------------------
    web = app.test_client()
    login(web, EMAILS['admin'])
    html = web.get(f'/clients/{client_id}').get_data(as_text=True)
    steps.check('ДГ-001' in html and 'ДГ-002' in html and 'ДГ-003' in html,
                'Договоры отображаются в карточке клиента',
                'на вкладке «Договоры» — все три договора',
                'договоры отображаются' if 'ДГ-001' in html
                else 'договоры не отображаются')
    steps.check(f'href="/contractors/{contractor_id}"' in html,
                'В столбце «Контрагент» есть ссылка на карточку контрагента',
                'наименование контрагента в договоре ведёт в его карточку',
                'ссылка на контрагента присутствует'
                if f'href="/contractors/{contractor_id}"' in html
                else 'ссылка на контрагента отсутствует')

    # --- Шаг 3.1. Карточка договора ------------------------------------
    card_html = web.get(f'/contracts/{c1_id}').get_data(as_text=True)
    card_ok = all(value in card_html for value in (
        'Договор № ДГ-001', 'Бухгалтерское сопровождение',
        'Обработан', 'Нет', f'href="/clients/{client_id}"',
        f'href="/contractors/{contractor_id}"'))
    steps.check(card_ok and f'/contracts/{c1_id}/edit' in card_html,
                'Карточка договора открывается и содержит реквизиты',
                'номер, клиент, контрагент, описание, признак «Обработан» '
                'и ссылка на редактирование',
                'карточка договора оформлена корректно' if card_ok
                else 'карточка договора неполная')

    # --- Шаг 3.2. При редактировании доступен только «Обработан» -------
    edit_html = web.get(f'/contracts/{c1_id}/edit').get_data(as_text=True)
    readonly_ok = (all(value in edit_html for value in (
        'ДГ-001', 'Бухгалтерское сопровождение', 'Редактируемые поля',
        'name="processed"'))
        and 'name="number"' not in edit_html
        and 'name="amount"' not in edit_html)
    steps.check(readonly_ok,
                'При редактировании договора доступен только признак «Обработан»',
                'остальные реквизиты показаны только для чтения',
                'поля только для чтения' if readonly_ok
                else 'редактируемые поля не ограничены')
    resp = web.post(f'/contracts/{c1_id}/edit',
                    data={'processed': 'y'}, follow_redirects=True)
    saved_html = resp.get_data(as_text=True)
    with app.app_context():
        saved = db.session.get(Contract, c1_id)
        saved_ok = (saved is not None and saved.processed is True
                    and saved.number == 'ДГ-001'
                    and saved.description == 'Бухгалтерское сопровождение')
        saved_value = saved.processed if saved else None
    steps.check(resp.status_code == 200 and saved_ok and 'Да' in saved_html,
                'Признак «Обработан» сохраняется, остальные поля не меняются',
                'после сохранения «Обработан» = Да, реквизиты прежние',
                f'Обработан: {saved_value}')

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

    # --- Шаг 6. Фильтр и пагинация реестра договоров --------------------
    with app.app_context():
        for i in range(1, 23):
            db.session.add(Contract(
                number=f'ПГ-{i:03d}', client_id=client_id,
                signed_date=date(2024, 1, 1) + timedelta(days=i),
                status='active', status_1c='Действует',
                amount=Decimal('1000.00'),
                description='Договор для проверки пагинации'))
        db.session.commit()
        total = Contract.query.count()

    def _body_rows(html):
        return html.split('<tbody>', 1)[1].split('</tbody>', 1)[0].count('<tr>')

    page1 = web.get('/contracts').get_data(as_text=True)
    page2 = web.get('/contracts',
                    query_string={'page': 2}).get_data(as_text=True)
    rows1, rows2 = _body_rows(page1), _body_rows(page2)
    steps.check(total == 25 and rows1 == 20 and rows2 == 5
                and 'page=2' in page1,
                'В реестре договоров работает пагинация по 20 записей',
                '25 договоров: 20 на первой странице и 5 на второй',
                f'всего {total}, на странице 1 — {rows1}, '
                f'на странице 2 — {rows2}')

    search_html = web.get('/contracts',
                          query_string={'q': 'Бухгалтерское'}).get_data(as_text=True)
    status_html = web.get('/contracts',
                          query_string={'status': 'closed'}).get_data(as_text=True)
    steps.check('ДГ-001' in search_html
                and 'ДГ-002' not in search_html
                and 'ДГ-003' not in search_html,
                'В реестре договоров работает поиск по всем полям',
                'поиск «Бухгалтерское» находит только договор ДГ-001',
                'найден только ДГ-001' if 'ДГ-001' in search_html
                and 'ДГ-002' not in search_html else 'фильтр по полям не работает')
    registry_ok = ('<th>Номер</th>' in page1
                   and '>Действия<' in page1
                   and f'href="/contracts/{c1_id}"' in search_html
                   and f'href="/contracts/{c1_id}/edit"' in search_html
                   and 'fa-pen' in search_html)
    steps.check(registry_ok,
                'В реестре договоров есть карточка и кнопка редактирования',
                'столбец «Номер» ведёт в карточку договора, в конце таблицы — '
                '«Действия» с кнопкой-карандашом',
                'столбцы и ссылки присутствуют' if registry_ok
                else 'столбцы или ссылки отсутствуют')
    steps.check(f'href="/contractors/{contractor_id}"' in search_html,
                'В реестре договоров есть ссылка на карточку контрагента',
                'наименование контрагента в строке таблицы ведёт в его карточку',
                'ссылка на контрагента присутствует'
                if f'href="/contractors/{contractor_id}"' in search_html
                else 'ссылка на контрагента отсутствует')
    steps.check('ДГ-002' in status_html
                and 'ДГ-001' not in status_html
                and 'ДГ-003' not in status_html
                and 'name="status"' in page1 and '<select' in page1,
                'Фильтр по статусу договора оформлен выпадающим списком',
                'select «Все статусы / Проект / Действующие / Завершённые»; '
                'статус «Завершённые» оставляет только ДГ-002',
                'select и фильтр по статусу работают'
                if 'ДГ-002' in status_html and 'ДГ-001' not in status_html
                else 'фильтр по статусу не работает')

    conclusion = ('Договоры загружаются из «1С:Бухгалтерия» по реквизитам '
                  'клиента, дублирование отсутствует.'
                  if steps.passed else 'Загрузка договоров работает некорректно.')
    return steps, conclusion
