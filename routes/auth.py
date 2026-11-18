"""Аутентификация и разграничение доступа (ФТ-1, НФТ-4)."""
from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user

from engine.audit import LOGIN, LOGIN_FAILED, LOGOUT, log_action
from engine.forms import LoginForm
from engine.login_guard import (check as check_login, record_failure,
                                record_success, scope as login_scope)
from engine.models import User
from engine.user_sessions import (_client_ip, start_session,
                                  terminate_current_session)

bp = Blueprint('auth', __name__)


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        key = login_scope(_client_ip(), email)
        allowed, retry_after = check_login(key)
        if not allowed:
            # Сообщение одинаково для любого email: блокировка не раскрывает,
            # существует ли учётная запись в системе.
            user = User.query.filter_by(email=email).first()
            if user is not None:
                log_action(LOGIN_FAILED, 'Лимит попыток входа исчерпан',
                           user=user)
            flash(f'Слишком много неудачных попыток. '
                  f'Повторите через {retry_after} с.', 'danger')
            return render_template('auth/login.html', form=form), 429
        user = User.query.filter_by(email=email).first()
        if user is None or not user.check_password(form.password.data):
            record_failure(key)
            if user is not None:
                log_action(LOGIN_FAILED, 'Неверный пароль', user=user)
            flash('Неверный email или пароль', 'danger')
            return render_template('auth/login.html', form=form), 401
        if not user.is_active_flag:
            log_action(LOGIN_FAILED, 'Учётная запись отключена', user=user)
            flash('Учётная запись отключена', 'warning')
            return render_template('auth/login.html', form=form)
        record_success(key)
        login_user(user, remember=form.remember.data)
        start_session(user)
        log_action(LOGIN, 'Вход в систему', user=user)
        flash(f'Добро пожаловать, {user.fio}!', 'success')
        next_url = request.args.get('next')
        # Защита от open redirect: только локальный абсолютный путь.
        if next_url:
            parsed = urlparse(next_url)
            if (parsed.scheme or parsed.netloc
                    or next_url.startswith('//') or '\\' in next_url):
                next_url = None
        return redirect(next_url or url_for('dashboard.index'))

    return render_template('auth/login.html', form=form)


@bp.route('/logout')
@login_required
def logout():
    log_action(LOGOUT, 'Выход из системы')
    terminate_current_session()
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('auth.login'))
