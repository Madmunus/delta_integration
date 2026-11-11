"""Инфраструктура сценарного тестирования системы «Дельта».

Каждый сценарий получает собственное изолированное приложение:
SQLite-база в памяти (данные исчезают после сценария) и отключённый
CSRF (тестовый клиент отправляет формы без токена). HTTP-запросы
к OData-интерфейсу «1С» подменяются заранее подготовленными ответами
(fake_1c), поэтому сценарии воспроизводимы и не зависят от внешних
сервисов.
"""
import json
from unittest import mock

from app import create_app
from engine.models import Client, User, db

# Единый пароль тестовых учётных записей (соответствует политике паролей)
TEST_PASSWORD = 'Scenario!2026'

# Тестовые учётные записи: руководитель и два сотрудника
EMAILS = {
    'admin': 'admin@test.local',
    'emp1': 'emp1@test.local',
    'emp2': 'emp2@test.local',
}


class Steps:
    """Сборщик результатов шагов сценария."""

    def __init__(self):
        self.items = []

    def check(self, ok, name, expected, actual, details=''):
        """Шаг-проверка: ожидаемый и фактический результат попадают в отчёт."""
        self.items.append({
            'name': name,
            'status': 'passed' if ok else 'failed',
            'expected': str(expected),
            'actual': str(actual),
            'details': str(details or ''),
        })
        return ok

    def note(self, name, actual=''):
        """Информационный шаг (всегда успешен, проверки нет)."""
        self.items.append({
            'name': name, 'status': 'passed',
            'expected': '', 'actual': str(actual), 'details': '',
        })

    @property
    def passed(self):
        return all(s['status'] == 'passed' for s in self.items)


class FakeResponse:
    """Имитация ответа requests для подмены OData-запросов."""

    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


def create_scenario_app():
    """Изолированное приложение со своей базой данных SQLite в памяти."""
    app = create_app('testing')
    with app.app_context():
        db.create_all()
    return app


def fake_1c(contractors=None, documents=None):
    """Контекст-менеджер: подмена HTTP-запросов к «1С» (OData).

    contractors — строки справочника Catalog_Контрагенты (синхронизация
    реестра клиентов, ФТ-1); documents — строки сущности Document_*
    из «1С:Документооборот» (загрузка документов, ФТ-2).
    """
    contractors = contractors or []
    documents = documents or []

    def fake_get(self, url, **kwargs):
        if 'Catalog_' in url:
            return FakeResponse({'value': contractors})
        if 'Document_' in url:
            return FakeResponse({'value': documents})
        return FakeResponse({'value': []})

    return mock.patch('requests.Session.get', new=fake_get)


def seed_users(app):
    """Создать тестовые учётные записи (руководитель + два сотрудника).

    Возвращает словарь {'admin': id, 'emp1': id, 'emp2': id}.
    """
    accounts = [
        (EMAILS['admin'], 'Админов Аркадий Петрович', 'admin'),
        (EMAILS['emp1'], 'Сотрудников Сергей Иванович', 'employee'),
        (EMAILS['emp2'], 'Работникова Ольга Львовна', 'employee'),
    ]
    with app.app_context():
        for email, fio, role in accounts:
            user = User(email=email, fio=fio, role=role, is_active_flag=True)
            user.set_password(TEST_PASSWORD)
            db.session.add(user)
        db.session.flush()
        ids = {}
        for email, _, _ in accounts:
            ids[email] = User.query.filter_by(email=email).first().id
        db.session.commit()
    return {'admin': ids[EMAILS['admin']], 'emp1': ids[EMAILS['emp1']],
            'emp2': ids[EMAILS['emp2']]}


def seed_clients(app, rows):
    """Создать клиентов. rows — кортежи (name, inn, status, responsible_id).

    Возвращает словарь {name: id}.
    """
    with app.app_context():
        ids = {}
        for name, inn, status, responsible_id in rows:
            client = Client(name=name, inn=inn, status=status,
                            responsible_id=responsible_id)
            db.session.add(client)
        db.session.flush()
        for name, _, _, _ in rows:
            ids[name] = Client.query.filter_by(name=name).first().id
        db.session.commit()
    return ids


def login(test_client, email, password=TEST_PASSWORD):
    """Вход в систему через веб-форму (как обычный пользователь)."""
    return test_client.post(
        '/login', data={'email': email, 'password': password},
        follow_redirects=True)
