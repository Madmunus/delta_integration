"""Точка входа системы «Дельта».

Запуск:
    python app.py            — дев-режим (SQLite + планировщик)
Переменные окружения:
    DELTA_SECRET_KEY         — секретный ключ сессий
    DELTA_DATABASE_URL       — строка подключения (PostgreSQL в продакшене)
    DELTA_SYNC_INTERVAL      — период синхронизации клиентов, сек (по умолчанию 3600)
    DELTA_DOCUMENTS_INTERVAL — период загрузки документов, сек (по умолчанию 3600)
"""
import logging
import os

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import LoginManager, current_user, logout_user
from flask_wtf import CSRFProtect
from sqlalchemy import inspect, text

from engine import models as _m  # noqa: F401  (регистрация моделей в db)
from engine.config import config
from engine.forms import PASSWORD_HINT
from engine.models import User, db

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s: %(message)s')


def _ensure_schema():
    """Привести существующую БД к актуальной схеме без полноценных миграций."""
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    if not tables:
        return

    def columns_of(table):
        return {column['name'] for column in inspector.get_columns(table)}

    with db.engine.begin() as connection:
        # clients.date_added (DATE) → clients.created_at (DATETIME)
        if 'clients' in tables:
            client_columns = columns_of('clients')
            if 'date_added' in client_columns and 'created_at' not in client_columns:
                connection.execute(text(
                    'ALTER TABLE clients RENAME COLUMN date_added TO created_at'))
                client_columns.discard('date_added')
                client_columns.add('created_at')
            elif 'created_at' not in client_columns:
                connection.execute(text(
                    'ALTER TABLE clients ADD COLUMN created_at '
                    "DATETIME NOT NULL DEFAULT '1970-01-01 00:00:00'"))
                client_columns.add('created_at')
            # В старых записях хранилась только дата — добавляем время.
            connection.execute(text(
                "UPDATE clients SET created_at = created_at || ' 00:00:00' "
                'WHERE created_at IS NOT NULL AND length(created_at) = 10'))

        # created_at во всех остальных таблицах моделей
        for table in ('onec_settings', 'contractors', 'contracts',
                      'documents', 'reporting_deadlines'):
            if table not in tables:
                continue
            if 'created_at' not in columns_of(table):
                connection.execute(text(
                    f'ALTER TABLE {table} ADD COLUMN created_at '
                    "DATETIME NOT NULL DEFAULT '1970-01-01 00:00:00'"))
                connection.execute(text(
                    f'UPDATE {table} SET created_at = CURRENT_TIMESTAMP '
                    "WHERE created_at = '1970-01-01 00:00:00'"))

        # Внутренний признак договора «Обработан»
        if 'contracts' in tables and 'processed' not in columns_of('contracts'):
            connection.execute(text(
                'ALTER TABLE contracts ADD COLUMN processed '
                'BOOLEAN NOT NULL DEFAULT FALSE'))


def create_app(config_name=None):
    app = Flask(__name__)
    config_name = config_name or os.environ.get('DELTA_ENV', 'development')
    app.config.from_object(config.get(config_name, config['default']))

    # --- Расширения ---
    db.init_app(app)
    csrf = CSRFProtect(app)  # глобальная защита POST + csrf_token() в шаблонах

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

    # Отключённый пользователь теряет доступ к системе сразу,
    # даже если его сессия была активной
    @app.before_request
    def reject_disabled_users():
        if (current_user.is_authenticated
                and not current_user.is_active_flag
                and not request.path.startswith('/static/')):
            logout_user()
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
            return f'{float(value):,.2f}'.replace(',', ' ').replace('.', ',')
        except (TypeError, ValueError):
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
        _ensure_schema()

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
