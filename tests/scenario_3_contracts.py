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

# Catalog_Договоры архивного клиента: открытые договоры должны
# загрузиться как завершённые.
ARCHIVED_CONTRACTS = [
    {'Ref_Key': 'eeeeeeee-0004-0000-0000-000000000004',
     'Контрагент': '', 'Наименование': 'АРХ-001',
     'Дата': '2026-01-10T00:00:00', 'СрокДействия': '2026-12-31T00:00:00',
     'Статус': 'Действует', 'Сумма': 10000,
     'Description': 'Действующий договор архивного клиента'},
    {'Ref_Key': 'eeeeeeee-0005-0000-0000-000000000005',
     'Контрагент': '', 'Наименование': 'АРХ-002',
     'Дата': '2026-02-01T00:00:00', 'СрокДействия': '2026-12-31T00:00:00',
     'Статус': 'Проект', 'Сумма': 5000,
     'Description': 'Проект договора архивного клиента'},
    {'Ref_Key': 'eeeeeeee-0006-0000-0000-000000000006',
     'Контрагент': '', 'Наименование': 'АРХ-003',
     'Дата': '2025-01-01T00:00:00', 'СрокДействия': '2025-12-31T00:00:00',
     'Статус': 'Закрыт', 'Сумма': 7000,
     'Description': 'Завершённый договор архивного клиента'},
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
            inn='7705555555', kpp='770501001', notes='Контрагент')
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
        'Обработан', 'Нет', 'Дата создания', 'Дата обновления',
        f'href="/clients/{client_id}"',
        f'href="/contractors/{contractor_id}"'))
    steps.check(card_ok and f'/contracts/{c1_id}/edit' in card_html,
                'Карточка договора открывается и содержит реквизиты',
                'номер, клиент, контрагент, описание, признак «Обработан», '
                'даты создания и обновления и ссылка на редактирование',
                'карточка договора оформлена корректно' if card_ok
                else 'карточка договора неполная')
    key_pos = card_html.find('>Ключ 1С<')
    number_pos = card_html.find('>Номер<')
    steps.check(0 <= key_pos < number_pos,
                'В карточке договора «Ключ 1С» выводится первым',
                'поле «Ключ 1С» стоит перед номером договора',
                '«Ключ 1С» — первое поле' if 0 <= key_pos < number_pos
                else f'порядок полей нарушен: Ключ 1С={key_pos}, Номер={number_pos}')
    with app.app_context():
        contract_mapping_ok = (
            Contract.STATUS_BADGES == {
                'draft': 'text-bg-light text-dark',
                'active': 'text-bg-success',
                'closed': 'text-bg-secondary',
            }
            and Contract(status='draft').status_badge_class
            == 'text-bg-light text-dark'
            and Contract(status='active').status_badge_class == 'text-bg-success'
            and Contract(status='closed').status_badge_class == 'text-bg-secondary'
            and Contract(status='unknown').status_badge_class == 'text-bg-dark')
    steps.check(contract_mapping_ok,
                'Для статусов договора задано соответствие цветных бейджей',
                '«проект» — светлый, «действующий» — зелёный, '
                '«завершённый» — серый; для неизвестного — нейтральный тёмный',
                'соответствие статусов и цветов настроено'
                if contract_mapping_ok
                else 'соответствие статусов и цветов неверно')

    # --- Шаг 3.2. При редактировании доступны «Обработан» и «Заметки» ---
    edit_html = web.get(f'/contracts/{c1_id}/edit').get_data(as_text=True)
    readonly_ok = (all(value in edit_html for value in (
        'ДГ-001', 'Бухгалтерское сопровождение', 'Редактируемые поля',
        'name="processed"', 'name="notes"', 'Заметки', 'Просмотр'))
        and 'name="number"' not in edit_html
        and 'name="amount"' not in edit_html)
    steps.check(readonly_ok,
                'При редактировании договора доступны «Обработан» и «Заметки»',
                'остальные реквизиты показаны только для чтения',
                'поля «Обработан» и «Заметки» редактируются' if readonly_ok
                else 'редактируемые поля не ограничены')
    data_block = edit_html.split('>Данные<', 1)[-1].split('>Редактируемые поля<', 1)[0]
    data_ok = ('Обработан' not in data_block and 'Заметки' not in data_block)
    steps.check(data_ok,
                'В блоке «Данные» карточки редактирования нет внутренних полей',
                'в «Данных» не дублируются «Обработан» и «Заметки» — они '
                'выводятся только в редактируемых полях',
                'внутренние поля в «Данных» отсутствуют' if data_ok
                else 'в «Данных» остались «Обработан» или «Заметки»')
    resp = web.post(f'/contracts/{c1_id}/edit',
                    data={'processed': 'y',
                          'notes': 'Проверка заметок договора'},
                    follow_redirects=True)
    saved_html = resp.get_data(as_text=True)
    with app.app_context():
        saved = db.session.get(Contract, c1_id)
        saved_ok = (saved is not None and saved.processed is True
                    and saved.number == 'ДГ-001'
                    and saved.description == 'Бухгалтерское сопровождение'
                    and saved.notes == 'Проверка заметок договора')
        saved_value = saved.processed if saved else None
    steps.check(resp.status_code == 200 and saved_ok and 'Да' in saved_html
                and 'Проверка заметок договора' in saved_html,
                'Признак «Обработан» и заметки сохраняются, реквизиты прежние',
                'после сохранения «Обработан» = Да, заметки видны в карточке, '
                'остальные реквизиты не изменились',
                f'Обработан: {saved_value}, заметки: '
                f'{saved.notes if saved else None}')

    # --- Шаг 3.3. Фильтр и столбец «Обработан» в реестре договоров ------
    processed_page = web.get('/contracts').get_data(as_text=True)
    processed_yes = web.get('/contracts', query_string={
        'processed': 'yes'}).get_data(as_text=True)
    processed_no = web.get('/contracts', query_string={
        'processed': 'no'}).get_data(as_text=True)
    registry_head = processed_page.split('<thead', 1)[1].split('</thead>', 1)[0]
    processed_ok = ('name="processed"' in processed_page
                    and 'Обработан' in registry_head
                    and 'class="badge text-bg-success">Да</span>' in processed_yes
                    and 'class="badge text-bg-secondary">Нет</span>' in processed_no
                    and 'ДГ-001' in processed_yes
                    and 'ДГ-002' not in processed_yes
                    and 'ДГ-003' not in processed_yes
                    and 'ДГ-001' not in processed_no
                    and 'ДГ-002' in processed_no
                    and 'ДГ-003' in processed_no
                    and 'value="yes" selected' in processed_yes)
    steps.check(processed_ok,
                'В реестре договоров есть фильтр и столбец «Обработан»',
                'выпадающий список «Обработан: Да / Нет» и столбец с бейджем; '
                'фильтр «Да» оставляет только обработанный ДГ-001, «Нет» — '
                'остальные договоры',
                'фильтр и столбец «Обработан» работают'
                if processed_ok else 'фильтр или столбец «Обработан» не работают')

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
    date_html = web.get('/contracts', query_string={
        'date_range': '15.01.2026 — 15.01.2026'}).get_data(as_text=True)
    date_filter_ok = ('ДГ-001' in date_html and 'ДГ-002' not in date_html
                      and 'ДГ-003' not in date_html)
    steps.check('name="date_range"' in page1
                and 'title="Дата: с — по"' in page1 and date_filter_ok,
                'В реестре договоров фильтр по дате работает по полю «Дата»',
                'единое поле «Дата: с — по»; диапазон 15.01.2026 оставляет '
                'только ДГ-001',
                'фильтр по дате договора работает' if date_filter_ok
                else 'фильтр по дате договора не работает')
    # --- Шаг 6.1. Фильтры по клиенту и контрагенту ----------------------
    with app.app_context():
        other_client = Client(name='ООО «Второй-Тест»', inn='7707777777',
                              status='active')
        other_contractor = Contractor(
            full_name='ООО «Второй контрагент-Т»', inn='7707777799',
            external_ref='cccccccc-8888-8888-8888-888888888888')
        db.session.add_all([other_client, other_contractor])
        db.session.flush()
        db.session.add(Contract(
            number='ЧК-001', client_id=other_client.id,
            contractor_id=other_contractor.id,
            signed_date=date(2026, 2, 1), status='active'))
        db.session.commit()
        other_client_id = other_client.id
        other_contractor_id = other_contractor.id

    page_html = web.get('/contracts').get_data(as_text=True)
    positions = [page_html.find(key) for key in (
        'name="date_range"', 'name="client"', 'name="contractor"',
        'name="status"')]
    order_ok = (all(pos >= 0 for pos in positions)
                and positions == sorted(positions))
    selects_ok = ('Все клиенты' in page_html
                  and 'Все контрагенты' in page_html
                  and '>ООО «Второй-Тест»<' in page_html
                  and '>ООО «Второй контрагент-Т»<' in page_html)
    steps.check(order_ok and selects_ok,
                'В реестре договоров есть фильтры по клиенту и контрагенту',
                'после полей даты идут списки «Все клиенты» и «Все контрагенты» '
                'со значениями из справочников',
                'фильтры по клиенту и контрагенту стоят после даты'
                if order_ok and selects_ok
                else 'фильтры отсутствуют или стоят не на своём месте')

    by_client = web.get('/contracts', query_string={
        'client': other_client_id}).get_data(as_text=True)
    by_contractor = web.get('/contracts', query_string={
        'contractor': other_contractor_id}).get_data(as_text=True)
    by_main_contractor = web.get('/contracts', query_string={
        'contractor': contractor_id}).get_data(as_text=True)
    client_filter_ok = ('ЧК-001' in by_client
                        and 'ДГ-001' not in by_client
                        and f'value="{other_client_id}" selected' in by_client)
    contractor_filter_ok = ('ЧК-001' in by_contractor
                            and 'ДГ-001' not in by_contractor
                            and 'ДГ-001' in by_main_contractor
                            and 'ЧК-001' not in by_main_contractor)
    steps.check(client_filter_ok and contractor_filter_ok,
                'Фильтры по клиенту и контрагенту отбирают договоры',
                'по клиенту «Второй-Тест» виден только ЧК-001, по контрагенту '
                '«Второй контрагент-Т» — только ЧК-001, по основному '
                'контрагенту — только договоры ДГ; выбранное значение '
                'остаётся в списке',
                'фильтры по клиенту и контрагенту работают'
                if client_filter_ok and contractor_filter_ok
                else 'фильтры по клиенту или контрагенту не работают')

    preserved = web.get('/contracts', query_string={
        'client': other_client_id, 'sort': 'number',
        'dir': 'asc'}).get_data(as_text=True)
    preserved_ok = (f'client={other_client_id}' in preserved
                    and 'name="sort" value="number"' in preserved
                    and 'name="dir" value="asc"' in preserved)
    steps.check(preserved_ok,
                'Фильтры по клиенту и контрагенту сохраняются при сортировке',
                'ссылки сортировки и пагинации содержат выбранного клиента '
                'и контрагента',
                'фильтры сохраняются в ссылках'
                if preserved_ok else 'фильтры теряются при сортировке')

    page2_sorted = web.get('/contracts', query_string={
        'page': 2, 'sort': 'number', 'dir': 'asc'}).get_data(as_text=True)
    pagination_sort_ok = ('▲' in page2_sorted
                          and 'name="sort" value="number"' in page2_sorted
                          and 'name="dir" value="asc"' in page2_sorted
                          and 'sort=number' in page2_sorted
                          and 'dir=asc' in page2_sorted)
    steps.check(pagination_sort_ok,
                'При переходе на другую страницу сортировка сохраняется',
                'ссылки пагинации содержат sort и dir, а заголовок '
                'по-прежнему показывает ▲',
                'сортировка сохраняется при листании'
                if pagination_sort_ok
                else 'при смене страницы сортировка сбрасывается')

    default_html = web.get('/contracts',
                           query_string={'q': 'ДГ'}).get_data(as_text=True)
    p_draft = default_html.find('ДГ-003')
    p_active = default_html.find('ДГ-001')
    p_closed = default_html.find('ДГ-002')
    default_order_ok = (-1 < p_draft < p_active < p_closed)
    steps.check(default_order_ok,
                'По умолчанию договоры идут по статусу: проект, действующий, закрытый',
                'без выбранной сортировки сначала идут проекты, затем '
                'действующие и завершённые, внутри статуса — по дате убыванию',
                'порядок статусов соблюдён' if default_order_ok
                else f'порядок нарушен: {p_draft}, {p_active}, {p_closed}')

    with app.app_context():
        db.session.add_all([
            Contract(number='ТАЙ-Б', client_id=client_id,
                     signed_date=date(2025, 6, 1), status='active'),
            Contract(number='ТАЙ-А', client_id=client_id,
                     signed_date=date(2025, 6, 1), status='active'),
        ])
        db.session.commit()
    tie_html = web.get('/contracts',
                       query_string={'q': 'ТАЙ'}).get_data(as_text=True)
    p_b = tie_html.find('ТАЙ-Б')
    p_a = tie_html.find('ТАЙ-А')
    tie_ok = -1 < p_a < p_b
    steps.check(tie_ok,
                'При равных статусе и дате договоры сортируются по номеру',
                'договоры с одинаковым статусом и датой идут по возрастанию номера',
                'номерной порядок соблюдён' if tie_ok
                else f'номерной порядок нарушен: {p_a}, {p_b}')

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
    registry_ok = ('sort=number' in page1
                   and f'href="/contracts/{c1_id}"' in search_html
                   and f'href="/contracts/{c1_id}/edit"' in search_html
                   and 'fa-pen' in search_html)
    steps.check(registry_ok,
                'В реестре договоров есть карточка и кнопка редактирования',
                'столбец «Номер» ведёт в карточку договора, в конце таблицы — '
                'кнопка-карандаш для редактирования',
                'столбцы и ссылки присутствуют' if registry_ok
                else 'столбцы или ссылки отсутствуют')
    sort_asc = web.get('/contracts', query_string={
        'q': 'ДГ', 'sort': 'number', 'dir': 'asc'}).get_data(as_text=True)
    sort_desc = web.get('/contracts', query_string={
        'q': 'ДГ', 'sort': 'number', 'dir': 'desc'}).get_data(as_text=True)
    sorting_ok = ('▲' in sort_asc and '▼' in sort_desc
                  and 'name="sort" value="number"' in sort_asc
                  and 'name="dir" value="asc"' in sort_asc
                  and sort_asc.find('ДГ-001') < sort_asc.find('ДГ-002')
                  < sort_asc.find('ДГ-003')
                  and sort_desc.find('ДГ-003') < sort_desc.find('ДГ-002')
                  < sort_desc.find('ДГ-001'))
    steps.check(sorting_ok,
                'В реестре договоров работает сортировка по столбцам',
                'клик по заголовку сортирует по возрастанию (▲), повторный — '
                'по убыванию (▼); выбранная сортировка сохраняется в фильтре',
                'сортировка по номеру работает' if sorting_ok
                else 'сортировка по столбцам не работает')
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
    draft_html = web.get('/contracts',
                         query_string={'status': 'draft'}).get_data(as_text=True)
    contract_badges_ok = (
        'class="badge text-bg-success">Действует</span>' in search_html
        and 'class="badge text-bg-secondary">Закрыт</span>' in status_html
        and 'class="badge text-bg-light text-dark">Проект</span>' in draft_html
        and 'class="badge text-bg-success">Действует</span>' in card_html)
    steps.check(contract_badges_ok,
                'Статус договора выводится своим цветным бейджем',
                '«Действует» — зелёный, «Закрыт» — серый, «Проект» — светлый; '
                'бейдж отображается в реестре и карточке договора',
                'бейджи статусов договора отображаются'
                if contract_badges_ok
                else 'бейджи статусов договора отсутствуют или неверны')

    # --- Шаг 7. У архивного клиента нет открытых договоров --------------
    with app.app_context():
        archived = Client(
            name='ООО «Архив-Тест»', inn='7708888888', status='archived',
            external_ref='dddddddd-2222-2222-2222-222222222222',
            odata_url='http://archive-1c.local/odata/standard.odata/',
            odata_username='archive-user', odata_password='archive-secret')
        db.session.add(archived)
        db.session.commit()
        archived_id = archived.id
        with fake_1c(contracts=ARCHIVED_CONTRACTS):
            stats = sync_contracts(archived)
        rows = (Contract.query.filter_by(client_id=archived_id)
                .order_by(Contract.number).all())
        archived_statuses = {c.number: c.status for c in rows}
        archived_status_1c = {c.number: c.status_1c for c in rows}
    steps.check(
        stats['created'] == 3
        and archived_statuses == {'АРХ-001': 'closed',
                                  'АРХ-002': 'closed',
                                  'АРХ-003': 'closed'}
        and archived_status_1c == {'АРХ-001': 'Действует',
                                   'АРХ-002': 'Проект',
                                   'АРХ-003': 'Закрыт'},
        'У архивного клиента нет проектов и действующих договоров',
        'договоры из 1С со статусами «Действует» и «Проект» загружаются '
        'как завершённые; исходный статус 1С сохраняется',
        f"создано {stats['created']}, статусы: {archived_statuses}, "
        f'статусы 1С: {archived_status_1c}')

    archived_html = web.get(f'/clients/{archived_id}').get_data(as_text=True)
    steps.check(
        'Клиент в архиве' in archived_html and 'АРХ-001' in archived_html
        and '>Действует</span>' not in archived_html
        and '>Проект</span>' not in archived_html,
        'В карточке архивного клиента показано ограничение по договорам',
        'предупреждение «Клиент в архиве» и список завершённых договоров '
        'без статусов «Действует» и «Проект»',
        'предупреждение и завершённые договоры отображаются'
        if 'Клиент в архиве' in archived_html
        else 'предупреждение отсутствует')

    conclusion = ('Договоры загружаются из «1С:Бухгалтерия» по реквизитам '
                  'клиента, дублирование отсутствует.'
                  if steps.passed else 'Загрузка договоров работает некорректно.')
    return steps, conclusion
