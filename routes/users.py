"""Управление пользователями (доступ только руководителю, ФТ-7)."""
from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import case

from engine.forms import UserForm
from engine.models import User, db

bp = Blueprint('users', __name__)


def _admin_required():
    if not current_user.is_admin:
        abort(403)


def _would_lose_last_admin(user, new_role: str, new_active: bool) -> bool:
    """Проверка: не останется ли система без единого активного руководителя."""
    if user.role != 'admin':
        return False
    if new_role == 'admin' and new_active:
        return False
    other_active_admins = User.query.filter(
        User.role == 'admin',
        User.is_active_flag.is_(True),
        User.id != user.id,
    ).count()
    return other_active_admins == 0


@bp.route('/users')
@login_required
def list_users():
    """Список пользователей системы."""
    _admin_required()
    # Сначала руководители, затем сотрудники;
    # внутри каждой группы — по фамилии (ФИО начинается с фамилии) по возрастанию
    users = User.query.order_by(
        case((User.role == 'admin', 0), else_=1),
        User.fio.asc(),
    ).all()
    return render_template('users/list.html', users=users)


@bp.route('/users/add', methods=['GET', 'POST'])
@login_required
def add_user():
    """Создание нового пользователя."""
    _admin_required()
    form = UserForm()
    if request.method == 'GET':
        form.is_active.data = True
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        if User.query.filter_by(email=email).first():
            flash('Пользователь с таким логином уже существует', 'danger')
        elif not form.password.data:
            flash('Для нового пользователя укажите пароль', 'danger')
        else:
            user = User(
                fio=form.fio.data.strip(),
                email=email,
                role=form.role.data,
                is_active_flag=form.is_active.data,
            )
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.commit()
            flash(f'Пользователь «{user.fio}» создан', 'success')
            return redirect(url_for('users.list_users'))
    return render_template('users/form.html', form=form, user=None)


@bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_user(user_id):
    """Карточка пользователя: логин, пароль, роль, активность."""
    _admin_required()
    user = User.query.get_or_404(user_id)
    form = UserForm(obj=user)

    if request.method == 'GET':
        form.is_active.data = user.is_active_flag
        form.password.data = ''  # текущий пароль не показываем

    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        new_role = form.role.data
        new_active = form.is_active.data

        if User.query.filter(User.email == email,
                             User.id != user.id).first():
            flash('Этот логин уже занят другим пользователем', 'danger')
        elif user.id == current_user.id and not new_active:
            flash('Нельзя отключить собственную учётную запись', 'danger')
        elif _would_lose_last_admin(user, new_role, new_active):
            flash('Нельзя понизить роль или отключить последнего '
                  'активного руководителя', 'danger')
        else:
            user.fio = form.fio.data.strip()
            user.email = email
            user.role = new_role
            user.is_active_flag = new_active
            if form.password.data:
                user.set_password(form.password.data)
                flash('Пароль обновлён', 'info')
            db.session.commit()
            flash(f'Пользователь «{user.fio}» сохранён', 'success')
            return redirect(url_for('users.list_users'))

    return render_template('users/form.html', form=form, user=user)


@bp.route('/users/<int:user_id>/toggle', methods=['POST'])
@login_required
def toggle_user(user_id):
    """Быстрое включение/отключение пользователя из списка."""
    _admin_required()
    user = User.query.get_or_404(user_id)
    new_active = not user.is_active_flag

    if user.id == current_user.id:
        flash('Нельзя отключить собственную учётную запись', 'danger')
    elif _would_lose_last_admin(user, user.role, new_active):
        flash('Нельзя отключить последнего активного руководителя', 'danger')
    else:
        user.is_active_flag = new_active
        db.session.commit()
        flash(f'Пользователь «{user.fio}» '
              f'{"включён" if new_active else "отключён"}', 'success')

    return redirect(url_for('users.list_users'))
