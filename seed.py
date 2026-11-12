"""Наполнение базы демонстрационными данными.

Запуск из корня проекта:
    python seed.py

Создаёт: 14 пользователей (2 руководителя, 10 активных сотрудников
и 2 неактивных сотрудника), настройку подключения к 1С, 24 клиента
(20 активных — по 2 на каждого активного сотрудника, 4 архивных —
по 2 на каждого неактивного сотрудника), 15 контрагентов, 50 договоров,
справочник сроков отчётности и задачи.
Пароли тестовых учётных записей соответствуют политике:
не менее 13 символов, минимум одна цифра, заглавная и
строчная латинские буквы, спецсимвол; без частей логина
и транслитерации ФИО. Повторный запуск на заполненной базе
обновляет только пароли демо-пользователей.
"""
from datetime import date, datetime, timedelta

from app import app
from engine.models import (Client, Contract, Contractor, OneCSetting,
                           ReportingDeadline, Task, User, db)

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

# Клиенты: (наименование, ИНН, КПП, статус, индекс ответственного
# в USERS (с единицы; 0 — не назначен), дата добавления, заметки).
# Активные клиенты распределены по 2 на каждого активного сотрудника
# (индексы 2, 3, 5-12), архивные — по 2 на неактивных (13, 14).
CLIENTS = [
    ('ООО «Ромашка»', '7701234567', '770101001', 'active', 2,
     date(2025, 9, 15), 'Розничная торговля. Отчётность УСН.'),
    ('ООО «ТехноПром»', '7702345678', '770201001', 'active', 2,
     date(2025, 9, 15), 'Производственное предприятие, ОСНО.'),
    ('ИП Сидоров А.В.', '770312345678', None, 'active', 3,
     date(2025, 9, 20), 'ИП на УСН, торговля через маркетплейсы.'),
    ('ООО «Северное сияние»', '7704567890', '770401001', 'active', 3,
     date(2025, 10, 1), ''),
    ('ООО «Вектор»', '7703456789', '770301001', 'active', 5,
     date(2025, 10, 12), 'Оптовая торговля, ОСНО.'),
    ('ООО «Гамма»', '7705678901', '770501001', 'active', 5,
     date(2025, 11, 3), 'Услуги связи.'),
    ('ООО «Дельта-Строй»', '7706789012', '770601001', 'active', 6,
     date(2025, 11, 17), 'Строительство, ОСНО.'),
    ('ООО «Енисей»', '7707890123', '770701001', 'active', 6,
     date(2025, 12, 1), 'Лесозаготовка.'),
    ('ООО «Жемчужина»', '7708901234', '770801001', 'active', 7,
     date(2026, 1, 12), 'Ювелирная розница, УСН.'),
    ('ООО «Заря»', '7709012345', '770901001', 'active', 7,
     date(2026, 1, 20), 'Сельхозпроизводство.'),
    ('ООО «Импульс»', '7710123456', '771001001', 'active', 8,
     date(2026, 2, 2), 'ИТ-услуги.'),
    ('ООО «Кедр»', '7711234567', '771101001', 'active', 8,
     date(2026, 2, 16), 'Мебельное производство.'),
    ('ООО «Лагуна»', '7712345678', '771201001', 'active', 9,
     date(2026, 3, 2), 'Туристические услуги.'),
    ('ООО «Меридиан»', '7713456789', '771301001', 'active', 9,
     date(2026, 3, 16), 'Логистика.'),
    ('ООО «Нептун»', '7714567890', '771401001', 'active', 10,
     date(2026, 4, 1), 'Рыбопереработка.'),
    ('ООО «Орион»', '7715678901', '771501001', 'active', 10,
     date(2026, 4, 15), 'Оптовая торговля.'),
    ('ООО «Пилот»', '7716789012', '771601001', 'active', 11,
     date(2026, 5, 5), 'Авиаперевозки, ОСНО.'),
    ('ООО «Радуга»', '7717890123', '771701001', 'active', 11,
     date(2026, 5, 19), 'Детские товары, УСН.'),
    ('ООО «Сокол»', '7718901234', '771801001', 'active', 12,
     date(2026, 6, 1), 'Охранные услуги.'),
    ('ООО «Темп»', '7719012345', '771901001', 'active', 12,
     date(2026, 6, 15), 'Ремонт оборудования.'),
    ('ООО «Урал»', '7720123456', '772001001', 'archived', 13,
     date(2024, 4, 10), 'Клиент передан другому оператору.'),
    ('ООО «Феникс»', '7721234567', '772101001', 'archived', 13,
     date(2024, 6, 18), 'Деятельность приостановлена.'),
    ('ООО «Хрусталь»', '7722345678', '772201001', 'archived', 14,
     date(2024, 9, 5), 'Смена оператора в 2024 г.'),
    ('ООО «Чайка»', '7723456789', '772301001', 'archived', 14,
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

_STATUS_1C = {'active': 'Действует', 'closed': 'Закрыт', 'draft': 'Проект'}


def _update_passwords():
    """Обновляет пароли уже существующих демо-пользователей (без пересева)."""
    print('Обновление паролей существующих пользователей…')
    updated = 0
    for email, _fio, _role, password, _is_active in USERS:
        user = User.query.filter_by(email=email).first()
        if user is None:
            continue
        user.set_password(password)
        updated += 1
    db.session.commit()
    print(f'Обновлено паролей: {updated}.')
    for email, _fio, _role, password, _is_active in USERS:
        if User.query.filter_by(email=email).first() is not None:
            print(f'  {email:<22} {password}')


def main():
    with app.app_context():
        if User.query.count() > 0:
            _update_passwords()
            print('База уже содержит пользователей — остальные данные не изменялись.')
            return

        print('Создание пользователей…')
        users = {}
        for email, fio, role, password, is_active in USERS:
            u = User(email=email, fio=fio, role=role,
                     is_active_flag=is_active)
            u.set_password(password)
            db.session.add(u)
            users[email] = u
        db.session.flush()  # чтобы получить id до итогового commit
        user_list = list(users.values())

        print('Настройка подключения к 1С:Предприятие…')
        db.session.add(OneCSetting(
            id=1,
            odata_url='',
            username='',
            password='',
        ))

        print(f'Создание клиентов ({len(CLIENTS)})…')
        clients = []
        for name, inn, kpp, status, resp_idx, added, notes in CLIENTS:
            c = Client(
                name=name, inn=inn or None, kpp=kpp or None,
                status=status, notes=notes,
                responsible_id=user_list[resp_idx - 1].id if resp_idx else None,
                date_added=added)
            db.session.add(c)
            clients.append(c)
        db.session.flush()  # id клиентов нужны для договоров и задач

        print(f'Создание контрагентов ({len(CONTRACTORS)})…')
        contractors = []
        for ref, full_name, inn, kpp, description in CONTRACTORS:
            c = Contractor(external_ref=ref, full_name=full_name,
                           inn=inn, kpp=kpp, description=description)
            db.session.add(c)
            contractors.append(c)
        db.session.flush()

        print('Создание справочника отчётности…')
        for name, code, periodicity, due_day, offset, category in DEADLINES:
            db.session.add(ReportingDeadline(
                name=name, code=code, periodicity=periodicity,
                due_day=due_day, period_offset=offset,
                taxpayer_category=category, is_enabled=True))

        print(f'Создание договоров ({len(CONTRACTS)})…')
        for idx, (number, client_idx, contractor_idx, description,
                  amount, status, start, end) in enumerate(CONTRACTS, start=1):
            contractor = contractors[contractor_idx - 1]
            db.session.add(Contract(
                number=number, signed_date=start,
                client_id=clients[client_idx - 1].id,
                contractor_id=contractor.id,
                contractor_ref=contractor.external_ref,
                external_ref=f'aaaaaaaa-0000-0000-0000-{idx:012d}',
                description=description,
                amount=amount, status=status,
                status_1c=_STATUS_1C.get(status, status),
                start_date=start, end_date=end))

        print('Создание демонстрационных задач…')
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
            db.session.add(Task(
                title=title,
                description='Демонстрационная задача, создана скриптом seed.py.',
                client_id=clients[client_idx - 1].id if client_idx else None,
                assignee_id=user_list[assignee_idx - 1].id,
                due_date=due, priority=priority, status=status))

        db.session.commit()
        print('Готово. Тестовые учётные записи:')
        for email, fio, role, password, is_active in USERS:
            print(f'  {email:<22} {password:<20} '
                  f'{"руководитель" if role == "admin" else "сотрудник":<12} '
                  f'{"активна" if is_active else "ОТКЛЮЧЕНА"}')


if __name__ == '__main__':
    main()
