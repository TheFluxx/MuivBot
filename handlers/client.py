import asyncio
import os
import re
import html
from pathlib import Path
from datetime import datetime, timedelta
from aiogram import types, Dispatcher
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Text
from aiogram.utils.exceptions import MessageNotModified
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

def _short_day_name(day_name):
    """Возвращает короткое имя дня недели."""
    normalized = _normalize_cell_text(day_name)
    if not normalized:
        return "--"
    return normalized[:2].title()


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
        return f"Курс '{course}' не найден в расписании.", None, None, None

    if group_code not in schedule_data[course]:
        return f"Группа '{group_code}' не найдена в расписании курса '{course}'.", None, None, None

    group_schedule = schedule_data[course][group_code]
    available_weeks = sorted(group_schedule.keys(), key=_week_sort_key)
    if not available_weeks:
        return f"Для группы '{group_code}' нет расписания на какие-либо недели.", None, None, None

    target_week = week if week and week in available_weeks else available_weeks[-1]
    week_schedule = group_schedule.get(target_week, [])
    days_in_week = _collect_days_for_week(course, target_week, week_schedule)

    if not days_in_week:
        return f"Для группы '{group_code}' нет дней в неделе '{target_week}'.", None, None, None

    if day_index is None or day_index >= len(days_in_week):
        current_day_index = 0
    else:
        current_day_index = max(0, min(day_index, len(days_in_week) - 1))

    current_day, current_date = days_in_week[current_day_index]

    schedule_text = f"<b>📅 Расписание для группы {group_code}</b>\n"
    schedule_text += f"<b>🎓 Курс:</b> {_course_display(course)}\n"
    schedule_text += f"<b>🗓️ Неделя:</b> {target_week}\n"
    schedule_text += f"<b>📅 День:</b> {current_day} ({current_date})\n\n"

    day_lessons = []
    for entry in week_schedule:
        if _normalize_cell_text(entry.get("day")).lower() == current_day:
            day_lessons.append(entry)

    day_lessons_sorted = sorted(day_lessons, key=lambda x: x["time"])

    if not day_lessons_sorted:
        schedule_text += "<b>😌 Свободный день!</b>\n"
        schedule_text += "Нет лекций и семинаров\n"
    else:
        for entry in day_lessons_sorted:
            time_display = entry["time"]
            lesson_display = entry["lesson"]
            if len(lesson_display) > 80:
                lesson_display = lesson_display[:77] + "..."
            schedule_text += f"- <b>{time_display}</b> - {lesson_display}\n"

    schedule_text += f"\n<b>День {current_day_index + 1} из {len(days_in_week)}</b>"
    return schedule_text, target_week, current_day_index, days_in_week

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

    buttons = [
        types.InlineKeyboardButton(text=name[:64], callback_data=f"course_{name}") 
        for name in course_names
    ]
    keyboard.add(*buttons)

    await message.answer("🎓 Выберите курс:", reply_markup=keyboard)


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
        f"✅ Выбран курс: {course_name}\n👥 Выберите вашу группу:",
        reply_markup=keyboard
    )
    await callback_query.answer()


async def _safe_edit_text(message: types.Message, text: str, reply_markup=None, parse_mode: str | None = None):
    """Безопасно редактирует сообщение."""
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except MessageNotModified:
        pass


def _build_period_to_course(course_name, group_code):
    """Строит связку периода и курса для группы."""
    period_items = []
    seen_periods = set()

    course_keys = sorted(
        _course_keys_by_name(course_name),
        key=lambda item: (
            _course_start_date(item, group_code) is None,
            _course_start_date(item, group_code) or datetime.max.date(),
            _period_label_for_course(item),
        ),
    )

    for course_key in course_keys:
        if group_code not in schedule_data.get(course_key, {}):
            continue

        period_label = _period_label_for_course(course_key)
        if period_label in seen_periods:
            continue

        seen_periods.add(period_label)
        period_items.append((period_label, course_key))

    return period_items

def _get_available_weeks(course, group_code):
    """Возвращает отсортированные недели группы."""
    if course not in schedule_data:
        return []
    if group_code not in schedule_data[course]:
        return []
    return sorted(schedule_data[course][group_code].keys(), key=_week_sort_key)


def _week_dates(week_days):
    """Возвращает отсортированные даты недели."""
    return sorted(item['date_obj'] for item in week_days if item['date_obj'] is not None)


def _course_start_date(course, group_code):
    """Возвращает дату начала периода для группы."""
    weeks = _get_available_weeks(course, group_code)
    start_dates = []

    for week_label in weeks:
        week_days = _build_week_day_items(course, group_code, week_label)
        dates = _week_dates(week_days)
        if dates:
            start_dates.append(dates[0])

    if start_dates:
        return min(start_dates)
    return None


def _week_number_text(week_label):
    """Извлекает номер недели из заголовка XLS (служебно)."""
    week_text = _normalize_cell_text(week_label)
    for token in week_text.replace('-', ' ').split():
        if token.isdigit():
            return token
    return week_text


def _build_week_day_items(course, group_code, week_label):
    """Формирует список дней недели с датами."""
    week_schedule = schedule_data.get(course, {}).get(group_code, {}).get(week_label, [])
    days_in_week = _collect_days_for_week(course, week_label, week_schedule)
    today = datetime.now().date()

    items = []
    for day_name, date_text in days_in_week:
        parsed_date = _parse_schedule_date(date_text)
        day_date = parsed_date.date() if parsed_date else None
        has_lessons = any(
            _normalize_cell_text(entry.get('day')).lower() == day_name
            for entry in week_schedule
        )
        date_short = parsed_date.strftime('%d.%m.%y') if parsed_date else _normalize_cell_text(date_text)

        items.append(
            {
                'day_name': day_name,
                'day_title': day_name.capitalize(),
                'date_obj': day_date,
                'date_short': date_short,
                'has_lessons': has_lessons,
                'is_today': day_date == today,
            }
        )

    return items


def _month_week_number(week_days):
    """Возвращает номер недели в месяце по первой дате недели."""
    dates = _week_dates(week_days)
    if not dates:
        return None

    reference_date = dates[0]
    return ((reference_date.day - 1) // 7) + 1


def _month_week_number_text(week_days):
    """Текстовое представление номера недели месяца."""
    month_week_number = _month_week_number(week_days)
    if month_week_number is None:
        return '-'
    return str(month_week_number)


def _build_group_week_timeline(course_name, group_code):
    """Строит непрерывную шкалу недель группы по всем периодам."""
    timeline = []
    course_keys = sorted(
        _course_keys_by_name(course_name),
        key=lambda item: (
            _course_start_date(item, group_code) is None,
            _course_start_date(item, group_code) or datetime.max.date(),
            _period_label_for_course(item),
        ),
    )

    for course_key in course_keys:
        weeks = _get_available_weeks(course_key, group_code)
        for week_index, week_label in enumerate(weeks):
            week_days = _build_week_day_items(course_key, group_code, week_label)
            dates = _week_dates(week_days)
            start_date = dates[0] if dates else None

            timeline.append(
                {
                    'course': course_key,
                    'week_label': week_label,
                    'week_index': week_index,
                    'start_date': start_date,
                }
            )

    timeline.sort(
        key=lambda item: (
            item['start_date'] is None,
            item['start_date'] or datetime.max.date(),
            item['course'],
            item['week_index'],
        )
    )

    return timeline

def _pick_today_week_for_group(course_name, group_code):
    """Выбирает неделю, в которую попадает сегодняшняя дата."""
    today = datetime.now().date()
    timeline = _build_group_week_timeline(course_name, group_code)

    if not timeline:
        return None

    nearest_item = None
    nearest_key = None

    for item in timeline:
        week_days = _build_week_day_items(item['course'], group_code, item['week_label'])
        dates = _week_dates(week_days)
        if not dates:
            continue

        week_start = dates[0]
        week_end = dates[-1]

        if week_start <= today <= (week_end + timedelta(days=1)):
            return item['course'], item['week_index']

        if today < week_start:
            distance = (week_start - today).days
        else:
            distance = (today - week_end).days

        candidate_key = (distance, week_start)
        if nearest_key is None or candidate_key < nearest_key:
            nearest_key = candidate_key
            nearest_item = item

    if nearest_item is not None:
        return nearest_item['course'], nearest_item['week_index']

    first_item = timeline[0]
    return first_item['course'], first_item['week_index']

def _period_has_current_month(course, group_code, weeks):
    """Проверяет, есть ли в периоде недели текущего месяца."""
    today = datetime.now().date()
    for week_label in weeks:
        for day_item in _build_week_day_items(course, group_code, week_label):
            day_date = day_item['date_obj']
            if day_date and day_date.year == today.year and day_date.month == today.month:
                return True
    return False

def _pick_initial_week_index(course, group_code, weeks):
    """Выбирает стартовую неделю."""
    if not weeks:
        return 0

    today = datetime.now().date()
    if not _period_has_current_month(course, group_code, weeks):
        return 0

    for index, week_label in enumerate(weeks):
        week_days = _build_week_day_items(course, group_code, week_label)
        dates = [item['date_obj'] for item in week_days if item['date_obj'] is not None]
        if not dates:
            continue

        week_start = min(dates)
        week_end = max(dates)
        if week_start <= today <= (week_end + timedelta(days=1)):
            return index

    for index, week_label in enumerate(weeks):
        week_days = _build_week_day_items(course, group_code, week_label)
        if any(
            item['date_obj']
            and item['date_obj'].year == today.year
            and item['date_obj'].month == today.month
            for item in week_days
        ):
            return index

    return 0


def _week_date_range_text(week_days):
    """Форматирует диапазон дат недели."""
    dates = sorted(item['date_obj'] for item in week_days if item['date_obj'] is not None)
    if not dates:
        return 'даты не указаны'
    return f"{dates[0].strftime('%d.%m')} - {dates[-1].strftime('%d.%m')}"


def _get_day_lessons(week_schedule, day_name):
    """Возвращает занятия выбранного дня."""
    lessons = [
        entry
        for entry in week_schedule
        if _normalize_cell_text(entry.get('day')).lower() == day_name
    ]
    return sorted(lessons, key=lambda item: _normalize_cell_text(item.get('time')))


def _build_week_overview_text(course, group_code, week_label, week_days):
    """Формирует текст страницы недели."""
    month_week_number = _month_week_number_text(week_days)
    week_range = _week_date_range_text(week_days)

    lines = [
        f"<b>{html.escape(_period_label_for_course(course))}</b>\n",
        f"🎓 Курс: <b>{html.escape(_course_name(course))}</b>\n",
        f"👥 Группа: <b>{html.escape(group_code)}</b>\n",
        f"🗓️ Неделя месяца: <b>{month_week_number}</b> ({week_range})\n\n",
    ]

    lines.append('👇 Нажмите, чтобы открыть расписание.')
    return ''.join(lines)

def _build_week_keyboard(week_label, week_days):
    """Строит клавиатуру страницы недели."""
    keyboard = types.InlineKeyboardMarkup(row_width=1)

    for day_index, day_item in enumerate(week_days):
        day_button_text = f"{day_item['date_short']} ({day_item['day_title']})"
        if day_item['is_today']:
            day_button_text += ' | Сегодня 💠'

        keyboard.add(
            types.InlineKeyboardButton(
                text=day_button_text[:64],
                callback_data=f"cal_day_{day_index}",
            )
        )

    month_week_number = _month_week_number_text(week_days)
    keyboard.row(
        types.InlineKeyboardButton(text='⬅️ Назад', callback_data='cal_shift_-1'),
        types.InlineKeyboardButton(text=f"📆 Неделя {month_week_number}"[:64], callback_data='cal_noop'),
        types.InlineKeyboardButton(text='Вперед ➡️', callback_data='cal_shift_1'),
    )

    keyboard.row(
        types.InlineKeyboardButton(text='🗂️ Выбор недели', callback_data='cal_pick_week'),
        types.InlineKeyboardButton(text='🗓️ Выбор месяца', callback_data='cal_pick_month'),
    )

    return keyboard

def _build_day_keyboard(week_days, selected_day_index):
    """Строит клавиатуру страницы дня."""
    keyboard = types.InlineKeyboardMarkup(row_width=6)

    prev_day_index = selected_day_index - 1
    next_day_index = selected_day_index + 1

    keyboard.row(
        types.InlineKeyboardButton(text='⬅️ Предыдущий день', callback_data=f"cal_day_{prev_day_index}"),
        types.InlineKeyboardButton(text='Следующий день ➡️', callback_data=f"cal_day_{next_day_index}"),
    )

    day_buttons = []
    for day_index, day_item in enumerate(week_days):
        short_name = _short_day_name(day_item['day_name'])
        button_text = f"{short_name} {day_item['date_short'][:5]}"

        if day_index == selected_day_index:
            button_text = f"* {button_text}"
        elif day_item['is_today']:
            button_text = f"{button_text} *"

        day_buttons.append(
            types.InlineKeyboardButton(
                text=button_text[:64],
                callback_data=f"cal_day_{day_index}",
            )
        )



    keyboard.row(types.InlineKeyboardButton(text='↩️ Назад к неделе', callback_data='cal_back_week'))

    return keyboard

def _build_week_picker_keyboard(course, group_code, weeks, current_week_index):
    """Строит клавиатуру выбора недели."""
    keyboard = types.InlineKeyboardMarkup(row_width=2)

    if not weeks:
        return keyboard

    current_week_index = current_week_index % len(weeks)

    for week_index, week_label in enumerate(weeks):
        week_days = _build_week_day_items(course, group_code, week_label)
        range_text = _week_date_range_text(week_days)
        month_week_number = _month_week_number_text(week_days)
        selected_prefix = '* ' if week_index == current_week_index else ''
        button_text = f"{selected_prefix}📆 Неделя {month_week_number} ({range_text})"

        keyboard.add(
            types.InlineKeyboardButton(
                text=button_text[:64],
                callback_data=f"cal_weekpick_{week_index}",
            )
        )

    current_week_days = _build_week_day_items(course, group_code, weeks[current_week_index])
    current_dates = _week_dates(current_week_days)
    month_names = [
        'Январь', 'Февраль', 'Март', 'Апрель',
        'Май', 'Июнь', 'Июль', 'Август',
        'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
    ]

    if current_dates:
        month_date = current_dates[0]
        month_text = f"🗓️ {month_names[month_date.month - 1]} {month_date.year}"
    else:
        month_text = '🗓️ Месяц'

    keyboard.row(
        types.InlineKeyboardButton(text='⬅️ Назад', callback_data='cal_month_shift_-1'),
        types.InlineKeyboardButton(text=month_text[:64], callback_data='cal_noop'),
        types.InlineKeyboardButton(text='Вперед ➡️', callback_data='cal_month_shift_1'),
    )

    keyboard.row(
        types.InlineKeyboardButton(text='🎯 Перейти к нынешней неделе', callback_data='cal_current_week'),
    )


    return keyboard

async def _render_period_picker(callback_query: types.CallbackQuery, state: FSMContext, course_name: str, group_code: str):
    """Показывает выбор месяца или периода."""
    await state.update_data(
        selected_course_name=course_name,
        selected_group=group_code,
    )

    period_to_course = _build_period_to_course(course_name, group_code)
    if not period_to_course:
        return False

    keyboard = types.InlineKeyboardMarkup(row_width=1)
    group_info = _group_info_for_course_name(course_name, group_code)
    group_display = f"{group_code}"
    if len(group_info) > 2 and group_info[2]:
        group_display += f"\n📚 Направление: {group_info[2]}"

    for period_label, course_key in period_to_course:
        keyboard.add(
            types.InlineKeyboardButton(
                text=f"{period_label}"[:64],
                callback_data=f"show_schedule_{course_key}_{group_code}",
            )
        )

    keyboard.add(
        types.InlineKeyboardButton(
            text='👥 Выбрать другую группу',
            callback_data=f"course_{course_name}",
        )
    )
    keyboard.add(
        types.InlineKeyboardButton(
            text='🏠 В главное меню',
            callback_data='main_menu',
        )
    )

    await _safe_edit_text(
        callback_query.message,
        f"<b>✅ Вы выбрали:</b>\n"
        f"🎓 Курс: {html.escape(course_name)}\n"
        f"👥 Группа: {html.escape(group_display)}\n\n"
        f"🗓️ Теперь выберите месяц/период:",
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    return True

async def _render_week_view(callback_query: types.CallbackQuery, state: FSMContext, week_index: int | None = None):
    """Показывает страницу недели."""
    user_data = await state.get_data()
    course = user_data.get('selected_course')
    group_code = user_data.get('selected_group')

    if not course or not group_code:
        await callback_query.answer('⚠️ Сначала выберите курс, группу и месяц', show_alert=True)
        return

    weeks = _get_available_weeks(course, group_code)
    if not weeks:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    if week_index is None:
        week_index = user_data.get('selected_week_index', 0)

    week_index = week_index % len(weeks)
    week_label = weeks[week_index]
    week_days = _build_week_day_items(course, group_code, week_label)

    await state.update_data(selected_week_index=week_index)

    text = _build_week_overview_text(course, group_code, week_label, week_days)
    keyboard = _build_week_keyboard(week_label, week_days)

    await _safe_edit_text(callback_query.message, text, reply_markup=keyboard, parse_mode='HTML')
    await callback_query.answer()


async def _render_day_view(callback_query: types.CallbackQuery, state: FSMContext, day_index: int | None = None):
    """Показывает страницу дня."""
    user_data = await state.get_data()
    course = user_data.get('selected_course')
    group_code = user_data.get('selected_group')

    if not course or not group_code:
        await callback_query.answer('⚠️ Сначала выберите курс, группу и месяц', show_alert=True)
        return

    weeks = _get_available_weeks(course, group_code)
    if not weeks:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    week_index = user_data.get('selected_week_index', 0) % len(weeks)
    week_label = weeks[week_index]
    week_days = _build_week_day_items(course, group_code, week_label)

    if not week_days:
        await callback_query.answer('⚠️ Неделя не содержит дней', show_alert=True)
        return

    if day_index is None:
        day_index = user_data.get('selected_day_index')

    if day_index is None:
        today_index = next((idx for idx, item in enumerate(week_days) if item['is_today']), None)
        day_index = today_index if today_index is not None else 0

    day_index = day_index % len(week_days)
    day_item = week_days[day_index]

    week_schedule = schedule_data.get(course, {}).get(group_code, {}).get(week_label, [])
    lessons = _get_day_lessons(week_schedule, day_item['day_name'])

    await state.update_data(selected_week_index=week_index, selected_day_index=day_index)

    header_day = f"{day_item['date_short']} ({day_item['day_title']})"
    if day_item['is_today']:
        header_day += ' | Сегодня'

    month_week_number = _month_week_number_text(week_days)
    week_range = _week_date_range_text(week_days)

    lines = [
        f"🎓 Курс: <b>{html.escape(_course_name(course))}</b>",
        f"👥 Группа: <b>{html.escape(group_code)}</b>",
        f"📅 День: <b>{html.escape(header_day)}</b>",
        f"🗓️ Неделя месяца: <b>{month_week_number}</b> ({week_range})",
        '',
    ]

    if not lessons:
        lines.append('😌 Свободный день: занятий нет')
    else:
        for lesson in lessons:
            time_text = html.escape(_normalize_cell_text(lesson.get('time')) or 'Время не указано')
            lesson_text = html.escape(_normalize_cell_text(lesson.get('lesson')) or 'Без названия')
            if len(lesson_text) > 180:
                lesson_text = f"{lesson_text[:177]}..."
            lines.append(f"• ⏰ <b>{time_text}</b> {lesson_text}")

    keyboard = _build_day_keyboard(week_days, day_index)

    await _safe_edit_text(
        callback_query.message,
        '\n'.join(lines),
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    await callback_query.answer()

async def _render_week_picker(callback_query: types.CallbackQuery, state: FSMContext):
    """Показывает список недель."""
    user_data = await state.get_data()
    course = user_data.get('selected_course')
    group_code = user_data.get('selected_group')

    if not course or not group_code:
        await callback_query.answer('⚠️ Сначала выберите курс, группу и месяц', show_alert=True)
        return

    weeks = _get_available_weeks(course, group_code)
    if not weeks:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    current_week_index = user_data.get('selected_week_index', 0) % len(weeks)
    keyboard = _build_week_picker_keyboard(course, group_code, weeks, current_week_index)

    lines = [
        '<b>🗂️ Выбор недели</b>',
        f"🎓 Курс: <b>{html.escape(_course_name(course))}</b>",
        f"👥 Группа: <b>{html.escape(group_code)}</b>",
        '',
    ]


    await _safe_edit_text(
        callback_query.message,
        '\n'.join(lines),
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    await callback_query.answer()

async def process_group_choice(callback_query: types.CallbackQuery, state: FSMContext):
    """Обрабатывает выбор группы."""
    payload = callback_query.data[len('group_'):]
    if '_' not in payload:
        await callback_query.answer('⚠️ Некорректные данные группы', show_alert=True)
        return

    course_name, group_code = payload.rsplit('_', 1)
    period_to_course = _build_period_to_course(course_name, group_code)
    if not period_to_course:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    selected_course = None
    selected_week_index = 0

    for _, course_key in period_to_course:
        weeks = _get_available_weeks(course_key, group_code)
        if not weeks:
            continue
        if _period_has_current_month(course_key, group_code, weeks):
            selected_course = course_key
            selected_week_index = _pick_initial_week_index(course_key, group_code, weeks)
            break

    if selected_course is None:
        selected_course = period_to_course[0][1]
        weeks = _get_available_weeks(selected_course, group_code)
        if weeks:
            selected_week_index = _pick_initial_week_index(selected_course, group_code, weeks)

    await state.update_data(
        selected_course_name=course_name,
        selected_course=selected_course,
        selected_group=group_code,
        selected_week_index=selected_week_index,
        selected_day_index=None,
    )
    await _render_week_view(callback_query, state, selected_week_index)

async def show_schedule_day(callback_query: types.CallbackQuery, state: FSMContext):
    """Точка входа в расписание."""
    callback_data = callback_query.data

    course = None
    group_code = None
    target_week_label = None
    target_day_index = None
    open_day_view = False
    use_auto_week = False

    if callback_data.startswith('show_schedule_'):
        payload = callback_data[len('show_schedule_'):]
        if '_' in payload:
            course, group_code = payload.rsplit('_', 1)
            use_auto_week = True
    elif callback_data.startswith('show_day_'):
        payload = callback_data[len('show_day_'):]
        parts = payload.rsplit('_', 2)
        if len(parts) == 3 and parts[1].isdigit():
            course_group, day_token, week_token = parts
            if '_' in course_group:
                course, group_code = course_group.rsplit('_', 1)
                target_day_index = int(day_token)
                target_week_label = week_token
                open_day_view = True
        elif len(parts) == 2 and parts[1].isdigit():
            course_group, day_token = parts
            if '_' in course_group:
                course, group_code = course_group.rsplit('_', 1)
                target_day_index = int(day_token)
                open_day_view = True
    elif callback_data.startswith('show_week_'):
        payload = callback_data[len('show_week_'):]
        if '_' in payload:
            course_group, target_week_label = payload.rsplit('_', 1)
            if '_' in course_group:
                course, group_code = course_group.rsplit('_', 1)

    if not course or not group_code:
        await callback_query.answer('⚠️ Сначала выберите курс, группу и месяц', show_alert=True)
        return

    weeks = _get_available_weeks(course, group_code)
    if not weeks:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    if target_week_label and target_week_label in weeks:
        week_index = weeks.index(target_week_label)
    elif use_auto_week:
        week_index = _pick_initial_week_index(course, group_code, weeks)
    else:
        user_data = await state.get_data()
        week_index = user_data.get('selected_week_index', 0) % len(weeks)

    await state.update_data(
        selected_course=course,
        selected_course_name=_course_name(course),
        selected_group=group_code,
        selected_week_index=week_index,
    )

    if open_day_view:
        await _render_day_view(callback_query, state, target_day_index)
    else:
        await _render_week_view(callback_query, state, week_index)


async def calendar_shift_week(callback_query: types.CallbackQuery, state: FSMContext):
    """Переключает неделю вперед или назад, включая переход между месяцами."""
    try:
        delta = int(callback_query.data.split('_', 2)[2])
    except (ValueError, IndexError):
        await callback_query.answer('⚠️ Некорректный шаг недели', show_alert=True)
        return

    user_data = await state.get_data()
    course = user_data.get('selected_course')
    group_code = user_data.get('selected_group')
    course_name = user_data.get('selected_course_name')

    if not course or not group_code:
        await callback_query.answer('⚠️ Сначала выберите курс, группу и месяц', show_alert=True)
        return

    if not course_name:
        course_name = _course_name(course)

    weeks = _get_available_weeks(course, group_code)
    if not weeks:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    current_week_index = user_data.get('selected_week_index', 0) % len(weeks)
    current_week_label = weeks[current_week_index]

    timeline = _build_group_week_timeline(course_name, group_code)
    if not timeline:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    current_timeline_index = next(
        (
            idx
            for idx, item in enumerate(timeline)
            if item['course'] == course and item['week_label'] == current_week_label
        ),
        None,
    )

    if current_timeline_index is None:
        current_timeline_index = 0

    target_timeline_index = current_timeline_index + delta

    if target_timeline_index < 0:
        await callback_query.answer('📭 Предыдущих недель больше нет.', show_alert=True)
        return

    if target_timeline_index >= len(timeline):
        await callback_query.answer(
            '📭 Следующих недель нет. Как они появятся, мы вам обязательно сообщим.',
            show_alert=True,
        )
        return

    target = timeline[target_timeline_index]

    await state.update_data(
        selected_course_name=course_name,
        selected_course=target['course'],
        selected_group=group_code,
        selected_week_index=target['week_index'],
        selected_day_index=None,
    )

    await _render_week_view(callback_query, state, target['week_index'])

async def calendar_open_day(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает расписание дня со сквозной навигацией между неделями и месяцами."""
    try:
        day_index = int(callback_query.data.split('_', 2)[2])
    except (ValueError, IndexError):
        await callback_query.answer('⚠️ Некорректный день', show_alert=True)
        return

    user_data = await state.get_data()
    course = user_data.get('selected_course')
    group_code = user_data.get('selected_group')

    if not course or not group_code:
        await callback_query.answer('⚠️ Сначала выберите курс, группу и месяц', show_alert=True)
        return

    weeks = _get_available_weeks(course, group_code)
    if not weeks:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    current_week_index = user_data.get('selected_week_index', 0) % len(weeks)
    current_week_label = weeks[current_week_index]
    current_week_days = _build_week_day_items(course, group_code, current_week_label)

    if not current_week_days:
        await callback_query.answer('⚠️ Неделя не содержит дней', show_alert=True)
        return

    if 0 <= day_index < len(current_week_days):
        await _render_day_view(callback_query, state, day_index)
        return

    direction = -1 if day_index < 0 else 1
    course_name = user_data.get('selected_course_name') or _course_name(course)
    timeline = _build_group_week_timeline(course_name, group_code)

    if not timeline:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    current_timeline_index = next(
        (
            idx
            for idx, item in enumerate(timeline)
            if item['course'] == course and item['week_label'] == current_week_label
        ),
        None,
    )

    if current_timeline_index is None:
        current_timeline_index = 0

    target_timeline_index = current_timeline_index + direction

    while 0 <= target_timeline_index < len(timeline):
        target = timeline[target_timeline_index]
        target_week_days = _build_week_day_items(target['course'], group_code, target['week_label'])

        if target_week_days:
            target_day_index = len(target_week_days) - 1 if direction < 0 else 0

            await state.update_data(
                selected_course_name=course_name,
                selected_course=target['course'],
                selected_group=group_code,
                selected_week_index=target['week_index'],
                selected_day_index=target_day_index,
            )

            await _render_day_view(callback_query, state, target_day_index)
            return

        target_timeline_index += direction

    if direction < 0:
        await callback_query.answer('📭 Предыдущих дней больше нет.', show_alert=True)
        return

    await callback_query.answer(
        '📭 Следующих дней нет. Как они появятся, мы вам обязательно сообщим.',
        show_alert=True,
    )



async def calendar_shift_month_in_week_picker(callback_query: types.CallbackQuery, state: FSMContext):
    """Листает месяцы на экране выбора недели."""
    try:
        delta = int(callback_query.data.split('_', 3)[3])
    except (ValueError, IndexError):
        await callback_query.answer('⚠️ Некорректное направление', show_alert=True)
        return

    user_data = await state.get_data()
    selected_course = user_data.get('selected_course')
    group_code = user_data.get('selected_group')
    course_name = user_data.get('selected_course_name')

    if not selected_course or not group_code:
        await callback_query.answer('⚠️ Сначала выберите курс, группу и месяц', show_alert=True)
        return

    if not course_name:
        course_name = _course_name(selected_course)

    period_to_course = _build_period_to_course(course_name, group_code)
    if not period_to_course:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    course_keys = [course_key for _, course_key in period_to_course]
    if selected_course in course_keys:
        current_index = course_keys.index(selected_course)
    else:
        current_index = 0

    target_index = current_index + delta
    if target_index < 0 or target_index >= len(course_keys):
        await callback_query.answer('📭 Больше месяцев нет', show_alert=True)
        return

    target_course = course_keys[target_index]
    weeks = _get_available_weeks(target_course, group_code)
    week_index = _pick_initial_week_index(target_course, group_code, weeks) if weeks else 0

    await state.update_data(
        selected_course_name=course_name,
        selected_course=target_course,
        selected_group=group_code,
        selected_week_index=week_index,
        selected_day_index=None,
    )

    await _render_week_picker(callback_query, state)



async def calendar_open_current_week(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает неделю по сегодняшней дате для текущей группы."""
    user_data = await state.get_data()
    course = user_data.get('selected_course')
    group_code = user_data.get('selected_group')
    course_name = user_data.get('selected_course_name')

    if not course or not group_code:
        await callback_query.answer('⚠️ Сначала выберите курс, группу и месяц', show_alert=True)
        return

    if not course_name:
        course_name = _course_name(course)

    target_week = _pick_today_week_for_group(course_name, group_code)
    if target_week is None:
        await callback_query.answer('📭 Для этой группы нет доступных недель', show_alert=True)
        return

    target_course, week_index = target_week

    await state.update_data(
        selected_course_name=course_name,
        selected_course=target_course,
        selected_group=group_code,
        selected_week_index=week_index,
        selected_day_index=None,
    )
    await _render_week_view(callback_query, state, week_index)

async def calendar_pick_week(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает выбор недели."""
    await _render_week_picker(callback_query, state)


async def calendar_choose_week(callback_query: types.CallbackQuery, state: FSMContext):
    """Выбирает неделю из списка."""
    try:
        week_index = int(callback_query.data.split('_', 2)[2])
    except (ValueError, IndexError):
        await callback_query.answer('⚠️ Некорректный индекс недели', show_alert=True)
        return

    await state.update_data(selected_week_index=week_index)
    await _render_week_view(callback_query, state, week_index)


async def calendar_back_week(callback_query: types.CallbackQuery, state: FSMContext):
    """Возвращает к неделе."""
    user_data = await state.get_data()
    week_index = user_data.get('selected_week_index', 0)
    await _render_week_view(callback_query, state, week_index)


async def calendar_pick_month(callback_query: types.CallbackQuery, state: FSMContext):
    """Возвращает к выбору месяца."""
    user_data = await state.get_data()
    course_name = user_data.get('selected_course_name')
    selected_course = user_data.get('selected_course')
    group_code = user_data.get('selected_group')

    if not course_name and selected_course:
        course_name = _course_name(selected_course)

    if not course_name or not group_code:
        await callback_query.answer('⚠️ Сначала выберите курс и группу', show_alert=True)
        return

    rendered = await _render_period_picker(callback_query, state, course_name, group_code)
    if not rendered:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    await callback_query.answer()


async def calendar_noop(callback_query: types.CallbackQuery):
    """Служебная кнопка."""
    await callback_query.answer()


async def choose_day(callback_query: types.CallbackQuery, state: FSMContext):
    """Обрабатывает старый choose_day callback."""
    payload = callback_query.data[len('choose_day_'):]
    parts = payload.rsplit('_', 2)
    if len(parts) < 2:
        await callback_query.answer('⚠️ Некорректные данные выбора дня', show_alert=True)
        return

    if len(parts) == 3:
        course_group, _, target_week_label = parts
    else:
        course_group, _ = parts
        target_week_label = None

    if '_' not in course_group:
        await callback_query.answer('⚠️ Некорректные данные курса и группы', show_alert=True)
        return

    course, group_code = course_group.rsplit('_', 1)

    weeks = _get_available_weeks(course, group_code)
    if not weeks:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    week_index = 0
    if target_week_label and target_week_label in weeks:
        week_index = weeks.index(target_week_label)

    await state.update_data(
        selected_course=course,
        selected_course_name=_course_name(course),
        selected_group=group_code,
        selected_week_index=week_index,
    )

    await _render_week_view(callback_query, state, week_index)


async def show_week(callback_query: types.CallbackQuery, state: FSMContext):
    """Обрабатывает старый show_week callback."""
    await show_schedule_day(callback_query, state)


async def refresh_schedule(callback_query: types.CallbackQuery, state: FSMContext):
    """Перезагружает расписание."""
    payload = callback_query.data[len('refresh_'):]
    course = None
    group_code = None

    if '_' in payload:
        course, group_code = payload.rsplit('_', 1)

    user_data = await state.get_data()
    course = course or user_data.get('selected_course')
    group_code = group_code or user_data.get('selected_group')

    await _safe_edit_text(callback_query.message, '🔄 Обновляю расписание...', parse_mode='HTML')

    init_schedule()

    if course and group_code and course in schedule_data and group_code in schedule_data.get(course, {}):
        await state.update_data(selected_course=course, selected_group=group_code, selected_course_name=_course_name(course))
        callback_query.data = f"show_schedule_{course}_{group_code}"
        await show_schedule_day(callback_query, state)
        await callback_query.answer('✅ Расписание обновлено')
        return

    await callback_query.answer('✅ Расписание обновлено, выберите месяц заново', show_alert=True)


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
            f"<b>📋 Ваше текущее расписание:</b>\n"
            f"<b>🎓 Курс:</b> {_course_display(course)}\n"
            f"<b>👥 Группа:</b> {group_display}\n\n"
            "👇 Нажмите кнопку ниже, чтобы открыть расписание.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "ℹ️ Вы еще не выбрали группу. Давайте выберем её сейчас:"
        )
        await choose_course(message)

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
    dp.register_callback_query_handler(show_week, Text(startswith="show_week_"))
    dp.register_callback_query_handler(choose_day, Text(startswith="choose_day_"))

    dp.register_callback_query_handler(calendar_shift_week, Text(startswith="cal_shift_"))
    dp.register_callback_query_handler(calendar_open_day, Text(startswith="cal_day_"))

    dp.register_callback_query_handler(calendar_pick_week, Text(equals="cal_pick_week"))
    dp.register_callback_query_handler(calendar_open_current_week, Text(equals="cal_current_week"))
    dp.register_callback_query_handler(calendar_shift_month_in_week_picker, Text(startswith="cal_month_shift_"))

    dp.register_callback_query_handler(calendar_choose_week, Text(startswith="cal_weekpick_"))
    dp.register_callback_query_handler(calendar_back_week, Text(equals="cal_back_week"))
    dp.register_callback_query_handler(calendar_pick_month, Text(equals="cal_pick_month"))
    dp.register_callback_query_handler(calendar_noop, Text(equals="cal_noop"))

    dp.register_callback_query_handler(refresh_schedule, Text(startswith="refresh_"))
    dp.register_callback_query_handler(back_to_courses, Text(startswith="back_to_courses"))
    dp.register_callback_query_handler(main_menu, Text(startswith="main_menu"))
    dp.register_callback_query_handler(change_group, Text(startswith="change_group"))

