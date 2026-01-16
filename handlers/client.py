from aiogram import types, Dispatcher
from keyboards.client_kb import kb_client
from aiogram.dispatcher import FSMContext
from db_api import db_commands
from aiogram.dispatcher.filters import Text
from create_bot import bot


async def cmd_start(message: types.Message):
    telegram_id = message.from_user.id
    username = message.from_user.username
    if username is None:
        username = message.from_user.first_name
    user_exists = await db_commands.registration_check(telegram_id)
    if not user_exists:
        try:
            referrer_id = int(message.get_args())
            await db_commands.register_user(telegram_id, username, referrer_id)
        except ValueError:
            await db_commands.register_user(telegram_id, username, 0)
        await message.reply(f"Привет, Вы успешно зарегистрированы.", reply_markup=kb_client)

    else:
        await message.reply("Вы уже зарегистрированы.", reply_markup=kb_client)


async def settings(message: types.Message):
    await message.reply(f"💼 Настройки", reply_markup=kb_client)


def register_handlers_client(dp: Dispatcher):
    dp.register_message_handler(cmd_start, commands=['start'])
    dp.register_message_handler(settings, '💼 Настройки')