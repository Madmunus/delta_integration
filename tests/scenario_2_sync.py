"""Сценарий 2 (ФТ-2): синхронизация клиентов и контрагентов из «1С».

Запуск синхронизации из главной базы «1С:Предприятие»: клиенты
загружаются из Catalog_Организации, контрагенты — из
Catalog_Контрагенты. Проверяется появление записей, признак удаления
(DeletionMark) и отсутствие дублирования при повторном запуске.
"""
from engine.models import Client, Contractor, OneCSetting, db
from engine.sync import sync_clients
from tests.base import (EMAILS, Steps, create_scenario_app, fake_1c,
                        login, seed_users)

FT = 'ФТ-2'
TITLE = 'Синхронизация клиентов и контрагентов из базы 1С:Предприятие'
DESCRIPTION = ('Запуск синхронизации из главной базы «1С:Предприятие»: '
               'клиенты из Catalog_Организации, контрагенты из '
               'Catalog_Контрагенты. Результат: записи созданы, признак '
               'удаления учтён, дублирование отсутствует.')

ALPHA_KEY = '11111111-1111-1111-1111-111111111111'
BETA_KEY = '22222222-2222-2222-2222-222222222222'
GAMMA_KEY = '33333333-3333-3333-3333-333333333333'
DELETED_KEY = '44444444-4444-4444-4444-444444444444'
ALPHA_CONTRACTOR_KEY = 'aaaaaaaa-1111-1111-1111-111111111111'

# Catalog_Организации: Ref_Key, Наименование, ИНН, DeletionMark.
ORGANIZATIONS = [
    {'Ref_Key': ALPHA_KEY, 'Наименование': 'ООО «Альфа-Тест»',
     'ИНН': '7701111111', 'DeletionMark': False},
    {'Ref_Key': BETA_KEY, 'Наименование': 'ООО «Бета-Тест»',
     'ИНН': '7702222222', 'DeletionMark': False},
    {'Ref_Key': GAMMA_KEY, 'Наименование': 'ИП Гамма-Тест',
     'ИНН': '770333333333', 'DeletionMark': 0},
    {'Ref_Key': DELETED_KEY, 'Наименование': 'ООО «Помечена на удаление в 1С»',
     'ИНН': '7704444444', 'DeletionMark': True},
]

# Catalog_Контрагенты: Ref_Key, НаименованиеПолное, ИНН, КПП, Description.
CONTRACTORS = [
    {'Ref_Key': 'aaaaaaaa-1111-1111-1111-111111111111',
     'НаименованиеПолное': 'ООО «Альфа-Тест»', 'ИНН': '7701111111',
     'КПП': '770101001', 'Description': 'Основной контрагент'},
    {'Ref_Key': 'bbbbbbbb-2222-2222-2222-222222222222',
     'НаименованиеПолное': 'ООО «Бета-Тест»', 'ИНН': '7702222222',
     'КПП': '770201001', 'Description': ''},
]


def run():
    app = create_scenario_app()
    steps = Steps()
    seed_users(app)

    with app.app_context():
        db.session.add(OneCSetting(
            id=1,
            odata_url='http://test-1c.local/odata/standard.odata/',
            username='odata', password='secret'))
        db.session.commit()

    # --- Шаг 1. Запуск синхронизации из веб-интерфейса ---------------
    web = app.test_client()
    login(web, EMAILS['admin'])
    with fake_1c(organizations=ORGANIZATIONS, contractors=CONTRACTORS):
        resp = web.post('/bases/sync', follow_redirects=True)
    steps.check(resp.status_code == 200,
                'Запуск синхронизации из веб-интерфейса',
                'команда выполнена, открыта страница «1С База»',
                f'HTTP {resp.status_code}')

    # --- Шаг 2. Клиенты появились в реестре --------------------------
    with app.app_context():
        total = Client.query.count()
        alpha = Client.query.filter_by(inn='7701111111').first()
        deleted = Client.query.filter_by(inn='7704444444').first()
    steps.check(total == 4,
                'Организации перенесены в реестр клиентов',
                '4 клиента из Catalog_Организации',
                f'в реестре {total} клиентов')
    steps.check(alpha is not None and alpha.name == 'ООО «Альфа-Тест»'
                and alpha.external_ref == ALPHA_KEY,
                'Реквизиты клиента перенесены из «1С»',
                'наименование, ИНН и внешняя ссылка соответствуют данным 1С',
                (f'«Альфа-Тест»: {alpha.name}, ИНН {alpha.inn}, '
                 f'Ref {alpha.external_ref}'
                 if alpha else 'клиент с ИНН 7701111111 не найден'))
    steps.check(deleted is not None and deleted.status == 'archived',
                'DeletionMark = 1 переводит клиента в архив',
                'организация, помеченная на удаление, получает статус «архивный»',
                (f'статус: {deleted.status}'
                 if deleted else 'организация с ИНН 7704444444 не найдена'))

    # --- Шаг 3. Контрагенты загружены в отдельный справочник ----------
    with app.app_context():
        contractors_total = Contractor.query.count()
        alpha_contractor = Contractor.query.filter_by(
            inn='7701111111').first()
    steps.check(contractors_total == 2,
                'Контрагенты перенесены в справочник',
                '2 контрагента из Catalog_Контрагенты',
                f'в справочнике {contractors_total} контрагентов')
    steps.check(alpha_contractor is not None
                and alpha_contractor.full_name == 'ООО «Альфа-Тест»'
                and alpha_contractor.kpp == '770101001'
                and alpha_contractor.description == 'Основной контрагент',
                'Реквизиты контрагента перенесены из «1С»',
                'наименование полное, ИНН, КПП и описание соответствуют 1С',
                (f'{alpha_contractor.full_name}, ИНН {alpha_contractor.inn}, '
                 f'КПП {alpha_contractor.kpp}'
                 if alpha_contractor else 'контрагент не найден'))

    # --- Шаг 4. Клиенты видны на странице реестра --------------------
    html = web.get('/clients').get_data(as_text=True)
    steps.check('Альфа-Тест' in html and 'Гамма-Тест' in html,
                'Синхронизированные клиенты отображаются в реестре',
                'клиенты из «1С» видны на странице «Клиенты»',
                'клиенты отображаются' if 'Альфа-Тест' in html
                else 'клиенты не отображаются')

    # --- Шаг 5. Контрагенты на отдельной странице --------------------
    contractors_html = web.get('/contractors').get_data(as_text=True)
    bases_html = web.get('/bases').get_data(as_text=True)
    steps.check('Альфа-Тест' in contractors_html
                and 'Наименование полное' in contractors_html
                and 'href="/contractors"' in html,
                'Контрагенты отображаются на отдельной странице',
                'таблица контрагентов доступна в разделе «Контрагенты», '
                'пункт есть в навигации',
                'страница контрагентов и пункт меню доступны'
                if 'Альфа-Тест' in contractors_html
                else 'контрагенты не отображаются')
    steps.check('Наименование полное' not in bases_html,
                'Таблица контрагентов убрана со страницы «1С База»',
                'на странице настройки остаётся только счётчик контрагентов',
                'таблица перенесена' if 'Наименование полное' not in bases_html
                else 'таблица всё ещё на странице «1С База»')

    # --- Шаг 5.1. Поиск по таблице контрагентов ------------------------
    filtered_html = web.get('/contractors',
                            query_string={'q': 'Альфа'}).get_data(as_text=True)
    filter_ok = ('Альфа-Тест' in filtered_html
                 and 'Бета-Тест' not in filtered_html
                 and 'Поиск по таблице' in contractors_html
                 and 'fa-xmark' in contractors_html
                 and 'fa-magnifying-glass' in contractors_html
                 and 'fa-arrow-rotate-left' in contractors_html
                 and 'requestSubmit' not in contractors_html)
    steps.check(filter_ok,
                'В реестре контрагентов работает поиск по таблице',
                'поле «Поиск по таблице» с кнопками применения, очистки и '
                'сброса; фильтр по наименованию, ИНН, КПП и описанию',
                'найдена только «Альфа-Тест»' if filter_ok
                else 'фильтр не работает')

    # --- Шаг 6. Карточка контрагента ----------------------------------
    with app.app_context():
        contractor_id = Contractor.query.filter_by(inn='7701111111').first().id
    contractor_html = web.get(f'/contractors/{contractor_id}').get_data(as_text=True)
    requisites = ('Ключ 1С', 'Наименование полное', 'ИНН', 'КПП', 'Описание')
    positions = [contractor_html.find(f'>{label}<') for label in requisites]
    steps.check(
        all(value in contractor_html for value in (
            'ООО «Альфа-Тест»', '7701111111', '770101001',
            'Основной контрагент', ALPHA_CONTRACTOR_KEY))
        and all(pos >= 0 for pos in positions)
        and positions == sorted(positions),
        'Карточка контрагента содержит все реквизиты',
        'Ключ 1С, наименование полное, ИНН, КПП, описание — в заданном порядке',
        'реквизиты отображаются в нужном порядке'
        if positions == sorted(positions) else f'порядок нарушен: {positions}')
    tabs_ok = all(f'data-bs-target="#tab-{tab}"' in contractor_html
                  for tab in ('requisites', 'contracts', 'docs'))
    steps.check(tabs_ok and '<p class="text-muted mb-0">' not in contractor_html,
                'Разделы карточки контрагента оформлены вкладками',
                'вкладки «Реквизиты», «Договоры», «Документы»; строки под '
                'заголовком нет',
                'вкладки и заголовок оформлены корректно'
                if tabs_ok else 'вкладки отсутствуют')
    steps.check(f'href="/contractors/{contractor_id}"' in contractors_html,
                'Из списка контрагентов открывается карточка',
                'наименование в списке — ссылка на карточку контрагента',
                'ссылка на карточку есть' if f'href="/contractors/{contractor_id}"'
                in contractors_html else 'ссылка на карточку отсутствует')

    # --- Шаг 6.1. Пагинация реестра контрагентов -----------------------
    with app.app_context():
        for i in range(1, 24):
            db.session.add(Contractor(
                external_ref=f'ffffffff-0000-0000-0000-{i:012d}',
                full_name=f'ООО «Пагинация-{i:02d}»',
                inn=f'7799{i:08d}', kpp=f'7799{i:05d}',
                description='Демо-контрагент для проверки пагинации'))
        db.session.commit()
        contractors_count = Contractor.query.count()

    def _body_rows(html):
        return html.split('<tbody>', 1)[1].split('</tbody>', 1)[0].count('<tr>')

    page1 = web.get('/contractors').get_data(as_text=True)
    page2 = web.get('/contractors',
                    query_string={'page': 2}).get_data(as_text=True)
    rows1, rows2 = _body_rows(page1), _body_rows(page2)
    steps.check(contractors_count == 25 and rows1 == 20 and rows2 == 5
                and 'page=2' in page1,
                'В реестре контрагентов работает пагинация по 20 записей',
                '25 контрагентов: 20 на первой странице и 5 на второй',
                f'всего {contractors_count}, на странице 1 — {rows1}, '
                f'на странице 2 — {rows2}')

    # --- Шаг 7. Нередактируемые поля в карточке клиента ----------------
    edit_html = web.get(f'/clients/{alpha.id}/edit').get_data(as_text=True)
    read_only_ok = all(value in edit_html for value in (
        alpha.name, alpha.inn, alpha.kpp or '—', ALPHA_KEY,
        'Реквизиты', 'Редактируемые поля'))
    steps.check(
        read_only_ok,
        'В карточке редактирования показаны нередактируемые поля',
        'наименование, ИНН, КПП, Ref_Key и статус доступны только для чтения',
        'информационные поля отображаются'
        if read_only_ok else 'информационные поля отсутствуют')
    steps.check('badge text-bg-info' not in html,
                'На странице списка клиентов нет badge «1С»',
                'бейдж источника удалён из реестра клиентов',
                'badge отсутствует' if 'badge text-bg-info' not in html
                else 'badge всё ещё отображается')

    # --- Шаг 8. Карточка клиента ---------------------------------------
    card_html = web.get(f'/clients/{alpha.id}').get_data(as_text=True)
    labels = ('Ключ 1С', 'Наименование', 'ИНН', 'КПП', 'Статус',
              'Дата создания', 'Ответственный', 'Заметки',
              'URL OData (1С:Бухгалтерия)', 'Пользователь', 'Пароль')
    positions = [card_html.find(f'>{label}<') for label in labels]
    order_ok = (all(pos >= 0 for pos in positions)
                and positions == sorted(positions))
    steps.check(order_ok,
                'В карточке клиента реквизиты идут в заданном порядке',
                'Ключ 1С, наименование, ИНН, КПП, статус, дата создания, '
                'ответственный, заметки, URL OData, пользователь, пароль',
                'порядок реквизитов соблюдён' if order_ok
                else f'порядок нарушен: {positions}')
    header_ok = ('>1С</span>' not in card_html
                 and 'ответственный:' not in card_html
                 and 'badge text-bg-info' in card_html)
    steps.check(header_ok,
                'В заголовке карточки нет badge «1С» и подписи ответственного',
                'статус и ответственный отображаются в заголовке без подписи',
                'заголовок карточки оформлен корректно'
                if header_ok else 'заголовок карточки оформлен некорректно')

    # --- Шаг 9. Повторный запуск не создаёт дублей --------------------
    with app.app_context():
        contractors_before = Contractor.query.count()
        with fake_1c(organizations=ORGANIZATIONS, contractors=CONTRACTORS):
            stats = sync_clients()
        total_after = Client.query.count()
        contractors_after = Contractor.query.count()
    steps.check(stats['created'] == 0 and stats['contractors_created'] == 0
                and total_after == total
                and contractors_after == contractors_before,
                'Повторный запуск не дублирует записи',
                'новых клиентов и контрагентов 0, количество записей не изменилось',
                f"создано клиентов {stats['created']}, "
                f"контрагентов {stats['contractors_created']}; "
                f'в реестре {total_after} клиентов, '
                f'{contractors_after} контрагентов')

    conclusion = ('Синхронизация клиентов и контрагентов выполнена, '
                  'дублирование отсутствует.'
                  if steps.passed
                  else 'Синхронизация выполнена некорректно — см. шаги.')
    return steps, conclusion
