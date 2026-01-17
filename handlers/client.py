from aiogram import types, Dispatcher
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Text
from db_api import db_commands
from create_bot import bot
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# Создаем клавиатуру
def get_main_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("📅 Мое расписание"))
    kb.add(KeyboardButton("💼 Настройки"))
    return kb

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
        await message.reply(f"Привет, Вы успешно зарегистрированы.", reply_markup=get_main_keyboard())
    else:
        await message.reply("Вы уже зарегистрированы.", reply_markup=get_main_keyboard())

async def settings(message: types.Message):
    await message.reply(f"💼 Настройки", reply_markup=get_main_keyboard())

# Словарь для хранения расписания из Excel
schedule_data = {
    "1 курс": {},
    "1 курс СПО": {},
    "2 курс": {},
    "2 курс СПО": {},
    "3 курс": {},
    "3 курс СПО": {},
    "4 курс": {},
}

# Заполним структуру данными из Excel
def init_schedule():
    # 1 курс
    schedule_data["1 курс"] = {
        "ИД 23.1/Б3-25": ["Прикладная информатика_Искусственный интеллект и анализ данных"],
        "ИД 23.1/Б4-25": ["Прикладная информатика_Кибербезопасность цифрового предприятия"],
        "ИД 23.1/Б1-25": ["Прикладная информатика_Корпоративные информационные системы"],
        "ИД 30.1/Б4-25": ["Бизнес-информатика_Игровая компьютерная индустрия"],
        "ИД 30.1/Б5-25": ["Бизнес-информатика_Бизнес-аналитик 1С"],
        "ИД 30.1/Б6-25": ["Бизнес информатика_Цифровой дизайн и веб-разработка"]
    }
    
    # 1 курс СПО
    schedule_data["1 курс СПО"] = {
        "ИДс 23.1/Б3-25": ["Прикладная информатика, Корпоративные информационные системы"],
        "ИДс 23.1/Б4-25": ["Прикладная информатика, Кибербезопасность цифрового предприятия"],
        "ИДс 30.1/Б4-25": ["Бизнес-информатика, Игровая компьютерная индустрия"],
        "ИДс 30.1/Б6-25": ["Бизнес информатика_Цифровой дизайн и веб-разработка"]
    }
    
    # 2 курс
    schedule_data["2 курс"] = {
        "ИД 23.1/Б3-24": ["Прикладная информатика Искусственный интеллект и анализ данных"],
        "ИД 23.1/Б4-24": ["Прикладная информатика Кибербезопасность цифрового предприятия"],
        "ИД 23.1/Б1-24": ["Прикладная информатика Корпоративные информационные системы"],
        "ИД 30.1/Б4-24": ["Бизнес-информатика Игровая компьютерная индустрия"],
        "ИД 30.1/Б3-24": ["Бизнес информатика_Цифровая экономика"]
    }
    
    # 2 курс СПО
    schedule_data["2 курс СПО"] = {
        "ИДс 23.1/Б3-24": ["Прикладная информатика, Искусственный интеллект и анализ данных"]
    }
    
    # 3 курс
    schedule_data["3 курс"] = {
        "ИД 30.1/Б3-23": ["Бизнес информатика_Цифровая экономика"],
        "ИД 23.1/Б3-23": ["Прикладная информатика_Искусственный интеллект и анализ данных"]
    }
    
    # 3 курс СПО
    schedule_data["3 курс СПО"] = {
        "ИДс 23.1/Б3-23": ["Искусственный интеллект и анализ данных"]
    }
    
    # 4 курс
    schedule_data["4 курс"] = {
        "ИД 30.1/Б3-22": ["Бизнес информатика_Цифровая экономика"],
        "ИД 23.1/Б3-22": ["Прикладная информатика_Искусственный интеллект и анализ данных"]
    }

# Инициализируем расписание при запуске
init_schedule()

# Функция для получения расписания группы
async def get_group_schedule(course, group_code, week=None):
    return f"""
📅 Расписание для группы {group_code}
🎓 Курс: {course}

📌 Пример занятий:
Понедельник 2026-01-05:
• 8:20-9:50 - Праздничный день

Пятница 2026-01-09:
• 8:20-9:50 - Основы российской государственности (СПЗ)
• 10:00-11:30 - Основы российской государственности (СПЗ)
• 15:25-16:55 - Основы информационных систем и технологий (Лекция)

📝 Для получения полного расписания обратитесь к файлу Excel.
"""

# Хендлер для выбора курса
async def choose_course(message: types.Message):
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    
    courses = list(schedule_data.keys())
    for course in courses:
        keyboard.add(types.InlineKeyboardButton(
            text=f"🎓 {course}",
            callback_data=f"course_{course}"
        ))
    
    await message.answer("Выберите ваш курс:", reply_markup=keyboard)

# Колбек для обработки выбора курса
async def process_course_choice(callback_query: types.CallbackQuery):
    course = callback_query.data.split("_", 1)[1]
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    
    # Получаем группы для выбранного курса
    if course in schedule_data:
        groups = schedule_data[course]
        for group_code, group_info in groups.items():
            group_name = f"{group_code} - {group_info[0]}"
            keyboard.add(types.InlineKeyboardButton(
                text=group_name[:50],  # Обрезаем слишком длинные названия
                callback_data=f"group_{course}_{group_code}"
            ))
    
    keyboard.add(types.InlineKeyboardButton(
        text="⬅️ Назад к выбору курса",
        callback_data="back_to_courses"
    ))
    
    await callback_query.message.edit_text(
        f"Выбран курс: {course}\nВыберите вашу группу:",
        reply_markup=keyboard
    )
    await callback_query.answer()

# Колбек для обработки выбора группы
async def process_group_choice(callback_query: types.CallbackQuery, state: FSMContext):
    data = callback_query.data.split("_", 2)
    course = data[1]
    group_code = data[2]
    
    # Сохраняем выбор пользователя
    await state.update_data(
        selected_course=course,
        selected_group=group_code
    )
    
    # Получаем информацию о группе
    group_info = schedule_data[course][group_code][0] if schedule_data[course][group_code] else ""
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton(
            text="📅 Показать расписание",
            callback_data=f"show_schedule_{course}_{group_code}"
        ),
        types.InlineKeyboardButton(
            text="⬅️ Выбрать другую группу",
            callback_data=f"course_{course}"
        )
    )
    keyboard.add(
        types.InlineKeyboardButton(
            text="🏠 В главное меню",
            callback_data="main_menu"
        )
    )
    
    await callback_query.message.edit_text(
        f"✅ Вы выбрали:\n"
        f"🎓 Курс: {course}\n"
        f"👥 Группа: {group_code}\n"
        f"📚 Направление: {group_info}\n\n"
        f"Теперь вы можете посмотреть расписание.",
        reply_markup=keyboard
    )
    await callback_query.answer()

# Колбек для показа расписания
async def show_schedule(callback_query: types.CallbackQuery, state: FSMContext):
    data = callback_query.data.split("_", 3)
    course = data[2]
    group_code = data[3]
    
    # Получаем расписание
    schedule_text = await get_group_schedule(course, group_code)
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton(
            text="◀️ Предыдущая неделя",
            callback_data=f"prev_week_{course}_{group_code}"
        ),
        types.InlineKeyboardButton(
            text="Следующая неделя ▶️",
            callback_data=f"next_week_{course}_{group_code}"
        )
    )
    keyboard.add(
        types.InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=f"show_schedule_{course}_{group_code}"
        ),
        types.InlineKeyboardButton(
            text="⬅️ Назад к группе",
            callback_data=f"group_{course}_{group_code}"
        )
    )
    
    await callback_query.message.edit_text(
        schedule_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback_query.answer()

# Колбеки для навигации по неделям
async def prev_week(callback_query: types.CallbackQuery):
    await callback_query.answer("Функция перехода к предыдущей неделе в разработке")

async def next_week(callback_query: types.CallbackQuery):
    await callback_query.answer("Функция перехода к следующей неделе в разработке")

# Колбек для возврата к выбору курса
async def back_to_courses(callback_query: types.CallbackQuery):
    await choose_course(callback_query.message)
    await callback_query.answer()

# Колбек для возврата в главное меню
async def main_menu(callback_query: types.CallbackQuery):
    await callback_query.message.answer(
        "🏠 Главное меню",
        reply_markup=get_main_keyboard()
    )
    await callback_query.answer()

# Хендлер для команды "Мое расписание"
async def my_schedule(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    
    if 'selected_course' in user_data and 'selected_group' in user_data:
        course = user_data['selected_course']
        group_code = user_data['selected_group']
        
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            types.InlineKeyboardButton(
                text="📅 Показать расписание",
                callback_data=f"show_schedule_{course}_{group_code}"
            ),
            types.InlineKeyboardButton(
                text="✏️ Изменить группу",
                callback_data="change_group"
            )
        )
        
        group_info = schedule_data[course][group_code][0] if schedule_data[course][group_code] else ""
        
        await message.answer(
            f"📋 Ваше текущее расписание:\n"
            f"🎓 Курс: {course}\n"
            f"👥 Группа: {group_code}\n"
            f"📚 Направление: {group_info}\n\n"
            f"Что вы хотите сделать?",
            reply_markup=keyboard
        )
    else:
        await message.answer(
            "Вы еще не выбрали группу. Давайте выберем её сейчас:"
        )
        await choose_course(message)

# Колбек для изменения группы
async def change_group(callback_query: types.CallbackQuery):
    await choose_course(callback_query.message)
    await callback_query.answer()

# Регистрация хендлеров
def register_handlers_client(dp: Dispatcher):
    dp.register_message_handler(cmd_start, commands=['start'])
    dp.register_message_handler(settings, text='💼 Настройки')
    dp.register_message_handler(my_schedule, text='📅 Мое расписание')
    
    # Регистрация колбеков
    dp.register_callback_query_handler(process_course_choice, Text(startswith="course_"))
    dp.register_callback_query_handler(process_group_choice, Text(startswith="group_"))
    dp.register_callback_query_handler(show_schedule, Text(startswith="show_schedule_"))
    dp.register_callback_query_handler(prev_week, Text(startswith="prev_week_"))
    dp.register_callback_query_handler(next_week, Text(startswith="next_week_"))
    dp.register_callback_query_handler(back_to_courses, Text(startswith="back_to_courses"))
    dp.register_callback_query_handler(main_menu, Text(startswith="main_menu"))
    dp.register_callback_query_handler(change_group, Text(startswith="change_group"))