"""Страница сценарного тестирования системы (доступ только руководителю).

Сценарии выполняются в изолированной базе данных и не затрагивают
рабочие данные. Результаты последнего запуска хранятся в памяти процесса.

Страница работает через AJAX: запуск сценария возвращает JSON-сводку,
а результат каждого шага запрашивается отдельным запросом
(``/testing/run/<scenario_id>/step?index=N``) и сразу выводится в таблицу.
Без JavaScript формы отправляются обычным POST с редиректом.
"""
from flask import (Blueprint, abort, current_app, flash, jsonify, redirect,
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
    from tests.runner import scenario_summary
    return scenario_summary(result)


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
    """Выполнить все сценарии (в живой системе — в фоновом потоке)."""
    denied = _admin_guard()
    if denied:
        return denied
    from tests.runner import start_run_all

    app = current_app._get_current_object()
    if _wants_json():
        job = start_run_all(current_user.id, app)
        if job.get('status') == 'running':
            # Живое приложение: клиент опрашивает status до завершения.
            return jsonify(job), 202
        # TestingConfig: синхронный путь, ответ сохраняет прежнюю форму.
        return jsonify(total=job['total'], passed=job['passed'],
                       scenarios=job['scenarios'])

    result = start_run_all(current_user.id, app, synchronous=True)
    flash(f"Выполнено сценариев: {result['total']}, "
          f"пройдено: {result['passed']}",
          'success' if result['passed'] == result['total'] else 'danger')
    return redirect(url_for('testing.index'))


@bp.route('/testing/run-all/status/<job_id>')
@login_required
def run_all_status(job_id):
    """Состояние фонового запуска всех сценариев."""
    denied = _admin_guard()
    if denied:
        return denied
    from tests.runner import get_run_all_job
    job = get_run_all_job(current_user.id, job_id)
    if job is None:
        return jsonify(error='Задание не найдено'), 404
    if job.get('status') == 'done':
        return jsonify(total=job['total'], completed=job['completed'],
                       passed=job['passed'], scenarios=job['scenarios'],
                       status='done', job_id=job['job_id'])
    return jsonify(job)
