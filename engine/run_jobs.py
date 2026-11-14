"""Однократный запуск фоновых заданий (для cron или внешнего планировщика).

Запуск из корня проекта:
    python -m engine.run_jobs

Выполняет синхронизацию клиентов и контрагентов, загрузку документов
из «1С:Документооборот» и формирование задач по срокам отчётности.
Планировщик APScheduler при этом не запускается — задания выполняются
один раз и завершаются.
"""
import logging

from app import app
from engine.sync import sync_clients, sync_documents
from engine.tasks import generate_deadline_tasks


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    with app.app_context():
        clients_stats = sync_clients()
        print('Клиенты и контрагенты:', clients_stats)

        documents = sync_documents()
        print(f'Документы: обработано {documents}')

        tasks = generate_deadline_tasks(app.config['DEADLINE_LEAD_DAYS'])
        print(f'Задачи по срокам: создано {len(tasks)}')


if __name__ == '__main__':
    main()
