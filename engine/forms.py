"""Формы Flask-WTF: CSRF-защита и валидация данных."""
import re

from flask_wtf import FlaskForm
from wtforms import (BooleanField, DateField, DecimalField, IntegerField,
                     PasswordField, SelectField, SelectMultipleField, StringField,
                     SubmitField, TextAreaField)
from wtforms.validators import (DataRequired, Length, NumberRange,
                                Optional, Regexp, ValidationError)

# Лёгкая проверка формата email без обращения к внешним сервисам
# (email-validator по умолчанию отклоняет внутренние домены вида .local)
EMAIL_RE = r'^[^@\s]+@[^@\s]+\.[^@\s]+$'
EMAIL_MSG = 'Введите корректный email, например ivanov@company.ru'

# Политика паролей: не менее 13 символов, минимум одна цифра,
# заглавная и строчная латинские буквы, спецсимвол.
PASSWORD_RULES = [
    (r'.{13,}', 'не менее 13 символов'),
    (r'\d', 'хотя бы одну цифру'),
    (r'[A-Z]', 'хотя бы одну заглавную латинскую букву'),
    (r'[a-z]', 'хотя бы одну строчную латинскую букву'),
    (r'[^0-9A-Za-z]', 'хотя бы один спецсимвол'),
]
PASSWORD_HINT = ('не менее 13 символов, минимум одна цифра, '
                 'заглавная и строчная латинские буквы, спецсимвол; '
                 'без частей логина и транслитерации ФИО')

# Транслитерация кириллицы для проверки пароля на части ФИО
TRANSLIT_MAP = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e',
    'ё': 'e', 'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k',
    'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r',
    'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'kh', 'ц': 'ts',
    'ч': 'ch', 'ш': 'sh', 'щ': 'shch', 'ъ': '', 'ы': 'y', 'ь': '',
    'э': 'e', 'ю': 'yu', 'я': 'ya',
}
# Минимальная длина совпадающей части логина/ФИО, запрещённой в пароле
PASSWORD_IDENTITY_PART_LEN = 4


def check_password_policy(value):
    """Список нарушений политики паролей для заданного значения."""
    return [msg for pattern, msg in PASSWORD_RULES
            if not re.search(pattern, value or '')]


def transliterate(text):
    """Транслитерация кириллического текста в латиницу."""
    return ''.join(TRANSLIT_MAP.get(ch, ch) for ch in (text or '').lower())


def _identity_parts(login, fio):
    """Части логина и транслитерации ФИО, которые нельзя включать в пароль."""
    sources = [(login or '').split('@', 1)[0]]
    sources += re.findall(r'[a-z0-9]+', transliterate(fio))
    parts = set()
    for source in sources:
        source = re.sub(r'[^a-z0-9]', '', source.lower())
        if not source:
            continue
        if len(source) < PASSWORD_IDENTITY_PART_LEN:
            if len(source) >= 2:
                parts.add(source)
            continue
        for i in range(len(source) - PASSWORD_IDENTITY_PART_LEN + 1):
            parts.add(source[i:i + PASSWORD_IDENTITY_PART_LEN])
    return parts


def password_contains_identity(value, login, fio):
    """True, если пароль содержит часть логина или транслитерации ФИО."""
    lowered = (value or '').lower()
    return any(part in lowered for part in _identity_parts(login, fio))


class LoginForm(FlaskForm):
    email = StringField('Email', validators=[
        DataRequired(), Regexp(EMAIL_RE, message=EMAIL_MSG)])
    password = PasswordField('Пароль', validators=[DataRequired()])
    remember = BooleanField('Запомнить меня')
    submit = SubmitField('Войти')


class AccountForm(FlaskForm):
    """Базовые поля учётной записи: ФИО, логин и смена пароля."""
    fio = StringField('ФИО', validators=[DataRequired(), Length(max=255)])
    email = StringField('Логин (email)', validators=[
        DataRequired(), Length(max=255), Regexp(EMAIL_RE, message=EMAIL_MSG)])
    # При редактировании пустой пароль = не менять
    password = PasswordField('Пароль', validators=[Optional(), Length(max=128)])
    password_confirm = PasswordField('Подтверждение пароля', validators=[
        Optional(), Length(max=128)])
    submit = SubmitField('Сохранить')

    def validate_password(self, field):
        """Проверка политики паролей (пустое значение не проверяем)."""
        if not field.data:
            return
        errors = check_password_policy(field.data)
        if errors:
            raise ValidationError('Пароль должен содержать '
                                  + ', '.join(errors) + '.')
        if password_contains_identity(field.data,
                                      self.email.data or '',
                                      self.fio.data or ''):
            raise ValidationError('Пароль не должен содержать частей логина '
                                  'или транслитерации ФИО.')

    def validate_password_confirm(self, field):
        """Пароль и подтверждение должны совпадать."""
        if (self.password.data or '') != (field.data or ''):
            raise ValidationError('Пароли не совпадают.')


class UserForm(AccountForm):
    """Карточка пользователя для руководителя: плюс роль и активность."""
    role = SelectField('Роль', choices=[('admin', 'Руководитель'),
                                        ('employee', 'Сотрудник')])
    is_active = BooleanField('Активен')


class ProfileForm(AccountForm):
    """Самостоятельное редактирование своих данных без роли и активности."""


class ClientForm(FlaskForm):
    """Редактирование клиента: ответственный, заметки и реквизиты 1С."""
    responsible_id = SelectField('Ответственный сотрудник', coerce=int,
                                 validators=[Optional()])
    notes = TextAreaField('Заметки', validators=[Optional(), Length(max=5000)])
    # Подключение к «1С:Бухгалтерия» клиента (справочник Catalog_Договоры)
    odata_url = StringField('URL OData', validators=[
        Optional(), Length(max=512),
        Regexp(r'^(https?://.+)?$',
               message='URL должен начинаться с http:// или https://')])
    odata_username = StringField('Пользователь', validators=[Optional(), Length(max=255)])
    odata_password = PasswordField('Пароль', validators=[Optional(), Length(max=512)])
    submit = SubmitField('Сохранить')


class ContractForm(FlaskForm):
    number = StringField('Номер договора', validators=[DataRequired(), Length(max=64)])
    signed_date = DateField('Дата договора', validators=[DataRequired()])
    client_id = SelectField('Клиент', coerce=int, validators=[DataRequired()])
    contractor_id = SelectField('Контрагент', coerce=int, validators=[Optional()])
    description = TextAreaField('Описание', validators=[Optional()])
    amount = DecimalField('Стоимость, руб.', places=2, validators=[
        Optional(), NumberRange(min=0)])
    start_date = DateField('Действует с', validators=[Optional()])
    end_date = DateField('Действует по', validators=[Optional()])
    status = SelectField('Статус', choices=[('draft', 'Проект'),
                                            ('active', 'Действующий'),
                                            ('closed', 'Завершённый')])
    submit = SubmitField('Сохранить')


class TaskForm(FlaskForm):
    title = StringField('Наименование', validators=[DataRequired(), Length(max=512)])
    description = TextAreaField('Описание', validators=[Optional()])
    client_id = SelectField('Клиент', coerce=int, validators=[Optional()])
    assignee_id = SelectField('Исполнитель', coerce=int, validators=[Optional()])
    due_date = DateField('Срок', validators=[DataRequired()])
    priority = SelectField('Приоритет', coerce=int, choices=[
        (1, 'Высокий'), (2, 'Средний'), (3, 'Низкий')])
    status = SelectField('Статус', choices=[
        ('new', 'Новая'), ('in_progress', 'В работе'),
        ('done', 'Выполнена'), ('overdue', 'Просрочена')])
    submit = SubmitField('Сохранить')


class ReportingDeadlineForm(FlaskForm):
    name = StringField('Наименование формы', validators=[DataRequired(), Length(max=255)])
    code = StringField('Код', validators=[DataRequired(), Length(max=64)])
    periodicity = SelectField('Периодичность', choices=[
        ('monthly', 'Ежемесячная'), ('quarterly', 'Ежеквартальная'),
        ('annual', 'Годовая')])
    due_day = IntegerField('День сдачи', validators=[
        DataRequired(), NumberRange(min=1, max=31)])
    period_offset = IntegerField('Смещение периода (мес.)', validators=[
        DataRequired(), NumberRange(min=0, max=6)], default=0)
    taxpayer_category = StringField('Категория налогоплательщика',
                                    validators=[Optional(), Length(max=50)])
    description = TextAreaField('Описание', validators=[Optional()])
    is_enabled = BooleanField('Включена')
    submit = SubmitField('Сохранить')


class OneCSettingForm(FlaskForm):
    """Настройка единственного подключения к главной базе 1С:Предприятие."""
    odata_url = StringField('URL OData', validators=[
        DataRequired(), Length(max=512),
        Regexp(r'^https?://', message='URL должен начинаться с http:// или https://')])
    username = StringField('Пользователь', validators=[Optional(), Length(max=255)])
    password = PasswordField('Пароль', validators=[Optional(), Length(max=512)])
    submit = SubmitField('Сохранить настройку')
