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


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    executor.start_polling(dp, on_startup=on_startup)


