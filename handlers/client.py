import asyncio
import os
import re
from pathlib import Path
from datetime import datetime, timedelta
from aiogram import types, Dispatcher
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Text
import xlrd
from db_api import db_commands
from create_bot import bot

# Создаем клавиатуру
def get_main_keyboard():
    """Создает основную клавиатуру бота."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("📅 Мое расписание"))
    kb.add(types.KeyboardButton("💼 Настройки"))
    return kb

async def cmd_start(message: types.Message):
    """Обрабатывает команду /start и регистрирует пользователя."""
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
    """Показывает раздел настроек."""
    await message.reply(f"💼 Настройки", reply_markup=get_main_keyboard())

# Словари для хранения расписания
schedule_data = {}
group_info_data = {}
week_days_info = {}  # Структура для хранения информации о днях недели: {курс: {неделя: {день: дата}}}
DAY_NAMES = {
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
}

GROUP_PREFIX = "ИД"
WEEK_TOKEN = "НЕДЕЛЯ"
TIME_HEADER = "время"
DEFAULT_DATE = "Не указана"
DEFAULT_DIRECTION = "Не указано"

IGNORED_LESSON_VALUES = {
    "",
    "none",
    "null",
    "нет",
    "праздничный день",
}

SCHEDULES_DIR = Path(__file__).resolve().parents[1] / "schedules"
EXCEL_EXTENSIONS = {".xls", ".xlsx"}

# Отображаемые названия курсов с учетом периода из имени файла
course_display_names = {}
course_period_ids = {}
period_id_to_label = {}
period_courses = {}


def _discover_excel_files():
    """Ищет все Excel-файлы в папке schedules."""
    SCHEDULES_DIR.mkdir(parents=True, exist_ok=True)
    files = [
        file_path
        for file_path in SCHEDULES_DIR.iterdir()
        if file_path.is_file()
        and file_path.suffix.lower() in EXCEL_EXTENSIONS
        and not file_path.name.startswith("~$")
    ]
    return sorted(files, key=lambda item: item.name.lower())


def _extract_period_label(file_name):
    """Формирует подпись периода по имени Excel-файла."""
    stem = Path(file_name).stem
    normalized = stem.lower()
    normalized = normalized.replace(".", " ").replace("-", " ").replace("_", " ")

    parts = []

    year_match = re.search(r"(\d{2})\s+(\d{2})", normalized)
    if year_match:
        start_year = int(year_match.group(1))
        end_year = int(year_match.group(2))
        parts.append(f"20{start_year:02d}/20{end_year:02d}")

    season_map = [
        ("vesen", "Весенний"),
        ("osen", "Осенний"),
    ]

    month_map = [
        ("yanvar", "Январь"),
        ("january", "Январь"),
        ("fevr", "Февраль"),
        ("feb", "Февраль"),
        ("mart", "Март"),
        ("march", "Март"),
        ("aprel", "Апрель"),
        ("april", "Апрель"),
        ("may", "Май"),
        ("mai", "Май"),
        ("iyun", "Июнь"),
        ("june", "Июнь"),
        ("iyul", "Июль"),
        ("july", "Июль"),
        ("avgust", "Август"),
        ("august", "Август"),
        ("sentyabr", "Сентябрь"),
        ("september", "Сентябрь"),
        ("oktyabr", "Октябрь"),
        ("october", "Октябрь"),
        ("noyabr", "Ноябрь"),
        ("november", "Ноябрь"),
        ("dekabr", "Декабрь"),
        ("december", "Декабрь"),
    ]

    for token, label in season_map:
        if token in normalized:
            parts.append(f"{label} семестр")
            break

    for token, label in month_map:
        if token in normalized:
            parts.append(label)
            break

    return " | ".join(parts) if parts else stem


def _register_period(period_label):
    """Регистрирует период и возвращает его внутренний идентификатор."""
    for period_id, label in period_id_to_label.items():
        if label == period_label:
            return period_id

    period_id = f"P{len(period_id_to_label) + 1}"
    period_id_to_label[period_id] = period_label
    period_courses[period_id] = []
    return period_id


def _course_display(course_key):
    """Возвращает отображаемое имя курса."""
    return course_display_names.get(course_key, course_key)


def _course_name(course_key):
    """Извлекает название курса без технического префикса источника."""
    key = _normalize_cell_text(course_key)
    if ":" in key:
        return key.split(":", 1)[1].strip()
    return key


def _course_keys_by_name(course_name):
    """Возвращает ключи курсов с одинаковым названием из разных файлов."""
    target_name = _normalize_cell_text(course_name)
    return [
        course_key
        for course_key in schedule_data.keys()
        if _course_name(course_key) == target_name
    ]


def _period_label_for_course(course_key):
    """Возвращает подпись периода для курса."""
    period_id = course_period_ids.get(course_key)
    if period_id and period_id in period_id_to_label:
        return period_id_to_label[period_id]

    display_name = _course_display(course_key)
    if " | " in display_name:
        return display_name.split(" | ", 1)[0]
    return display_name


def _group_info_for_course_name(course_name, group_code):
    """Ищет информацию о группе по названию курса и коду группы."""
    for course_key in _course_keys_by_name(course_name):
        if course_key in group_info_data and group_code in group_info_data[course_key]:
            return group_info_data[course_key][group_code]
    return [course_name, group_code, "Информация не найдена"]


def _normalize_cell_text(value):
    """Нормализует значение ячейки Excel в строку."""
    if value is None:
        return ""

    if isinstance(value, str):
        return " ".join(value.replace("\n", " ").split())

    return str(value).strip()


def _format_excel_date(value, datemode):
    """Преобразует дату из Excel в строку."""
    if isinstance(value, float):
        try:
            y, m, d, _, _, _ = xlrd.xldate_as_tuple(value, datemode)
            return f"{y:04d}-{m:02d}-{d:02d}"
        except Exception:
            pass

    text_value = _normalize_cell_text(value)
    return text_value if text_value else DEFAULT_DATE


def _format_excel_time(value, datemode):
    """Преобразует время из Excel в строку."""
    if isinstance(value, float):
        try:
            _, _, _, h, m, _ = xlrd.xldate_as_tuple(value, datemode)
            return f"{h:02d}:{m:02d}"
        except Exception:
            pass

    return _normalize_cell_text(value)


def _find_week_label(row_values):
    """Ищет в строке метку недели."""
    for cell_value in row_values:
        text_value = _normalize_cell_text(cell_value)
        if text_value and WEEK_TOKEN in text_value.upper():
            return text_value
    return None





def _week_sort_key(week_label):
    """Возвращает ключ сортировки недель по номеру."""
    week_text = _normalize_cell_text(week_label)
    match = re.search(r"\d+", week_text)
    if match:
        return int(match.group(0)), week_text
    return 10 ** 9, week_text

def parse_excel_file():
    """Парсит все Excel-файлы с расписанием из папки schedules."""
    global schedule_data, group_info_data, week_days_info, course_display_names, course_period_ids, period_id_to_label, period_courses

    excel_files = _discover_excel_files()
    if not excel_files:
        print(f"ERROR: no Excel files found in {SCHEDULES_DIR}")
        return

    print(f"Found Excel files: {len(excel_files)}")
    for file_path in excel_files:
        print(f"  - {file_path.name}")

    for source_index, excel_path in enumerate(excel_files, start=1):
        source_token = f"S{source_index}"
        source_label = _extract_period_label(excel_path.name)
        period_id = _register_period(source_label)

        print(f"Reading workbook: {excel_path.name} ({source_label})")

        try:
            workbook = xlrd.open_workbook(str(excel_path))
        except Exception as open_error:
            print(f"ERROR: cannot open workbook {excel_path.name}: {open_error}")
            continue

        sheets = workbook.sheet_names()
        print(f"Sheets found in {excel_path.name}: {len(sheets)}")

        for sheet_name_raw in sheets:
            course_name = sheet_name_raw.strip()
            course_key = f"{source_token}:{course_name}"
            course_display_names[course_key] = f"{source_label} | {course_name}"
            course_period_ids[course_key] = period_id
            period_courses.setdefault(period_id, []).append(course_key)

            print(f"Processing sheet: {course_display_names[course_key]}")

            try:
                sheet = workbook.sheet_by_name(sheet_name_raw)
            except Exception as sheet_error:
                print(f"  ERROR: cannot read sheet {sheet_name_raw}: {sheet_error}")
                continue

            schedule_data[course_key] = {}
            group_info_data[course_key] = {}
            week_days_info[course_key] = {}

            group_codes = {}

            for row_idx in range(sheet.nrows):
                row = sheet.row_values(row_idx)
                if not any(row):
                    continue

                for col_idx, cell_value in enumerate(row):
                    cell_text = _normalize_cell_text(cell_value)
                    if not cell_text:
                        continue

                    upper_text = cell_text.upper()
                    if not (upper_text.startswith("ИД") or upper_text.startswith("ID")):
                        continue

                    group_code = cell_text
                    if group_code in group_codes:
                        continue

                    group_codes[group_code] = {"col_idx": col_idx}
                    schedule_data[course_key][group_code] = {}

                    direction = DEFAULT_DIRECTION
                    if row_idx + 1 < sheet.nrows:
                        next_row = sheet.row_values(row_idx + 1)
                        if col_idx < len(next_row):
                            direction_value = _normalize_cell_text(next_row[col_idx])
                            if direction_value:
                                direction = direction_value

                    group_info_data[course_key][group_code] = [course_key, group_code, direction]

            print(f"  Groups found: {len(group_codes)}")
            if not group_codes:
                print("  WARNING: no groups found on this sheet")
                continue

            current_week = None
            current_day = None
            current_date = DEFAULT_DATE

            for row_idx in range(sheet.nrows):
                row = sheet.row_values(row_idx)
                if not any(row):
                    continue

                week_label = _find_week_label(row)
                if week_label:
                    current_week = week_label
                    current_day = None
                    current_date = DEFAULT_DATE

                    week_days_info[course_key].setdefault(current_week, {})
                    for group_code in group_codes:
                        schedule_data[course_key][group_code].setdefault(current_week, [])
                    continue

                first_cell = _normalize_cell_text(row[0]) if row else ""
                if first_cell.lower() in DAY_NAMES:
                    current_day = first_cell
                    date_cell = row[1] if len(row) > 1 else None
                    current_date = _format_excel_date(date_cell, workbook.datemode)

                    if current_week:
                        week_days_info[course_key].setdefault(current_week, {})
                        week_days_info[course_key][current_week][current_day] = current_date

                if not current_week or not current_day:
                    continue

                time_cell = row[2] if len(row) > 2 else None
                time_str = _format_excel_time(time_cell, workbook.datemode)
                if not time_str or time_str.lower() == TIME_HEADER:
                    continue

                for group_code, group_data in group_codes.items():
                    col_idx = group_data["col_idx"]
                    if col_idx >= len(row):
                        continue

                    lesson_info = _normalize_cell_text(row[col_idx])
                    if not lesson_info:
                        continue

                    if lesson_info.lower() in IGNORED_LESSON_VALUES:
                        continue

                    schedule_entry = {
                        "day": current_day,
                        "date": current_date,
                        "time": time_str,
                        "lesson": lesson_info,
                        "week": current_week,
                    }
                    schedule_data[course_key][group_code][current_week].append(schedule_entry)

            total_lessons = sum(
                len(week_schedule)
                for group in schedule_data[course_key].values()
                for week_schedule in group.values()
            )
            print(f"  Lessons loaded: {total_lessons}")

            if total_lessons == 0:
                # Не оставляем в меню курсы без занятий.
                schedule_data.pop(course_key, None)
                group_info_data.pop(course_key, None)
                week_days_info.pop(course_key, None)
                course_display_names.pop(course_key, None)

                period_id_for_course = course_period_ids.pop(course_key, None)
                if period_id_for_course and period_id_for_course in period_courses:
                    period_courses[period_id_for_course] = [
                        item for item in period_courses[period_id_for_course] if item != course_key
                    ]
    empty_period_ids = [period_id for period_id, courses in period_courses.items() if not courses]
    for period_id in empty_period_ids:
        period_courses.pop(period_id, None)
        period_id_to_label.pop(period_id, None)

    print("Parsing Excel files finished")


def init_schedule():
    """Инициализирует и загружает расписание в память."""
    global schedule_data, group_info_data, week_days_info, course_display_names, course_period_ids, period_id_to_label, period_courses
    
    # Очищаем предыдущие данные
    schedule_data.clear()
    group_info_data.clear()
    week_days_info.clear()
    course_display_names.clear()
    course_period_ids.clear()
    period_id_to_label.clear()
    period_courses.clear()
    
    # Пробуем парсить файл
    parse_excel_file()
    
    # Если данные не загружены, используем тестовые данные
    if not schedule_data:
        print("⚠️ Использую тестовые данные...")
        create_test_data()

def create_test_data():
    """Создает тестовые данные, если файлы расписания недоступны."""
    global schedule_data, group_info_data, week_days_info, course_display_names, course_period_ids, period_id_to_label, period_courses

    course_key = "S1:TestCourse"
    group_key = "ID-TEST-01"

    schedule_data = {
        course_key: {
            group_key: {
                "19 WEEK": [
                    {
                        "day": "monday",
                        "date": "2026-01-05",
                        "time": "08:20-09:50",
                        "lesson": "Test subject",
                        "week": "19 WEEK",
                    }
                ]
            }
        }
    }

    group_info_data = {
        course_key: {
            group_key: [course_key, group_key, "Test direction"],
        }
    }

    week_days_info = {
        course_key: {
            "19 WEEK": {
                "monday": "2026-01-05",
            }
        }
    }

    course_display_names = {
        course_key: "test | course",
    }
    course_period_ids = {
        course_key: "P1",
    }
    period_id_to_label = {
        "P1": "test period",
    }
    period_courses = {
        "P1": [course_key],
    }


WEEKDAY_ORDER = [
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
]
WEEKDAY_INDEX = {day_name: idx for idx, day_name in enumerate(WEEKDAY_ORDER)}


def _parse_schedule_date(date_text):
    """Преобразует строку даты расписания в datetime."""
    value = _normalize_cell_text(date_text)
    if not value:
        return None

    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    return None


def _collect_days_for_week(course, target_week, week_schedule):
    """Собирает полный упорядоченный список дней выбранной недели."""
    day_to_date = {}

    saved_days = week_days_info.get(course, {}).get(target_week, {})
    for day_name, date_value in saved_days.items():
        normalized_day = _normalize_cell_text(day_name).lower()
        normalized_date = _normalize_cell_text(date_value)
        if normalized_day:
            day_to_date[normalized_day] = normalized_date or DEFAULT_DATE

    for entry in week_schedule:
        normalized_day = _normalize_cell_text(entry.get("day")).lower()
        normalized_date = _normalize_cell_text(entry.get("date"))

        if not normalized_day or normalized_day not in DAY_NAMES:
            continue
        if not normalized_date:
            continue

        existing_date = day_to_date.get(normalized_day)
        if not existing_date or existing_date in (DEFAULT_DATE, "Дата не указана"):
            day_to_date[normalized_day] = normalized_date

    known_points = []
    for day_name, date_value in day_to_date.items():
        if day_name not in WEEKDAY_INDEX:
            continue

        parsed_date = _parse_schedule_date(date_value)
        if parsed_date:
            known_points.append((WEEKDAY_INDEX[day_name], parsed_date))

    if known_points:
        monday_candidates = [date_obj - timedelta(days=day_idx) for day_idx, date_obj in known_points]
        monday_date = min(monday_candidates)

        for day_idx, day_name in enumerate(WEEKDAY_ORDER):
            day_to_date.setdefault(day_name, (monday_date + timedelta(days=day_idx)).strftime("%Y-%m-%d"))
    else:
        for day_name in WEEKDAY_ORDER:
            day_to_date.setdefault(day_name, "Дата не указана")

    ordered_days = [
        (day_name, day_to_date.get(day_name, "Дата не указана"))
        for day_name in WEEKDAY_ORDER
    ]

    sunday_name = "воскресенье"
    if sunday_name in day_to_date:
        ordered_days.append((sunday_name, day_to_date[sunday_name]))

    return ordered_days


async def get_day_schedule(course, group_code, week, day_index):
    """Формирует расписание на конкретный день."""
    
    if course not in schedule_data:
        return f"❌ Курс '{course}' не найден в расписании.", None, None, None
    
    if group_code not in schedule_data[course]:
        return f"❌ Группа '{group_code}' не найдена в расписании курса '{course}'.", None, None, None
    
    group_schedule = schedule_data[course][group_code]
    
    # Определяем неделю
    available_weeks = sorted(group_schedule.keys(), key=_week_sort_key)
    if not available_weeks:
        return f"📭 Для группы '{group_code}' нет расписания на какие-либо недели.", None, None, None
    
    if week and week in available_weeks:
        target_week = week
    else:
        # Берем первую доступную неделю
        target_week = available_weeks[-1]
    
    week_schedule = group_schedule.get(target_week, [])

    days_in_week = _collect_days_for_week(course, target_week, week_schedule)

    if not days_in_week:
        return f"📭 Для группы '{group_code}' нет дней в неделе '{target_week}'.", None, None, None
    
    # Определяем текущий день
    if day_index is None or day_index >= len(days_in_week):
        current_day_index = 0
    else:
        current_day_index = day_index
    
    if current_day_index < 0:
        current_day_index = 0
    
    if current_day_index >= len(days_in_week):
        current_day_index = len(days_in_week) - 1
    
    # Получаем текущий день
    current_day, current_date = days_in_week[current_day_index]
    
    # Форматируем расписание для дня
    schedule_text = f"📅 <b>Расписание для группы {group_code}</b>\n"
    schedule_text += f"🎓 <b>Курс:</b> {_course_display(course)}\n"
    schedule_text += f"📆 <b>Неделя:</b> {target_week}\n"
    schedule_text += f"📌 <b>День:</b> {current_day} ({current_date})\n\n"
    
    # Получаем занятия для этого дня
    day_lessons = []
    for entry in week_schedule:
        entry_day = _normalize_cell_text(entry.get('day')).lower()
        if entry_day == current_day:
            day_lessons.append(entry)
    
    # Сортируем занятия по времени
    day_lessons_sorted = sorted(day_lessons, key=lambda x: x['time'])
    
    if not day_lessons_sorted:
        schedule_text += "✅ <b>Свободный день!</b>\n"
        schedule_text += "🎉 Нет лекций и семинаров\n"
    else:
        for entry in day_lessons_sorted:
            time_display = entry['time']
            lesson_display = entry['lesson']
            
            # Обрезаем слишком длинные названия
            if len(lesson_display) > 80:
                lesson_display = lesson_display[:77] + "..."
            
            schedule_text += f"• ⏰ <b>{time_display}</b> - {lesson_display}\n"
    
    # Информация о навигации
    schedule_text += f"\n📋 <b>День {current_day_index + 1} из {len(days_in_week)}</b>"
    
    return schedule_text, target_week, current_day_index, days_in_week

# Функция для получения информации о группе
def get_group_info(course, group_code):
    """Возвращает информацию о группе."""
    if course in group_info_data and group_code in group_info_data[course]:
        return group_info_data[course][group_code]
    return [course, group_code, "Информация не найдена"]

# Хендлер для выбора курса
async def choose_course(message: types.Message):
    """Показывает пользователю список курсов."""
    keyboard = types.InlineKeyboardMarkup(row_width=2)

    course_names = sorted({_course_name(course_key) for course_key in schedule_data.keys()})

    if not course_names:
        await message.answer("⚠️ Расписание еще не загружено. Попробуйте позже.")
        return

    for course_name in course_names:
        keyboard.add(types.InlineKeyboardButton(
            text=course_name[:64],
            callback_data=f"course_{course_name}"
        ))

    await message.answer("Выберите курс:", reply_markup=keyboard)


async def process_course_choice(callback_query: types.CallbackQuery):
    """Обрабатывает выбор курса и показывает группы."""
    course_name = callback_query.data.split("_", 1)[1]

    keyboard = types.InlineKeyboardMarkup(row_width=2)

    groups = set()
    for course_key in _course_keys_by_name(course_name):
        groups.update(schedule_data.get(course_key, {}).keys())

    for group_code in sorted(groups):
        group_info = _group_info_for_course_name(course_name, group_code)
        group_name = f"{group_code}"
        if len(group_info) > 2 and group_info[2]:
            group_name += f" - {group_info[2]}"

        keyboard.add(types.InlineKeyboardButton(
            text=group_name[:50],
            callback_data=f"group_{course_name}_{group_code}"
        ))

    keyboard.add(types.InlineKeyboardButton(
        text="⬅️ Назад к выбору курса",
        callback_data="back_to_courses"
    ))

    await callback_query.message.edit_text(
        f"Выбран курс: {course_name}\nВыберите вашу группу:",
        reply_markup=keyboard
    )
    await callback_query.answer()


async def process_group_choice(callback_query: types.CallbackQuery, state: FSMContext):
    """Обрабатывает выбор группы и показывает доступные месяцы/периоды."""
    data = callback_query.data.split("_", 2)
    course_name = data[1]
    group_code = data[2]

    await state.update_data(
        selected_course_name=course_name,
        selected_group=group_code
    )

    group_info = _group_info_for_course_name(course_name, group_code)
    group_display = f"{group_code}"
    if len(group_info) > 2 and group_info[2]:
        group_display += f"\n📚 Направление: {group_info[2]}"

    period_to_course = {}
    course_keys = sorted(_course_keys_by_name(course_name), key=lambda item: _period_label_for_course(item))
    for course_key in course_keys:
        if group_code not in schedule_data.get(course_key, {}):
            continue

        period_label = _period_label_for_course(course_key)
        period_to_course.setdefault(period_label, course_key)

    if not period_to_course:
        await callback_query.answer("Для выбранной группы нет расписания", show_alert=True)
        return

    keyboard = types.InlineKeyboardMarkup(row_width=2)
    for period_label, course_key in sorted(period_to_course.items(), key=lambda item: item[0]):
        keyboard.add(types.InlineKeyboardButton(
            text=f"📅 {period_label}"[:64],
            callback_data=f"show_schedule_{course_key}_{group_code}"
        ))

    keyboard.add(types.InlineKeyboardButton(
        text="⬅️ Выбрать другую группу",
        callback_data=f"course_{course_name}"
    ))
    keyboard.add(types.InlineKeyboardButton(
        text="🏠 В главное меню",
        callback_data="main_menu"
    ))

    await callback_query.message.edit_text(
        f"✅ <b>Вы выбрали:</b>\n"
        f"🎓 <b>Курс:</b> {course_name}\n"
        f"👥 <b>Группа:</b> {group_display}\n\n"
        f"Теперь выберите месяц/период:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback_query.answer()


async def show_schedule_day(callback_query: types.CallbackQuery, state: FSMContext):
    """Показывает расписание по дням и неделям."""
    callback_data = callback_query.data

    course = None
    group_code = None
    day_index = None
    week = None

    if callback_data.startswith("show_schedule_"):
        parts = callback_data.split("_", 3)
        if len(parts) >= 4:
            course = parts[2]
            group_code = parts[3]
            await state.update_data(selected_course=course, selected_group=group_code)
    elif callback_data.startswith("show_day_"):
        parts = callback_data.split("_", 5)
        if len(parts) >= 5:
            course = parts[2]
            group_code = parts[3]
            if parts[4].isdigit():
                day_index = int(parts[4])
        if len(parts) >= 6:
            week = parts[5]
    elif callback_data.startswith("show_week_"):
        parts = callback_data.split("_", 4)
        if len(parts) >= 5:
            course = parts[2]
            group_code = parts[3]
            week = parts[4]
            day_index = 0

    if not course or not group_code:
        await callback_query.answer("Invalid schedule callback data")
        return

    schedule_text, target_week, current_day_index, days_in_week = await get_day_schedule(
        course, group_code, week, day_index
    )

    if schedule_text is None or current_day_index is None:
        await callback_query.answer("Ошибка при получении расписания")
        return

    keyboard = types.InlineKeyboardMarkup(row_width=3)

    if days_in_week and len(days_in_week) > 1:
        prev_day_index = current_day_index - 1
        if prev_day_index >= 0:
            keyboard.add(types.InlineKeyboardButton(
                text="◀️ Предыдущий день",
                callback_data=f"show_day_{course}_{group_code}_{prev_day_index}_{target_week}"
            ))
        else:
            keyboard.add(types.InlineKeyboardButton(
                text="◀️ Последний день",
                callback_data=f"show_day_{course}_{group_code}_{len(days_in_week)-1}_{target_week}"
            ))

        keyboard.add(types.InlineKeyboardButton(
            text="📅 Выбрать день",
            callback_data=f"choose_day_{course}_{group_code}_{target_week}"
        ))

        next_day_index = current_day_index + 1
        if next_day_index < len(days_in_week):
            keyboard.add(types.InlineKeyboardButton(
                text="Следующий день ▶️",
                callback_data=f"show_day_{course}_{group_code}_{next_day_index}_{target_week}"
            ))
        else:
            keyboard.add(types.InlineKeyboardButton(
                text="Первый день ▶️",
                callback_data=f"show_day_{course}_{group_code}_0_{target_week}"
            ))

    available_weeks = []
    if course in schedule_data and group_code in schedule_data[course]:
        available_weeks = sorted(schedule_data[course][group_code].keys(), key=_week_sort_key)

    if len(available_weeks) > 1:
        keyboard.row()
        for week_item in available_weeks:
            if week_item != target_week:
                keyboard.add(types.InlineKeyboardButton(
                    text=f"📆 {week_item}",
                    callback_data=f"show_week_{course}_{group_code}_{week_item}"
                ))

    keyboard.row()
    keyboard.add(
        types.InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=f"refresh_{course}_{group_code}"
        ),
        types.InlineKeyboardButton(
            text="⬅️ Назад к месяцам",
            callback_data=f"group_{_course_name(course)}_{group_code}"
        )
    )

    await callback_query.message.edit_text(
        schedule_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback_query.answer()


async def choose_day(callback_query: types.CallbackQuery):
    """Показывает выбор дня недели."""
    data = callback_query.data.split("_", 4)
    course = data[2]
    group_code = data[3]
    week = data[4] if len(data) > 4 else None
    
    # Получаем доступные дни для этой недели
    schedule_text, target_week, current_day_index, days_in_week = await get_day_schedule(
        course, group_code, week, 0
    )
    
    if not days_in_week:
        await callback_query.answer("Нет данных о днях недели")
        return
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    
    for i, (day, date) in enumerate(days_in_week):
        # Проверяем, есть ли занятия в этот день
        has_lessons = False
        if course in schedule_data and group_code in schedule_data[course]:
            week_schedule = schedule_data[course][group_code].get(target_week, [])
            for entry in week_schedule:
                entry_day = _normalize_cell_text(entry.get('day')).lower()
                if entry_day == day:
                    has_lessons = True
                    break
        
        day_text = f"{day} ({date})"
        if has_lessons:
            day_text = "✅ " + day_text
        else:
            day_text = "📭 " + day_text
        
        keyboard.add(types.InlineKeyboardButton(
            text=day_text,
            callback_data=f"show_day_{course}_{group_code}_{i}_{target_week}"
        ))
    
    keyboard.row()
    keyboard.add(types.InlineKeyboardButton(
        text="⬅️ Назад к расписанию",
        callback_data=f"show_schedule_{course}_{group_code}"
    ))
    
    await callback_query.message.edit_text(
        f"📅 Выберите день недели ({target_week}):\n"
        f"✅ - есть занятия\n"
        f"📭 - свободный день",
        reply_markup=keyboard
    )
    await callback_query.answer()

# Колбек для выбора недели
async def show_week(callback_query: types.CallbackQuery, state: FSMContext):
    """Переключает отображение на выбранную неделю."""
    await show_schedule_day(callback_query, state)

async def refresh_schedule(callback_query: types.CallbackQuery, state: FSMContext):
    """Перезагружает расписание из Excel-файлов."""
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
    callback_query.data = f"show_schedule_{course}_{group_code}"
    await show_schedule_day(callback_query, state)
    await callback_query.answer("Расписание обновлено!")

# Колбек для возврата к выбору курса
async def back_to_courses(callback_query: types.CallbackQuery):
    """Возвращает пользователя к выбору курса."""
    await choose_course(callback_query.message)
    await callback_query.answer()

# Колбек для возврата в главное меню
async def main_menu(callback_query: types.CallbackQuery):
    """Возвращает пользователя в главное меню."""
    await callback_query.message.answer(
        "🏠 Главное меню",
        reply_markup=get_main_keyboard()
    )
    await callback_query.answer()

# Хендлер для команды "Мое расписание"
async def my_schedule(message: types.Message, state: FSMContext):
    """Показывает текущее выбранное расписание пользователя."""
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
            f"🎓 <b>Курс:</b> {_course_display(course)}\n"
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
    """Запускает повторный выбор группы."""
    await choose_course(callback_query.message)
    await callback_query.answer()


# Регистрация хендлеров
def register_handlers_client(dp: Dispatcher):
    """Регистрирует обработчики сообщений и callback-кнопок."""
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
