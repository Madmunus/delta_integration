"""Сценарий 1 (ФТ-1): синхронизация справочника клиентов из «1С».

Запуск синхронизации справочника клиентов из тестовой базы «1С»;
проверка появления записей в реестре; повторный запуск (проверка
отсутствия дублирования). Результат: синхронизация выполнена,
дублирование отсутствует.
"""
from engine.models import Client, ODataBase, db
from services.sync import sync_clients
from tests.base import (EMAILS, Steps, create_scenario_app, fake_1c,
                        login, seed_users)

FT = 'ФТ-1'
TITLE = 'Синхронизация справочника клиентов из тестовой базы «1С»'
DESCRIPTION = ('Запуск синхронизации справочника клиентов из тестовой базы '
               '«1С»; проверка появления записей в реестре; повторный запуск '
               '(проверка отсутствия дублирования). Результат: синхронизация '
               'выполнена, дублирование отсутствует.')

ALPHA_KEY = '11111111-1111-1111-1111-111111111111'
BETA_KEY = '22222222-2222-2222-2222-222222222222'
GAMMA_KEY = '33333333-3333-3333-3333-333333333333'

# Ответ тестовой базы 1С: справочник контрагентов.
# Четвёртая запись помечена на удаление и в реестр попасть не должна.
CONTRACTORS = [
    {'Ref_Key': ALPHA_KEY, 'Description': 'ООО «Альфа-Тест»',
     'ИНН': '7701111111', 'КПП': '770101001', 'DeletionMark': False},
    {'Ref_Key': BETA_KEY, 'Description': 'ООО «Бета-Тест»',
     'ИНН': '7702222222', 'КПП': '770201001', 'DeletionMark': False},
    {'Ref_Key': GAMMA_KEY, 'Description': 'ИП Гамма-Тест',
     'ИНН': '770333333333', 'КПП': '', 'DeletionMark': False},
    {'Ref_Key': '44444444-4444-4444-4444-444444444444',
     'Description': 'ООО «Помечена на удаление в 1С»',
     'ИНН': '7704444444', 'КПП': '', 'DeletionMark': True},
]


def run():
    app = create_scenario_app()
    steps = Steps()
    seed_users(app)

    with app.app_context():
        db.session.add(ODataBase(
            name='Тестовая база 1С',
            odata_url='http://test-1c.local/odata/standard.odata/',
            username='odata', password='secret', is_enabled=True))
        db.session.commit()

    # --- Шаг 1. Запуск синхронизации из веб-интерфейса ---------------
    web = app.test_client()
    login(web, EMAILS['admin'])
    with fake_1c(contractors=CONTRACTORS):
        resp = web.post('/sync/run', follow_redirects=True)
    steps.check(resp.status_code == 200,
                'Запуск синхронизации из веб-интерфейса',
                'команда выполнена, открыт список учётных баз',
                f'HTTP {resp.status_code}')

    # --- Шаг 2. Записи появились в реестре ---------------------------
    with app.app_context():
        total = Client.query.count()
        alpha = Client.query.filter_by(inn='7701111111').first()
        gamma = Client.query.filter_by(inn='770333333333').first()
    steps.check(total == 3,
                'Записи появились в реестре клиентов',
                '3 клиента; запись, помеченная на удаление, не переносится',
                f'в реестре {total} клиентов')
    steps.check(alpha is not None and alpha.name == 'ООО «Альфа-Тест»'
                and alpha.kpp == '770101001'
                and alpha.external_ref == ALPHA_KEY,
                'Реквизиты клиента перенесены из «1С»',
                'наименование, ИНН, КПП и внешняя ссылка соответствуют данным 1С',
                (f'«Альфа-Тест»: {alpha.name}, ИНН {alpha.inn}, '
                 f'КПП {alpha.kpp}, Ref {alpha.external_ref}'
                 if alpha else 'клиент с ИНН 7701111111 не найден'))
    steps.check(gamma is not None and gamma.kpp is None,
                'Пустые реквизиты из «1С» не затирают данные',
                'КПП не заполняется пустым значением',
                f'КПП ИП Гамма-Тест: {gamma.kpp if gamma else "клиент не найден"}')

    # --- Шаг 3. Клиенты видны на странице реестра --------------------
    html = web.get('/clients').get_data(as_text=True)
    steps.check('Альфа-Тест' in html and 'Гамма-Тест' in html,
                'Синхронизированные клиенты отображаются в реестре',
                'клиенты из «1С» видны на странице «Клиенты»',
                'клиенты отображаются' if 'Альфа-Тест' in html
                else 'клиенты не отображаются')

    # --- Шаг 4. Повторный запуск не создаёт дублей --------------------
    with app.app_context():
        with fake_1c(contractors=CONTRACTORS):
            stats = sync_clients()
        total_after = Client.query.count()
    steps.check(stats['created'] == 0 and total_after == total,
                'Повторный запуск не дублирует записи',
                'новых записей 0, количество клиентов в реестре не изменилось',
                f"создано {stats['created']}, обновлено {stats['updated']}, "
                f'в реестре {total_after} клиентов')

    conclusion = ('Синхронизация выполнена, дублирование отсутствует.'
                  if steps.passed
                  else 'Синхронизация выполнена некорректно — см. шаги.')
    return steps, conclusion
