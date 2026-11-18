"""Простейшие потокобезопасные метрики обращений к OData.

Счётчики живут в памяти процесса и сбрасываются при перезапуске. Этого
достаточно, чтобы увидеть ретраи, ошибки 429/5xx и размеры выборок на
странице «1С База» без внешних зависимостей (Prometheus и т.п.).
"""
import threading

_LOCK = threading.Lock()
_COUNTERS: dict[str, float] = {}

KEYS = (
    'requests',
    'cache_hits',
    'retries',
    'timeouts',
    'connection_errors',
    'http_304',
    'http_429',
    'http_5xx',
    'http_4xx',
    'bad_json',
    'pages',
    'rows',
    'document_entities',
    'document_workers',
    'document_failures',
    'breaker_skips',
    'sync_errors',
)


def increment(name: str, amount: int = 1) -> None:
    """Увеличить счётчик метрики."""
    with _LOCK:
        _COUNTERS[name] = _COUNTERS.get(name, 0.0) + int(amount)


def observe_latency(milliseconds: float) -> None:
    """Записать длительность HTTP-запроса."""
    with _LOCK:
        _COUNTERS['latency_total_ms'] = (
            _COUNTERS.get('latency_total_ms', 0.0) + float(milliseconds))
        _COUNTERS['latency_count'] = (
            _COUNTERS.get('latency_count', 0.0) + 1)


def snapshot() -> dict:
    """Снимок метрик для интерфейса и JSON-эндпоинта."""
    with _LOCK:
        values = dict(_COUNTERS)
    result: dict[str, float | int] = {
        key: int(values.get(key, 0)) for key in KEYS
    }
    count = int(values.get('latency_count', 0))
    total = float(values.get('latency_total_ms', 0.0))
    result['avg_latency_ms'] = round(total / count, 1) if count else 0.0
    return result


def reset() -> None:
    """Сбросить метрики (используется тестами)."""
    with _LOCK:
        _COUNTERS.clear()
