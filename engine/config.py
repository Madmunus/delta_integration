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
    SECRET_KEY = os.environ.get('DELTA_SECRET_KEY')
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
