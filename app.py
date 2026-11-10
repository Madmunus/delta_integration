"""Точка входа системы «Дельта».

Запуск:
    python app.py            — дев-режим (SQLite + планировщик)
Переменные окружения:
    DELTA_SECRET_KEY         — секретный ключ сессий
    DELTA_DATABASE_URL       — строка подключения (PostgreSQL в продакшене)
    DELTA_SYNC_INTERVAL      — период синхронизации, сек (по умолчанию 3600)
"""
import logging
import os

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import LoginManager, current_user, logout_user
from flask_wtf import CSRFProtect

import models as _m  # noqa: F401  (регистрация моделей в db)
from config import config
from models import User, db

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s: %(message)s')


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

    # --- Маршруты ---
    from routes.auth import bp as auth_bp
    from routes.clients import bp as clients_bp
    from routes.reports import bp as reports_bp
    from routes.tasks import bp as tasks_bp
    from routes.users import bp as users_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(reports_bp)   # включает /dashboard
    app.register_blueprint(clients_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(users_bp)

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
            ('contracts', '/contracts'),
            ('tasks', '/tasks'),
            ('deadlines', '/deadlines'),
            ('reports', '/reports'),
            ('bases', '/bases'),
            ('users', '/users'),
        ]
        active_nav = ''
        for key, prefix in nav_prefixes:
            if request.path == prefix or request.path.startswith(prefix + '/'):
                active_nav = key
                break
        return {'current_user': current_user, 'active_nav': active_nav}

    @app.template_filter('dt')
    def format_date(value):
        if value is None:
            return '—'
        try:
            return value.strftime('%d.%m.%Y')
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
            return redirect(url_for('reports.dashboard'))
        return redirect(url_for('auth.login'))

    # --- База данных ---
    with app.app_context():
        db.create_all()

    return app


app = create_app()

# --- Планировщик (только вне отладчика, чтобы не дублировать процессы) ---
if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or app.config.get('DEBUG') is False:
    from services.tasks import init_scheduler
    init_scheduler(app)


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000)
