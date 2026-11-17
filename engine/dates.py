"""Разбор дат из параметров запроса и фильтр по диапазону дат.

Реестры клиентов, контрагентов, договоров, документов и задач используют
единое поле «дд.мм.гггг — дд.мм.гггг»: диапазон можно ввести вручную или
выбрать в календаре. Старые параметры ``date_from`` / ``date_to``
поддерживаются для совместимости со ссылками.
"""
import re
from datetime import datetime

from sqlalchemy import func

DATE_FORMATS = ('%Y-%m-%d', '%d.%m.%Y')
DISPLAY_FORMAT = '%d.%m.%Y'

# Дата в тексте: 15.01.2026 или 2026-01-15
_DATE_TOKEN = re.compile(r'\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2}')
# Слова-подсказки у одиночной даты: «с 15.01.2026» или «по 31.01.2026»
_FROM_PREFIX = re.compile(r'^(?:с|от)\s*', re.IGNORECASE)
_TO_PREFIX = re.compile(r'^(?:по|до)\s*', re.IGNORECASE)


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


def parse_date_range(value):
    """Границы диапазона из текста единого поля.

    «15.01.2026 — 31.01.2026» — диапазон; одна дата без слов-подсказок
    означает этот день (границы совпадают); «с 15.01.2026» — только
    нижняя граница, «по 31.01.2026» — только верхняя.
    Возвращает кортеж ``(date_from, date_to)`` в формате ``дд.мм.гггг``
    (пустая строка — граница не задана).
    """
    raw = (value or '').strip()
    tokens = _DATE_TOKEN.findall(raw)
    if not tokens:
        return '', ''
    if len(tokens) == 1:
        single = parse_query_date(tokens[0])
        if single is None:
            return '', ''
        display = single.strftime(DISPLAY_FORMAT)
        if _FROM_PREFIX.match(raw):
            return display, ''
        if _TO_PREFIX.match(raw):
            return '', display
        return display, display
    first = parse_query_date(tokens[0])
    last = parse_query_date(tokens[-1])
    if first is None or last is None:
        return '', ''
    if first > last:
        first, last = last, first
    return first.strftime(DISPLAY_FORMAT), last.strftime(DISPLAY_FORMAT)


def read_date_range(args):
    """Границы диапазона из параметров запроса.

    Основной параметр — ``date_range`` (единое поле). Старые
    ``date_from`` / ``date_to`` читаются, если ``date_range`` пуст.
    """
    raw = (args.get('date_range') or '').strip()
    if raw:
        return parse_date_range(raw)
    return ((args.get('date_from') or '').strip(),
            (args.get('date_to') or '').strip())


def date_range_value(date_from, date_to):
    """Значение единого поля «с — по» по границам диапазона."""
    if date_from and date_to:
        return f'{date_from} — {date_to}'
    if date_from:
        return f'с {date_from}'
    if date_to:
        return f'по {date_to}'
    return ''


def apply_date_range(query, column, args):
    """Отфильтровать запрос по диапазону дат указанного столбца.

    ``args`` — параметры запроса (``request.args``). Возвращает кортеж
    ``(query, date_range)``, где ``date_range`` — значение для единого
    поля формы.
    """
    date_from, date_to = read_date_range(args)
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
            date_range_value(
                d_from.strftime(DISPLAY_FORMAT) if d_from else '',
                d_to.strftime(DISPLAY_FORMAT) if d_to else ''))
