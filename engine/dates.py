"""Разбор дат из параметров запроса и фильтр по дате создания.

Используется реестрами клиентов, контрагентов, договоров и документов,
чтобы фильтр «с — по» работал одинаково на всех страницах.
"""
from datetime import datetime

from sqlalchemy import func

DATE_FORMATS = ('%Y-%m-%d', '%d.%m.%Y')
DISPLAY_FORMAT = '%d.%m.%Y'


def parse_query_date(value):
    """Дата из параметра запроса (ISO или ДД.ММ.ГГГГ); None при ошибке."""
    if not value:
        return None
    value = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def apply_created_at_range(query, column, date_from, date_to):
    """Отфильтровать запрос по диапазону даты создания.

    Возвращает кортеж ``(query, date_from, date_to)``, где даты приведены
    к привычному для полей формы формату ``дд.мм.гггг`` (или пустой строке).
    """
    d_from = parse_query_date(date_from)
    d_to = parse_query_date(date_to)
    # Сравниваем именно календарные даты: в SQLite значение хранится как
    # текст, и сравнение с datetime на границе суток (00:00:00) теряет
    # записи, созданные ровно в полночь.
    if d_from:
        query = query.filter(func.date(column) >= d_from)
    if d_to:
        query = query.filter(func.date(column) <= d_to)
    return (query,
            d_from.strftime(DISPLAY_FORMAT) if d_from else '',
            d_to.strftime(DISPLAY_FORMAT) if d_to else '')
