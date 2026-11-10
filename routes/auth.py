"""Аутентификация и разграничение доступа (ФТ-7, НФТ-4)."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from models import User, db

bp = Blueprint('auth', __name__)


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('reports.dashboard'))

    from forms import LoginForm
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip().lower()).first()
        if user is None or not user.check_password(form.password.data):
            flash('Неверный email или пароль', 'danger')
            return render_template('login.html', form=form), 401
        if not user.is_active_flag:
            flash('Учётная запись отключена', 'warning')
            return render_template('login.html', form=form)
        login_user(user, remember=form.remember.data)
        flash(f'Добро пожаловать, {user.fio}!', 'success')
        next_url = request.args.get('next')
        # Защита от open redirect
        if next_url and not next_url.startswith('/'):
            next_url = None
        return redirect(next_url or url_for('reports.dashboard'))

    return render_template('login.html', form=form)


@bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('auth.login'))
