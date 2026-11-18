"""Вспомогательная логика сводных отчётов.

Здесь собрано то, что одинаково нужно всем четырём отчётам:

* разбор периода и окна сравнения из параметров запроса;
* подготовка рядов для графиков;
* экспорт таблиц в CSV и XLSX без сторонних библиотек.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import OrderedDict
from datetime import date, datetime, timedelta
from decimal import Decimal
from xml.sax.saxutils import escape


# ----------------------------------------------------------------------------
# Фильтры отчётов
# ----------------------------------------------------------------------------
def report_filters(args):
    """Параметры общего фильтра отчётов.

    ``date_range`` разбирается тем же хелпером, что и в реестрах. Параметр
    ``compare`` включает окно сравнения с предыдущим интервалом той же
    длительности. Выбор пользователя сохраняется в ``compare_selected``
    даже тогда, когда сравнение ещё нельзя посчитать без обеих границ
    периода: иначе select «слетал» после применения фильтра.
    """
    from engine.dates import date_range_value, parse_query_date, read_date_range

    raw_from, raw_to = read_date_range(args)
    d_from = parse_query_date(raw_from)
    d_to = parse_query_date(raw_to)
    date_range = date_range_value(raw_from, raw_to)

    compare = (args.get('compare') or '').strip()
    # Старые значения week/month/quarter/year продолжают включать сравнение,
    # но предыдущий период всегда равен выбранному по длительности.
    valid_compare = ('previous', 'week', 'month', 'quarter', 'year')
    compare_selected = compare if compare in valid_compare else ''
    compare = compare_selected
    # Без обеих границ сравнение неоднозначно: «предыдущий период» от
    # открытого диапазона не имеет верхней границы. Сам выбор в форме
    # сохраняем, чтобы он не сбрасывался после отправки фильтра.
    if compare and not (d_from and d_to):
        compare = ''

    previous = None
    if compare:
        length = (d_to - d_from).days + 1
        previous = (d_from - timedelta(days=length),
                    d_from - timedelta(days=1))
    return {
        'date_range': date_range,
        'date_from': d_from,
        'date_to': d_to,
        'compare': compare,
        'compare_selected': compare_selected,
        'previous': previous,
        'range_length': ((d_to - d_from).days + 1) if d_from and d_to else 0,
    }


def filtered(rows, column, date_from, date_to):
    """Отфильтровать список ORM-объектов по дате (индекс в аргументе)."""
    result = []
    for row in rows:
        value = row[column] if isinstance(column, int) else getattr(row, column)
        if value is None:
            continue
        if isinstance(value, datetime):
            value = value.date()
        if date_from and value < date_from:
            continue
        if date_to and value > date_to:
            continue
        result.append(row)
    return result


def series_by_month(items, date_attr, value_fn=None):
    """Ряд «месяц → значение» для графика динамики.

    ``value_fn`` по умолчанию считает количество записей. Ключ — первый
    день месяца; сортировка по возрастанию.
    """
    buckets = OrderedDict()
    for item in items:
        raw = getattr(item, date_attr, None)
        if raw is None:
            continue
        value = raw.date() if hasattr(raw, 'date') and not isinstance(raw, date) else raw
        key = date(value.year, value.month, 1)
        buckets[key] = buckets.get(key, 0) + (
            1 if value_fn is None else value_fn(item))
    return list(buckets.items())


def series_by_status(items, status_fn, status_order):
    """Ряд «статус → количество» в заданном порядке статусов."""
    counts = {label: 0 for label in status_order}
    for item in items:
        label = status_fn(item)
        if label in counts:
            counts[label] += 1
    return list(counts.items())


def delta_text(current, previous):
    """Текст изменения для блока сравнения периодов."""
    if previous is None:
        return 'нет данных за прошлый период'
    diff = current - previous
    if diff > 0:
        return f'+{diff}'
    if diff < 0:
        return str(diff)
    return 'без изменений'


# ----------------------------------------------------------------------------
# Экспорт
# ----------------------------------------------------------------------------
def _csv_cell(value):
    if value is None:
        return ''
    if isinstance(value, Decimal):
        return f'{value:.2f}'
    if isinstance(value, date):
        return value.strftime('%d.%m.%Y')
    return str(value)


def build_csv(headers, rows):
    """CSV с BOM и точкой с запятой — открывается в русской Excel."""
    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter=';', lineterminator='\r\n')
    writer.writerow([_csv_cell(value) for value in headers])
    for row in rows:
        writer.writerow([_csv_cell(value) for value in row])
    return '\ufeff' + output.getvalue()


def _column_letter(index):
    result = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _xlsx_cell(index, row_index, value):
    ref = f'{_column_letter(index)}{row_index}'
    if value is None:
        return ''
    if isinstance(value, bool):
        return (f'<c r="{ref}" t="b"><v>{int(value)}</v></c>')
    if isinstance(value, (int, float, Decimal)):
        return f'<c r="{ref}"><v>{value}</v></c>'
    text = escape(_csv_cell(value))
    return (f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">'
            f'{text}</t></is></c>')


def build_xlsx(headers, rows, sheet_name='Отчёт'):
    """Минимальная valid XLSX-книга (OOXML) на стандартной библиотеке.

    Генерируется один лист без стилей: Excel, LibreOffice и Calc открывают
    такой файл без предупреждений. Формулы иRich-текст намеренно не
    используются, чтобы не усложнять генератор.
    """
    all_rows = [list(headers)] + [list(row) for row in rows]
    row_elements = []
    for row_index, row in enumerate(all_rows, start=1):
        cells = ''.join(
            _xlsx_cell(column_index, row_index, value)
            for column_index, value in enumerate(row, start=1))
        row_elements.append(f'<row r="{row_index}">{cells}</row>')
    columns = len(headers) or 1
    dimension = f'A1:{_column_letter(columns)}{len(all_rows)}'
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dimension}"/><sheetViews><sheetView workbookViewId="0"/>'
        '</sheetViews><sheetFormatPr defaultRowHeight="15"/>'
        '<sheetData>' + ''.join(row_elements) + '</sheetData></worksheet>'
    )
    safe_name = re.sub(r'[\\/*?:\[\]]', '_', sheet_name)[:31] or 'Отчёт'
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets>'
        f'<sheet name="{escape(safe_name)}" sheetId="1" r:id="rId1"/>'
        '</sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
        '</cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"'
        ' xfId="0"/></cellXfs></styleSheet>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', content_types)
        archive.writestr('_rels/.rels', root_rels)
        archive.writestr('xl/workbook.xml', workbook)
        archive.writestr('xl/_rels/workbook.xml.rels', workbook_rels)
        archive.writestr('xl/styles.xml', styles)
        archive.writestr('xl/worksheets/sheet1.xml', sheet)
    return buffer.getvalue()
