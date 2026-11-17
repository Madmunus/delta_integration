"""Страница сценарного тестирования системы (доступ только руководителю).

Сценарии выполняются в изолированной базе данных и не затрагивают
рабочие данные. Результаты последнего запуска хранятся в памяти процесса.

Страница работает через AJAX: запуск сценария возвращает JSON-сводку,
а результат каждого шага запрашивается отдельным запросом
(``/testing/run/<scenario_id>/step?index=N``) и сразу выводится в таблицу.
Без JavaScript формы отправляются обычным POST с редиректом.
"""
from flask import (Blueprint, abort, flash, jsonify, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required

bp = Blueprint('testing', __name__)


def _wants_json():
    """Запрос пришёл из AJAX-скрипта страницы тестирования."""
    return (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or request.accept_mimetypes.best == 'application/json')


def _admin_guard():
    """Проверка прав: для AJAX — JSON-ответ, иначе HTML-страница 403."""
    if current_user.is_admin:
        return None
    if _wants_json():
        return jsonify(error='Доступ к тестированию разрешён только '
                             'руководителю'), 403
    abort(403)


def _summary(result):
    """Сводка по сценарию без пошаговых результатов (для AJAX)."""
    return {
        'id': result['id'],
        'ft': result['ft'],
        'title': result['title'],
        'status': result['status'],
        'executed_at': result['executed_at'],
        'duration_ms': result['duration_ms'],
        'total_steps': len(result['steps']),
        'conclusion': result['conclusion'],
    }


@bp.route('/testing')
@login_required
def index():
    """Список сценариев тестирования и результаты последнего запуска."""
    denied = _admin_guard()
    if denied:
        return denied
    from tests.runner import load_results, registry
    return render_template('testing/index.html',
                           scenarios=registry(),
                           results=load_results(current_user.id))


@bp.route('/testing/run/<scenario_id>', methods=['POST'])
@login_required
def run(scenario_id):
    """Выполнить один сценарий."""
    denied = _admin_guard()
    if denied:
        return denied
    from tests.runner import load_results, run_scenario, store_results
    result = run_scenario(scenario_id)
    if result is None:
        if _wants_json():
            return jsonify(error=f'Сценарий {scenario_id} не найден'), 404
        abort(404)
    results = load_results(current_user.id)
    results[scenario_id] = result
    store_results(current_user.id, results)
    if _wants_json():
        return jsonify(_summary(result))
    if result['status'] == 'passed':
        flash(f"Сценарий {result['ft']} пройден: {result['conclusion']}",
              'success')
    else:
        flash(f"Сценарий {result['ft']} не пройден — см. результаты ниже",
              'danger')
    return redirect(url_for('testing.index'))


@bp.route('/testing/run/<scenario_id>/step')
@login_required
def run_step(scenario_id):
    """Результат одного шага последнего запуска сценария.

    Каждый шаг запрашивается отдельно, чтобы таблица заполнялась по мере
    поступления результатов, а не одним куском.
    """
    denied = _admin_guard()
    if denied:
        return denied
    from tests.runner import load_results
    result = load_results(current_user.id).get(scenario_id)
    if result is None:
        return jsonify(error='Сценарий ещё не выполнялся'), 404
    index = request.args.get('index', type=int)
    steps = result['steps']
    if index is None or not 0 <= index < len(steps):
        return jsonify(error='Шаг не найден'), 404
    return jsonify(index=index, total=len(steps), step=steps[index])


@bp.route('/testing/run-all', methods=['POST'])
@login_required
def run_all():
    """Выполнить все сценарии последовательно."""
    denied = _admin_guard()
    if denied:
        return denied
    from tests.runner import registry, run_scenario, store_results
    items = registry()
    results = {}
    total = passed = 0
    for item in items:
        result = run_scenario(item['id'])
        results[item['id']] = result
        total += 1
        passed += result['status'] == 'passed'
    store_results(current_user.id, results)
    if _wants_json():
        return jsonify(total=total, passed=passed,
                       scenarios=[_summary(results[item['id']])
                                  for item in items])
    flash(f'Выполнено сценариев: {total}, пройдено: {passed}',
          'success' if passed == total else 'danger')
    return redirect(url_for('testing.index'))
