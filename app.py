"""Точка входа системы «Дельта».

Запуск:
    python app.py            — дев-режим (SQLite + планировщик)
Переменные окружения:
    DELTA_SECRET_KEY         — секретный ключ сессий
    DELTA_1C_ENCRYPTION_KEY  — ключ шифрования паролей подключения к 1С
    DELTA_DATABASE_URL       — строка подключения (PostgreSQL в продакшене)
    DELTA_SYNC_INTERVAL      — период синхронизации клиентов, сек (по умолчанию 3600)
    DELTA_DOCUMENTS_INTERVAL — период загрузки документов, сек (по умолчанию 3600)
"""
import logging
import os
from decimal import Decimal, InvalidOperation

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import LoginManager, current_user
from flask_wtf import CSRFProtect
from sqlalchemy import inspect, text
from sqlalchemy.orm import load_only

from engine import models as _m  # noqa: F401  (регистрация моделей в db)
from engine.config import config
from engine.forms import PASSWORD_HINT
from engine.models import (Client, OneCSetting, User,
                           assign_missing_task_assignees,
                           close_archived_client_contracts, db)
from engine.secret_storage import (SecretDecryptionError, encrypt_secret,
                                   is_encrypted, needs_rotation,
                                   reencrypt_secret)
from engine.user_sessions import enforce_session, terminate_current_session

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s: %(message)s')


def _ensure_schema_upgrades():
    """Добавить новые колонки в существующую базу (проект работает без Alembic).

    ``db.create_all()`` создаёт только отсутствующие таблицы, поэтому для
    уже существующих ``user_sessions`` и ``user_actions`` колонки добавляем
    явно. Для свежей базы запросы не выполняются — колонки уже есть.
    """
    upgrades = {
        'user_sessions': [('is_active_flag', 'BOOLEAN NOT NULL DEFAULT TRUE')],
        'user_actions': [('session_id', "VARCHAR(128) NOT NULL DEFAULT ''")],
    }
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    for table, columns in upgrades.items():
        if table not in tables:
            continue
        present = {column['name'] for column in inspector.get_columns(table)}
        for name, definition in columns:
            if name in present:
                continue
            with db.engine.begin() as connection:
                connection.execute(text(
                    f'ALTER TABLE {table} ADD COLUMN {name} {definition}'))

    # db.create_all() создаёт индексы только вместе с новой таблицей.
    # В уже существующей базе объявленные в моделях составные индексы
    # остаются отсутствующими, поэтому создаём их идемпотентно по metadata.
    for table in db.metadata.tables.values():
        for index in table.indexes:
            index.create(bind=db.engine, checkfirst=True)


def _encrypt_legacy_onec_passwords():
    """Зашифровать пароли 1С, сохранённые до внедрения шифрования."""
    changed = False
    setting = db.session.get(OneCSetting, 1)
    if setting and setting.password and not is_encrypted(setting.password):
        setting.password = encrypt_secret(setting.password)
        changed = True
    # При запуске достаточно id и пароля: полные объекты клиентов с заметками
    # и реквизитами подключения здесь не нужны.
    clients = (Client.query
               .options(load_only(Client.id, Client.odata_password))
               .filter(Client.odata_password != '').all())
    for client in clients:
        if not is_encrypted(client.odata_password):
            client.odata_password = encrypt_secret(client.odata_password)
            changed = True
    if changed:
        db.session.commit()


def _rotate_onec_secrets():
    """Перешифровать пароли 1С текущим ключом после ротации.

    Старый ключ задаётся через ``DELTA_1C_ENCRYPTION_KEY_OLD``; пока он
    указан, ``decrypt_secret()`` умеет читать старые значения. Миграция
    идемпотентна: если значение уже открывается текущим ключом, оно не
    меняется. При отсутствии старого ключа ошибка логируется, но запуск
    приложения не прерывается — администратор видит проблему в логе и
    может задать правильный OLD-ключ без потери данных.
    """
    logger = logging.getLogger(__name__)
    changed = 0
    setting = db.session.get(OneCSetting, 1)
    if setting and needs_rotation(setting.password):
        try:
            setting.password = reencrypt_secret(setting.password)
            changed += 1
        except SecretDecryptionError:
            logger.exception('Не удалось ротировать пароль главной базы 1С')
    clients = (Client.query
               .options(load_only(Client.id, Client.odata_password))
               .filter(Client.odata_password != '').all())
    for client in clients:
        if not needs_rotation(client.odata_password):
            continue
        try:
            client.odata_password = reencrypt_secret(client.odata_password)
            changed += 1
        except SecretDecryptionError:
            logger.exception(
                'Не удалось ротировать пароль 1С клиента #%s', client.id)
    if changed:
        db.session.commit()
        logger.info('Перешифровано паролей 1С после ротации ключа: %s', changed)


def create_app(config_name=None):
    app = Flask(__name__)
    config_name = config_name or os.environ.get('DELTA_ENV', 'development')
    app.config.from_object(config.get(config_name, config['default']))

    # --- Расширения ---
    db.init_app(app)
    CSRFProtect(app)  # глобальная защита POST + csrf_token() в шаблонах

    login_manager = LoginManager()
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Войдите в систему для доступа к этой странице.'
    login_manager.login_message_category = 'warning'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # --- Маршруты (отдельный модуль на каждый раздел навигации) ---
    from routes.auth import bp as auth_bp
    from routes.bases import bp as bases_bp
    from routes.clients import bp as clients_bp
    from routes.contractors import bp as contractors_bp
    from routes.contracts import bp as contracts_bp
    from routes.dashboard import bp as dashboard_bp
    from routes.deadlines import bp as deadlines_bp
    from routes.documents import bp as documents_bp
    from routes.reports import bp as reports_bp
    from routes.tasks import bp as tasks_bp
    from routes.testing import bp as testing_bp
    from routes.users import bp as users_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(clients_bp)
    app.register_blueprint(contractors_bp)
    app.register_blueprint(contracts_bp)
    app.register_blueprint(documents_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(deadlines_bp)
    app.register_blueprint(bases_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(testing_bp)

    # Сессия считается активной, пока её запись есть в базе и не истекла.
    # Удалённая из другой сессии запись разлогинивает пользователя на
    # следующем же запросе, даже если cookie ещё подписана корректно.
    @app.before_request
    def enforce_active_session():
        return enforce_session()

    # Отключённый пользователь теряет доступ к системе сразу,
    # даже если его сессия была активной
    @app.before_request
    def reject_disabled_users():
        if (current_user.is_authenticated
                and not current_user.is_active_flag
                and not request.path.startswith('/static/')):
            terminate_current_session()
            flash('Ваша учётная запись отключена', 'warning')
            return redirect(url_for('auth.login'))

    # --- Контекст шаблонов ---
    @app.context_processor
    def inject_globals():
        # Текущий раздел для подсветки пункта в навигации
        nav_prefixes = [
            ('dashboard', '/dashboard'),
            ('clients', '/clients'),
            ('contractors', '/contractors'),
            ('contracts', '/contracts'),
            ('documents', '/documents'),
            ('tasks', '/tasks'),
            ('deadlines', '/deadlines'),
            ('reports', '/reports'),
            ('bases', '/bases'),
            ('users', '/users'),
            ('testing', '/testing'),
        ]
        active_nav = ''
        for key, prefix in nav_prefixes:
            if request.path == prefix or request.path.startswith(prefix + '/'):
                active_nav = key
                break
        return {'current_user': current_user, 'active_nav': active_nav,
                'PASSWORD_HINT': PASSWORD_HINT}

    @app.template_filter('dt')
    def format_date(value):
        if value is None:
            return '—'
        try:
            return value.strftime('%d.%m.%Y')
        except AttributeError:
            return str(value)

    @app.template_filter('dtfull')
    def format_datetime(value):
        """Дата и время создания записи: дд.мм.гггг чч:мм:сс."""
        if value is None:
            return '—'
        try:
            return value.strftime('%d.%m.%Y %H:%M:%S')
        except AttributeError:
            return str(value)

    @app.template_filter('money')
    def format_money(value):
        if value is None:
            return '—'
        try:
            # Decimal форматируем напрямую: float теряет точность на суммах.
            text = (f'{value:,.2f}' if isinstance(value, Decimal)
                    else f'{float(value):,.2f}')
            return text.replace(',', ' ').replace('.', ',')
        except (TypeError, ValueError, InvalidOperation):
            return str(value)

    # --- Обработка ошибок ---
    @app.errorhandler(403)
    def forbidden(e):
        return render_template('errors/403.html'), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def server_error(e):
        db.session.rollback()
        return render_template('errors/500.html'), 500

    @app.route('/')
    def index():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard.index'))
        return redirect(url_for('auth.login'))

    # --- База данных ---
    with app.app_context():
        db.create_all()
        _ensure_schema_upgrades()
        _encrypt_legacy_onec_passwords()
        _rotate_onec_secrets()
        # Приводим существующие данные к правилам системы:
        # — у архивных клиентов не может быть проектов и действующих
        #   договоров;
        # — задача не может быть без исполнителя.
        closed = close_archived_client_contracts()
        assigned = assign_missing_task_assignees()
        if closed or assigned:
            db.session.commit()
            logger = logging.getLogger(__name__)
            if closed:
                logger.info('У архивных клиентов закрыто договоров: %s', closed)
            if assigned:
                logger.info('Задачам без исполнителя назначен исполнитель: %s',
                            assigned)

    return app


app = create_app()

# --- Планировщик ---
# В dev-режиме запускается только в дочернем процессе отладчика; при импорте
# приложения (gunicorn, `python -m engine.run_jobs`) не стартует, чтобы задания
# не дублировались. В продакшене используйте `python app.py` или cron/run_jobs.
if (os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
        or (__name__ == '__main__' and app.config.get('DEBUG') is False)):
    from engine.tasks import init_scheduler
    init_scheduler(app)


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000)
