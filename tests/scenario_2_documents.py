"""Сценарий 2 (ФТ-2): запрос документов клиента из «1С:Документооборот».

Запрос документов клиента из «1С:Документооборот» через OData;
отображение в карточке клиента. Результат: документы отображаются
с корректными реквизитами.
"""
from datetime import date

from engine.models import Client, DocumentRef, ODataBase, db
from services.sync import sync_documents
from tests.base import (EMAILS, Steps, create_scenario_app, fake_1c,
                        login, seed_users)

FT = 'ФТ-2'
TITLE = 'Запрос документов клиента из «1С:Документооборот» через OData'
DESCRIPTION = ('Запрос документов клиента из «1С:Документооборот» через OData; '
               'отображение в карточке клиента. Результат: документы '
               'отображаются с корректными реквизитами.')

ALPHA_KEY = '11111111-1111-1111-1111-111111111111'

# Ответ «1С:Документооборот»: входящие письма.
# Третий документ относится к неизвестному контрагенту и должен
# быть пропущен (нет привязки к клиенту).
DOCUMENTS = [
    {'Ref_Key': 'aaaaaaaa-0001-0000-0000-000000000001', 'Number': 'Вх-101',
     'Date': '2026-11-03T00:00:00', 'Контрагент_Key': ALPHA_KEY,
     'СуммаДокумента': 12500.00, 'Статус': 'Зарегистрирован'},
    {'Ref_Key': 'aaaaaaaa-0002-0000-0000-000000000002', 'Number': 'Вх-102',
     'Date': '2026-11-08T00:00:00', 'Контрагент_Key': ALPHA_KEY,
     'СуммаДокумента': None, 'Статус': 'На рассмотрении'},
    {'Ref_Key': 'aaaaaaaa-0003-0000-0000-000000000003', 'Number': 'Вх-999',
     'Date': '2026-11-09T00:00:00', 'Контрагент_Key': 'unknown-contractor',
     'СуммаДокумента': 10, 'Статус': 'Черновик'},
]


def run():
    app = create_scenario_app()
    steps = Steps()
    seed_users(app)

    with app.app_context():
        db.session.add(ODataBase(
            name='Тестовая база 1С:Документооборот',
            odata_url='http://test-do.local/odata/standard.odata/',
            username='odata', password='secret', is_enabled=True))
        db.session.add(Client(
            name='ООО «Альфа-Тест»', inn='7701111111', kpp='770101001',
            status='active', external_ref=ALPHA_KEY))
        db.session.commit()

    # --- Шаг 1. Загрузка документов через OData ------------------------
    with app.app_context():
        base = ODataBase.query.first()
        client_id = Client.query.filter_by(inn='7701111111').first().id
        with fake_1c(documents=DOCUMENTS):
            processed = sync_documents(base)
    steps.check(processed == 2,
                'Запрос документов через OData',
                'обработано 2 документа клиента; документ неизвестного '
                'контрагента пропущен',
                f'обработано {processed} документов')

    # --- Шаг 2. Реквизиты документов в базе ----------------------------
    with app.app_context():
        docs = (DocumentRef.query.order_by(DocumentRef.number).all())
        d1, d2 = docs[0], docs[1]
    steps.check(all(d.client_id == client_id for d in docs),
                'Документы привязаны к клиенту',
                'оба документа связаны с карточкой клиента',
                f'client_id: {[d.client_id for d in docs]}')
    steps.check(
        d1.number == 'Вх-101' and d1.doc_date == date(2026, 11, 3)
        and float(d1.amount or 0) == 12500.0
        and d1.status == 'Зарегистрирован' and d1.doc_type == 'ВходящееПисьмо',
        'Реквизиты первого документа корректны',
        'тип, номер, дата, сумма и статус совпадают с данными 1С:ДО',
        f'{d1.doc_type} №{d1.number} от {d1.doc_date}, '
        f'сумма {d1.amount}, статус «{d1.status}»')
    steps.check(
        d2.number == 'Вх-102' and d2.doc_date == date(2026, 11, 8)
        and d2.amount is None and d2.status == 'На рассмотрении',
        'Реквизиты второго документа корректны',
        'тип, номер, дата и статус совпадают; сумма отсутствует',
        f'{d2.doc_type} №{d2.number} от {d2.doc_date}, '
        f'сумма {d2.amount}, статус «{d2.status}»')

    # --- Шаг 3. Документы отображаются в карточке клиента ---------------
    web = app.test_client()
    login(web, EMAILS['admin'])
    html = web.get(f'/clients/{client_id}').get_data(as_text=True)
    steps.check('Документы (2)' in html and 'Вх-101' in html and 'Вх-102' in html,
                'Документы отображаются в карточке клиента',
                'на вкладке «Документы» — оба документа с номерами',
                'документы отображаются' if 'Вх-101' in html and 'Вх-102' in html
                else 'документы не отображаются')
    steps.check('Вх-999' not in html,
                'Чужой документ в карточку не попадает',
                'документ неизвестного контрагента не отображается',
                'чужой документ скрыт' if 'Вх-999' not in html
                else 'чужой документ отображается')

    # --- Шаг 4. Повторная загрузка не создаёт дублей -------------------
    with app.app_context():
        base = ODataBase.query.first()
        with fake_1c(documents=DOCUMENTS):
            sync_documents(base)
        total = DocumentRef.query.count()
    steps.check(total == 2,
                'Повторная загрузка не дублирует документы',
                'в карточке по-прежнему 2 документа',
                f'всего документов: {total}')

    conclusion = ('Документы загружаются из «1С:Документооборот» и отображаются '
                  'в карточке клиента с корректными реквизитами.'
                  if steps.passed else 'Загрузка документов работает некорректно.')
    return steps, conclusion
