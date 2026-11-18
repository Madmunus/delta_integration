"""Конфигурация системы «Дельта»."""
import os
import tempfile

# Корень проекта — родительская папка для engine/
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


class Config:
    """Базовая конфигурация (значения из переменных окружения)."""

    SECRET_KEY = os.environ.get('DELTA_SECRET_KEY', 'dev-secret-change-me')
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DELTA_DATABASE_URL',
        'sqlite:///' + os.path.join(BASE_DIR, 'delta.db'),
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- Интеграция с 1С ---
    # Ключ шифрования паролей подключения к 1С. Если не задан, ключ
    # детерминированно выводится из SECRET_KEY (для разработки и тестов).
    ONEC_ENCRYPTION_KEY = os.environ.get('DELTA_1C_ENCRYPTION_KEY')
    # Старый ключ (или список через запятую) для ротации без простоя.
    ONEC_ENCRYPTION_KEY_OLD = [
        item.strip() for item in
        (os.environ.get('DELTA_1C_ENCRYPTION_KEY_OLD') or '').split(',')
        if item.strip()
    ]
    # Таймауты HTTP-запросов к OData, сек
    ODATA_TIMEOUT = int(os.environ.get('DELTA_ODATA_TIMEOUT', '15'))
    # Размер страницы и параметры повторных запросов OData
    ODATA_PAGE_SIZE = int(os.environ.get('DELTA_ODATA_PAGE_SIZE', '1000'))
    ODATA_MAX_PAGES = int(os.environ.get('DELTA_ODATA_MAX_PAGES', '10000'))
    ODATA_MAX_RETRIES = int(os.environ.get('DELTA_ODATA_MAX_RETRIES', '3'))
    ODATA_RETRY_BACKOFF = float(
        os.environ.get('DELTA_ODATA_RETRY_BACKOFF', '0.5'))
    # Параллелизм загрузки разных видов документов из 1С
    ODATA_MAX_WORKERS = int(os.environ.get('DELTA_ODATA_MAX_WORKERS', '4'))

    # Инкрементальная синхронизация — opt-in: не все публикации 1С
    # отдают поле даты изменения, поэтому по умолчанию сохраняется
    # полная выборка (прошлое поведение).
    ODATA_INCREMENTAL_ENABLED = os.environ.get(
        'DELTA_ODATA_INCREMENTAL', '0').lower() in ('1', 'true', 'yes', 'on')
    ODATA_INCREMENTAL_CHANGE_FIELD = os.environ.get(
        'DELTA_ODATA_CHANGE_FIELD', 'ДатаИзменения')
    ODATA_INCREMENTAL_OVERLAP_MINUTES = int(os.environ.get(
        'DELTA_ODATA_INCREMENTAL_OVERLAP', '60'))
    ODATA_INCREMENTAL_DELETION_FIELD = os.environ.get(
        'DELTA_ODATA_DELETION_FIELD', 'DeletionMark')

    # Circuit breaker для per-client подключений к «1С:Бухгалтерия».
    ODATA_CIRCUIT_FAILURE_THRESHOLD = int(os.environ.get(
        'DELTA_ODATA_CIRCUIT_THRESHOLD', '5'))
    ODATA_CIRCUIT_COOLDOWN_SECONDS = int(os.environ.get(
        'DELTA_ODATA_CIRCUIT_COOLDOWN', '300'))
    ODATA_CIRCUIT_PERMANENT_COOLDOWN_SECONDS = int(os.environ.get(
        'DELTA_ODATA_CIRCUIT_PERMANENT_COOLDOWN', '3600'))
    # Очередь ошибок синхронизации
    SYNC_ERROR_RETRY_DELAY_SECONDS = int(os.environ.get(
        'DELTA_SYNC_ERROR_RETRY_DELAY', '300'))
    SYNC_ERROR_MAX_ATTEMPTS = int(os.environ.get(
        'DELTA_SYNC_ERROR_MAX_ATTEMPTS', '20'))

    # Период фоновой синхронизации справочника клиентов, сек
    SYNC_INTERVAL_SECONDS = int(os.environ.get('DELTA_SYNC_INTERVAL', '3600'))
    # Период фоновой загрузки документов из «1С:Документооборот», сек
    DOCUMENTS_SYNC_INTERVAL = int(
        os.environ.get('DELTA_DOCUMENTS_INTERVAL', '3600'))
    # Период проверки сроков отчётности, сек
    DEADLINE_CHECK_INTERVAL = int(os.environ.get('DELTA_DEADLINE_INTERVAL', '3600'))
    # За сколько дней до срока создавать задачу
    DEADLINE_LEAD_DAYS = int(os.environ.get('DELTA_DEADLINE_LEAD_DAYS', '7'))
    # Допуск опоздания заданий планировщика, сек
    SCHEDULER_MISFIRE_GRACE_SECONDS = int(
        os.environ.get('DELTA_MISFIRE_GRACE', '300'))

    # --- Безопасность ---
    SESSION_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    WTF_CSRF_ENABLED = True
    # Блокировка /login после повторных неудачных попыток входа.
    LOGIN_FAILURE_THRESHOLD = int(
        os.environ.get('DELTA_LOGIN_FAILURE_THRESHOLD', '5'))
    LOGIN_FAILURE_WINDOW_SECONDS = int(
        os.environ.get('DELTA_LOGIN_FAILURE_WINDOW', '900'))
    LOGIN_LOCKOUT_SECONDS = int(
        os.environ.get('DELTA_LOGIN_LOCKOUT', '900'))

    # Общее хранилище заданий сценарного тестирования: разделяется между
    # воркерами gunicorn. В тестовом режиме используется память процесса.
    TEST_JOB_DIR = os.environ.get(
        'DELTA_TEST_JOB_DIR',
        os.path.join(tempfile.gettempdir(), 'delta-test-jobs'))
    TEST_JOB_TTL_SECONDS = int(
        os.environ.get('DELTA_TEST_JOB_TTL', '1800'))


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    # В продакшене SECRET_KEY обязательно задаётся переменной окружения
    SECRET_KEY = os.environ.get('DELTA_SECRET_KEY') or ''
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = 'https'


class TestingConfig(Config):
    """Изолированная конфигурация для сценарного тестирования системы.

    База данных создаётся в памяти (sqlite://) и исчезает после теста,
    CSRF отключён — тестовый клиент отправляет формы без токена.
    """
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite://'
    WTF_CSRF_ENABLED = False
    SECRET_KEY = 'testing-secret'
    DEADLINE_LEAD_DAYS = 7


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig,
}
