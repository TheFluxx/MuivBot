from handlers import client
from db_api.database import create_base
from aiogram.utils import executor
from create_bot import dp
import logging


def setup_handlers(dp):
    client.register_handlers_client(dp)


async def on_startup(dp):
    await create_base()
    setup_handlers(dp)
    
    # Загружаем расписание при запуске бота
    from handlers.client import init_schedule
    print("Загрузка расписания из Excel файла...")
    init_schedule()
    
    # Проверяем результат
    from handlers.client import schedule_data
    total_courses = len(schedule_data)
    total_groups = sum(len(groups) for groups in schedule_data.values())
    
    print(f"Загружено расписание: {total_courses} курсов, {total_groups} групп")
    
    if total_groups == 0:
        print("⚠️ Внимание: расписание не загружено или файл не найден!")
        print("Проверьте наличие файла: Raspisanie-FIT-ochnaya-f.o.-25_26-osenniy-sem.-YAnvar.xls")
    else:
        print("✅ Расписание успешно загружено!")
    
    print("Бот запущен!")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    executor.start_polling(dp, on_startup=on_startup)