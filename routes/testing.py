"""Страница сценарного тестирования системы (доступ только руководителю).

Сценарии выполняются в изолированной базе данных и не затрагивают
рабочие данные. Результаты последнего запуска хранятся в сессии.
"""
from flask import (Blueprint, abort, flash, redirect, render_template,
                   url_for)
from flask_login import current_user, login_required

bp = Blueprint('testing', __name__)


def _admin_required():
    if not current_user.is_admin:
        abort(403)


@bp.route('/testing')
@login_required
def index():
    """Список сценариев тестирования и результаты последнего запуска."""
    _admin_required()
    from tests.runner import load_results, registry
    return render_template('testing/index.html',
                           scenarios=registry(),
                           results=load_results(current_user.id))


@bp.route('/testing/run/<scenario_id>', methods=['POST'])
@login_required
def run(scenario_id):
    """Выполнить один сценарий."""
    _admin_required()
    from tests.runner import load_results, run_scenario, store_results
    result = run_scenario(scenario_id)
    if result is None:
        abort(404)
    results = load_results(current_user.id)
    results[scenario_id] = result
    store_results(current_user.id, results)
    if result['status'] == 'passed':
        flash(f"Сценарий {result['ft']} пройден: {result['conclusion']}",
              'success')
    else:
        flash(f"Сценарий {result['ft']} не пройден — см. результаты ниже",
              'danger')
    return redirect(url_for('testing.index'))


@bp.route('/testing/run-all', methods=['POST'])
@login_required
def run_all():
    """Выполнить все сценарии последовательно."""
    _admin_required()
    from tests.runner import registry, run_scenario, store_results
    results = {}
    total = passed = 0
    for item in registry():
        result = run_scenario(item['id'])
        results[item['id']] = result
        total += 1
        passed += result['status'] == 'passed'
    store_results(current_user.id, results)
    flash(f'Выполнено сценариев: {total}, пройдено: {passed}',
          'success' if passed == total else 'danger')
    return redirect(url_for('testing.index'))
