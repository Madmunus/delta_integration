"""Сводная отчётность для руководителя (ФТ-7)."""
from collections import defaultdict
from datetime import date
from decimal import Decimal
from urllib.parse import quote

from flask import (Blueprint, abort, make_response, render_template, request,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import and_, case, func
from sqlalchemy.orm import joinedload, load_only

from engine.dates import _range_bounds
from engine.models import (Client, Contract, Contractor, Document, Task, User,
                           db)
from engine.reporting import (build_csv, build_xlsx, delta_text, filtered,
                              report_filters, series_by_month,
                              series_by_status)

bp = Blueprint('reports', __name__)

# Статусы документов в порядке отображения (как в реестре документов).
# Статус из «1С» приходит свободным текстом, поэтому сравнение идёт
# по нормализованному значению: без пробелов по краям, в нижнем регистре,
# с заменой «ё» на «е».
DOCUMENT_STATUS_LABELS = ('Черновик', 'Зарегистрирован', 'Проведён', 'Отменён')
DOCUMENT_STATUS_KEYS = {
    label: label.lower().replace('ё', 'е') for label in DOCUMENT_STATUS_LABELS
}
DOCUMENT_STATUS_LABELS_BY_KEY = {
    key: label for label, key in DOCUMENT_STATUS_KEYS.items()
}

_TASK_STATUS_LABELS = {
    'new': 'Новая',
    'in_progress': 'В работе',
    'done': 'Выполнена',
}
_CONTRACT_STATUS_LABELS = {
    'draft': 'Проект',
    'active': 'Действующий',
    'closed': 'Завершённый',
}

COMPARE_LABEL = 'с предыдущим периодом'


def _document_status_key(document):
    """Нормализованный статус документа для группировки и сортировки."""
    return (document.status or '').strip().lower().replace('ё', 'е')


def _document_status_counts(documents):
    """Количество документов по известным статусам."""
    counts = {label: 0 for label in DOCUMENT_STATUS_LABELS}
    for document in documents:
        label = DOCUMENT_STATUS_LABELS_BY_KEY.get(_document_status_key(document))
        if label is not None:
            counts[label] += 1
    return counts


def _report_context(filters, export_name, **extra):
    """Общие параметры шаблона отчёта."""
    context = dict(filters)
    context['current_range'] = filters['date_range']
    if filters['previous']:
        first, last = filters['previous']
        context['previous_range'] = (
            f'{first:%d.%m.%Y} — {last:%d.%m.%Y}')
    else:
        context['previous_range'] = ''
    context.update({
        'registry_link': _registry_link,
        'export_name': export_name,
        'export_query': request.query_string.decode('utf-8', 'replace'),
        'csv_url': url_for('reports.export_report', report=export_name,
                           format='csv', **request.args.to_dict(flat=True)),
        'xlsx_url': url_for('reports.export_report', report=export_name,
                            format='xlsx', **request.args.to_dict(flat=True)),
    })
    context.update(extra)
    return context


def _registry_link(endpoint, date_range='', **params):
    """Ссылка в реестр с сохранением текущего фильтра дат."""
    if date_range:
        params['date_range'] = date_range
    return url_for(endpoint, **params)


def _period_join(column, filters):
    """Условие JOIN с датой периода.

    Фильтр живёт в условии соединения, а не в WHERE: пользователи без
    подходящих записей остаются в отчёте с нулевыми показателями.
    Границы берутся из того же хелпера, что и в реестрах, поэтому
    datetime-колонки сравниваются полуоткрытым интервалом.
    """
    conditions = [column.isnot(None)]
    low, high = _range_bounds(column, filters['date_from'],
                              filters['date_to'])
    if low is not None:
        conditions.append(column >= low)
    if high is not None:
        conditions.append(column < high)
    return and_(*conditions)


def _chart(current, previous=(), money=False):
    """Подготовить ряд для графика.

    Значения ``current`` и ``previous`` — пары «подпись → число». В результате
    лежат обе серии и общий максимум, чтобы шаблону не пришлось считать
    высоту столбцов.
    """
    previous_map = dict(previous)
    present = {label for label, _ in current}
    items = [
        {'label': label, 'current': value,
         'previous': previous_map.get(label)}
        for label, value in current
    ]
    items.extend(
        {'label': label, 'current': 0, 'previous': value}
        for label, value in previous if label not in present
    )
    values = [item['current'] for item in items]
    values += [item['previous'] for item in items if item['previous'] is not None]
    maximum = max(values) if values else 1
    return {
        'items': items,
        'max': maximum or 1,
        'money': money,
        'with_previous': any(item['previous'] is not None for item in items),
    }


def _status_chart(current, previous=()):
    """Распределение по статусам: подписи уже готовы к выводу."""
    return _chart(current, previous)


def _months(series):
    """Месячные ряды из engine.reporting к подписям «мм.гггг»."""
    return [(key.strftime('%m.%Y'), value) for key, value in series]


def _comparison(label, metrics):
    """Блок сравнения двух периодов со списком показателей."""
    return {
        'label': label,
        'items': [
            {
                'label': title,
                'current': current,
                'previous': previous,
                'delta': delta_text(current, previous),
                'money': money,
            }
            for title, current, previous, money in metrics
        ],
    }


def _export(report, headers, rows, name, filters):
    """Ответ с CSV/XLSX-файлом отчёта."""
    if report == 'csv':
        payload = build_csv(headers, rows)
        mimetype = 'text/csv; charset=utf-8'
    else:
        payload = build_xlsx(headers, rows, sheet_name=name)
        mimetype = ('application/vnd.openxmlformats-officedocument.'
                    'spreadsheetml.sheet')
    suffix = '.csv' if report == 'csv' else '.xlsx'
    range_part = ''
    if filters['date_from'] and filters['date_to']:
        range_part = (f"_{filters['date_from'].strftime('%Y%m%d')}"
                      f"-{filters['date_to'].strftime('%Y%m%d')}")
    filename = f'{name}{range_part}{suffix}'
    response = make_response(payload)
    response.headers['Content-Type'] = mimetype
    response.headers['Content-Disposition'] = (
        f'attachment; filename="report{suffix}"; '
        f"filename*=UTF-8''{quote(filename)}")
    return response


@bp.route('/reports')
@login_required
def index():
    """Список доступных отчётов."""
    if not current_user.is_admin:
        abort(403, 'Отчёты доступны только руководителю')
    return render_template('reports/index.html')


# ----------------------------------------------------------------------------
# Отчёт по клиентской базе
# ----------------------------------------------------------------------------
def _clients_data(filters):
    """Данные отчёта по клиентам с учётом фильтра дат."""
    clients = (Client.query
               .options(load_only(Client.id, Client.name, Client.inn,
                                  Client.status, Client.responsible_id,
                                  Client.created_at))
               .order_by(Client.name).all())
    current = filtered(clients, 'created_at', filters['date_from'],
                       filters['date_to'])
    previous = None
    if filters['previous']:
        previous = filtered(clients, 'created_at', *filters['previous'])

    def build(items):
        by_user = defaultdict(list)
        for client in items:
            by_user[client.responsible_id].append(client)
        return {
            'clients_by_user': by_user,
            'total': len(items),
            'active': sum(1 for client in items if client.status == 'active'),
            'unassigned': sum(1 for client in items
                              if client.responsible_id is None),
            'series': series_by_month(items, 'created_at'),
        }

    current_data = build(current)
    previous_data = build(previous) if previous is not None else None
    # Все пользователи остаются в отчёте, даже если за период у них нет
    # клиентов: период живёт в условии LEFT JOIN, а не в WHERE.
    active_clients = func.sum(case((Client.status == 'active', 1), else_=0))
    rows = (db.session.query(
                User.id, User.fio, User.is_active_flag,
                func.count(Client.id), active_clients,
            ).outerjoin(Client, and_(
                Client.responsible_id == User.id,
                _period_join(Client.created_at, filters),
            ))
             .group_by(User.id, User.fio, User.is_active_flag)
             .order_by(User.is_active_flag.desc(),
                       active_clients.desc(), User.fio.asc())).all()
    return current_data, previous_data, rows


@bp.route('/reports/clients')
@login_required
def clients_report():
    """Отчёт по клиентской базе: активные, распределение по ответственным."""
    if not current_user.is_admin:
        abort(403)
    filters = report_filters(request.args)
    data, previous, rows = _clients_data(filters)
    if filters['previous']:
        comparison = _comparison(COMPARE_LABEL, [
            ('Всего клиентов', data['total'], previous['total'], False),
            ('Активных', data['active'], previous['active'], False),
            ('Без ответственного', data['unassigned'],
             previous['unassigned'], False),
        ])
    else:
        comparison = None
    return render_template(
        'reports/clients.html', rows=rows, **_report_context(
            filters, 'clients', total=data['total'], active=data['active'],
            unassigned=data['unassigned'],
            clients_by_user=data['clients_by_user'],
            comparison=comparison,
            chart=_chart(_months(data['series']),
                         _months(previous['series']) if previous else [])))


# ----------------------------------------------------------------------------
# Отчёт по задачам
# ----------------------------------------------------------------------------
def _tasks_data(filters):
    """Данные отчёта по задачам с учётом фильтра дат."""
    today = date.today()
    tasks = (Task.query
             .options(
                 load_only(Task.id, Task.title, Task.client_id,
                           Task.due_date, Task.priority, Task.status,
                           Task.assignee_id, Task.reporting_deadline_id,
                           Task.created_at),
                 joinedload(Task.client).load_only(Client.id, Client.name),
             )
             .order_by(Task.due_date.asc().nullslast(), Task.title.asc()).all())
    current = filtered(tasks, 'due_date', filters['date_from'],
                       filters['date_to'])
    previous = None
    if filters['previous']:
        previous = filtered(tasks, 'due_date', *filters['previous'])

    def build(items):
        by_assignee = defaultdict(list)
        for task in items:
            by_assignee[task.assignee_id].append(task)
        done = sum(1 for task in items if task.status == Task.STATUS_DONE)
        opened = sum(1 for task in items
                     if task.status in (Task.STATUS_NEW, Task.STATUS_IN_PROGRESS))
        overdue = sum(1 for task in items
                      if (task.status != Task.STATUS_DONE
                          and task.due_date is not None
                          and task.due_date < today))
        return {
            'tasks_by_assignee': by_assignee,
            'total': len(items),
            'done': done,
            'open': opened,
            'overdue': overdue,
            'status_series': series_by_status(
                items, lambda t: t.status,
                (Task.STATUS_NEW, Task.STATUS_IN_PROGRESS, Task.STATUS_DONE)),
            'month_series': series_by_month(items, 'due_date'),
            'overdue_series': series_by_month(
                [t for t in items
                 if t.status != Task.STATUS_DONE and t.due_date
                 and t.due_date < today], 'due_date'),
        }

    current_data = build(current)
    previous_data = build(previous) if previous is not None else None
    # Агрегаты остаются SQL: условие периода входит в LEFT JOIN, поэтому
    # сотрудники без задач за период не исчезают из таблицы.
    overdue_condition = and_(Task.status != Task.STATUS_DONE,
                             Task.due_date.isnot(None),
                             Task.due_date < today)
    done = func.sum(case((Task.status == Task.STATUS_DONE, 1), else_=0))
    work = func.sum(case((Task.status.in_(
        [Task.STATUS_NEW, Task.STATUS_IN_PROGRESS]), 1), else_=0))
    over = func.sum(case((overdue_condition, 1), else_=0))
    rows = (db.session.query(
                User.id, User.fio, User.is_active_flag, done, work, over,
            ).outerjoin(Task, and_(
                Task.assignee_id == User.id,
                _period_join(Task.due_date, filters),
            ))
             .group_by(User.id, User.fio, User.is_active_flag)
             .order_by(User.is_active_flag.desc(), over.desc(), work.desc(),
                       done.desc(), User.fio.asc())).all()
    return current_data, previous_data, rows


@bp.route('/reports/tasks')
@login_required
def tasks_report():
    """Отчёт по задачам: выполненные, в работе, просроченные по сроку."""
    if not current_user.is_admin:
        abort(403)
    filters = report_filters(request.args)
    data, previous, rows = _tasks_data(filters)
    if filters['previous']:
        comparison = _comparison(COMPARE_LABEL, [
            ('Всего задач', data['total'], previous['total'], False),
            ('Выполнено', data['done'], previous['done'], False),
            ('В работе', data['open'], previous['open'], False),
            ('Просрочено', data['overdue'], previous['overdue'], False),
        ])
    else:
        comparison = None
    return render_template(
        'reports/tasks.html', rows=rows, **_report_context(
            filters, 'tasks', totals={
                'total': data['total'], 'done': data['done'],
                'open': data['open'], 'overdue': data['overdue'],
            },
            tasks_by_assignee=data['tasks_by_assignee'],
            comparison=comparison,
            chart=_chart(_months(data['month_series']),
                         _months(previous['month_series']) if previous else []),
            status_chart=_status_chart(
                [(_TASK_STATUS_LABELS.get(status, status), count)
                 for status, count in data['status_series']],
                [(_TASK_STATUS_LABELS.get(status, status), count)
                 for status, count in previous['status_series']]
                if previous else []),
            overdue_chart=_chart(_months(data['overdue_series']))))


# ----------------------------------------------------------------------------
# Отчёт по договорам
# ----------------------------------------------------------------------------
def _contracts_data(filters):
    """Данные отчёта по договорам с учётом фильтра дат."""
    status_order = {'draft': 0, 'active': 1, 'closed': 2}
    contracts = (Contract.query
                 .options(
                     load_only(Contract.id, Contract.number,
                               Contract.signed_date, Contract.start_date,
                               Contract.end_date, Contract.status,
                               Contract.status_1c,
                               Contract.client_id, Contract.contractor_id,
                               Contract.amount, Contract.processed),
                     joinedload(Contract.client).load_only(
                         Client.id, Client.name, Client.status),
                     joinedload(Contract.contractor).load_only(
                         Contractor.id, Contractor.full_name,
                         Contractor.external_ref),
                 ).all())
    current = filtered(contracts, 'signed_date', filters['date_from'],
                       filters['date_to'])
    previous = None
    if filters['previous']:
        previous = filtered(contracts, 'signed_date', *filters['previous'])

    def build(items):
        grouped = defaultdict(list)
        for contract in items:
            grouped[contract.client_id].append(contract)
        groups = []
        for group_items in grouped.values():
            group_items = list(group_items)
            group_items.sort(key=lambda c: (
                status_order.get(c.status, 3),
                -(c.signed_date.toordinal()) if c.signed_date else 0,
                c.number))
            counts = {status: 0 for status in status_order}
            amount_active = Decimal('0')
            amount_total = Decimal('0')
            processed = 0
            for contract in group_items:
                counts[contract.status] = counts.get(contract.status, 0) + 1
                amount_total += contract.amount or 0
                if contract.status == 'active':
                    amount_active += contract.amount or 0
                if contract.processed:
                    processed += 1
            groups.append({
                'client': group_items[0].client,
                'contracts': group_items,
                'total': len(group_items),
                'counts': counts,
                'processed': processed,
                'unprocessed': len(group_items) - processed,
                'amount_active': amount_active,
                'amount_total': amount_total,
            })
        groups.sort(key=lambda g: (
            0 if g['client'].is_active else 1,
            -g['amount_active'], g['client'].name))
        totals = {
            'clients': len(groups),
            'contracts': sum(g['total'] for g in groups),
            'draft': sum(g['counts'].get('draft', 0) for g in groups),
            'active': sum(g['counts'].get('active', 0) for g in groups),
            'closed': sum(g['counts'].get('closed', 0) for g in groups),
            'processed': sum(g['processed'] for g in groups),
            'amount_active': sum((g['amount_active'] for g in groups),
                                 Decimal('0')),
            'amount_total': sum((g['amount_total'] for g in groups),
                                Decimal('0')),
        }
        return {
            'groups': groups, 'totals': totals,
            'status_series': series_by_status(
                items, lambda c: c.status,
                ('draft', 'active', 'closed')),
            'amount_series': series_by_month(
                items, 'signed_date', lambda c: c.amount or 0),
        }

    current_data = build(current)
    previous_data = build(previous) if previous is not None else None
    return current_data, previous_data


@bp.route('/reports/contracts')
@login_required
def contracts_report():
    """Отчёт по договорам и поступлениям в разрезе клиентов."""
    if not current_user.is_admin:
        abort(403)
    filters = report_filters(request.args)
    data, previous = _contracts_data(filters)
    if filters['previous']:
        comparison = _comparison(COMPARE_LABEL, [
            ('Договоров', data['totals']['contracts'],
             previous['totals']['contracts'], False),
            ('Сумма действующих', data['totals']['amount_active'],
             previous['totals']['amount_active'], True),
            ('Действующих', data['totals']['active'],
             previous['totals']['active'], False),
            ('Сумма всех', data['totals']['amount_total'],
             previous['totals']['amount_total'], True),
        ])
    else:
        comparison = None
    client_amounts = [
        (group['client'].name, group['amount_active'])
        for group in data['groups'] if group['amount_active'] > 0
    ][:8]
    return render_template(
        'reports/contracts.html', **_report_context(
            filters, 'contracts', groups=data['groups'], totals=data['totals'],
            comparison=comparison,
            chart=_chart(_months(data['amount_series']),
                         _months(previous['amount_series']) if previous else [],
                         money=True),
            client_chart=_chart(client_amounts, money=True),
            status_chart=_status_chart(
                [(_CONTRACT_STATUS_LABELS.get(status, status), count)
                 for status, count in data['status_series']],
                [(_CONTRACT_STATUS_LABELS.get(status, status), count)
                 for status, count in previous['status_series']]
                if previous else [])))


# ----------------------------------------------------------------------------
# Отчёт по документам
# ----------------------------------------------------------------------------
def _documents_data(filters):
    """Данные отчёта по документам с учётом фильтра дат."""
    status_order = {key: index
                    for index, key in enumerate(DOCUMENT_STATUS_KEYS.values())}
    documents = (Document.query
                 .options(
                     load_only(Document.id, Document.doc_type, Document.number,
                               Document.doc_date, Document.status,
                               Document.client_id, Document.contractor_id,
                               Document.processed),
                     joinedload(Document.client).load_only(
                         Client.id, Client.name, Client.status),
                     joinedload(Document.contractor).load_only(
                         Contractor.id, Contractor.full_name,
                         Contractor.external_ref),
                 ).all())
    current = filtered(documents, 'doc_date', filters['date_from'],
                       filters['date_to'])
    previous = None
    if filters['previous']:
        previous = filtered(documents, 'doc_date', *filters['previous'])

    def build(items):
        grouped = defaultdict(list)
        for document in items:
            grouped[document.client_id].append(document)
        groups = []
        for group_items in grouped.values():
            group_items = list(group_items)
            group_items.sort(key=lambda d: (
                status_order.get(_document_status_key(d), len(status_order)),
                -(d.doc_date.toordinal()) if d.doc_date else 0,
                d.number or ''))
            counts = _document_status_counts(group_items)
            processed = sum(1 for d in group_items if d.processed)
            groups.append({
                'client': group_items[0].client,
                'documents': group_items,
                'total': len(group_items),
                'counts': counts,
                'processed': processed,
                'unprocessed': len(group_items) - processed,
            })
        groups.sort(key=lambda g: (
            0 if g['client'].is_active else 1,
            -g['total'], g['client'].name))
        totals = {
            'clients': len(groups),
            'documents': sum(g['total'] for g in groups),
            'draft': sum(g['counts']['Черновик'] for g in groups),
            'registered': sum(g['counts']['Зарегистрирован'] for g in groups),
            'conducted': sum(g['counts']['Проведён'] for g in groups),
            'cancelled': sum(g['counts']['Отменён'] for g in groups),
            'processed': sum(g['processed'] for g in groups),
        }
        def status_label(document):
            return DOCUMENT_STATUS_LABELS_BY_KEY.get(
                _document_status_key(document), 'Прочие')
        series = series_by_status(items, status_label,
                                  DOCUMENT_STATUS_LABELS + ('Прочие',))
        return {
            'groups': groups, 'totals': totals,
            'status_series': series[:-1] if series[-1][1] == 0 else series,
            'month_series': series_by_month(items, 'doc_date'),
        }

    current_data = build(current)
    previous_data = build(previous) if previous is not None else None
    return current_data, previous_data


@bp.route('/reports/documents')
@login_required
def documents_report():
    """Отчёт по документам в разрезе клиентов."""
    if not current_user.is_admin:
        abort(403)
    filters = report_filters(request.args)
    data, previous = _documents_data(filters)
    if filters['previous']:
        comparison = _comparison(COMPARE_LABEL, [
            ('Всего документов', data['totals']['documents'],
             previous['totals']['documents'], False),
            ('Зарегистрировано', data['totals']['registered'],
             previous['totals']['registered'], False),
            ('Черновиков', data['totals']['draft'],
             previous['totals']['draft'], False),
            ('Обработано', data['totals']['processed'],
             previous['totals']['processed'], False),
        ])
    else:
        comparison = None
    return render_template(
        'reports/documents.html', **_report_context(
            filters, 'documents', groups=data['groups'], totals=data['totals'],
            comparison=comparison,
            chart=_chart(_months(data['month_series']),
                         _months(previous['month_series']) if previous else []),
            status_chart=_status_chart(
                data['status_series'],
                previous['status_series'] if previous else [])))


# ----------------------------------------------------------------------------
# Экспорт отчётов
# ----------------------------------------------------------------------------
def _export_context(report):
    """Проверка доступа и подготовка данных для экспорта."""
    if not current_user.is_admin:
        abort(403)
    if report not in ('clients', 'tasks', 'contracts', 'documents'):
        abort(404)
    return report_filters(request.args)


@bp.route('/reports/export/<report>/<format>')
@login_required
def export_report(report, format):
    """Экспорт отчёта в CSV или XLSX с учётом текущего фильтра дат."""
    filters = _export_context(report)
    if format not in ('csv', 'xlsx'):
        abort(404)
    if report == 'clients':
        data, _, _ = _clients_data(filters)
        header = ('Ответственный', 'Клиент', 'ИНН', 'Статус', 'Добавлен')
        rows = []
        for clients in data['clients_by_user'].values():
            for client in clients:
                responsible = (client.responsible.fio
                               if client.responsible else '—')
                rows.append((responsible, client.name, client.inn,
                             'активный' if client.status == 'active'
                             else 'архивный', client.created_at))
        return _export(format, header, rows, 'Клиенты', filters)
    if report == 'tasks':
        data, _, _ = _tasks_data(filters)
        header = ('Задача', 'Клиент', 'Исполнитель', 'Срок', 'Приоритет',
                  'Статус', 'Просрочена')
        rows = []
        for tasks in data['tasks_by_assignee'].values():
            for task in tasks:
                rows.append((
                    task.title,
                    task.client.name if task.client else '—',
                    task.assignee.fio if task.assignee else '—',
                    task.due_date, task.priority, task.status,
                    'да' if (task.status != Task.STATUS_DONE
                             and task.due_date and task.due_date < date.today())
                    else 'нет',
                ))
        return _export(format, header, rows, 'Задачи', filters)
    if report == 'contracts':
        data, _ = _contracts_data(filters)
        header = ('Клиент', 'Номер', 'Дата подписания', 'Контрагент',
                  'Сумма', 'Статус', 'Обработан', 'Начало', 'Окончание')
        rows = []
        for group in data['groups']:
            for contract in group['contracts']:
                rows.append((
                    group['client'].name, contract.number,
                    contract.signed_date,
                    contract.contractor.full_name if contract.contractor else '—',
                    contract.amount, contract.status,
                    'да' if contract.processed else 'нет',
                    contract.start_date, contract.end_date,
                ))
        return _export(format, header, rows, 'Договоры', filters)
    data, _ = _documents_data(filters)
    header = ('Клиент', 'Вид документа', 'Номер', 'Дата', 'Контрагент',
              'Статус', 'Обработан')
    rows = []
    for group in data['groups']:
        for document in group['documents']:
            rows.append((
                group['client'].name, document.doc_type, document.number,
                document.doc_date,
                (document.contractor.display_name
                 if document.contractor else '—'),
                document.status,
                'да' if document.processed else 'нет',
            ))
    return _export(format, header, rows, 'Документы', filters)
