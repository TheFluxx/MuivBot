import asyncio
import os
from datetime import datetime
from aiogram import types, Dispatcher
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Text
import xlrd
from db_api import db_commands
from create_bot import bot

# Создаем клавиатуру
def get_main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("📅 Мое расписание"))
    kb.add(types.KeyboardButton("💼 Настройки"))
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

# Словари для хранения расписания
schedule_data = {}
group_info_data = {}

def parse_excel_file():
    """Парсит Excel файл .xls формата с расписанием"""
    global schedule_data, group_info_data
    
    try:
        # Путь к файлу
        excel_file = "Raspisanie-FIT-ochnaya-f.o.-25_26-osenniy-sem.-YAnvar.xls"
        
        # Проверяем наличие файла
        if not os.path.exists(excel_file):
            print(f"❌ Файл {excel_file} не найден!")
            print(f"Текущая директория: {os.getcwd()}")
            
            # Ищем файл в разных местах
            possible_paths = [
                excel_file,
                os.path.join("data", excel_file),
                os.path.join("..", excel_file),
                os.path.join(".", excel_file),
            ]
            
            for path in possible_paths:
                if os.path.exists(path):
                    excel_file = path
                    print(f"✅ Файл найден по пути: {path}")
                    break
            else:
                print("❌ Файл не найден ни по одному из путей!")
                return
        
        print(f"📖 Чтение файла .xls: {excel_file}")
        
        # Открываем книгу .xls
        wb = xlrd.open_workbook(excel_file)
        
        # Список всех листов (курсов)
        sheets = wb.sheet_names()
        print(f"📑 Найдено листов: {len(sheets)}")
        
        for sheet_idx, sheet_name in enumerate(sheets, 1):
            print(f"📋 Обработка листа {sheet_idx}/{len(sheets)}: {sheet_name}")
            
            try:
                ws = wb.sheet_by_name(sheet_name)
                
                # Инициализируем структуру для этого курса
                schedule_data[sheet_name] = {}
                group_info_data[sheet_name] = {}
                
                # Ищем строки с кодами групп (они содержат "ИД")
                group_codes = {}
                
                # Проходим по всем строкам
                for row_idx in range(ws.nrows):
                    row = ws.row_values(row_idx)
                    
                    if not any(row):
                        continue
                    
                    # Ищем строку с кодами групп
                    for col_idx, cell_value in enumerate(row):
                        if cell_value and isinstance(cell_value, str) and "ИД" in cell_value:
                            group_code = cell_value.strip()
                            if group_code not in group_codes:
                                group_codes[group_code] = {
                                    'col_idx': col_idx,
                                    'group_info': group_code
                                }
                                # Инициализируем структуру для группы
                                schedule_data[sheet_name][group_code] = {}
                                
                                # Ищем направление обучения (обычно следующая строка после кодов групп)
                                if row_idx + 1 < ws.nrows:
                                    next_row = ws.row_values(row_idx + 1)
                                    if col_idx < len(next_row) and next_row[col_idx]:
                                        direction = str(next_row[col_idx]).strip()
                                        group_info_data[sheet_name][group_code] = [sheet_name, group_code, direction]
                                    else:
                                        group_info_data[sheet_name][group_code] = [sheet_name, group_code, "Не указано"]
                
                print(f"   Найдено групп: {len(group_codes)}")
                
                if not group_codes:
                    print(f"   ⚠️ Группы не найдены на листе {sheet_name}")
                    continue
                
                # Теперь парсим расписание
                current_week = None
                
                for row_idx in range(ws.nrows):
                    row = ws.row_values(row_idx)
                    
                    if not any(row):
                        continue
                    
                    # Проверяем на неделю (например, "19 НЕДЕЛЯ")
                    first_cell = str(row[0]) if row[0] else ""
                    if "НЕДЕЛЯ" in first_cell.upper():
                        current_week = first_cell.strip()
                        print(f"   Найдена неделя: {current_week}")
                        
                        # Инициализируем неделю для всех групп
                        for group_code in group_codes.keys():
                            schedule_data[sheet_name][group_code][current_week] = []
                    
                    # Парсим строки с днями недели
                    elif first_cell.lower() in ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота']:
                        day_of_week = first_cell.strip()
                        date_cell = row[1]
                        time_cell = row[2]
                        
                        # Преобразуем дату из Excel формата
                        if isinstance(date_cell, float):
                            # Дата в Excel хранится как число дней от 1900-01-01
                            try:
                                date_tuple = xlrd.xldate_as_tuple(date_cell, wb.datemode)
                                date_str = f"{date_tuple[0]}-{date_tuple[1]:02d}-{date_tuple[2]:02d}"
                            except:
                                date_str = str(date_cell)
                        elif date_cell:
                            date_str = str(date_cell)
                        else:
                            date_str = "Не указана"
                        
                        if time_cell:
                            # Время может быть в разных форматах
                            if isinstance(time_cell, float):
                                # Время как десятичная дробь
                                try:
                                    time_tuple = xlrd.xldate_as_tuple(time_cell, wb.datemode)
                                    time_str = f"{time_tuple[3]:02d}:{time_tuple[4]:02d}"
                                    # Если указан интервал, добавляем окончание
                                    if time_tuple[4] == 0:
                                        time_str += ":00"
                                except:
                                    time_str = str(time_cell)
                            else:
                                time_str = str(time_cell)
                        else:
                            continue
                        
                        # Для каждой группы
                        for group_code, group_data in group_codes.items():
                            col_idx = group_data['col_idx']
                            
                            if col_idx < len(row) and row[col_idx]:
                                lesson_info = str(row[col_idx]).strip()
                                
                                if lesson_info and lesson_info.lower() not in ['', 'none', 'null']:
                                    schedule_entry = {
                                        'day': day_of_week,
                                        'date': date_str,
                                        'time': time_str,
                                        'lesson': lesson_info,
                                        'week': current_week if current_week else "Неизвестная неделя"
                                    }
                                    
                                    if current_week:
                                        schedule_data[sheet_name][group_code][current_week].append(schedule_entry)
                                    else:
                                        # Если неделя не определена
                                        temp_week = "Неделя_1"
                                        if temp_week not in schedule_data[sheet_name][group_code]:
                                            schedule_data[sheet_name][group_code][temp_week] = []
                                        schedule_data[sheet_name][group_code][temp_week].append(schedule_entry)
                
                # Проверяем, есть ли данные для этого листа
                total_lessons = sum(
                    len(week_schedule) 
                    for group in schedule_data[sheet_name].values() 
                    for week_schedule in group.values()
                )
                print(f"   Загружено занятий: {total_lessons}")
                
            except Exception as e:
                print(f"   ❌ Ошибка при обработке листа {sheet_name}: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        print("✅ Парсинг .xls файла завершен успешно!")
        
    except Exception as e:
        print(f"❌ Ошибка при парсинге .xls файла: {e}")
        import traceback
        traceback.print_exc()

# Инициализируем расписание при запуске
def init_schedule():
    """Инициализация расписания из Excel файла"""
    global schedule_data, group_info_data
    
    # Очищаем предыдущие данные
    schedule_data.clear()
    group_info_data.clear()
    
    # Пробуем парсить файл
    parse_excel_file()
    
    # Если данные не загружены, используем тестовые данные
    if not schedule_data:
        print("⚠️ Использую тестовые данные...")
        create_test_data()

def create_test_data():
    """Создает тестовые данные если файл не найден или парсинг не удался"""
    global schedule_data, group_info_data
    
    schedule_data = {
        "1 курс": {
            "ИД 23.1/Б3-25": {
                "19 НЕДЕЛЯ": [
                    {"day": "понедельник", "date": "2026-01-05", "time": "8:20-9:50", "lesson": "Праздничный день", "week": "19 НЕДЕЛЯ"},
                    {"day": "пятница", "date": "2026-01-09", "time": "8:20-9:50", "lesson": "Основы российской государственности (СПЗ)", "week": "19 НЕДЕЛЯ"},
                    {"day": "пятница", "date": "2026-01-09", "time": "10:00-11:30", "lesson": "Основы российской государственности (СПЗ)", "week": "19 НЕДЕЛЯ"},
                    {"day": "пятница", "date": "2026-01-09", "time": "15:25-16:55", "lesson": "Основы информационных систем и технологий (Лекция)", "week": "19 НЕДЕЛЯ"},
                ],
                "20 НЕДЕЛЯ": [
                    {"day": "понедельник", "date": "2026-01-12", "time": "8:20-9:50", "lesson": "Математика (СПЗ)", "week": "20 НЕДЕЛЯ"},
                    {"day": "вторник", "date": "2026-01-13", "time": "10:00-11:30", "lesson": "Физика (Лекция)", "week": "20 НЕДЕЛЯ"},
                    {"day": "среда", "date": "2026-01-14", "time": "11:40-13:10", "lesson": "Информатика (СПЗ)", "week": "20 НЕДЕЛЯ"},
                ]
            }
        }
    }
    
    group_info_data = {
        "1 курс": {
            "ИД 23.1/Б3-25": ["1 курс", "ИД 23.1/Б3-25", "Прикладная информатика_Искусственный интеллект и анализ данных"],
        }
    }

# Функция для получения расписания на конкретный день
async def get_day_schedule(course, group_code, week, day_index):
    """Получает расписание для конкретного дня недели"""
    
    if course not in schedule_data:
        return f"❌ Курс '{course}' не найден в расписании.", None, None
    
    if group_code not in schedule_data[course]:
        return f"❌ Группа '{group_code}' не найдена в расписании курса '{course}'.", None, None
    
    group_schedule = schedule_data[course][group_code]
    
    if not group_schedule:
        return f"📭 Для группы '{group_code}' нет данных о расписании.", None, None
    
    # Определяем неделю
    available_weeks = list(group_schedule.keys())
    if not available_weeks:
        return f"📭 Для группы '{group_code}' нет расписания на какие-либо недели.", None, None
    
    if week and week in available_weeks:
        target_week = week
    else:
        # Берем первую доступную неделю
        target_week = available_weeks[0]
    
    week_schedule = group_schedule[target_week]
    
    if not week_schedule:
        return f"📭 Для группы '{group_code}' нет занятий на неделе '{target_week}'.", None, None
    
    # Группируем по дням
    days_schedule = {}
    days_order = []  # Сохраняем порядок дней
    
    for entry in week_schedule:
        day_key = entry['day']
        if day_key not in days_schedule:
            days_schedule[day_key] = []
            days_order.append(day_key)
        days_schedule[day_key].append(entry)
    
    # Сортируем дни по порядку недели
    day_order_map = {
        'понедельник': 0,
        'вторник': 1,
        'среда': 2,
        'четверг': 3,
        'пятница': 4,
        'суббота': 5,
        'воскресенье': 6
    }
    
    sorted_days = sorted(days_schedule.items(), key=lambda x: day_order_map.get(x[0], 99))
    
    if not sorted_days:
        return f"📭 Для группы '{group_code}' нет занятий на неделе '{target_week}'.", None, None
    
    # Определяем текущий день
    if day_index is None or day_index >= len(sorted_days):
        current_day_index = 0
    else:
        current_day_index = day_index
    
    if current_day_index < 0:
        current_day_index = 0
    
    if current_day_index >= len(sorted_days):
        current_day_index = len(sorted_days) - 1
    
    # Получаем текущий день
    current_day, day_entries = sorted_days[current_day_index]
    
    # Получаем дату для текущего дня (берем первую запись)
    current_date = day_entries[0]['date'] if day_entries else "Не указана"
    
    # Форматируем расписание для дня
    schedule_text = f"📅 <b>Расписание для группы {group_code}</b>\n"
    schedule_text += f"🎓 <b>Курс:</b> {course}\n"
    schedule_text += f"📆 <b>Неделя:</b> {target_week}\n"
    schedule_text += f"📌 <b>День:</b> {current_day} ({current_date})\n\n"
    
    # Сортируем занятия по времени
    sorted_entries = sorted(day_entries, key=lambda x: x['time'])
    
    if not sorted_entries:
        schedule_text += "📭 Занятий нет\n"
    else:
        for entry in sorted_entries:
            time_display = entry['time']
            lesson_display = entry['lesson']
            
            # Обрезаем слишком длинные названия
            if len(lesson_display) > 80:
                lesson_display = lesson_display[:77] + "..."
            
            schedule_text += f"• ⏰ <b>{time_display}</b> - {lesson_display}\n"
    
    # Информация о навигации
    schedule_text += f"\n📋 <b>День {current_day_index + 1} из {len(sorted_days)}</b>"
    
    return schedule_text, target_week, current_day_index

# Функция для получения информации о группе
def get_group_info(course, group_code):
    """Получает информацию о группе"""
    if course in group_info_data and group_code in group_info_data[course]:
        return group_info_data[course][group_code]
    return [course, group_code, "Информация не найдена"]

# Хендлер для выбора курса
async def choose_course(message: types.Message):
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    
    # Используем реальные курсы из распарсенных данных
    courses = list(schedule_data.keys())
    
    if not courses:
        await message.answer("⚠️ Расписание еще не загружено. Попробуйте позже.")
        return
    
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
        groups = schedule_data[course].keys()
        for group_code in groups:
            # Получаем информацию о группе для отображения
            group_info = get_group_info(course, group_code)
            group_name = f"{group_code}"
            if len(group_info) > 2 and group_info[2]:
                group_name += f" - {group_info[2]}"
            
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
    group_info = get_group_info(course, group_code)
    group_display = f"{group_code}"
    if len(group_info) > 2 and group_info[2]:
        group_display += f"\n📚 Направление: {group_info[2]}"
    
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
        f"✅ <b>Вы выбрали:</b>\n"
        f"🎓 <b>Курс:</b> {course}\n"
        f"👥 <b>Группа:</b> {group_display}\n\n"
        f"Теперь вы можете посмотреть расписание.",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback_query.answer()

# Колбек для показа расписания по дням
async def show_schedule_day(callback_query: types.CallbackQuery, state: FSMContext):
    data = callback_query.data.split("_", 5)
    course = data[2]
    group_code = data[3]
    
    # Получаем индекс дня, если передан
    day_index = None
    week = None
    
    if len(data) > 4 and data[4].isdigit():
        day_index = int(data[4])
    
    if len(data) > 5:
        week = data[5]
    
    # Получаем расписание на день
    schedule_text, target_week, current_day_index = await get_day_schedule(course, group_code, week, day_index)
    
    keyboard = types.InlineKeyboardMarkup(row_width=3)
    
    # Кнопки навигации по дням
    if current_day_index is not None:
        # Кнопка предыдущего дня
        if current_day_index > 0:
            keyboard.add(types.InlineKeyboardButton(
                text="◀️ Предыдущий день",
                callback_data=f"show_day_{course}_{group_code}_{current_day_index - 1}_{target_week}"
            ))
        
        # Кнопка выбора дня
        keyboard.add(types.InlineKeyboardButton(
            text="📅 Выбрать день",
            callback_data=f"choose_day_{course}_{group_code}_{target_week}"
        ))
        
        # Кнопка следующего дня
        keyboard.add(types.InlineKeyboardButton(
            text="Следующий день ▶️",
            callback_data=f"show_day_{course}_{group_code}_{current_day_index + 1}_{target_week}"
        ))
    
    # Кнопки навигации по неделям
    available_weeks = []
    if course in schedule_data and group_code in schedule_data[course]:
        available_weeks = list(schedule_data[course][group_code].keys())
    
    if len(available_weeks) > 1:
        keyboard.row()
        for week_item in available_weeks[:3]:
            if week_item != target_week:
                keyboard.add(types.InlineKeyboardButton(
                    text=f"📆 {week_item}",
                    callback_data=f"show_week_{course}_{group_code}_{week_item}"
                ))
    
    # Кнопки возврата
    keyboard.row()
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

# Колбек для выбора конкретного дня
async def choose_day(callback_query: types.CallbackQuery):
    data = callback_query.data.split("_", 4)
    course = data[2]
    group_code = data[3]
    week = data[4] if len(data) > 4 else None
    
    # Получаем доступные дни для этой недели
    if course not in schedule_data or group_code not in schedule_data[course]:
        await callback_query.answer("Нет данных о группе")
        return
    
    available_weeks = list(schedule_data[course][group_code].keys())
    if not available_weeks:
        await callback_query.answer("Нет данных о расписании")
        return
    
    if not week:
        week = available_weeks[0]
    
    week_schedule = schedule_data[course][group_code][week]
    
    # Группируем по дням
    days_schedule = {}
    for entry in week_schedule:
        day_key = entry['day']
        if day_key not in days_schedule:
            days_schedule[day_key] = entry['date']
    
    # Сортируем дни по порядку недели
    day_order_map = {
        'понедельник': 0,
        'вторник': 1,
        'среда': 2,
        'четверг': 3,
        'пятница': 4,
        'суббота': 5,
        'воскресенье': 6
    }
    
    sorted_days = sorted(days_schedule.items(), key=lambda x: day_order_map.get(x[0], 99))
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    
    for i, (day, date) in enumerate(sorted_days):
        keyboard.add(types.InlineKeyboardButton(
            text=f"{day} ({date})",
            callback_data=f"show_day_{course}_{group_code}_{i}_{week}"
        ))
    
    keyboard.row()
    keyboard.add(types.InlineKeyboardButton(
        text="⬅️ Назад к расписанию",
        callback_data=f"show_schedule_{course}_{group_code}"
    ))
    
    await callback_query.message.edit_text(
        f"📅 Выберите день недели ({week}):",
        reply_markup=keyboard
    )
    await callback_query.answer()

# Колбек для выбора недели
async def show_week(callback_query: types.CallbackQuery):
    data = callback_query.data.split("_", 4)
    course = data[2]
    group_code = data[3]
    week = data[4]
    
    # Показываем первый день выбранной недели
    await show_schedule_day(callback_query, None)

# Колбек для обновления расписания
async def refresh_schedule(callback_query: types.CallbackQuery):
    data = callback_query.data.split("_", 3)
    course = data[1]
    group_code = data[2]
    
    # Показываем сообщение о загрузке
    await callback_query.message.edit_text(
        "🔄 Обновляю расписание...",
        parse_mode="HTML"
    )
    
    # Перепарсиваем файл
    init_schedule()
    
    # Возвращаемся к показу расписания
    schedule_text, target_week, current_day_index = await get_day_schedule(course, group_code, None, 0)
    
    keyboard = types.InlineKeyboardMarkup(row_width=3)
    
    if current_day_index is not None:
        keyboard.add(
            types.InlineKeyboardButton(
                text="◀️ Предыдущий день",
                callback_data=f"show_day_{course}_{group_code}_{current_day_index - 1}_{target_week}"
            ),
            types.InlineKeyboardButton(
                text="📅 Выбрать день",
                callback_data=f"choose_day_{course}_{group_code}_{target_week}"
            ),
            types.InlineKeyboardButton(
                text="Следующий день ▶️",
                callback_data=f"show_day_{course}_{group_code}_{current_day_index + 1}_{target_week}"
            )
        )
    
    keyboard.row()
    keyboard.add(
        types.InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=f"refresh_{course}_{group_code}"
        ),
        types.InlineKeyboardButton(
            text="⬅️ Назад к группе",
            callback_data=f"group_{course}_{group_code}"
        )
    )
    
    await callback_query.message.edit_text(
        schedule_text + "\n\n✅ Расписание обновлено!",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback_query.answer("Расписание обновлено!")

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
        
        group_info = get_group_info(course, group_code)
        group_display = f"{group_code}"
        if len(group_info) > 2 and group_info[2]:
            group_display += f"\n📚 Направление: {group_info[2]}"
        
        await message.answer(
            f"📋 <b>Ваше текущее расписание:</b>\n"
            f"🎓 <b>Курс:</b> {course}\n"
            f"👥 <b>Группа:</b> {group_display}\n\n"
            f"Что вы хотите сделать?",
            reply_markup=keyboard,
            parse_mode="HTML"
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
    dp.register_callback_query_handler(show_schedule_day, Text(startswith="show_schedule_"))
    dp.register_callback_query_handler(show_schedule_day, Text(startswith="show_day_"))
    dp.register_callback_query_handler(choose_day, Text(startswith="choose_day_"))
    dp.register_callback_query_handler(show_week, Text(startswith="show_week_"))
    dp.register_callback_query_handler(refresh_schedule, Text(startswith="refresh_"))
    dp.register_callback_query_handler(back_to_courses, Text(startswith="back_to_courses"))
    dp.register_callback_query_handler(main_menu, Text(startswith="main_menu"))
    dp.register_callback_query_handler(change_group, Text(startswith="change_group"))