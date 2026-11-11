"""Наполнение базы демонстрационными данными.

Запуск из корня проекта:
    python seed.py

Создаёт: пользователей (2 активных руководителя, 6 активных
и 2 неактивных сотрудника), 100 клиентов (5 основных
демонстрационных и 95 сгенерированных детерминированно),
договоры, справочник сроков отчётности и демонстрационные задачи.
Пароли тестовых учётных записей соответствуют политике:
не менее 13 символов, минимум одна цифра, заглавная и
строчная латинские буквы, спецсимвол.
"""
import random
from datetime import date, timedelta

from app import app
from engine.models import (Client, Contract, ReportingDeadline, Task, User, db)

# (email, ФИО, роль, пароль, активен)
USERS = [
    ('admin@delta.local', 'Иванова Мария Петровна', 'admin',
     'Admin!2026Delta', True),
    ('ivanov@delta.local', 'Иванов Пётр Сергеевич', 'employee',
     'Ivanov!2026Empl', True),
    ('petrova@delta.local', 'Петрова Анна Львовна', 'employee',
     'Petrova!2026Empl', True),
    ('smirnov@delta.local', 'Смирнов Олег Витальевич', 'admin',
     'Smirnov!2026Boss', True),
    ('kuznetsov@delta.local', 'Кузнецов Дмитрий Александрович', 'employee',
     'Kuznetsov!2026Empl', True),
    ('orlova@delta.local', 'Орлова Елена Викторовна', 'employee',
     'Orlova!2026Empl', True),
    ('volkov@delta.local', 'Волков Артём Игоревич', 'employee',
     'Volkov!2026Empl', True),
    ('fedorova@delta.local', 'Фёдорова Ольга Николаевна', 'employee',
     'Fedorova!2026Empl', True),
    ('morozov@delta.local', 'Морозов Илья Павлович', 'employee',
     'Morozov!2026Old', False),
    ('nikolaeva@delta.local', 'Николаева Светлана Андреевна', 'employee',
     'Nikolaeva!2026Old', False),
]

# Клиенты: (наименование, ИНН, КПП, статус, индекс ответственного
# в USERS (с единицы; 0 — не назначен), дата добавления, заметки).
CLIENTS = [
    # --- основные демонстрационные клиенты ---
    ('ООО «Ромашка»', '7701234567', '770101001', 'active', 2,
     date(2025, 9, 15), 'Розничная торговля. Отчётность УСН.'),
    ('ООО «ТехноПром»', '7702345678', '770201001', 'active', 3,
     date(2025, 9, 15), 'Производственное предприятие, ОСНО.'),
    ('ИП Сидоров А.В.', '770312345678', None, 'active', 2,
     date(2025, 9, 15), 'ИП на УСН, торговля через маркетплейсы.'),
    ('ООО «Вектор»', '7703456789', '770301001', 'archived', 0,
     date(2025, 9, 15), 'Клиент передан другому оператору в 2025 г.'),
    ('ООО «Северное сияние»', '7704567890', '770401001', 'active', 3,
     date(2025, 9, 15), ''),
]

_ORG_FORMS = ('ООО', 'АО', 'ПАО')
_ROOTS = ('Агат', 'Альтаир', 'Арго', 'Атлант', 'Барс', 'Вега', 'Восход',
          'Гарант', 'Гранит', 'Диалог', 'Жемчуг', 'Заря', 'Зенит', 'Изумруд',
          'Ирис', 'Каскад', 'Кварц', 'Кристалл', 'Ладога', 'Легенда',
          'Магнит', 'Меридиан', 'Метеор', 'Монолит', 'Нептун', 'Оникс',
          'Орбита', 'Пилот', 'Прогресс', 'Радуга', 'Ресурс', 'Родник',
          'Рубин', 'Сапфир', 'Сатурн', 'Сокол', 'Темп', 'Топаз', 'Урал',
          'Факел', 'Феникс', 'Хрусталь', 'Чайка', 'Шанс', 'Эверест',
          'Эталон', 'Юпитер', 'Янтарь', 'Якорь')
_ACTIVITIES = ('Трейд', 'Строй', 'Сервис', 'Логистик', 'Фарм', 'Металл',
               'Пласт', 'Агро', 'Тур', 'Плюс', 'Групп', 'Дом', '')
_IP_SURNAMES = ('Гуров', 'Данилов', 'Ефимов', 'Захаров', 'Ильин', 'Карпов',
                'Лебедев', 'Майоров', 'Назаров', 'Осипов', 'Пахомов',
                'Родионов', 'Савельев', 'Тарасов', 'Уваров', 'Хохлов',
                'Чернов', 'Шубин', 'Щербаков', 'Юдин')
_IP_INITIALS = ('А.В.', 'Д.Г.', 'Е.И.', 'К.Л.', 'М.Н.', 'О.П.', 'Р.С.',
                'Т.Ф.', 'У.Х.', 'Ю.Я.')
_INSPECTIONS = ('7701', '7702', '7703', '7704', '7705', '7706', '7707',
                '7708', '7709', '7840', '7841', '7842', '5001', '5007',
                '5008', '5010', '5260', '5262', '6625', '6626', '2310',
                '2311', '1601', '1602', '5405', '5406', '3810')
_NOTES = ('УСН, розничная торговля.', 'ОСНО, оптовая торговля.',
          'УСН, производство.', 'ОСНО, производство.', 'УСН, услуги.',
          'ОСНО, строительство.', 'УСН, транспортные услуги.',
          'ОСНО, ИТ-услуги.', 'УСН, аренда и недвижимость.', '')


def _extra_clients(count=95, seed=2026):
    """Детерминированная генерация дополнительных тестовых клиентов.

    Уникальные наименования и ИНН, разные статус, ответственный
    (иногда не назначен) и дата добавления. Ответственные берутся
    только из активных пользователей (индексы 1-8 в USERS).
    """
    rng = random.Random(seed)
    used_names = {row[0] for row in CLIENTS}
    used_inn = {row[1] for row in CLIENTS if row[1]}
    extra = []
    while len(extra) < count:
        if rng.random() < 0.15:
            name = f'ИП {rng.choice(_IP_SURNAMES)} {rng.choice(_IP_INITIALS)}'
            is_ip = True
        else:
            name = (f'{rng.choice(_ORG_FORMS)} '
                    f'«{rng.choice(_ROOTS)}{rng.choice(_ACTIVITIES)}»')
            is_ip = False
        if name in used_names:
            continue
        insp = rng.choice(_INSPECTIONS)
        while True:
            if is_ip:  # 12-значный ИНН для ИП
                inn = f'{insp}{rng.randint(0, 999999):06d}{rng.randint(0, 99):02d}'
            else:      # 10-значный ИНН для организаций
                inn = f'{insp}{rng.randint(0, 99999):05d}{rng.randint(0, 9)}'
            if inn not in used_inn:
                break
        used_names.add(name)
        used_inn.add(inn)
        status = 'archived' if rng.random() < 0.2 else 'active'
        resp_idx = 0 if rng.random() < 0.08 else rng.randint(1, 8)
        added = date(2024, 1, 1) + timedelta(days=rng.randint(0, 1034))
        extra.append((name, inn,
                      None if is_ip else f'{insp}01001',
                      status, resp_idx, added, rng.choice(_NOTES)))
    return extra


CLIENTS += _extra_clients()

DEADLINES = [
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

CONTRACTS = [
    ('БС-2025-014', 1, 'Бухгалтерское сопровождение: банк, кадры, отчётность УСН',
     18000, 'active', date(2025, 2, 1), date(2026, 1, 31)),
    ('БС-2025-027', 2, 'Сопровождение ОСНО: НДС, прибыль, зарплата, отчётность',
     45000, 'active', date(2025, 4, 10), date(2026, 4, 9)),
    ('БС-2025-031', 3, 'Консультации и сдача отчётности ИП на УСН',
     6000, 'active', date(2025, 6, 1), date(2026, 5, 31)),
    ('БС-2024-052', 4, 'Сопровождение ООО «Вектор» (завершён)',
     12000, 'closed', date(2024, 3, 1), date(2025, 2, 28)),
    ('БС-2026-001', 5, 'Проект договора на сопровождение',
     21000, 'draft', date(2026, 1, 20), date(2026, 2, 19)),
]


def main():
    with app.app_context():
        if User.query.count() > 0:
            print('База уже содержит пользователей — сид пропущен.')
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

        print('Создание справочника отчётности…')
        for name, code, periodicity, due_day, offset, category in DEADLINES:
            db.session.add(ReportingDeadline(
                name=name, code=code, periodicity=periodicity,
                due_day=due_day, period_offset=offset,
                taxpayer_category=category, is_enabled=True))

        print('Создание договоров…')
        for number, client_idx, subject, amount, status, start, end in CONTRACTS:
            db.session.add(Contract(
                number=number, signed_date=start,
                client_id=clients[client_idx - 1].id,
                subject=subject, amount=amount, status=status,
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
