"""Модели данных системы (SQLAlchemy ORM)."""
from datetime import date, datetime

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


# ----------------------------------------------------------------------------
# Единственная настройка подключения к главной базе «1С:Предприятие»
# ----------------------------------------------------------------------------
class OneCSetting(db.Model):
    """URL OData, пользователь и пароль главной базы 1С:Предприятие.

    По этой настройке загружаются клиенты (Catalog_Организации) и
    контрагенты (Catalog_Контрагенты).
    """
    __tablename__ = 'onec_settings'

    id = db.Column(db.Integer, primary_key=True, default=1)
    odata_url = db.Column(db.String(512), nullable=False, default='')
    username = db.Column(db.String(255), nullable=False, default='')
    password = db.Column(db.String(512), nullable=False, default='')
    last_sync_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    @classmethod
    def get(cls):
        """Вернуть настройку; при первом обращении — новый объект в памяти."""
        return db.session.get(cls, 1) or cls(id=1)

    def __repr__(self):
        return f'<OneCSetting {self.odata_url or "не настроено"}>'


# ----------------------------------------------------------------------------
# Пользователь (ФТ-1: ролевая модель admin / employee)
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
# Клиент (ФТ-2: справочник Catalog_Организации)
# ----------------------------------------------------------------------------
class Client(db.Model):
    __tablename__ = 'clients'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(512), nullable=False, index=True)       # Наименование
    inn = db.Column(db.String(12), index=True)                         # ИНН
    kpp = db.Column(db.String(9))
    status = db.Column(db.String(20), nullable=False, default='active')  # active | archived
    responsible_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    external_ref = db.Column(db.String(64), index=True)  # Ref_Key в Catalog_Организации
    # Подключение к «1С:Бухгалтерия» конкретного клиента (Catalog_Договоры)
    odata_url = db.Column(db.String(512), nullable=False, default='')
    odata_username = db.Column(db.String(255), nullable=False, default='')
    odata_password = db.Column(db.String(512), nullable=False, default='')
    notes = db.Column(db.Text, nullable=False, default='')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    responsible = db.relationship('User', back_populates='clients')
    contracts = db.relationship('Contract', back_populates='client', lazy='dynamic',
                                cascade='all, delete-orphan')
    tasks = db.relationship('Task', back_populates='client', lazy='dynamic',
                            cascade='all, delete-orphan')
    documents = db.relationship('Document', back_populates='client', lazy='dynamic',
                                cascade='all, delete-orphan')

    @property
    def is_active(self) -> bool:
        return self.status == 'active'

    @property
    def active_contracts(self):
        return [c for c in self.contracts if c.status == 'active']

    @property
    def has_odata(self) -> bool:
        return bool(self.odata_url)

    def __repr__(self):
        return f'<Client {self.name}>'


# ----------------------------------------------------------------------------
# Контрагент (ФТ-2: справочник Catalog_Контрагенты)
# ----------------------------------------------------------------------------
class Contractor(db.Model):
    __tablename__ = 'contractors'

    id = db.Column(db.Integer, primary_key=True)
    external_ref = db.Column(db.String(64), unique=True, index=True)  # Ref_Key в Catalog_Контрагенты
    full_name = db.Column(db.String(512), nullable=False, default='')  # НаименованиеПолное
    inn = db.Column(db.String(12), index=True)
    kpp = db.Column(db.String(9))
    description = db.Column(db.Text, nullable=False, default='')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    contracts = db.relationship('Contract', back_populates='contractor', lazy='dynamic')

    @property
    def display_name(self) -> str:
        return self.full_name or self.description or self.external_ref or '—'

    def __repr__(self):
        return f'<Contractor {self.full_name or self.external_ref}>'


# ----------------------------------------------------------------------------
# Договор (ФТ-3: справочник Catalog_Договоры)
# ----------------------------------------------------------------------------
class Contract(db.Model):
    __tablename__ = 'contracts'

    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(64), nullable=False)          # Наименование
    signed_date = db.Column(db.Date, nullable=False, default=date.today)  # Дата
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False, index=True)
    contractor_id = db.Column(db.Integer, db.ForeignKey('contractors.id'), index=True)
    external_ref = db.Column(db.String(64), unique=True, index=True)  # Ref_Key
    description = db.Column(db.Text, nullable=False, default='')  # Description из 1С
    amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # Сумма
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)                              # СрокДействия
    status = db.Column(db.String(20), nullable=False, default='draft')  # draft | active | closed
    status_1c = db.Column(db.String(64), nullable=False, default='')   # Статус из 1С
    processed = db.Column(db.Boolean, nullable=False, default=False)   # внутренний признак «Обработан»
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    client = db.relationship('Client', back_populates='contracts')
    contractor = db.relationship('Contractor', back_populates='contracts')

    @property
    def status_label(self):
        return {'draft': 'проект', 'active': 'действующий', 'closed': 'завершённый'}.get(
            self.status, self.status)

    @property
    def contractor_name(self) -> str:
        if self.contractor is not None:
            return self.contractor.display_name
        return '—'

    @property
    def processed_label(self) -> str:
        return 'Да' if self.processed else 'Нет'

    def __repr__(self):
        return f'<Contract №{self.number}>'


# ----------------------------------------------------------------------------
# Документ из «1С:Документооборот» (ФТ-4)
# ----------------------------------------------------------------------------
class Document(db.Model):
    __tablename__ = 'documents'

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False, index=True)
    contractor_id = db.Column(db.Integer, db.ForeignKey('contractors.id'), index=True)
    doc_type = db.Column(db.String(255), nullable=False, default='')
    number = db.Column(db.String(64), nullable=False, default='')
    doc_date = db.Column(db.Date)
    status = db.Column(db.String(64), nullable=False, default='')
    external_ref = db.Column(db.String(64), index=True)  # GUID объекта в 1С:Документооборот
    external_url = db.Column(db.String(512))             # ссылка на объект в 1С:Документооборот
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    client = db.relationship('Client', back_populates='documents')
    contractor = db.relationship('Contractor')

    def __repr__(self):
        return f'<Document {self.doc_type} №{self.number}>'


# ----------------------------------------------------------------------------
# Задача (ФТ-5)
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
# Справочник сроков сдачи отчётности (ФТ-6)
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
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

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

