import logging

from aiogram.utils import executor

from create_bot import dp
from db_api.database import create_base
from handlers import client


def setup_handlers(dispatcher):
    """Регистрирует обработчики бота."""
    client.register_handlers_client(dispatcher)


async def on_startup(dispatcher):
    """Инициализация БД и расписания при запуске."""
    await create_base()
    setup_handlers(dispatcher)

    from handlers.client import init_schedule, persist_schedule_to_db, schedule_data

    print('Загрузка расписания из Excel...')
    init_schedule()

    total_courses = len(schedule_data)
    total_groups = sum(len(groups) for groups in schedule_data.values())
    print(f'Загружено расписание: {total_courses} курсов, {total_groups} групп')

    try:
        db_stats = await persist_schedule_to_db()
        print(
            'Сохранено в БД: '
            f"периодов={db_stats['periods']}, курсов={db_stats['courses']}, "
            f"групп={db_stats['groups']}, недель={db_stats['weeks']}, "
            f"дней={db_stats['days']}, занятий={db_stats['lessons']}"
        )
    except Exception as db_error:
        print(f'⚠️ Ошибка сохранения расписания в БД: {db_error}')

    if total_groups == 0:
        print('⚠️ Внимание: расписание не загружено или файлы не найдены!')
    else:
        print('✅ Расписание успешно загружено!')

    print('Бот запущен!')


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    executor.start_polling(dp, on_startup=on_startup)
