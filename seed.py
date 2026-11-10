"""Наполнение базы демонстрационными данными.

Запуск из корня проекта:
    python seed.py

Создаёт: пользователей (руководитель + сотрудники), клиентов, договоры,
справочник сроков отчётности и демонстрационные задачи.
"""
from datetime import date, timedelta

from app import app
from models import (Client, Contract, ReportingDeadline, Task, User, db)

USERS = [
    ('admin@delta.local', 'Иванова Мария Петровна', 'admin', 'admin123'),
    ('ivanov@delta.local', 'Иванов Пётр Сергеевич', 'employee', 'ivan123'),
    ('petrova@delta.local', 'Петрова Анна Львовна', 'employee', 'anna123'),
]

CLIENTS = [
    ('ООО «Ромашка»', '7701234567', '770101001', 'active', 2,
     'Розничная торговля. Отчётность УСН.'),
    ('ООО «ТехноПром»', '7702345678', '770201001', 'active', 3,
     'Производственное предприятие, ОСНО.'),
    ('ИП Сидоров А.В.', '770312345678', None, 'active', 2,
     'ИП на УСН, торговля через маркетплейсы.'),
    ('ООО «Вектор»', '7703456789', '770301001', 'archived', None,
     'Клиент передан другому оператору в 2025 г.'),
    ('ООО «Северное сияние»', '7704567890', '770401001', 'active', 3, ''),
]

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
        for email, fio, role, password in USERS:
            u = User(email=email, fio=fio, role=role)
            u.set_password(password)
            db.session.add(u)
            users[email] = u
        db.session.flush()  # чтобы получить id до итогового commit

        print('Создание клиентов…')
        clients = []
        for name, inn, kpp, status, resp_idx, notes in CLIENTS:
            c = Client(
                name=name, inn=inn or None, kpp=kpp or None,
                status=status, notes=notes,
                responsible_id=list(users.values())[resp_idx - 1].id if resp_idx else None,
                date_added=date(2025, 9, 15))
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
                assignee_id=list(users.values())[assignee_idx - 1].id,
                due_date=due, priority=priority, status=status))

        db.session.commit()
        print('Готово. Вход: admin@delta.local / admin123 (руководитель),')
        print('        ivanov@delta.local / ivan123 (сотрудник),')
        print('        petrova@delta.local / anna123 (сотрудник).')


if __name__ == '__main__':
    main()
