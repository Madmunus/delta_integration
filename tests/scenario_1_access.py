"""Сценарий 1 (ФТ-1): ограничение видимости клиентов для сотрудника.

Вход под учётной записью сотрудника; проверка ограничения видимости
клиентов. Результат: сотрудник видит только закреплённых за ним клиентов.
"""
from datetime import date

from engine.models import Client, Contract, Contractor, Document, db
from tests.base import (EMAILS, Steps, create_scenario_app, login,
                        seed_users)

FT = 'ФТ-1'
TITLE = 'Ограничение видимости клиентов для учётной записи сотрудника'
DESCRIPTION = ('Вход под учётной записью сотрудника; проверка ограничения '
               'видимости клиентов. Результат: сотрудник видит только '
               'закреплённых за ним клиентов.')

MY_ACTIVE = 'ООО «Клевер-Т»'        # закреплён за сотрудником 1, активный
MY_ARCHIVED = 'ООО «Мята-Т»'        # закреплён за сотрудником 1, архивный
OTHER_EMP = 'ООО «Мак-Т»'           # закреплён за сотрудником 2
UNASSIGNED = 'ООО «Тмин-Т»'         # без ответственного


def run():
    app = create_scenario_app()
    steps = Steps()
    users = seed_users(app)

    with app.app_context():
        rows = [
            (MY_ACTIVE, '7706666601', 'active', users['emp1']),
            (MY_ARCHIVED, '7706666602', 'archived', users['emp1']),
            (OTHER_EMP, '7706666603', 'active', users['emp2']),
            (UNASSIGNED, '7706666604', 'active', None),
        ]
        ids = {}
        for name, inn, status, resp in rows:
            db.session.add(Client(name=name, inn=inn, status=status,
                                  responsible_id=resp))
        db.session.flush()
        for name, _, _, _ in rows:
            ids[name] = Client.query.filter_by(name=name).first().id

        # Общий контрагент: договоры и документы по «своему» и «чужому» клиенту
        contractor = Contractor(
            full_name='ООО «Общий контрагент-Т»', inn='7706666699',
            external_ref='cccccccc-7777-7777-7777-777777777777')
        db.session.add(contractor)
        db.session.flush()
        db.session.add_all([
            Contract(number='ДОГ-СВОЙ', signed_date=date.today(),
                     client_id=ids[MY_ACTIVE], contractor_id=contractor.id),
            Contract(number='ДОГ-ЧУЖОЙ', signed_date=date.today(),
                     client_id=ids[OTHER_EMP], contractor_id=contractor.id),
            Document(doc_type='СчетНаОплату', number='ДОК-СВОЙ',
                     client_id=ids[MY_ACTIVE], contractor_id=contractor.id),
            Document(doc_type='СчетНаОплату', number='ДОК-ЧУЖОЙ',
                     client_id=ids[OTHER_EMP], contractor_id=contractor.id),
        ])
        db.session.commit()
        contractor_id = contractor.id

    # --- Шаг 1. Вход под учётной записью сотрудника ------------------------
    web = app.test_client()
    resp = login(web, EMAILS['emp1'])
    steps.check(resp.status_code == 200,
                'Вход под учётной записью сотрудника',
                'сотрудник успешно вошёл в систему',
                f'HTTP {resp.status_code}')

    # --- Шаг 2. В реестре видны только свои клиенты --------------------------
    html = web.get('/clients').get_data(as_text=True)
    seen = {
        MY_ACTIVE: MY_ACTIVE in html,
        MY_ARCHIVED: MY_ARCHIVED in html,
        OTHER_EMP: OTHER_EMP in html,
        UNASSIGNED: UNASSIGNED in html,
    }
    ok = (seen[MY_ACTIVE] and seen[MY_ARCHIVED]
          and not seen[OTHER_EMP] and not seen[UNASSIGNED])
    steps.check(ok,
                'Сотрудник видит только закреплённых за ним клиентов',
                f'в реестре — «{MY_ACTIVE}» и «{MY_ARCHIVED}»; клиенты других '
                'сотрудников и клиенты без ответственного скрыты',
                '; '.join(f'{name} — {"виден" if v else "скрыт"}'
                          for name, v in seen.items()))

    # --- Шаг 3. Карточка своего клиента доступна -----------------------------
    resp = web.get(f"/clients/{ids[MY_ACTIVE]}")
    steps.check(resp.status_code == 200,
                'Карточка закреплённого клиента доступна',
                'карточка своего клиента открывается',
                f'HTTP {resp.status_code}')

    # --- Шаг 4. Карточка чужого клиента запрещена -----------------------------
    resp = web.get(f"/clients/{ids[OTHER_EMP]}")
    steps.check(resp.status_code == 403,
                'Карточка чужого клиента запрещена',
                'доступ к карточке клиента другого сотрудника блокируется (403)',
                f'HTTP {resp.status_code}')

    # --- Шаг 5. Поиск не выводит чужих клиентов ---------------------------------
    html = web.get('/clients', query_string={'q': 'Мак-Т'}).get_data(as_text=True)
    steps.check(OTHER_EMP not in html,
                'Поиск не находит чужих клиентов',
                'поиск по наименованию чужого клиента не даёт результатов',
                'чужой клиент не найден' if OTHER_EMP not in html
                else 'чужой клиент найден поиском')

    # --- Шаг 6. В карточке контрагента — только данные своих клиентов ---------
    html = web.get(f'/contractors/{contractor_id}').get_data(as_text=True)
    own_visible = 'ДОГ-СВОЙ' in html and 'ДОК-СВОЙ' in html
    other_hidden = 'ДОГ-ЧУЖОЙ' not in html and 'ДОК-ЧУЖОЙ' not in html
    steps.check(own_visible and other_hidden,
                'В карточке контрагента видны только данные своих клиентов',
                'договор и документ своего клиента отображаются, данные '
                'чужого клиента скрыты',
                'свои данные видны, чужие скрыты'
                if own_visible and other_hidden else 'видимость данных нарушена')

    # --- Шаг 7. Руководитель видит всех клиентов --------------------------------
    boss = app.test_client()
    login(boss, EMAILS['admin'])
    html = boss.get('/clients').get_data(as_text=True)
    ok = all(name in html for name in (MY_ACTIVE, MY_ARCHIVED,
                                       OTHER_EMP, UNASSIGNED))
    steps.check(ok,
                'Руководитель по-прежнему видит всех клиентов',
                'в реестре руководителя — все 4 клиента',
                'все клиенты видны' if ok else 'часть клиентов не видна')
    contractor_html = boss.get(f'/contractors/{contractor_id}').get_data(as_text=True)
    steps.check(all(value in contractor_html
                    for value in ('ДОГ-СВОЙ', 'ДОГ-ЧУЖОЙ',
                                  'ДОК-СВОЙ', 'ДОК-ЧУЖОЙ')),
                'Руководитель видит в карточке контрагента все договоры и документы',
                'доступны данные по всем клиентам контрагента',
                'все связанные данные видны'
                if 'ДОГ-ЧУЖОЙ' in contractor_html else 'часть данных скрыта')
    steps.check('Ссылка' in contractor_html,
                'В документах карточки контрагента есть столбец «Ссылка»',
                'таблица документов содержит столбец «Ссылка», как в карточке клиента',
                'столбец отображается' if 'Ссылка' in contractor_html
                else 'столбец отсутствует')

    conclusion = ('Сотрудник видит только закреплённых за ним клиентов.'
                  if steps.passed else 'Ограничение видимости нарушено — см. шаги.')
    return steps, conclusion
