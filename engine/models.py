"""Модели данных системы (SQLAlchemy ORM)."""
from datetime import date, datetime

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


# ----------------------------------------------------------------------------
# Пользователь (ФТ-7: ролевая модель admin / employee)
# ----------------------------------------------------------------------------
class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    fio = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='employee')  # admin | employee
    is_active_flag = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    clients = db.relationship('Client', back_populates='responsible', lazy='dynamic')
    tasks = db.relationship('Task', back_populates='assignee', lazy='dynamic')

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self) -> bool:
        return self.role == 'admin'

    @property
    def is_active(self) -> bool:
        """Flask-Login: вход и работа в системе только для активных записей."""
        return self.is_active_flag

    def __repr__(self):
        return f'<User {self.email} ({self.role})>'


# ----------------------------------------------------------------------------
# Учётная база 1С, зарегистрированная в системе (источник OData)
# ----------------------------------------------------------------------------
class ODataBase(db.Model):
    __tablename__ = 'odata_bases'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    odata_url = db.Column(db.String(512), nullable=False)
    username = db.Column(db.String(255), nullable=False, default='')
    password = db.Column(db.String(512), nullable=False, default='')
    is_enabled = db.Column(db.Boolean, nullable=False, default=True)
    last_sync_at = db.Column(db.DateTime)

    clients = db.relationship('Client', back_populates='odata_base', lazy='dynamic')

    def __repr__(self):
        return f'<ODataBase {self.name}>'


# ----------------------------------------------------------------------------
# Клиент (ФТ-1: сводный реестр, идентификационные данные + ручное дополнение)
# ----------------------------------------------------------------------------
class Client(db.Model):
    __tablename__ = 'clients'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(512), nullable=False, index=True)
    inn = db.Column(db.String(12), index=True)
    kpp = db.Column(db.String(9))
    status = db.Column(db.String(20), nullable=False, default='active')  # active | archived
    responsible_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    odata_base_id = db.Column(db.Integer, db.ForeignKey('odata_bases.id'))
    external_ref = db.Column(db.String(64), index=True)  # GUID объекта в 1С
    date_added = db.Column(db.Date, nullable=False, default=date.today)
    notes = db.Column(db.Text, nullable=False, default='')

    responsible = db.relationship('User', back_populates='clients')
    odata_base = db.relationship('ODataBase', back_populates='clients')
    contracts = db.relationship('Contract', back_populates='client', lazy='dynamic',
                                cascade='all, delete-orphan')
    tasks = db.relationship('Task', back_populates='client', lazy='dynamic',
                            cascade='all, delete-orphan')
    documents = db.relationship('DocumentRef', back_populates='client', lazy='dynamic',
                                cascade='all, delete-orphan')

    @property
    def is_active(self) -> bool:
        return self.status == 'active'

    @property
    def active_contracts(self):
        return [c for c in self.contracts if c.status == 'active']

    def __repr__(self):
        return f'<Client {self.name}>'


# ----------------------------------------------------------------------------
# Договор на бухгалтерское сопровождение (ФТ-5)
# ----------------------------------------------------------------------------
class Contract(db.Model):
    __tablename__ = 'contracts'

    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(64), nullable=False)
    signed_date = db.Column(db.Date, nullable=False, default=date.today)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False, index=True)
    subject = db.Column(db.Text, nullable=False, default='')      # предмет / перечень услуг
    amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # стоимость
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    status = db.Column(db.String(20), nullable=False, default='draft')  # draft | active | closed

    client = db.relationship('Client', back_populates='contracts')

    @property
    def status_label(self):
        return {'draft': 'проект', 'active': 'действующий', 'closed': 'завершённый'}.get(
            self.status, self.status)

    def __repr__(self):
        return f'<Contract №{self.number}>'


# ----------------------------------------------------------------------------
# Задача (ФТ-3)
# ----------------------------------------------------------------------------
class Task(db.Model):
    __tablename__ = 'tasks'

    STATUS_NEW = 'new'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_DONE = 'done'
    STATUS_OVERDUE = 'overdue'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(512), nullable=False)
    description = db.Column(db.Text, nullable=False, default='')
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), index=True)
    assignee_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    due_date = db.Column(db.Date, index=True)
    priority = db.Column(db.Integer, nullable=False, default=2)  # 1 высокий .. 3 низкий
    status = db.Column(db.String(20), nullable=False, default=STATUS_NEW, index=True)
    # Ссылка на справочник отчётности, если задача создана автоматически
    reporting_deadline_id = db.Column(db.Integer, db.ForeignKey('reporting_deadlines.id'))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                          onupdate=datetime.utcnow)

    client = db.relationship('Client', back_populates='tasks')
    assignee = db.relationship('User', back_populates='tasks')
    reporting_deadline = db.relationship('ReportingDeadline', back_populates='tasks')

    @property
    def status_label(self):
        return {
            'new': 'новая', 'in_progress': 'в работе',
            'done': 'выполнена', 'overdue': 'просрочена',
        }.get(self.status, self.status)

    @property
    def priority_label(self):
        return {1: 'высокий', 2: 'средний', 3: 'низкий'}.get(self.priority, 'средний')

    @property
    def is_overdue(self) -> bool:
        return (self.due_date is not None
                and self.status not in (self.STATUS_DONE,)
                and self.due_date < date.today())

    @property
    def days_left(self):
        if self.due_date is None:
            return None
        return (self.due_date - date.today()).days

    def __repr__(self):
        return f'<Task {self.title[:40]}>'


# ----------------------------------------------------------------------------
# Справочник сроков сдачи отчётности (ФТ-4)
# ----------------------------------------------------------------------------
class ReportingDeadline(db.Model):
    __tablename__ = 'reporting_deadlines'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)         # наименование формы
    code = db.Column(db.String(64), nullable=False, unique=True)
    periodicity = db.Column(db.String(20), nullable=False, default='quarterly')
    # monthly | quarterly | annual
    due_day = db.Column(db.Integer, nullable=False, default=25)  # день сдачи
    # Смещение периода: за сколько месяцев/периодов до срока заканчивается отчётный период
    period_offset = db.Column(db.Integer, nullable=False, default=0)
    taxpayer_category = db.Column(db.String(50), nullable=False, default='general')
    description = db.Column(db.Text, nullable=False, default='')
    is_enabled = db.Column(db.Boolean, nullable=False, default=True)

    tasks = db.relationship('Task', back_populates='reporting_deadline', lazy='dynamic')

    @property
    def periodicity_label(self):
        return {
            'monthly': 'ежемесячная', 'quarterly': 'ежеквартальная', 'annual': 'годовая',
        }.get(self.periodicity, self.periodicity)

    def due_date_for_period(self, period: date) -> date:
        """Расчёт срока сдачи для отчётного периода (period — первый день периода)."""
        if self.periodicity == 'monthly':
            # срок: due_day месяца, следующего за периодом (+ period_offset)
            year, month = period.year, period.month + 1 + self.period_offset
        elif self.periodicity == 'quarterly':
            # срок: due_day месяца, следующего за кварталом
            quarter_end_month = ((period.month - 1) // 3) * 3 + 3
            year, month = period.year, quarter_end_month + 1 + self.period_offset
        else:  # annual
            year, month = period.year + 1, 1 + self.period_offset
        # нормализация месяца
        year += (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = self.due_day
        # защита от 30/31 февраля
        while day > 28:
            try:
                return date(year, month, day)
            except ValueError:
                day -= 1
        return date(year, month, day)

    def __repr__(self):
        return f'<ReportingDeadline {self.code}>'


# ----------------------------------------------------------------------------
# Документ из «1С:Документооборот» (ФТ-2)
# ----------------------------------------------------------------------------
class DocumentRef(db.Model):
    __tablename__ = 'document_refs'

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False, index=True)
    doc_type = db.Column(db.String(255), nullable=False, default='')
    number = db.Column(db.String(64), nullable=False, default='')
    doc_date = db.Column(db.Date)
    amount = db.Column(db.Numeric(12, 2))
    status = db.Column(db.String(64), nullable=False, default='')
    external_ref = db.Column(db.String(64), index=True)  # GUID объекта в 1С:ДО
    external_url = db.Column(db.String(512))             # ссылка на объект в 1С:ДО

    client = db.relationship('Client', back_populates='documents')

    def __repr__(self):
        return f'<DocumentRef {self.doc_type} №{self.number}>'
