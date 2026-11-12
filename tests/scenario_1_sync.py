"""Сценарий 1 (ФТ-1): синхронизация клиентов и контрагентов из «1С».

Запуск синхронизации из главной базы «1С:Предприятие»: клиенты
загружаются из Catalog_Организации, контрагенты — из
Catalog_Контрагенты. Проверяется появление записей, признак удаления
(DeletionMark) и отсутствие дублирования при повторном запуске.
"""
from engine.models import Client, Contractor, OneCSetting, db
from services.sync import sync_clients
from tests.base import (EMAILS, Steps, create_scenario_app, fake_1c,
                        login, seed_users)

FT = 'ФТ-1'
TITLE = 'Синхронизация клиентов и контрагентов из базы 1С:Предприятие'
DESCRIPTION = ('Запуск синхронизации из главной базы «1С:Предприятие»: '
               'клиенты из Catalog_Организации, контрагенты из '
               'Catalog_Контрагенты. Результат: записи созданы, признак '
               'удаления учтён, дублирование отсутствует.')

ALPHA_KEY = '11111111-1111-1111-1111-111111111111'
BETA_KEY = '22222222-2222-2222-2222-222222222222'
GAMMA_KEY = '33333333-3333-3333-3333-333333333333'
DELETED_KEY = '44444444-4444-4444-4444-444444444444'

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

    # --- Шаг 6. Повторный запуск не создаёт дублей --------------------
    with app.app_context():
        with fake_1c(organizations=ORGANIZATIONS, contractors=CONTRACTORS):
            stats = sync_clients()
        total_after = Client.query.count()
        contractors_after = Contractor.query.count()
    steps.check(stats['created'] == 0 and stats['contractors_created'] == 0
                and total_after == total
                and contractors_after == contractors_total,
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
