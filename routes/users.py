"""Управление пользователями: руководитель — все, сотрудник — свои данные."""
from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import case

from engine.audit import (LATEST_LIMIT, SESSIONS_TERMINATED, USER_CREATE,
                          USER_TOGGLE, USER_UPDATE, latest_actions, log_action)
from engine.forms import ProfileForm, UserForm
from engine.models import User, db
from engine.user_sessions import (current_sid, list_sessions,
                                  terminate_others)

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
    # Сначала руководители, затем активные пользователи, затем отключённые;
    # внутри каждой группы — по ФИО (ФИО начинается с фамилии) по возрастанию
    users = User.query.order_by(
        case(
            (User.role == 'admin', 0),
            (User.is_active_flag.is_(True), 1),
            else_=2,
        ),
        User.fio.asc(),
    ).all()
    return render_template('users/list.html', users=users, users_total=len(users))


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
            log_action(USER_CREATE,
                       f'Создан пользователь «{user.fio}» ({user.email})')
            flash(f'Пользователь «{user.fio}» создан', 'success')
            return redirect(url_for('users.list_users'))
    return render_template('users/form.html', form=form, user=None,
                           is_admin_form=True)


@bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_user(user_id):
    """Карточка пользователя: свои данные доступны любому пользователю."""
    user = User.query.get_or_404(user_id)
    is_self = user.id == current_user.id
    is_admin = current_user.is_admin
    if not (is_admin or is_self):
        abort(403)

    form_cls = UserForm if is_admin else ProfileForm
    form = form_cls(obj=user)

    if request.method == 'GET':
        if is_admin:
            form.is_active.data = user.is_active_flag
        form.password.data = ''  # текущий пароль не показываем
        form.password_confirm.data = ''

    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        new_role = form.role.data if is_admin else user.role
        new_active = form.is_active.data if is_admin else user.is_active_flag

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
            if is_admin:
                user.role = new_role
                user.is_active_flag = new_active
            if form.password.data:
                user.set_password(form.password.data)
                flash('Пароль обновлён', 'info')
            db.session.commit()
            log_action(USER_UPDATE,
                       f'Изменён пользователь «{user.fio}» ({user.email})'
                       + ('; пароль обновлён' if form.password.data else ''))
            flash(f'Пользователь «{user.fio}» сохранён', 'success')
            if is_self:
                return redirect(url_for('users.edit_user', user_id=user.id))
            return redirect(url_for('users.list_users'))

    own_sessions = list_sessions(user.id)
    active_count = sum(1 for s in own_sessions if s.is_active_flag)
    lifetime_days = max(1, int(
        current_app.permanent_session_lifetime.total_seconds() // 86400))
    return render_template('users/form.html', form=form, user=user,
                           is_admin_form=is_admin, is_self=is_self,
                           user_sessions=own_sessions,
                           active_sessions_count=active_count,
                           current_sid=current_sid(),
                           session_lifetime_days=lifetime_days,
                           user_actions=latest_actions(user.id, LATEST_LIMIT))


@bp.route('/users/<int:user_id>/sessions/logout-others', methods=['POST'])
@login_required
def logout_other_sessions(user_id):
    """Завершить все активные сессии пользователя, кроме текущей."""
    if user_id != current_user.id:
        abort(403)

    removed = terminate_others(current_user.id, current_sid())
    log_action(SESSIONS_TERMINATED, f'Завершено других сессий: {removed}')
    if removed:
        flash(f'Завершено других сессий: {removed}.', 'success')
    else:
        flash('Других активных сессий нет.', 'info')
    return redirect(url_for('users.edit_user', user_id=current_user.id))


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
        log_action(USER_TOGGLE,
                   f'Пользователь «{user.fio}» '
                   f'{"включён" if new_active else "отключён"}')
        flash(f'Пользователь «{user.fio}» '
              f'{"включён" if new_active else "отключён"}', 'success')

    return redirect(url_for('users.list_users'))
