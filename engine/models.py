"""Модели данных системы (SQLAlchemy ORM)."""
from datetime import date, datetime

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from sqlalchemy.orm import joinedload
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
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

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
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

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
    __table_args__ = (
        db.Index('ix_clients_status_name', 'status', 'name'),
        db.Index('ix_clients_responsible_status', 'responsible_id', 'status'),
    )

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
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

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
    def can_have_open_contracts(self) -> bool:
        """Проекты и действующие договоры допустимы только у активных."""
        return self.is_active

    @property
    def active_contracts(self):
        return self.contracts.filter_by(status='active').all()

    def close_open_contracts(self) -> int:
        """Закрыть проекты и действующие договоры клиента.

        Правило: у архивного клиента не может быть проектов договоров
        и действующих договоров. Возвращает число закрытых договоров.
        """
        closed = 0
        for contract in self.contracts:
            if contract.status in Contract.OPEN_STATUSES:
                contract.status = 'closed'
                closed += 1
        return closed

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
    __table_args__ = (
        db.Index('ix_contractors_status_name', 'status', 'full_name'),
    )

    id = db.Column(db.Integer, primary_key=True)
    external_ref = db.Column(db.String(64), unique=True, index=True)  # Ref_Key в Catalog_Контрагенты
    full_name = db.Column(db.String(512), nullable=False, default='')  # НаименованиеПолное
    inn = db.Column(db.String(12), index=True)
    kpp = db.Column(db.String(9))
    status = db.Column(db.String(20), nullable=False, default='active')  # active | archived
    notes = db.Column(db.Text, nullable=False, default='')  # заметки пользователя
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    contracts = db.relationship('Contract', back_populates='contractor', lazy='dynamic')

    @property
    def is_active(self) -> bool:
        return self.status == 'active'

    @property
    def display_name(self) -> str:
        return self.full_name or self.external_ref or '—'

    def __repr__(self):
        return f'<Contractor {self.full_name or self.external_ref}>'


# ----------------------------------------------------------------------------
# Договор (ФТ-3: справочник Catalog_Договоры)
# ----------------------------------------------------------------------------
class Contract(db.Model):
    __tablename__ = 'contracts'
    __table_args__ = (
        db.Index('ix_contracts_client_status', 'client_id', 'status'),
        db.Index('ix_contracts_client_processed', 'client_id', 'processed'),
        db.Index('ix_contracts_client_date', 'client_id', 'signed_date'),
    )

    # Статусы, при которых договор считается открытым (не завершён)
    OPEN_STATUSES = ('draft', 'active')

    # Соответствие статуса и цвета бейджа (Bootstrap)
    STATUS_BADGES = {
        'draft': 'text-bg-light text-dark',  # проект
        'active': 'text-bg-success',         # действующий
        'closed': 'text-bg-secondary',       # завершённый
    }

    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(64), nullable=False)          # Наименование
    signed_date = db.Column(db.Date, nullable=False, default=date.today)  # Дата
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False, index=True)
    contractor_id = db.Column(db.Integer, db.ForeignKey('contractors.id'), index=True)
    external_ref = db.Column(db.String(64), unique=True, index=True)  # Ref_Key
    description = db.Column(db.Text, nullable=False, default='')  # Description из 1С
    notes = db.Column(db.Text, nullable=False, default='')        # заметки пользователя
    amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # Сумма
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)                              # СрокДействия
    status = db.Column(db.String(20), nullable=False, default='draft')  # draft | active | closed
    status_1c = db.Column(db.String(64), nullable=False, default='')   # Статус из 1С
    processed = db.Column(db.Boolean, nullable=False, default=False)   # внутренний признак «Обработан»
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    client = db.relationship('Client', back_populates='contracts')
    contractor = db.relationship('Contractor', back_populates='contracts')

    @property
    def is_open(self) -> bool:
        """Проект или действующий договор (не завершён)."""
        return self.status in self.OPEN_STATUSES

    @property
    def status_label(self):
        return {'draft': 'проект', 'active': 'действующий', 'closed': 'завершённый'}.get(
            self.status, self.status)

    @property
    def status_badge_class(self):
        """CSS-класс цветного бейджа для статуса договора."""
        return self.STATUS_BADGES.get(self.status, 'text-bg-dark')

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


def close_archived_client_contracts() -> int:
    """Закрыть проекты и действующие договоры у всех архивных клиентов.

    Правило системы: у архивного клиента не может быть проектов договоров
    и действующих договоров. Возвращает число закрытых договоров.
    """
    archived_ids = [row[0] for row in db.session.query(Client.id)
                    .filter(Client.status == 'archived').all()]
    if not archived_ids:
        return 0
    return Contract.query.filter(
        Contract.client_id.in_(archived_ids),
        Contract.status.in_(Contract.OPEN_STATUSES),
    ).update(
        {Contract.status: 'closed'},
        synchronize_session='fetch',
    )


# ----------------------------------------------------------------------------
# Документ из «1С:Документооборот» (ФТ-4)
# ----------------------------------------------------------------------------
class Document(db.Model):
    __tablename__ = 'documents'
    __table_args__ = (
        db.Index('ix_documents_client_date', 'client_id', 'doc_date'),
        db.Index('ix_documents_client_status', 'client_id', 'status'),
        db.Index('ix_documents_client_processed', 'client_id', 'processed'),
    )

    # Соответствие статуса и цвета бейджа (Bootstrap).
    # Статусы приходят из «1С:Документооборот» свободным текстом,
    # поэтому ключи приведены к нижнему регистру, «ё» заменена на «е».
    STATUS_BADGES = {
        'черновик': 'text-bg-secondary',
        'проект': 'text-bg-secondary',
        'новый': 'text-bg-secondary',
        'на согласовании': 'text-bg-warning',
        'в работе': 'text-bg-warning',
        'ожидает': 'text-bg-warning',
        'согласован': 'text-bg-info',
        'утвержден': 'text-bg-info',
        'подписан': 'text-bg-info',
        'зарегистрирован': 'text-bg-primary',
        'проведен': 'text-bg-success',
        'исполнен': 'text-bg-success',
        'завершен': 'text-bg-success',
        'оплачен': 'text-bg-success',
        'отменен': 'text-bg-danger',
        'отклонен': 'text-bg-danger',
        'ошибка': 'text-bg-danger',
    }

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False, index=True)
    contractor_id = db.Column(db.Integer, db.ForeignKey('contractors.id'), index=True)
    doc_type = db.Column(db.String(255), nullable=False, default='')
    number = db.Column(db.String(64), nullable=False, default='')
    doc_date = db.Column(db.Date)
    status = db.Column(db.String(64), nullable=False, default='')
    processed = db.Column(db.Boolean, nullable=False, default=False)  # внутренний признак «Обработан»
    notes = db.Column(db.Text, nullable=False, default='')  # заметки пользователя
    external_ref = db.Column(db.String(64), index=True)  # GUID объекта в 1С:Документооборот
    external_url = db.Column(db.String(512))             # ссылка на объект в 1С:Документооборот
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    client = db.relationship('Client', back_populates='documents')
    contractor = db.relationship('Contractor')

    @property
    def processed_label(self) -> str:
        return 'Да' if self.processed else 'Нет'

    @property
    def status_badge_class(self):
        """CSS-класс цветного бейджа для статуса документа."""
        key = (self.status or '').strip().lower().replace('ё', 'е')
        return self.STATUS_BADGES.get(key, 'text-bg-dark')

    def __repr__(self):
        return f'<Document {self.doc_type} №{self.number}>'


# ----------------------------------------------------------------------------
# Задача (ФТ-5)
# ----------------------------------------------------------------------------
class Task(db.Model):
    __tablename__ = 'tasks'
    __table_args__ = (
        db.Index('ix_tasks_client_status_due', 'client_id', 'status', 'due_date'),
        db.Index('ix_tasks_assignee_status_due', 'assignee_id', 'status', 'due_date'),
        db.Index('ix_tasks_client_due', 'client_id', 'due_date'),
    )

    STATUS_NEW = 'new'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_DONE = 'done'
    # Порядок статусов для кнопок «назад» / «вперёд» в реестре
    STATUS_SEQUENCE = (STATUS_NEW, STATUS_IN_PROGRESS, STATUS_DONE)
    # Соответствие статуса и цвета бейджа (Bootstrap)
    STATUS_BADGES = {
        STATUS_NEW: 'text-bg-secondary',        # серый
        STATUS_IN_PROGRESS: 'text-bg-primary',  # синий
        STATUS_DONE: 'text-bg-success',         # зелёный
    }
    # Соответствие приоритета и цвета бейджа (Bootstrap)
    PRIORITY_BADGES = {
        1: 'text-bg-danger',     # высокий
        2: 'text-bg-warning',    # средний
        3: 'text-bg-secondary',  # низкий
    }

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(512), nullable=False)
    description = db.Column(db.Text, nullable=False, default='')
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), index=True)
    # Правило системы: у задачи всегда есть исполнитель
    assignee_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                            nullable=False, index=True)
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
            'done': 'выполнена',
        }.get(self.status, self.status)

    @property
    def status_badge_class(self):
        """CSS-класс цветного бейджа для текущего статуса задачи."""
        return self.STATUS_BADGES.get(self.status, 'text-bg-dark')

    @property
    def priority_label(self):
        return {1: 'высокий', 2: 'средний', 3: 'низкий'}.get(self.priority, 'средний')

    @property
    def priority_badge_class(self):
        """CSS-класс цветного бейджа для текущего приоритета задачи."""
        return self.PRIORITY_BADGES.get(self.priority, 'text-bg-dark')

    def _status_index(self):
        try:
            return self.STATUS_SEQUENCE.index(self.status)
        except ValueError:
            return None

    @property
    def prev_status(self):
        """Предыдущий статус в цепочке или None, если текущий первый."""
        idx = self._status_index()
        if idx is None or idx == 0:
            return None
        return self.STATUS_SEQUENCE[idx - 1]

    @property
    def next_status(self):
        """Следующий статус в цепочке или None, если текущий последний."""
        idx = self._status_index()
        if idx is None or idx == len(self.STATUS_SEQUENCE) - 1:
            return None
        return self.STATUS_SEQUENCE[idx + 1]

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


def assign_missing_task_assignees() -> int:
    """Назначить исполнителя задачам, у которых его нет.

    Правило системы: задача не может быть без исполнителя. Задаче без
    исполнителя назначается ответственный её клиента, а если он не задан
    или отключён — первый активный пользователь. Возвращает число
    исправленных задач.
    """
    missing = (Task.query.options(joinedload(Task.client))
               .filter(Task.assignee_id.is_(None)).all())
    if not missing:
        return 0
    active_ids = {u.id for u in User.query.filter_by(is_active_flag=True).all()}
    if not active_ids:
        return 0
    fallback_id = min(active_ids)
    for task in missing:
        responsible_id = task.client.responsible_id if task.client else None
        task.assignee_id = (responsible_id if responsible_id in active_ids
                            else fallback_id)
    return len(missing)


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
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

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

