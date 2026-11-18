"""Сценарий 1 (ФТ-1): ограничение видимости клиентов для сотрудника.

Вход под учётной записью сотрудника; проверка ограничения видимости
клиентов. Результат: сотрудник видит только закреплённых за ним клиентов.
"""
import re
from datetime import date

from engine.models import (Client, Contract, Contractor, Document, User,
                           UserAction, UserSession, db)
from engine.user_sessions import SID_KEY
from tests.base import (EMAILS, TEST_PASSWORD, Steps, create_scenario_app,
                        login, seed_users)

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

    # --- Шаг 1.1. Неверный пароль: форма входа, а не ошибка сервера ---------
    wrong = app.test_client()
    resp = wrong.post('/login', data={'email': EMAILS['emp1'],
                                      'password': 'wrong-password'})
    wrong_html = resp.get_data(as_text=True)
    wrong_ok = (resp.status_code == 401
                and 'Неверный email или пароль' in wrong_html
                and 'name="password"' in wrong_html)
    steps.check(wrong_ok,
                'При неверном пароле показывается форма входа',
                'система возвращает 401 и сообщение «Неверный email или '
                'пароль» вместо страницы ошибки 500',
                f'HTTP {resp.status_code}'
                if wrong_ok else f'HTTP {resp.status_code}, форма не показана')

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
    contractor_ok = ('Ссылка' not in contractor_html
                     and 'Редактировать документ' in contractor_html
                     and '/documents/' in contractor_html)
    steps.check(contractor_ok,
                'Документы карточки контрагента оформлены как в реестре',
                'нет столбца «Ссылка», вид документа ведёт в карточку '
                'документа, есть кнопка редактирования',
                'оформление совпадает с реестром' if contractor_ok
                else 'оформление отличается от реестра')
    client_html = boss.get(f'/clients/{ids[MY_ACTIVE]}').get_data(as_text=True)
    client_ok = ('Ссылка' not in client_html
                 and 'Редактировать документ' in client_html
                 and 'Редактировать договор' in client_html
                 and '/documents/' in client_html)
    steps.check(client_ok,
                'Вкладки карточки клиента оформлены как в реестрах',
                'документы и договоры содержат кнопки редактирования, '
                'вид документа ведёт в карточку, столбца «Ссылка» нет',
                'оформление совпадает с реестрами' if client_ok
                else 'оформление отличается от реестров')

    # --- Шаг 8. Порядок пользователей в списке -------------------------------
    with app.app_context():
        extra = [
            # Отключённый руководитель — всё равно в первой группе
            User(email='boss2@test.local', fio='Яковлев Пётр Сергеевич',
                 role='admin', is_active_flag=False),
            # Отключённый сотрудник — в последней группе, несмотря на фамилию
            User(email='inactive@test.local', fio='Абрамова Инна Петровна',
                 role='employee', is_active_flag=False),
        ]
        for user in extra:
            user.set_password(TEST_PASSWORD)
            db.session.add(user)
        db.session.commit()

    html = boss.get('/users').get_data(as_text=True)
    body = html.split('<tbody>', 1)[-1]
    order = re.findall(r'<a href="/users/\d+/edit">([^<]+)</a>', body)
    expected = ['Админов Аркадий Петрович', 'Яковлев Пётр Сергеевич',
                'Работникова Ольга Львовна', 'Сотрудников Сергей Иванович',
                'Абрамова Инна Петровна']
    steps.check(order == expected,
                'Порядок пользователей: руководители, активные, отключённые',
                'сначала руководители, затем активные пользователи, затем '
                'отключённые; внутри каждой группы — по ФИО',
                f'порядок: {order}')

    # --- Шаг 9. В личном кабинете видны только свои сессии -------------------
    own_card = web.get(f"/users/{users['emp1']}/edit")
    own_html = own_card.get_data(as_text=True)
    own_ok = (own_card.status_code == 200
              and '<h2 class="h5 mb-0">Сессии</h2>' in own_html
              and 'ID сессии' in own_html
              and 'Завершить остальные сессии' in own_html
              and 'текущая' in own_html
              and '127.0.0.1' in own_html)
    steps.check(own_ok,
                'В личном кабинете отображаются сессии',
                'блок «Сессии» показывает ID, IP, устройство и бейдж «текущая»',
                'блок сессий показан' if own_ok else 'блок сессий отсутствует')

    foreign_card = boss.get(f"/users/{users['emp1']}/edit")
    foreign_html = foreign_card.get_data(as_text=True)
    foreign_ok = (foreign_card.status_code == 200
                  and '<h2 class="h5 mb-0">Сессии</h2>' in foreign_html
                  and 'Последняя активность' in foreign_html
                  and 'ID сессии' in foreign_html
                  and 'Завершить остальные сессии' not in foreign_html)
    steps.check(foreign_ok,
                'В чужой карточке видны сессии, но нет кнопки завершения',
                'руководитель видит блок «Сессии» пользователя, '
                'но не может завершать его сессии',
                'блок и кнопка в ожидаемом состоянии' if foreign_ok
                else 'блок сессий или кнопка отображаются неверно')

    # --- Шаг 10. Второй вход сразу виден как отдельная сессия ---------------
    iphone_ua = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
                 'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 '
                 'Mobile/15E148 Safari/604.1')
    other = app.test_client()
    other.environ_base['REMOTE_ADDR'] = '192.0.2.44'
    second_login = other.post(
        '/login', data={'email': EMAILS['emp1'], 'password': TEST_PASSWORD},
        headers={'User-Agent': iphone_ua}, follow_redirects=True)
    second_html = other.get(f"/users/{users['emp1']}/edit").get_data(as_text=True)
    with app.app_context():
        sessions_count = UserSession.query.filter_by(
            user_id=users['emp1'], is_active_flag=True).count()
    two_sessions_ok = (second_login.status_code == 200
                       and sessions_count == 2
                       and '192.0.2.44' in second_html
                       and 'Safari — iOS 17.0' in second_html
                       and second_html.count('текущая') == 1)
    steps.check(two_sessions_ok,
                'Второй вход отображается отдельной сессией',
                'в карточке две сессии: текущая и новая с IP 192.0.2.44 '
                'и устройством Safari — iOS 17.0',
                f'записей: {sessions_count}, IP/устройство найдены: '
                f"{'192.0.2.44' in second_html}/"
                f"{'Safari — iOS 17.0' in second_html}")

    # --- Шаг 11. Завершение остальных сессий --------------------------------
    termination = web.post(
        f"/users/{users['emp1']}/sessions/logout-others")
    with app.app_context():
        remaining = UserSession.query.filter_by(
            user_id=users['emp1'], is_active_flag=True).count()
        total_sessions = UserSession.query.filter_by(
            user_id=users['emp1']).count()
    current_alive = web.get('/dashboard').status_code == 200
    other_blocked = other.get('/dashboard').status_code == 302
    termination_ok = (termination.status_code == 302 and remaining == 1
                      and total_sessions == 2 and current_alive
                      and other_blocked)
    steps.check(termination_ok,
                'Остальные сессии завершены, текущая сохранена',
                'после POST активна одна сессия; вторая осталась в базе '
                'с is_active_flag=False и уходит на вход',
                f'HTTP {termination.status_code}, активных: {remaining}, '
                f'всего записей: {total_sessions}, '
                f'текущая жива: {current_alive}, вторая закрыта: {other_blocked}')

    # --- Шаг 12. Нельзя завершить чужие сессии ------------------------------
    forbidden = boss.post(
        f"/users/{users['emp1']}/sessions/logout-others")
    with app.app_context():
        after_foreign = UserSession.query.filter_by(
            user_id=users['emp1'], is_active_flag=True).count()
        all_after = UserSession.query.filter_by(
            user_id=users['emp1']).count()
    forbidden_ok = (forbidden.status_code == 403 and after_foreign == 1
                    and all_after == 2
                    and boss.get('/dashboard').status_code == 200)
    steps.check(forbidden_ok,
                'Завершение чужих сессий запрещено',
                'POST для чужой карточки возвращает 403 и не трогает сессии',
                f'HTTP {forbidden.status_code}, активных: {after_foreign}, '
                f'всего: {all_after}')

    # --- Шаг 13. В карточке видны последние действия пользователя ----------
    actions_card = web.get(f"/users/{users['emp1']}/edit")
    actions_html = actions_card.get_data(as_text=True)
    with web.session_transaction() as sess:
        web_sid = sess[SID_KEY]
    with app.app_context():
        emp1_actions = UserAction.query.filter_by(
            user_id=users['emp1']).order_by(UserAction.id).all()
    emp1_codes = [a.action for a in emp1_actions]
    logged_sids = {a.session_id for a in emp1_actions}
    needed = ('login', 'login_failed', 'sessions_terminated')
    actions_ok = (actions_card.status_code == 200
                  and 'Последние действия' in actions_html
                  and all(a in emp1_codes for a in needed)
                  and 'Вход в систему' in actions_html
                  and 'Неудачная попытка входа' in actions_html
                  and 'Завершение других сессий' in actions_html
                  and 'ID сессии' in actions_html
                  and web_sid in actions_html
                  and web_sid in logged_sids)
    steps.check(actions_ok,
                'В карточке отображаются последние действия пользователя',
                'блок «Последние действия» показывает вход, неудачную '
                'попытку, завершение сессий, дату и ID сессии',
                f'коды в журнале: {emp1_codes}; '
                f'ID текущей сессии сохранён: {web_sid in logged_sids}'
                if not actions_ok else 'блок и записи найдены')

    # --- Шаг 14. Изменение профиля тоже попадает в журнал -------------------
    saved = web.post(
        f"/users/{users['emp1']}/edit",
        data={'fio': 'Сотрудников Сергей Иванович',
              'email': EMAILS['emp1'],
              'password': '', 'password_confirm': ''})
    saved_html = web.get(f"/users/{users['emp1']}/edit").get_data(as_text=True)
    with app.app_context():
        update_rows = UserAction.query.filter_by(
            user_id=users['emp1'], action='user_update').all()
    updates = len(update_rows)
    update_ok = (saved.status_code == 302 and updates == 1
                 and update_rows[0].session_id == web_sid
                 and 'Изменение пользователя' in saved_html)
    steps.check(update_ok,
                'Редактирование профиля записывается в журнал',
                'успешное сохранение карточки добавляет действие '
                '«Изменение пользователя»',
                f'HTTP {saved.status_code}, записей: {updates}')

    # --- Шаг 15. Журнал разделён по пользователям ---------------------------
    boss_card = boss.get(f"/users/{users['admin']}/edit").get_data(as_text=True)
    with app.app_context():
        admin_codes = [a.action for a in UserAction.query.filter_by(
            user_id=users['admin']).all()]
        emp2_count = UserAction.query.filter_by(
            user_id=users['emp2']).count()
    isolation_ok = (emp2_count == 0
                    and 'login' in admin_codes
                    and 'sessions_terminated' not in admin_codes
                    and 'Вход в систему' in boss_card
                    and 'Завершение других сессий' not in boss_card)
    steps.check(isolation_ok,
                'Действия привязаны к исполнителю',
                'в карточке руководителя — только его действия; '
                'сотрудник без входов журнал не имеет',
                f'руководитель: {admin_codes}, '
                f'второй сотрудник: {emp2_count} записей')

    conclusion = ('Сотрудник видит только закреплённых за ним клиентов; '
                  'управление активными сессиями и журнал действий работают.'
                  if steps.passed else
                  'Ограничение видимости, управление сессиями или журнал '
                  'действий нарушено — см. шаги.')
    return steps, conclusion
