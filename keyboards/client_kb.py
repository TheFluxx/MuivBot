from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove


b1 = KeyboardButton('💼 Настройки')
kb_client = ReplyKeyboardMarkup(resize_keyboard=True)
kb_client.add(b1)


def kb_client():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("📅 Мое расписание"))
    kb.add(KeyboardButton("💼 Настройки"))
    return kb

