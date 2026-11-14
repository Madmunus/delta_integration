"""Наполнение базы демонстрационными данными.

Запуск из корня проекта:
    python seed.py

Создаёт: 14 пользователей (2 руководителя, 10 активных сотрудников
и 2 неактивных сотрудника), настройку подключения к 1С, 24 клиента
(20 активных — по 2 на каждого активного сотрудника, 4 архивных —
по 2 на каждого неактивного сотрудника), 25 контрагентов, 50 договоров,
50 документов из «1С:Документооборот», справочник сроков отчётности
и задачи.
Пароли тестовых учётных записей соответствуют политике:
не менее 13 символов, минимум одна цифра, заглавная и
строчная латинские буквы, спецсимвол; без частей логина
и транслитерации ФИО. Повторный запуск дополняет базу недостающими
демо-данными и обновляет пароли демо-пользователей, не создавая дублей.
"""
from datetime import date, datetime, timedelta

from app import app
from engine.models import (Client, Contract, Contractor, Document,
                           OneCSetting, ReportingDeadline, Task, User, db)
from engine.odata_client import DOCUMENT_ENTITIES

# (email, ФИО, роль, пароль, активен)
USERS = [
    ('admin@delta.local', 'Иванова Мария Петровна', 'admin',
     'Zvezda#Kod2026!', True),
    ('ivanov@delta.local', 'Иванов Пётр Сергеевич', 'employee',
     'Orbita#Sfera2026!', True),
    ('petrova@delta.local', 'Петрова Анна Львовна', 'employee',
     'Kaskad#Volna2026!', True),
    ('smirnov@delta.local', 'Смирнов Олег Витальевич', 'admin',
     'Granit#Luch2026!', True),
    ('kuznetsov@delta.local', 'Кузнецов Дмитрий Александрович', 'employee',
     'Fakel#Tuman2026!', True),
    ('orlova@delta.local', 'Орлова Елена Викторовна', 'employee',
     'Yantar#Rosa2026!', True),
    ('volkov@delta.local', 'Волков Артём Игоревич', 'employee',
     'Sapphir#Nebo2026!', True),
    ('fedorova@delta.local', 'Фёдорова Ольга Николаевна', 'employee',
     'Topaz#Zarya2026!', True),
    ('sokolov@delta.local', 'Соколов Андрей Викторович', 'employee',
     'Kedr#Prostor2026!', True),
    ('lebedev@delta.local', 'Лебедев Максим Юрьевич', 'employee',
     'Atlas#Rassvet2026!', True),
    ('novikova@delta.local', 'Новикова Ирина Павловна', 'employee',
     'Fialka#Zakat2026!', True),
    ('zaitsev@delta.local', 'Зайцев Глеб Сергеевич', 'employee',
     'Sever#Pritok2026!', True),
    ('morozov@delta.local', 'Морозов Илья Павлович', 'employee',
     'Rubin#Sneg2026!', False),
    ('nikolaeva@delta.local', 'Николаева Светлана Андреевна', 'employee',
     'Kristall#Veter2026!', False),
]

# Клиенты из Catalog_Организации главной базы 1С:
# (Ref_Key, Наименование, ИНН, КПП,
# Статус, индекс ответственного в USERS (с единицы; 0 — не назначен),
# Дата создания, Заметки).
CLIENTS = [
    ('22222222-2222-2222-2222-000000000001', 'ООО «Ромашка»', '7701234567', '770101001', 'active', 2,
     date(2025, 9, 15), 'Розничная торговля. Отчётность УСН.'),
    ('22222222-2222-2222-2222-000000000002', 'ООО «ТехноПром»', '7702345678', '770201001', 'active', 2,
     date(2025, 9, 15), 'Производственное предприятие, ОСНО.'),
    ('22222222-2222-2222-2222-000000000003', 'ИП Сидоров А.В.', '770312345678', None, 'active', 3,
     date(2025, 9, 20), 'ИП на УСН, торговля через маркетплейсы.'),
    ('22222222-2222-2222-2222-000000000004', 'ООО «Северное сияние»', '7704567890', '770401001', 'active', 3,
     date(2025, 10, 1), ''),
    ('22222222-2222-2222-2222-000000000005', 'ООО «Вектор»', '7703456789', '770301001', 'active', 5,
     date(2025, 10, 12), 'Оптовая торговля, ОСНО.'),
    ('22222222-2222-2222-2222-000000000006', 'ООО «Гамма»', '7705678901', '770501001', 'active', 5,
     date(2025, 11, 3), 'Услуги связи.'),
    ('22222222-2222-2222-2222-000000000007', 'ООО «Дельта-Строй»', '7706789012', '770601001', 'active', 6,
     date(2025, 11, 17), 'Строительство, ОСНО.'),
    ('22222222-2222-2222-2222-000000000008', 'ООО «Енисей»', '7707890123', '770701001', 'active', 6,
     date(2025, 12, 1), 'Лесозаготовка.'),
    ('22222222-2222-2222-2222-000000000009', 'ООО «Жемчужина»', '7708901234', '770801001', 'active', 7,
     date(2026, 1, 12), 'Ювелирная розница, УСН.'),
    ('22222222-2222-2222-2222-000000000010', 'ООО «Заря»', '7709012345', '770901001', 'active', 7,
     date(2026, 1, 20), 'Сельхозпроизводство.'),
    ('22222222-2222-2222-2222-000000000011', 'ООО «Импульс»', '7710123456', '771001001', 'active', 8,
     date(2026, 2, 2), 'ИТ-услуги.'),
    ('22222222-2222-2222-2222-000000000012', 'ООО «Кедр»', '7711234567', '771101001', 'active', 8,
     date(2026, 2, 16), 'Мебельное производство.'),
    ('22222222-2222-2222-2222-000000000013', 'ООО «Лагуна»', '7712345678', '771201001', 'active', 9,
     date(2026, 3, 2), 'Туристические услуги.'),
    ('22222222-2222-2222-2222-000000000014', 'ООО «Меридиан»', '7713456789', '771301001', 'active', 9,
     date(2026, 3, 16), 'Логистика.'),
    ('22222222-2222-2222-2222-000000000015', 'ООО «Нептун»', '7714567890', '771401001', 'active', 10,
     date(2026, 4, 1), 'Рыбопереработка.'),
    ('22222222-2222-2222-2222-000000000016', 'ООО «Орион»', '7715678901', '771501001', 'active', 10,
     date(2026, 4, 15), 'Оптовая торговля.'),
    ('22222222-2222-2222-2222-000000000017', 'ООО «Пилот»', '7716789012', '771601001', 'active', 11,
     date(2026, 5, 5), 'Авиаперевозки, ОСНО.'),
    ('22222222-2222-2222-2222-000000000018', 'ООО «Радуга»', '7717890123', '771701001', 'active', 11,
     date(2026, 5, 19), 'Детские товары, УСН.'),
    ('22222222-2222-2222-2222-000000000019', 'ООО «Сокол»', '7718901234', '771801001', 'active', 12,
     date(2026, 6, 1), 'Охранные услуги.'),
    ('22222222-2222-2222-2222-000000000020', 'ООО «Темп»', '7719012345', '771901001', 'active', 12,
     date(2026, 6, 15), 'Ремонт оборудования.'),
    ('22222222-2222-2222-2222-000000000021', 'ООО «Урал»', '7720123456', '772001001', 'archived', 13,
     date(2024, 4, 10), 'Клиент передан другому оператору.'),
    ('22222222-2222-2222-2222-000000000022', 'ООО «Феникс»', '7721234567', '772101001', 'archived', 13,
     date(2024, 6, 18), 'Деятельность приостановлена.'),
    ('22222222-2222-2222-2222-000000000023', 'ООО «Хрусталь»', '7722345678', '772201001', 'archived', 14,
     date(2024, 9, 5), 'Смена оператора в 2024 г.'),
    ('22222222-2222-2222-2222-000000000024', 'ООО «Чайка»', '7723456789', '772301001', 'archived', 14,
     date(2025, 1, 22), 'Клиент закрыл бизнес.'),
]

# Контрагенты из Catalog_Контрагенты главной базы 1С:
# (Ref_Key, НаименованиеПолное, ИНН, КПП, Description).
CONTRACTORS = [
    ('11111111-1111-1111-1111-111111111101', 'ООО «Ромашка»',
     '7701234567', '770101001', 'Розничная торговля'),
    ('11111111-1111-1111-1111-111111111102', 'ООО «ТехноПром»',
     '7702345678', '770201001', 'Производственное предприятие'),
    ('11111111-1111-1111-1111-111111111103', 'ИП Сидоров А.В.',
     '770312345678', None, 'ИП на УСН'),
    ('11111111-1111-1111-1111-111111111104', 'ООО «Северное сияние»',
     '7704567890', '770401001', 'Оптовая торговля'),
    ('11111111-1111-1111-1111-111111111105', 'ООО «Вектор»',
     '7703456789', '770301001', 'Логистика'),
    ('11111111-1111-1111-1111-111111111106', 'ООО «Гамма»',
     '7705678901', '770501001', 'Услуги связи'),
    ('11111111-1111-1111-1111-111111111107', 'ООО «Дельта-Строй»',
     '7706789012', '770601001', 'Строительство'),
    ('11111111-1111-1111-1111-111111111108', 'ООО «Енисей»',
     '7707890123', '770701001', 'Лесозаготовка'),
    ('11111111-1111-1111-1111-111111111109', 'ООО «Жемчужина»',
     '7708901234', '770801001', 'Ювелирная розница'),
    ('11111111-1111-1111-1111-111111111110', 'ООО «Заря»',
     '7709012345', '770901001', 'Сельхозпроизводство'),
    ('11111111-1111-1111-1111-111111111111', 'ООО «Импульс»',
     '7710123456', '771001001', 'ИТ-услуги'),
    ('11111111-1111-1111-1111-111111111112', 'ООО «Кедр»',
     '7711234567', '771101001', 'Мебельное производство'),
    ('11111111-1111-1111-1111-111111111113', 'ООО «Лагуна»',
     '7712345678', '771201001', 'Туристические услуги'),
    ('11111111-1111-1111-1111-111111111114', 'ООО «Меридиан»',
     '7713456789', '771301001', 'Транспортные услуги'),
    ('11111111-1111-1111-1111-111111111115', 'ООО «Нептун»',
     '7714567890', '771401001', 'Рыбопереработка'),
    ('11111111-1111-1111-1111-111111111116', 'ООО «Орион»',
     '7715678901', '771501001', 'Оптовая торговля'),
    ('11111111-1111-1111-1111-111111111117', 'ООО «Пилот»',
     '7716789012', '771601001', 'Авиаперевозки'),
    ('11111111-1111-1111-1111-111111111118', 'ООО «Радуга»',
     '7717890123', '771701001', 'Детские товары'),
    ('11111111-1111-1111-1111-111111111119', 'ООО «Сокол»',
     '7718901234', '771801001', 'Охранные услуги'),
    ('11111111-1111-1111-1111-111111111120', 'ООО «Темп»',
     '7719012345', '771901001', 'Ремонт оборудования'),
    ('11111111-1111-1111-1111-111111111121', 'ООО «Урал»',
     '7720123456', '772001001', 'Металлообработка'),
    ('11111111-1111-1111-1111-111111111122', 'ООО «Феникс»',
     '7721234567', '772101001', 'Консалтинг'),
    ('11111111-1111-1111-1111-111111111123', 'ООО «Хрусталь»',
     '7722345678', '772201001', 'Оптовая торговля посудой'),
    ('11111111-1111-1111-1111-111111111124', 'ООО «Чайка»',
     '7723456789', '772301001', 'Гостиничные услуги'),
    ('11111111-1111-1111-1111-111111111125', 'ООО «Эверест»',
     '7724567890', '772401001', 'Строительные материалы'),
]

_DEADLINES = [
    ('Декларация по НДС', 'vat-quarterly', 'quarterly', 25, 1, 'ОСНО'),
    ('Декларация по налогу на прибыль', 'profit-quarterly', 'quarterly', 28, 1, 'ОСНО'),
    ('Расчёт по страховым взносам', 'insurance-quarterly', 'quarterly', 30, 1, 'общая'),
    ('6-НДФЛ', 'ndfl-6', 'quarterly', 25, 1, 'общая'),
    ('Единый налоговый счёт (уведомление)', 'ens-monthly', 'monthly', 25, 0, 'общая'),
    ('Декларация по УСН', 'usn-annual', 'annual', 25, 3, 'УСН'),
    ('Бухгалтерская отчётность', 'buch-annual', 'annual', 31, 3, 'общая'),
    ('СЗВ-М', 'szv-m', 'monthly', 15, 0, 'общая'),
    ('Сведения о среднесписочной численности', 'ssch-annual', 'annual', 20, 0, 'общая'),
]
DEADLINES = _DEADLINES

_CONTRACT_DESCRIPTIONS = (
    'Бухгалтерское сопровождение',
    'Сдача отчётности и расчёт налогов',
    'Расчёт заработной платы',
    'Консультационные услуги',
    'Налоговое сопровождение',
)
_CONTRACT_STATUSES = ('active', 'active', 'active', 'closed', 'draft')


def _demo_contracts(count=50):
    """50 договоров с детерминированным распределением по клиентам."""
    base = date(2024, 1, 15)
    contracts = []
    for i in range(count):
        start = base + timedelta(days=i * 17)
        end = start + timedelta(days=365)
        contracts.append((
            f'БС-{start.year}-{i + 1:03d}',
            i % len(CLIENTS) + 1,
            i % len(CONTRACTORS) + 1,
            _CONTRACT_DESCRIPTIONS[i % len(_CONTRACT_DESCRIPTIONS)],
            6000 + (i % 10) * 3500,
            _CONTRACT_STATUSES[i % len(_CONTRACT_STATUSES)],
            start,
            end,
        ))
    return contracts


CONTRACTS = _demo_contracts()

# Демонстрационные документы «1С:Документооборот»:
# (номер, вид документа, дата, статус, индекс клиента, индекс контрагента
# в CONTRACTORS (0 — без контрагента), Ref_Key).
_DOC_STATUSES = ('Проведён', 'Черновик', 'Зарегистрирован', 'Отменён')


def _demo_documents(count=50):
    """50 документов разных видов; у каждого 7-го нет контрагента."""
    base = date(2025, 1, 20)
    documents = []
    for i in range(count):
        entity = DOCUMENT_ENTITIES[i % len(DOCUMENT_ENTITIES)]
        documents.append((
            f'ДОК-{i + 1:03d}',
            entity[len('Document_'):],
            base + timedelta(days=i * 9),
            _DOC_STATUSES[i % len(_DOC_STATUSES)],
            i % len(CLIENTS) + 1,
            i % len(CONTRACTORS) + 1 if i % 7 else 0,
            f'bbbbbbbb-0000-0000-0000-{i + 1:012d}',
        ))
    return documents


DOCUMENTS = _demo_documents()

_STATUS_1C = {'active': 'Действует', 'closed': 'Закрыт', 'draft': 'Проект'}


def _seed_users():
    """Создать недостающих демо-пользователей и обновить их пароли."""
    users = {}
    created = 0
    for email, fio, role, password, is_active in USERS:
        user = User.query.filter_by(email=email).first()
        if user is None:
            user = User(email=email, fio=fio, role=role,
                        is_active_flag=is_active)
            db.session.add(user)
            created += 1
        user.set_password(password)
        users[email] = user
    db.session.flush()
    return [users[email] for email, *_ in USERS], created


def _seed_setting():
    """Создать настройку подключения к 1С, если её ещё нет."""
    setting = OneCSetting.get()
    if setting not in db.session:
        db.session.add(setting)
        return 1
    return 0


def _seed_clients(user_list):
    """Создать недостающих клиентов; существующие записи не изменяются."""
    clients = []
    created = 0
    for ref, name, inn, kpp, status, resp_idx, added, notes in CLIENTS:
        client = Client.query.filter_by(external_ref=ref).first()
        if client is None:
            client = Client(
                external_ref=ref,
                name=name, inn=inn or None, kpp=kpp or None,
                status=status, notes=notes,
                responsible_id=user_list[resp_idx - 1].id if resp_idx else None,
                created_at=datetime.combine(added, datetime.min.time()))
            db.session.add(client)
            created += 1
        clients.append(client)
    db.session.flush()
    return clients, created


def _seed_contractors():
    """Создать недостающих контрагентов; существующие не изменяются."""
    contractors = []
    created = 0
    for ref, full_name, inn, kpp, description in CONTRACTORS:
        contractor = Contractor.query.filter_by(external_ref=ref).first()
        if contractor is None:
            contractor = Contractor(external_ref=ref, full_name=full_name,
                                    inn=inn, kpp=kpp, description=description)
            db.session.add(contractor)
            created += 1
        contractors.append(contractor)
    db.session.flush()
    return contractors, created


def _seed_deadlines():
    """Создать недостающие виды отчётности (по коду)."""
    created = 0
    for name, code, periodicity, due_day, offset, category in DEADLINES:
        if ReportingDeadline.query.filter_by(code=code).first() is not None:
            continue
        db.session.add(ReportingDeadline(
            name=name, code=code, periodicity=periodicity,
            due_day=due_day, period_offset=offset,
            taxpayer_category=category, is_enabled=True))
        created += 1
    return created


def _seed_contracts(clients, contractors):
    """Создать недостающие договоры (по external_ref)."""
    created = 0
    for idx, (number, client_idx, contractor_idx, description,
              amount, status, start, end) in enumerate(CONTRACTS, start=1):
        ref = f'aaaaaaaa-0000-0000-0000-{idx:012d}'
        if Contract.query.filter_by(external_ref=ref).first() is not None:
            continue
        contractor = contractors[contractor_idx - 1]
        db.session.add(Contract(
            number=number, signed_date=start,
            client_id=clients[client_idx - 1].id,
            contractor_id=contractor.id,
            external_ref=ref,
            description=description,
            amount=amount, status=status,
            status_1c=_STATUS_1C.get(status, status),
            start_date=start, end_date=end))
        created += 1
    return created


def _seed_documents(clients, contractors):
    """Создать недостающие документы (по external_ref)."""
    created = 0
    for number, doc_type, doc_date, status, client_idx, contractor_idx, ref in DOCUMENTS:
        if Document.query.filter_by(external_ref=ref).first() is not None:
            continue
        contractor = (contractors[contractor_idx - 1]
                      if contractor_idx else None)
        db.session.add(Document(
            external_ref=ref,
            client_id=clients[client_idx - 1].id,
            contractor_id=contractor.id if contractor else None,
            doc_type=doc_type,
            number=number,
            doc_date=doc_date,
            status=status))
        created += 1
    return created


def _seed_tasks(clients, user_list):
    """Создать недостающие демонстрационные задачи (по заголовку)."""
    created = 0
    today = date.today()
    demo_tasks = [
        ('Сверка с ФНС по ООО «Ромашка»', 1, 2, today + timedelta(days=5), 2, 'new'),
        ('Подготовка декларации по НДС ООО «ТехноПром»', 2, 3, today + timedelta(days=10), 1, 'in_progress'),
        ('Восстановление документов ИП Сидоров', 3, 2, today - timedelta(days=3), 1, 'overdue'),
        ('Получение справки о отсутствии задолженности', 1, 2, today + timedelta(days=2), 3, 'new'),
        ('Закрытие месяца по всем клиентам', 0, 1, today + timedelta(days=7), 2, 'in_progress'),
        ('Обновление карточек клиентов после сверки', 0, 1, today - timedelta(days=1), 3, 'done'),
    ]
    for title, client_idx, assignee_idx, due, priority, status in demo_tasks:
        if Task.query.filter_by(title=title).first() is not None:
            continue
        db.session.add(Task(
            title=title,
            description='Демонстрационная задача, создана скриптом seed.py.',
            client_id=clients[client_idx - 1].id if client_idx else None,
            assignee_id=user_list[assignee_idx - 1].id,
            due_date=due, priority=priority, status=status))
        created += 1
    return created


def main():
    with app.app_context():
        user_list, users_created = _seed_users()
        setting_created = _seed_setting()
        clients, clients_created = _seed_clients(user_list)
        contractors, contractors_created = _seed_contractors()
        deadlines_created = _seed_deadlines()
        contracts_created = _seed_contracts(clients, contractors)
        documents_created = _seed_documents(clients, contractors)
        tasks_created = _seed_tasks(clients, user_list)

        db.session.commit()
        print('Готово. Создано:')
        print(f'  пользователей: {users_created}')
        print(f'  настройка 1С: {setting_created}')
        print(f'  клиентов: {clients_created}')
        print(f'  контрагентов: {contractors_created}')
        print(f'  видов отчётности: {deadlines_created}')
        print(f'  договоров: {contracts_created}')
        print(f'  документов: {documents_created}')
        print(f'  задач: {tasks_created}')
        print('Пароли демо-учётных записей обновлены. Тестовые учётные записи:')
        for email, fio, role, password, is_active in USERS:
            print(f'  {email:<22} {password:<20} '
                  f'{"руководитель" if role == "admin" else "сотрудник":<12} '
                  f'{"активна" if is_active else "ОТКЛЮЧЕНА"}')


if __name__ == '__main__':
    main()
