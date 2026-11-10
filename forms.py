"""Формы Flask-WTF: CSRF-защита и валидация данных."""
from flask_wtf import FlaskForm
from wtforms import (BooleanField, DateField, DecimalField, IntegerField,
                     PasswordField, SelectField, SelectMultipleField, StringField,
                     SubmitField, TextAreaField)
from wtforms.validators import (DataRequired, Length, NumberRange,
                                Optional, Regexp)

# Лёгкая проверка формата email без обращения к внешним сервисам
# (email-validator по умолчанию отклоняет внутренние домены вида .local)
EMAIL_RE = r'^[^@\s]+@[^@\s]+\.[^@\s]+$'
EMAIL_MSG = 'Введите корректный email, например ivanov@company.ru'


class LoginForm(FlaskForm):
    email = StringField('Email', validators=[
        DataRequired(), Regexp(EMAIL_RE, message=EMAIL_MSG)])
    password = PasswordField('Пароль', validators=[DataRequired()])
    remember = BooleanField('Запомнить меня')
    submit = SubmitField('Войти')


class UserForm(FlaskForm):
    fio = StringField('ФИО', validators=[DataRequired(), Length(max=255)])
    email = StringField('Логин (email)', validators=[
        DataRequired(), Length(max=255), Regexp(EMAIL_RE, message=EMAIL_MSG)])
    # При редактировании пустой пароль = не менять
    password = PasswordField('Пароль', validators=[
        Optional(), Length(min=6, max=128,
                           message='Пароль должен быть не короче 6 символов')])
    role = SelectField('Роль', choices=[('admin', 'Руководитель'),
                                        ('employee', 'Сотрудник')])
    is_active = BooleanField('Активен')
    submit = SubmitField('Сохранить')


class ClientForm(FlaskForm):
    name = StringField('Наименование', validators=[DataRequired(), Length(max=512)])
    inn = StringField('ИНН', validators=[
        Optional(), Length(10, 12),
        Regexp(r'^\d+$', message='ИНН должен содержать только цифры')])
    kpp = StringField('КПП', validators=[
        Optional(), Length(9), Regexp(r'^\d+$', message='КПП должен содержать только цифры')])
    status = SelectField('Статус', choices=[('active', 'Активный'),
                                            ('archived', 'Архивный')])
    responsible_id = SelectField('Ответственный сотрудник', coerce=int,
                                 validators=[Optional()])
    notes = TextAreaField('Заметки', validators=[Optional(), Length(max=5000)])
    submit = SubmitField('Сохранить')


class ContractForm(FlaskForm):
    number = StringField('Номер договора', validators=[DataRequired(), Length(max=64)])
    signed_date = DateField('Дата договора', validators=[DataRequired()])
    client_id = SelectField('Клиент', coerce=int, validators=[DataRequired()])
    subject = TextAreaField('Предмет / перечень услуг', validators=[Optional()])
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


class ODataBaseForm(FlaskForm):
    name = StringField('Наименование базы', validators=[DataRequired(), Length(max=255)])
    odata_url = StringField('URL OData', validators=[
        DataRequired(), Length(max=512),
        Regexp(r'^https?://', message='URL должен начинаться с http:// или https://')])
    username = StringField('Пользователь', validators=[Optional(), Length(max=255)])
    password = PasswordField('Пароль', validators=[Optional(), Length(max=512)])
    is_enabled = BooleanField('Включена')
    submit = SubmitField('Сохранить')
