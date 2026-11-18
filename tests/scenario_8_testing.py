"""Сценарий 8: AJAX-выполнение сценариев на странице «Тестирование».

Проверяется, что страница запускает сценарий AJAX-запросом и получает
JSON-сводку, а результат каждого шага — отдельным запросом. Дополнительно
проверяются обычный POST без JavaScript и запрет для сотрудника.
"""
from pathlib import Path

from tests.base import EMAILS, Steps, create_scenario_app, login, seed_users

FT = 'Интерфейс'
TITLE = 'AJAX-выполнение сценариев с пошаговым выводом результатов'
DESCRIPTION = ('Сценарий запускается AJAX-запросом и возвращает JSON-сводку; '
               'результат каждого шага запрашивается отдельно. Проверяются '
               'также обычный POST без JavaScript и запрет для сотрудника.')

AJAX = {'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}


def run():
    app = create_scenario_app()
    steps = Steps()
    seed_users(app)

    boss = app.test_client()
    login(boss, EMAILS['admin'])

    # --- Шаг 1. Страница подключает AJAX-инфраструктуру -------------------
    page = boss.get('/testing')
    html = page.get_data(as_text=True)
    ok = (page.status_code == 200
          and '<meta name="csrf-token"' in html
          and 'data-step-url="/testing/run/scenario_1/step"' in html
          and '/static/js/testing.js' in html)
    steps.check(ok, 'Страница подключает AJAX-скрипт и адрес шагов',
                'есть CSRF-мета-тег, data-step-url и testing.js',
                'подключение найдено' if ok
                else 'часть подключений отсутствует')

    # --- Шаг 2. Запуск сценария возвращает JSON-сводку --------------------
    resp = boss.post('/testing/run/scenario_1', headers=AJAX)
    summary = resp.get_json()
    ok = (resp.status_code == 200 and resp.is_json and summary
          and summary['id'] == 'scenario_1'
          and summary['status'] == 'passed'
          and summary['total_steps'] > 0
          and 'steps' not in summary)
    steps.check(ok, 'Запуск сценария по AJAX возвращает JSON-сводку',
                'HTTP 200, JSON со статусом, числом шагов и итогом, '
                'без массива шагов',
                f'HTTP {resp.status_code}, шагов: '
                f'{summary["total_steps"] if summary else "—"}')

    # --- Шаг 3. Каждый шаг запрашивается отдельно -------------------------
    total = summary['total_steps']
    first = boss.get('/testing/run/scenario_1/step?index=0',
                     headers=AJAX).get_json()
    last = boss.get(f'/testing/run/scenario_1/step?index={total - 1}',
                    headers=AJAX).get_json()
    ok = (first and last
          and first['index'] == 0 and last['index'] == total - 1
          and first['total'] == total == last['total']
          and first['step']['name'] and last['step']['name']
          and {'name', 'status', 'expected', 'actual'} <= set(first['step']))
    steps.check(ok, 'Результат каждого шага — отдельный AJAX-запрос',
                'первый и последний шаги получены отдельными запросами '
                'с полями name/status/expected/actual',
                f'шаг 0: «{first["step"]["name"]}»'
                if ok else 'шаги не получены')

    # --- Шаг 4. Неверный индекс шага --------------------------------------
    resp = boss.get('/testing/run/scenario_1/step?index=999', headers=AJAX)
    ok = resp.status_code == 404 and resp.is_json
    steps.check(ok, 'Несуществующий шаг возвращает JSON-ошибку',
                'HTTP 404 и сообщение «Шаг не найден»',
                f'HTTP {resp.status_code}, '
                f'{(resp.get_json() or {}).get("error")}')

    # --- Шаг 5. Результаты сохранены и видны без JavaScript ---------------
    page = boss.get('/testing')
    html = page.get_data(as_text=True)
    ok = 'js-steps' in html and html.count('<tr') >= total
    steps.check(ok, 'После AJAX-запуска результаты видны и без JavaScript',
                'страница отдаёт таблицу с шагами последнего запуска',
                f'строк в таблице: не меньше {total}'
                if ok else 'шаги не отрисованы')

    # --- Шаг 6. Обычный POST без JavaScript -------------------------------
    resp = boss.post('/testing/run/scenario_1')
    location = resp.headers.get('Location', '')
    html = boss.get('/testing').get_data(as_text=True)
    ok = (resp.status_code == 302 and location.endswith('/testing')
          and 'Сценарий ФТ-1 пройден' in html)
    steps.check(ok, 'Без JavaScript работает обычный POST',
                'форма отправляется, возвращает редирект и сообщение '
                'о результате',
                f'HTTP {resp.status_code}, сообщение '
                f'{"показано" if "Сценарий ФТ-1 пройден" in html else "нет"}')

    # --- Шаг 7. Сотруднику AJAX-запуск запрещён ---------------------------
    emp = app.test_client()
    login(emp, EMAILS['emp1'])
    resp = emp.post('/testing/run/scenario_1', headers=AJAX)
    ok = resp.status_code == 403 and resp.is_json
    steps.check(ok, 'Сотрудник получает JSON-запрет на запуск',
                'HTTP 403 с JSON-сообщением вместо HTML-страницы',
                f'HTTP {resp.status_code}, '
                f'{(resp.get_json() or {}).get("error")}')
    resp = emp.get('/testing/run/scenario_1/step?index=0', headers=AJAX)
    steps.check(resp.status_code == 403 and resp.is_json,
                'Сотрудник не может читать шаги сценария',
                'HTTP 403 с JSON-сообщением',
                f'HTTP {resp.status_code}')
    resp = emp.get('/testing')
    steps.check(resp.status_code == 403 and not resp.is_json,
                'Страница тестирования закрыта для сотрудника',
                'HTTP 403 и HTML-страница ошибки',
                f'HTTP {resp.status_code}')

    # --- Шаг 8. Поисковый select в стиле Select2 подключён локально -------
    script_asset = 'js/searchable-select.js'
    css_asset = 'css/style.css'
    static_root = Path(__file__).resolve().parents[1] / 'static'
    script_path = static_root / script_asset
    css_path = static_root / css_asset
    script_text = script_path.read_text(encoding='utf-8') if script_path.is_file() else ''
    css_text = css_path.read_text(encoding='utf-8') if css_path.is_file() else ''
    ok = ('/static/' + script_asset in html
          and 'delta-select' in script_text
          and 'data-searchable' in script_text
          and 'MIN_OPTIONS' not in script_text
          and 'delta-select-display' in css_text)
    steps.check(ok, 'Поисковый select работает без внешних библиотек',
                'страница загружает собственный JS и CSS; поиск включается '
                'по data-searchable, а не по текущему числу option',
                'скрипт и стили найдены' if ok else 'часть файлов отсутствует')

    conclusion = ('AJAX-запуск сценариев, пошаговый вывод результатов '
                  'и поисковый select в стиле Select2 работают.' if steps.passed
                  else 'Интерфейс сценариев нарушен — см. шаги.')
    return steps, conclusion
