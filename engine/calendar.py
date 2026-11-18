"""Календарь сдачи отчётности: выходные, праздники и перенос сроков.

Без внешних зависимостей используются фиксированные праздники РФ и список
дополнительных дат из ``DELTA_CALENDAR_HOLIDAYS``. Дата сдачи, попавшая
на нерабочий день, переносится вперёд (по умолчанию) или назад — в зависимости
от ``DELTA_CALENDAR_SHIFT``. Настройки читаются из ``current_app.config``,
поэтому тестовый режим может отключить перенос, не меняя код расчёта.
"""
from datetime import date, timedelta

from flask import current_app, has_app_context

from engine.config import Config

# Ежегодные нерабочие даты: новогодние праздники 1–8 января и 31 декабря,
# затем официальные федеральные праздники.
FEDERAL_HOLIDAYS = frozenset({
    (1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7), (1, 8),
    (2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4), (12, 31),
})


def is_holiday(day: date, holidays=()) -> bool:
    """Праздничный день: ежегодный или из списка конкретных дат."""
    return (day.month, day.day) in FEDERAL_HOLIDAYS or day in holidays


def is_working_day(day: date, holidays=()) -> bool:
    """Рабочий день: не суббота/воскресенье и не праздник."""
    return day.weekday() < 5 and not is_holiday(day, holidays)


def calendar_options(holidays=None, shift=None):
    """Текущие настройки календаря из конфигурации приложения."""
    if has_app_context():
        config = current_app.config
        if holidays is None:
            holidays = config.get('CALENDAR_HOLIDAYS', Config.CALENDAR_HOLIDAYS)
        if shift is None:
            shift = config.get('CALENDAR_SHIFT', Config.CALENDAR_SHIFT)
    else:
        if holidays is None:
            holidays = Config.CALENDAR_HOLIDAYS
        if shift is None:
            shift = Config.CALENDAR_SHIFT
    return tuple(holidays or ()), shift or 'forward'


def adjust_due_date(due: date, holidays=None, shift=None) -> date:
    """Перенести дату сдачи с нерабочего дня на ближайший рабочий.

    ``shift='none'`` сохраняет арифметическую дату; ``'backward'``
    переносит на предыдущий рабочий день, остальные значения — на следующий.
    """
    holidays, shift = calendar_options(holidays, shift)
    if shift == 'none':
        return due
    step = -1 if shift == 'backward' else 1
    candidate = due
    for _ in range(31):
        if is_working_day(candidate, holidays):
            return candidate
        candidate += timedelta(days=step)
    # Нерабочих дней подряд больше месяца — возвращаем исходную дату.
    return due
