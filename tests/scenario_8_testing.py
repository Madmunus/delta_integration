"""Сценарий 8: AJAX-выполнение сценариев на странице «Тестирование».

Проверяется, что страница запускает сценарий AJAX-запросом и получает
JSON-сводку, а результат каждого шага — отдельным запросом. Дополнительно
проверяются обычный POST без JavaScript, запрет для сотрудника и
security-заголовки с жёстким CSP.
"""
import re
from pathlib import Path

from tests.base import EMAILS, Steps, create_scenario_app, login, seed_users

FT = 'Интерфейс'
TITLE = 'AJAX-выполнение сценариев с пошаговым выводом результатов'
DESCRIPTION = ('Сценарий запускается AJAX-запросом и возвращает JSON-сводку; '
               'результат каждого шага запрашивается отдельно. Проверяются '
               'обычный POST без JavaScript, запрет для сотрудника, '
               'элементы интерфейса без внешних библиотек, заголовки '
               'безопасности и CSP без инлайн-кода.')

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

    # --- Шаг 9. Календарь дат управляется select месяца и года -------------
    range_script_path = static_root / 'js/date-range.js'
    range_script_text = (range_script_path.read_text(encoding='utf-8')
                         if range_script_path.is_file() else '')
    ok = ('delta-range-month' in range_script_text
          and 'delta-range-year' in range_script_text
          and 'delta-range-title' not in range_script_text
          and 'delta-range-month' in css_text
          and 'delta-range-year' in css_text)
    steps.check(ok, 'В календаре дат есть select месяца и года',
                'заголовок календаря состоит из двух селектов, '
                'а статический текст месяца и года удалён',
                'селекты найдены' if ok else 'часть файлов устарела')

    ok = ('data-range-preset' in range_script_text
          and 'presetRange' in range_script_text
          and 'year' in range_script_text
          and 'quarter' in range_script_text
          and 'month' in range_script_text)
    steps.check(ok, 'Быстрые периоды заполняют поле дат',
                'для Года/Квартала/Месяца есть обработчик, '
                'который записывает диапазон в поле формы',
                'обработчик найден' if ok else 'обработчик отсутствует')

    # --- Шаг 10. Фильтры реестров сохраняются в браузере ---------------------
    saved_path = static_root / 'js/registry-filters.js'
    saved_text = (saved_path.read_text(encoding='utf-8')
                  if saved_path.is_file() else '')
    base_path = static_root.parent / 'templates' / 'base.html'
    base_text = (base_path.read_text(encoding='utf-8')
                 if base_path.is_file() else '')
    templates_root = static_root.parent / 'templates'
    registry_keys = ('clients', 'contractors', 'contracts', 'documents',
                     'tasks')
    registry_ok = True
    for key in registry_keys:
        template = templates_root / key / 'list.html'
        text = template.read_text(encoding='utf-8') if template.is_file() else ''
        registry_ok = (registry_ok
                       and f'data-registry-filter="{key}"' in text
                       and f'data-filter-reset="{key}"' in text)
    ok = ('js/registry-filters.js' in base_text
          and 'data-user-id=' in base_text
          and 'localStorage' in saved_text
          and 'data-registry-filter' in saved_text
          and 'EXCLUDED' in saved_text
          and 'data-filter-reset' in saved_text
          and registry_ok)
    steps.check(ok, 'Фильтры реестров сохраняются в localStorage',
                'состояние форм привязано к пользователю и реестру, '
                'пагинация и сортировка не сохраняются',
                'скрипт подключён' if ok else 'часть файлов устарела')

    # --- Шаг 11. Заголовки безопасности на всех типах ответов ---------------
    csp = page.headers.get('Content-Security-Policy', '')
    expected_headers = {
        'Content-Security-Policy': csp,
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY',
        'Referrer-Policy': 'no-referrer',
        'Permissions-Policy': (
            'camera=(), microphone=(), geolocation=(), payment=()'),
    }
    responses = {
        'HTML': boss.get('/testing'),
        'JS': boss.get('/static/js/ui-actions.js'),
        'JSON': boss.get('/testing/run/scenario_1/step?index=0',
                         headers=AJAX),
        '404': boss.get('/no-such-page'),
    }
    missing_headers = []
    for label, response in responses.items():
        for name, value in expected_headers.items():
            if response.headers.get(name) != value:
                missing_headers.append(f'{label}:{name}')
    statuses_ok = (responses['HTML'].status_code == 200
                   and responses['JS'].status_code == 200
                   and responses['JSON'].status_code == 200
                   and responses['404'].status_code == 404)
    actual = ', '.join(
        f'{label} {response.status_code}'
        for label, response in responses.items())
    if missing_headers:
        actual += '; нет заголовков: ' + ', '.join(missing_headers)
    steps.check(statuses_ok and not missing_headers,
                'Security-заголовки есть на HTML, статике, JSON и 404',
                'одинаковый набор заголовков во всех ответах, включая ошибки',
                actual)

    # --- Шаг 12. CSP не допускает инлайн-кода и внешних источников ---------
    directives = {}
    for part in csp.split(';'):
        part = part.strip()
        if not part:
            continue
        pieces = part.split(None, 1)
        directives[pieces[0]] = pieces[1] if len(pieces) == 2 else ''
    expected_csp = {
        'default-src': "'none'",
        'script-src': "'self'",
        'style-src': "'self'",
        'font-src': "'self'",
        'img-src': "'self' data:",
        'connect-src': "'self'",
        'form-action': "'self'",
        'base-uri': "'none'",
        'object-src': "'none'",
        'frame-ancestors': "'none'",
    }
    sources = set()
    for value in directives.values():
        sources.update(value.split())
    csp_ok = (
        all(directives.get(name) == value
            for name, value in expected_csp.items())
        and sources <= {"'none'", "'self'", 'data:'}
        and "'unsafe-inline'" not in csp
        and "'unsafe-eval'" not in csp
    )
    steps.check(csp_ok, 'CSP жёсткий и не содержит исключений',
                'только same-origin и data: для SVG в CSS Bootstrap, '
                "без 'unsafe-inline' и 'unsafe-eval'",
                csp if csp else 'заголовок отсутствует')

    # --- Шаг 13. HSTS управляется конфигурацией ----------------------------
    hsts_app = create_scenario_app()
    hsts_client = hsts_app.test_client()
    hsts_app.config.update(SECURITY_HSTS_ENABLED=False,
                           SECURITY_HSTS_MAX_AGE=600)
    hsts_off = hsts_client.get('/login')
    hsts_app.config['SECURITY_HSTS_ENABLED'] = True
    hsts_on = hsts_client.get('/login')
    hsts_ok = (
        'Strict-Transport-Security' not in hsts_off.headers
        and hsts_on.headers.get('Strict-Transport-Security')
        == 'max-age=600; includeSubDomains'
    )
    hsts_off_value = ('есть' if 'Strict-Transport-Security' in hsts_off.headers
                      else 'нет')
    hsts_on_value = hsts_on.headers.get('Strict-Transport-Security')
    steps.check(hsts_ok, 'HSTS включается через конфигурацию',
                'выключен по умолчанию, а при включении содержит '
                'max-age и includeSubDomains',
                f'выключен: {hsts_off_value}; включён: {hsts_on_value}')

    # --- Шаг 14. Шаблоны не содержат инлайн-JS и инлайн-стилей -------------
    inline_event = re.compile(r'\son[a-z]+\s*=', re.IGNORECASE)
    inline_style = re.compile(r"\sstyle\s*=\s*['\"]", re.IGNORECASE)
    violations = []
    for template in templates_root.rglob('*.html'):
        text = template.read_text(encoding='utf-8')
        if inline_event.search(text) or inline_style.search(text):
            violations.append(str(template.relative_to(templates_root)))
    steps.check(not violations, 'В шаблонах нет onclick, onsubmit и style=',
                'все обработчики и стили вынесены во внешние JS/CSS',
                'нарушений нет' if not violations
                else 'нарушения: ' + ', '.join(violations))

    # --- Шаг 15. Динамическая геометрия применяется внешним JS -------------
    ui_text = (static_root / 'js/ui-actions.js').read_text(encoding='utf-8')
    charts_text = (templates_root / 'reports/_charts.html').read_text(
        encoding='utf-8')
    report_text = (templates_root / 'reports/clients.html').read_text(
        encoding='utf-8')
    deadline_text = (templates_root / 'deadlines/list.html').read_text(
        encoding='utf-8')
    geometry_ok = (
        '/static/js/ui-actions.js' in html
        and 'data-bar-height' in charts_text
        and 'data-bar-width' in report_text
        and 'data-bar-width' in deadline_text
        and '[data-bar-height]' in ui_text
        and '[data-bar-width]' in ui_text
    )
    steps.check(geometry_ok, 'Геометрия графиков задаётся внешним скриптом',
                'data-bar-height/data-bar-width в шаблонах и обработчик '
                'в ui-actions.js вместо инлайн-стилей',
                'все файлы согласованы' if geometry_ok
                else 'часть файлов устарела')

    conclusion = ('AJAX-запуск сценариев, пошаговый вывод результатов, '
                  'поисковый select, календарь дат, сохранённые фильтры, '
                  'security-заголовки и жёсткий CSP работают без внешних '
                  'библиотек.' if steps.passed
                  else 'Интерфейс сценариев нарушен — см. шаги.')
    return steps, conclusion
