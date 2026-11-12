"""Реестр сценариев тестирования и их запуск (консоль и веб-интерфейс).

Консольный запуск всех сценариев:
    python -m tests.runner

Возвращает код выхода 0, если все сценарии пройдены, и 1 — иначе.
"""
import importlib
import time
import traceback
from datetime import datetime

# (идентификатор, модуль сценария) — порядок отображения на странице
SCENARIOS = [
    ('scenario_1', 'tests.scenario_1_sync'),
    ('scenario_2', 'tests.scenario_2_documents'),
    ('scenario_3', 'tests.scenario_3_tasks'),
    ('scenario_4', 'tests.scenario_4_deadlines'),
    ('scenario_5', 'tests.scenario_5_contracts'),
    ('scenario_6', 'tests.scenario_6_reports'),
    ('scenario_7', 'tests.scenario_7_access'),
]


# Последние результаты запуска по пользователям — хранятся в памяти
# процесса: объём отчёта превышает лимит cookie сессии (~4 КБ)
_RESULTS = {}


def store_results(user_id, results):
    """Сохранить результаты последнего запуска для пользователя."""
    _RESULTS[user_id] = results


def load_results(user_id):
    """Результаты последнего запуска пользователя (или пустой словарь)."""
    return _RESULTS.get(user_id) or {}


def registry():
    """Метаданные сценариев для страницы «Тестирование»."""
    items = []
    for sid, modname in SCENARIOS:
        mod = importlib.import_module(modname)
        items.append({'id': sid, 'ft': mod.FT, 'title': mod.TITLE,
                      'description': mod.DESCRIPTION})
    return items


def run_scenario(scenario_id):
    """Выполнить сценарий и вернуть отчёт-словарь (None — если не найден)."""
    modname = dict(SCENARIOS).get(scenario_id)
    if modname is None:
        return None
    mod = importlib.import_module(modname)

    started = time.perf_counter()
    try:
        steps, conclusion = mod.run()
        step_items = steps.items
        passed = steps.passed
    except Exception as exc:  # сценарий не должен обрушивать страницу
        step_items = [
            {'name': 'Выполнение сценария', 'status': 'failed',
             'expected': 'сценарий выполняется без ошибок',
             'actual': f'исключение: {exc}',
             'details': traceback.format_exc(limit=5)},
        ]
        passed = False
        conclusion = 'Сценарий завершился сбоем — см. детали шага.'
    duration_ms = round((time.perf_counter() - started) * 1000)

    return {
        'id': scenario_id,
        'ft': mod.FT,
        'title': mod.TITLE,
        'status': 'passed' if passed else 'failed',
        'executed_at': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        'duration_ms': duration_ms,
        'steps': step_items,
        'conclusion': conclusion,
    }


def main():
    """Консольный запуск всех сценариев."""
    print('=' * 72)
    print('Сценарное тестирование системы «Дельта»')
    print('=' * 72)
    results = [run_scenario(sid) for sid, _ in SCENARIOS]
    for r in results:
        marker = 'ПРОЙДЕН' if r['status'] == 'passed' else 'ПРОВАЛЕН'
        print(f"\n[{r['ft']}] {r['title']} — {marker} ({r['duration_ms']} мс)")
        for s in r['steps']:
            icon = ' OK ' if s['status'] == 'passed' else 'FAIL'
            print(f'  [{icon}] {s["name"]}')
            print(f'         {s["actual"]}')
        print(f'  Результат: {r["conclusion"]}')
    passed = sum(1 for r in results if r['status'] == 'passed')
    print('\n' + '=' * 72)
    print(f'Итог: пройдено {passed} из {len(results)} сценариев')
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
