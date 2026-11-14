"""Сценарий 4 (ФТ-4): загрузка документов из «1С:Документооборот».

Проверяется перечень видов документов: по каждой сущности Document_*
загружаются поля Ref_Key, Организация, Контрагент (может быть пустым),
Номер, Дата, Статус. Поле «Вид документа» вычисляется как имя сущности
без префикса Document_. Результат: документы отображаются в карточке
клиента с корректными реквизитами.
"""
from datetime import date

from engine.models import Client, Contractor, Document, OneCSetting, db
from engine.odata_client import client_for_settings
from engine.sync import sync_documents
from tests.base import (EMAILS, Steps, create_scenario_app, fake_1c,
                        login, seed_users)

FT = 'ФТ-4'
TITLE = 'Загрузка документов клиента из «1С:Документооборот»'
DESCRIPTION = ('Запрос документов клиента из «1С:Документооборот» через OData; '
               'определение вида документа по имени сущности Document_*; '
               'контрагент может отсутствовать. Результат: документы '
               'отображаются с корректными реквизитами.')

ALPHA_KEY = '11111111-1111-1111-1111-111111111111'
BETA_KEY = '11111111-1111-1111-1111-111111111112'

ENTITY_SALES = 'Document_РеализацияТоваровУслуг'
ENTITY_INVOICE = 'Document_СчетНаОплатуПокупателю'
ENTITIES = [ENTITY_SALES, ENTITY_INVOICE]

# Ответ «1С:Документооборот» по видам документов. Во втором документе
# контрагент не указан, в четвёртом — неизвестный клиент (пропускается).
DOCUMENTS = {
    ENTITY_SALES: [
        {'Ref_Key': 'aaaaaaaa-0001-0000-0000-000000000001', 'Number': 'РТ-101',
         'Date': '2026-11-03T00:00:00', 'Организация': ALPHA_KEY,
         'Контрагент': BETA_KEY, 'Статус': 'Проведён'},
        {'Ref_Key': 'aaaaaaaa-0002-0000-0000-000000000002', 'Number': 'РТ-102',
         'Date': '2026-11-08T00:00:00', 'Организация': ALPHA_KEY,
         'Контрагент': '', 'Статус': 'Черновик'},
    ],
    ENTITY_INVOICE: [
        {'Ref_Key': 'aaaaaaaa-0003-0000-0000-000000000003', 'Number': 'СЧ-201',
         'Date': '2026-11-09T00:00:00', 'Организация': ALPHA_KEY,
         'Контрагент': BETA_KEY, 'Статус': 'Зарегистрирован'},
        {'Ref_Key': 'aaaaaaaa-0004-0000-0000-000000000004', 'Number': 'СЧ-999',
         'Date': '2026-11-10T00:00:00', 'Организация': 'unknown-client',
         'Контрагент': BETA_KEY, 'Статус': 'Черновик'},
    ],
}


def run():
    app = create_scenario_app()
    steps = Steps()
    seed_users(app)

    with app.app_context():
        db.session.add(OneCSetting(
            id=1,
            odata_url='http://test-do.local/odata/standard.odata/',
            username='odata', password='secret'))
        db.session.add(Client(
            name='ООО «Альфа-Тест»', inn='7701111111', kpp='770101001',
            status='active', external_ref=ALPHA_KEY))
        db.session.add(Contractor(
            full_name='ООО «Ромашка»', inn='7701234567', kpp='770101001',
            description='Розничная торговля', external_ref=BETA_KEY))
        db.session.commit()

    # --- Шаг 1. Вид документа вычисляется из имени сущности -----------------
    with app.app_context():
        setting = OneCSetting.get()
        api = client_for_settings(setting)
        with fake_1c(documents=DOCUMENTS):
            rows = api.get_documents(entities=ENTITIES)
        api.close()
    computed = sorted({row.get('ВидДокумента') for row in rows})
    steps.check(computed == ['РеализацияТоваровУслуг',
                             'СчетНаОплатуПокупателю'],
                'Поле «Вид документа» вычисляется из имени сущности',
                'Document_РеализацияТоваровУслуг → РеализацияТоваровУслуг; '
                'Document_СчетНаОплатуПокупателю → СчетНаОплатуПокупателю',
                f'получены виды: {", ".join(computed)}')

    # --- Шаг 2. Загрузка документов через OData ----------------------------
    with app.app_context():
        setting = OneCSetting.get()
        with fake_1c(documents=DOCUMENTS):
            processed = sync_documents(setting, entities=ENTITIES)
    steps.check(processed == 3,
                'Запрос документов через OData',
                'обработано 3 документа; документ неизвестного клиента пропущен',
                f'обработано {processed} документов')

    # --- Шаг 3. Реквизиты документов в базе --------------------------------
    with app.app_context():
        docs = Document.query.order_by(Document.number).all()
        by_number = {d.number: d for d in docs}
        client_id = Client.query.filter_by(external_ref=ALPHA_KEY).first().id
        contractor_id = Contractor.query.filter_by(external_ref=BETA_KEY).first().id
    steps.check(all(d.client_id == client_id for d in docs),
                'Документы привязаны к клиенту',
                'все документы связаны с карточкой клиента',
                f'client_id: {[d.client_id for d in docs]}')

    sales_doc = by_number.get('РТ-101')
    steps.check(
        sales_doc is not None
        and sales_doc.number == 'РТ-101'
        and sales_doc.doc_date == date(2026, 11, 3)
        and sales_doc.status == 'Проведён'
        and sales_doc.doc_type == 'РеализацияТоваровУслуг'
        and sales_doc.contractor_id == contractor_id,
        'Реквизиты первого документа корректны',
        'вид документа, номер, дата, статус и контрагент совпадают с 1С',
        f'{sales_doc.doc_type} №{sales_doc.number} от {sales_doc.doc_date}, '
        f'статус «{sales_doc.status}», контрагент {sales_doc.contractor_id}'
        if sales_doc else 'документ не найден')

    empty_contractor = by_number.get('РТ-102')
    steps.check(
        empty_contractor is not None
        and empty_contractor.contractor_id is None
        and empty_contractor.doc_type == 'РеализацияТоваровУслуг',
        'Документ без контрагента сохраняется',
        'пустой контрагент сохраняется как отсутствие (NULL)',
        f'контрагент: {empty_contractor.contractor_id}'
        if empty_contractor else 'документ не найден')

    invoice_doc = by_number.get('СЧ-201')
    steps.check(
        invoice_doc is not None
        and invoice_doc.doc_type == 'СчетНаОплатуПокупателю'
        and invoice_doc.status == 'Зарегистрирован',
        'Вид второго документа определён корректно',
        'Document_СчетНаОплатуПокупателю → СчетНаОплатуПокупателю',
        f'{invoice_doc.doc_type} №{invoice_doc.number}'
        if invoice_doc else 'документ не найден')

    # --- Шаг 4. Документы отображаются в карточке клиента -------------------
    web = app.test_client()
    login(web, EMAILS['admin'])
    html = web.get(f'/clients/{client_id}').get_data(as_text=True)
    shown = all(value in html for value in ('РТ-101', 'РТ-102', 'СЧ-201'))
    contractor_shown = ('Контрагент' in html and 'ООО «Ромашка»' in html
                        and f'/contractors/{contractor_id}' in html)
    steps.check('Документы (3)' in html and shown and 'Вид документа' in html
                and contractor_shown,
                'Документы отображаются в карточке клиента',
                'на вкладке «Документы» — все документы с видом, номером '
                'и ссылкой на контрагента',
                'документы и контрагент отображаются'
                if shown and contractor_shown
                else 'документы или контрагент не отображаются')
    steps.check('СЧ-999' not in html,
                'Документ неизвестного клиента не попадает в карточку',
                'документ с неизвестной организацией не отображается',
                'чужой документ скрыт' if 'СЧ-999' not in html
                else 'чужой документ отображается')

    # --- Шаг 4.1. Общий реестр документов и пункт меню --------------------
    registry_html = web.get('/documents').get_data(as_text=True)
    shown_registry = all(value in registry_html
                         for value in ('РТ-101', 'РТ-102', 'СЧ-201'))
    nav_positions = [registry_html.find(f'href="{url}"')
                     for url in ('/contracts', '/documents', '/tasks')]
    steps.check(shown_registry and 'Вид документа' in registry_html
                and 'Контрагент' in registry_html,
                'Документы отображаются в общем реестре',
                'на странице «Документы» — все документы с видом и номером',
                'реестр документов доступен' if shown_registry
                else 'реестр пуст')
    steps.check(all(pos >= 0 for pos in nav_positions)
                and nav_positions == sorted(nav_positions),
                'Пункт «Документы» находится между «Договоры» и «Задачи»',
                'ссылки меню идут в порядке: Договоры → Документы → Задачи',
                'порядок пунктов меню соблюдён'
                if nav_positions == sorted(nav_positions)
                else f'порядок нарушен: {nav_positions}')
    search_html = web.get('/documents',
                          query_string={'q': 'СЧ-201'}).get_data(as_text=True)
    steps.check('СЧ-201' in search_html and 'РТ-101' not in search_html,
                'В реестре документов работает поиск по таблице',
                'поиск по номеру оставляет только найденный документ',
                'найден только СЧ-201' if 'СЧ-201' in search_html
                else 'поиск не работает')

    # --- Шаг 5. Повторная загрузка не создаёт дублей -----------------------
    with app.app_context():
        setting = OneCSetting.get()
        with fake_1c(documents=DOCUMENTS):
            sync_documents(setting, entities=ENTITIES)
        total = Document.query.count()
    steps.check(total == 3,
                'Повторная загрузка не дублирует документы',
                'в карточке по-прежнему 3 документа',
                f'всего документов: {total}')

    # --- Шаг 6. Ссылка на объект в 1С заполняется при загрузке -------------
    with app.app_context():
        sales_doc = Document.query.filter_by(number='РТ-101').first()
        external_url = sales_doc.external_url if sales_doc else None
    steps.check(
        bool(external_url) and 'e1cib/data/' in external_url
        and 'ref=' in external_url
        and 'aaaaaaaa-0001-0000-0000-000000000001' in external_url,
        'Поле «Ссылка» заполняется адресом объекта в 1С',
        'external_url содержит deep link веб-клиента 1С с Ref_Key документа',
        external_url or 'ссылка не заполнена')

    registry_html = web.get('/documents').get_data(as_text=True)
    link_shown = bool(external_url) and f'href="{external_url}"' in registry_html
    steps.check(link_shown,
                'Ссылка на документ отображается в реестре',
                'в столбце «Ссылка» есть гиперссылка на объект 1С',
                'ссылка отображается' if link_shown else 'ссылка отсутствует')

    # --- Шаг 7. Ручная синхронизация документов из интерфейса -------------
    bases_html = web.get('/bases').get_data(as_text=True)
    steps.check('Синхронизировать документы' in bases_html
                and '/bases/sync-documents' in bases_html,
                'На странице «1С База» есть кнопка загрузки документов',
                'форма POST /bases/sync-documents с кнопкой синхронизации',
                'кнопка присутствует' if '/bases/sync-documents' in bases_html
                else 'кнопка отсутствует')

    with app.app_context():
        Document.query.delete()
        db.session.commit()
    with fake_1c(documents=DOCUMENTS):
        response = web.post('/bases/sync-documents', follow_redirects=True)
    flash_html = response.get_data(as_text=True)
    with app.app_context():
        total_after_ui = Document.query.count()
    steps.check(response.status_code == 200
                and 'обработано 3' in flash_html
                and total_after_ui == 3,
                'Кнопка на странице «1С База» загружает документы',
                'после POST загружено 3 документа и показано сообщение',
                f'HTTP {response.status_code}, документов: {total_after_ui}')

    # --- Шаг 8. Загрузка документов подключена к планировщику -------------
    from engine.tasks import init_scheduler
    scheduler = init_scheduler(app)
    job_ids = {job.id for job in scheduler.get_jobs()}
    scheduler.shutdown(wait=False)
    steps.check({'sync_clients', 'sync_documents',
                 'generate_deadline_tasks'} <= job_ids,
                'Загрузка документов подключена к планировщику',
                'в планировщике зарегистрировано задание sync_documents',
                f'задания: {sorted(job_ids)}')

    conclusion = ('Документы загружаются из «1С:Документооборот», вид документа '
                  'вычисляется из имени сущности, контрагент может отсутствовать, '
                  'ссылка на объект заполняется и доступна из интерфейса.'
                  if steps.passed else 'Загрузка документов работает некорректно.')
    return steps, conclusion
