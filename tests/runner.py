"""Реестр сценариев тестирования и их запуск (консоль и веб-интерфейс).

Консольный запуск всех сценариев:
    python -m tests.runner

Возвращает код выхода 0, если все сценарии пройдены, и 1 — иначе.
"""
import hashlib
import importlib
import json
import os
import threading
import time
import traceback
import uuid
from datetime import datetime

from engine.config import Config

# (идентификатор, модуль сценария) — порядок отображения на странице
SCENARIOS = [
    ('scenario_1', 'tests.scenario_1_access'),
    ('scenario_2', 'tests.scenario_2_sync'),
    ('scenario_3', 'tests.scenario_3_contracts'),
    ('scenario_4', 'tests.scenario_4_documents'),
    ('scenario_5', 'tests.scenario_5_tasks'),
    ('scenario_6', 'tests.scenario_6_deadlines'),
    ('scenario_7', 'tests.scenario_7_reports'),
    ('scenario_8', 'tests.scenario_8_testing'),
    ('scenario_9', 'tests.scenario_9_query_budget'),
]


# Последние результаты запуска по пользователям. В живом приложении они
# хранятся в общем каталоге: отчёт больше лимита cookie, а воркеры gunicorn
# не должны терять шаги при опросе с другого процесса. В TESTING всё остаётся
# в памяти изолированного приложения.
_RESULTS = {}

# Фоновые запуски «выполнить все». Ключ — (база, пользователь, job_id).
_JOBS = {}
_JOB_LOCK = threading.Lock()
_FILE_LOCK = threading.Lock()
_LAST_CLEANUP = [0.0]


def _app_setting(name, default):
    from flask import current_app, has_app_context
    if has_app_context():
        return current_app.config.get(name, default)
    return default


def _shared_storage():
    """Общий каталог используется только в живом приложении."""
    from flask import current_app, has_app_context
    return has_app_context() and not current_app.config.get('TESTING', False)


def _store_dir():
    directory = _app_setting('TEST_JOB_DIR', Config.TEST_JOB_DIR)
    os.makedirs(directory, exist_ok=True)
    return directory


def _scope(user_id):
    """Пространство результатов: рабочая база и тестовая — раздельно."""
    from flask import current_app, has_app_context
    if has_app_context():
        return (current_app.config.get('SQLALCHEMY_DATABASE_URI'), user_id)
    return ('cli', user_id)


def _scope_key(scope):
    return hashlib.sha1(repr(scope).encode('utf-8')).hexdigest()[:16]


def _write_json(path, payload):
    """Атомарная запись: читатель не увидит незавершённый файл."""
    tmp = f'{path}.tmp-{uuid.uuid4().hex}'
    with open(tmp, 'w', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False)
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return None


def _ttl():
    return max(60, int(_app_setting(
        'TEST_JOB_TTL_SECONDS', Config.TEST_JOB_TTL_SECONDS)))


def _cleanup_store():
    """Удалить просроченные задания и результаты (не чаще раза в минуту)."""
    if not _shared_storage():
        return
    now = time.time()
    with _FILE_LOCK:
        if now - _LAST_CLEANUP[0] < 60:
            return
        _LAST_CLEANUP[0] = now
    directory = _store_dir()
    ttl = _ttl()
    for name in os.listdir(directory):
        if not name.startswith(('job_', 'results_', 'active_')):
            continue
        path = os.path.join(directory, name)
        payload = _read_json(path) or {}
        stamp = (payload.get('created_at') or payload.get('saved_at')
                 or payload.get('updated_at') or 0)
        try:
            if now - float(stamp) > ttl:
                os.remove(path)
        except (OSError, ValueError, TypeError):
            continue


def _results_path(scope):
    return os.path.join(_store_dir(), f'results_{_scope_key(scope)}.json')


def _job_path(scope, job_id):
    return os.path.join(
        _store_dir(), f'job_{_scope_key(scope)}_{str(job_id)}.json')


def _active_path(scope):
    return os.path.join(_store_dir(), f'active_{_scope_key(scope)}.lock')


def _save_job(scope, job, shared=None):
    # Фоновый поток не наследует контекст Flask, поэтому способ хранения
    # передаётся явно из запроса, где задание было создано.
    if _shared_storage() if shared is None else shared:
        _write_json(_job_path(scope, job['job_id']), {
            'created_at': time.time(),
            'job': job,
        })
    else:
        with _JOB_LOCK:
            _JOBS[(scope, str(job['job_id']))] = job


def _load_job(scope, job_id, shared=None):
    if _shared_storage() if shared is None else shared:
        payload = _read_json(_job_path(scope, job_id))
        return payload.get('job') if payload else None
    with _JOB_LOCK:
        return _JOBS.get((scope, str(job_id)))


def _running_job(scope):
    """Активный запуск пользователя, если он ещё не завершён."""
    if not _shared_storage():
        with _JOB_LOCK:
            for job in _JOBS.values():
                if job.get('status') == 'running':
                    return job
        return None
    prefix = f'job_{_scope_key(scope)}_'
    found = []
    directory = _store_dir()
    now = time.time()
    for name in os.listdir(directory):
        if not name.startswith(prefix):
            continue
        payload = _read_json(os.path.join(directory, name))
        job = payload.get('job') if payload else None
        if not job or job.get('status') != 'running':
            continue
        # Задание, просроченное по TTL, считается брошенным: иначе сбойный
        # воркер блокировал бы новые запуски до следующей чистки хранилища.
        try:
            if now - float(payload.get('created_at', 0)) > _ttl():
                continue
        except (TypeError, ValueError):
            continue
        found.append((payload.get('created_at', 0), job))
    if not found:
        return None
    return max(found, key=lambda item: item[0])[1]


def _acquire_start_lock(scope):
    """Атомарная блокировка: одновременно может идти только один run-all."""
    path = _active_path(scope)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump({'created_at': time.time()}, stream)
            return True
        except FileExistsError:
            payload = _read_json(path) or {}
            try:
                stale = time.time() - float(payload.get('created_at', 0)) > _ttl()
            except (TypeError, ValueError):
                stale = True
            if stale:
                try:
                    os.remove(path)
                except OSError:
                    return False
                continue
            return False
    return False


def _release_start_lock(scope, shared=None):
    if not (_shared_storage() if shared is None else shared):
        return
    try:
        os.remove(_active_path(scope))
    except OSError:
        pass


def store_results(user_id, results):
    """Сохранить результаты последнего запуска для пользователя."""
    scope = _scope(user_id)
    if _shared_storage():
        _cleanup_store()
        _write_json(_results_path(scope), {
            'saved_at': time.time(),
            'results': results,
        })
    else:
        _RESULTS[scope] = results


def load_results(user_id):
    """Результаты последнего запуска пользователя (или пустой словарь)."""
    scope = _scope(user_id)
    if _shared_storage():
        payload = _read_json(_results_path(scope))
        return (payload or {}).get('results') or {}
    return _RESULTS.get(scope) or {}


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


def scenario_summary(result):
    """Сводка сценария для страницы тестирования (без массива шагов)."""
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


def run_all_sync(app, user_id, progress=None):
    """Выполнить все сценарии в текущем потоке и сохранить результат."""
    with app.app_context():
        items = registry()
        results = {}
        total = passed = 0
        for index, item in enumerate(items, 1):
            result = run_scenario(item['id'])
            results[item['id']] = result
            total += 1
            if result['status'] == 'passed':
                passed += 1
            if progress is not None:
                progress(index, passed)
        store_results(user_id, results)
    return {
        'total': total,
        'passed': passed,
        'scenarios': [scenario_summary(results[item['id']]) for item in items],
    }


def _job_snapshot(job):
    """Копия состояния задания: не отдавать внутренние объекты наружу."""
    return dict(job)


def _run_all_worker(app, user_id, job_id, scope, shared):
    import logging

    def progress(completed, passed):
        job = _load_job(scope, job_id, shared)
        if job is not None:
            job['completed'] = completed
            job['passed'] = passed
            _save_job(scope, job, shared)

    try:
        result = run_all_sync(app, user_id, progress=progress)
        job = _load_job(scope, job_id, shared)
        if job is not None:
            job.update(result)
            job['completed'] = job['total']
            job['status'] = 'done'
            _save_job(scope, job, shared)
    except Exception as exc:  # задание не должно исчезать без ответа
        logging.getLogger(__name__).exception(
            'Фоновый запуск всех сценариев завершился сбоем')
        job = _load_job(scope, job_id, shared)
        if job is not None:
            job['status'] = 'failed'
            job['error'] = str(exc)
            _save_job(scope, job, shared)
    finally:
        _release_start_lock(scope, shared)


def start_run_all(user_id, app, synchronous=False):
    """Запустить все сценарии: синхронно в тестах, в фоне в живом приложении."""
    if synchronous or app.config.get('TESTING'):
        # В тестовом режиме не трогаем общий каталог и не создаём поток.
        return run_all_sync(app, user_id)

    scope = _scope(user_id)
    shared = _shared_storage()
    _cleanup_store()
    if not _acquire_start_lock(scope):
        # Повторный запуск не создаёт вторую копию сценариев: клиенту
        # возвращается уже работающее задание, которое он и опрашивает.
        running = _running_job(scope)
        if running is not None:
            return _job_snapshot(running)

    job_id = f'{int(time.time())}-{uuid.uuid4().hex[:8]}'
    job = {
        'job_id': job_id,
        'status': 'running',
        'total': len(SCENARIOS),
        'completed': 0,
        'passed': 0,
        'scenarios': [],
        'error': None,
    }
    _save_job(scope, job, shared)
    thread = threading.Thread(
        target=_run_all_worker,
        args=(app, user_id, job_id, scope, shared),
        name=f'delta-tests-{job_id}',
        daemon=True,
    )
    thread.start()
    return _job_snapshot(job)


def get_run_all_job(user_id, job_id):
    """Состояние фонового задания (только для его владельца)."""
    _cleanup_store()
    job = _load_job(_scope(user_id), str(job_id))
    return _job_snapshot(job) if job is not None else None


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
