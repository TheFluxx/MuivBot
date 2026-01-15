from aiogram import Dispatcher, Bot, types
import os
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from data.config import BOT_TOKEN

storage = MemoryStorage()

bot = Bot(token=os.getenv('TOKEN'))
dp = Dispatcher(bot, storage=storage)
