import asyncio
import logging
import re
import secrets
from datetime import date, datetime

from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Text
from aiogram.utils import executor

from create_bot import bot, dp
from db_api import db_commands
from db_api.database import create_base
from handlers import client
from muiv_schedule_monitor import POLL_SECONDS, check_for_schedule_updates

monitor_task = None

DEFAULT_TIME_SLOTS = [
    '8:20-9:50',
    '10:00-11:30',
    '11:40-13:10',
    '13:45-15:15',
    '15:25-16:55',
    '17:05-18:35',
]

WEEKDAY_NAMES = [
    'понедельник',
    'вторник',
    'среда',
    'четверг',
    'пятница',
    'суббота',
    'воскресенье',
]

EMPTY_DATE_MARKERS = {'', 'не указана', 'дата не указана', 'none', 'null'}
MONITOR_DIFF_CACHE = {}
MONITOR_OPEN_CACHE = {}
MONITOR_CACHE_LIMIT = 4000


def setup_handlers(dispatcher):
    """Регистрирует обработчики бота."""
    client.register_handlers_client(dispatcher)
    dispatcher.register_callback_query_handler(monitor_show_change_details, Text(startswith='mon_diff_'))
    dispatcher.register_callback_query_handler(monitor_back_to_change_summary, Text(startswith='mon_back_'))
    dispatcher.register_callback_query_handler(monitor_open_schedule, Text(startswith='mon_open_'))


def _normalize_text(value) -> str:
    """Преобразует значение в строку и убирает пробелы."""
    if value is None:
        return ''
    return str(value).strip()


def _course_name_from_key(course_key: str) -> str:
    """Извлекает имя курса из технического ключа."""
    key = _normalize_text(course_key)
    if ':' in key:
        return key.split(':', 1)[1].strip()
    return key


def _parse_schedule_date(date_text: str) -> date | None:
    """Преобразует текст даты в date."""
    value = _normalize_text(date_text)
    if not value:
        return None

    for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%d.%m.%y'):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_date_text(date_text: str) -> str:
    """Нормализует дату к формату YYYY-MM-DD, если это возможно."""
    value = _normalize_text(date_text)
    if not value or value.lower() in EMPTY_DATE_MARKERS:
        return ''

    parsed = _parse_schedule_date(value)
    if parsed is not None:
        return parsed.strftime('%Y-%m-%d')
    return value


def _normalize_day_name(day_name: str) -> str:
    """Нормализует название дня недели."""
    return _normalize_text(day_name).lower()


def _normalize_time_slot(time_text: str) -> str:
    """Нормализует время пары в единый формат."""
    value = _normalize_text(time_text).lower()
    if not value:
        return ''

    value = value.replace('—', '-').replace('–', '-').replace('.', ':')
    value = re.sub(r'\s+', '', value)
    match = re.match(r'^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$', value)
    if not match:
        return value

    start_hour = int(match.group(1))
    end_hour = int(match.group(3))
    return f'{start_hour}:{match.group(2)}-{end_hour}:{match.group(4)}'


def _time_sort_key(time_text: str):
    """Возвращает ключ сортировки для времени пары."""
    value = _normalize_time_slot(time_text)
    match = re.match(r'^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$', value)
    if match:
        return (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            int(match.group(4)),
            value,
        )
    return (99, 99, 99, 99, value)


def _clean_lesson_text(lesson_text: str) -> str:
    """Очищает текст занятия от лишних пробелов и переносов."""
    value = _normalize_text(lesson_text)
    if not value:
        return ''
    return re.sub(r'\s+', ' ', value)


def _week_sort_key(week_label: str):
    """Возвращает стабильный ключ сортировки недель."""
    value = _normalize_text(week_label)
    match = re.search(r'\d+', value)
    if match:
        return int(match.group(0)), value
    return 10**9, value


def _date_sort_key(date_text: str):
    """Возвращает ключ сортировки дат."""
    parsed = _parse_schedule_date(date_text)
    return (parsed is None, parsed or date.max, _normalize_text(date_text))


def _format_date_with_weekday(date_text: str, fallback_day_name: str | None = None) -> str:
    """Форматирует дату вида DD.MM.YY (день недели)."""
    parsed = _parse_schedule_date(date_text)
    if parsed is not None:
        return f"{parsed.strftime('%d.%m.%y')} ({WEEKDAY_NAMES[parsed.weekday()]})"

    fallback = _normalize_day_name(fallback_day_name)
    if fallback:
        return f'{date_text} ({fallback})'
    return _normalize_text(date_text)


def _cache_put(cache: dict, payload: dict) -> str:
    """Сохраняет payload в кэш и возвращает короткий токен."""
    token = secrets.token_hex(6)
    while token in cache:
        token = secrets.token_hex(6)

    cache[token] = payload

    while len(cache) > MONITOR_CACHE_LIMIT:
        first_key = next(iter(cache))
        cache.pop(first_key, None)

    return token


def _store_open_callback(course_key: str, group_code: str, week_label: str | None) -> str | None:
    """Сохраняет данные перехода к расписанию и возвращает callback_data."""
    course_value = _normalize_text(course_key)
    group_value = _normalize_text(group_code)
    if not course_value or not group_value:
        return None

    token = _cache_put(
        MONITOR_OPEN_CACHE,
        {
            'course': course_value,
            'group_code': group_value,
            'week_label': _normalize_text(week_label) or None,
        },
    )
    return f'mon_open_{token}'


def _store_diff_callback(
    diff_text: str,
    summary_text: str,
    open_callback_data: str | None,
) -> str:
    """Сохраняет подробности изменения дня и возвращает callback_data."""
    token = _cache_put(
        MONITOR_DIFF_CACHE,
        {
            'text': _normalize_text(diff_text),
            'summary_text': _normalize_text(summary_text),
            'open_callback_data': open_callback_data,
        },
    )
    return f'mon_diff_{token}'


async def monitor_show_change_details(callback_query: types.CallbackQuery):
    """Показывает подробности изменений по кнопке уведомления."""
    token = callback_query.data[len('mon_diff_'):]
    payload = MONITOR_DIFF_CACHE.get(token)
    if not payload:
        await callback_query.answer('Подробности изменения уже недоступны.', show_alert=True)
        return

    detail_text = payload.get('text') or 'Нет подробностей по этому изменению.'
    open_callback_data = payload.get('open_callback_data')
    back_callback_data = f"mon_back_{token}"

    keyboard = None
    if open_callback_data:
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        keyboard.row(
            types.InlineKeyboardButton(text='⬅️ Назад', callback_data=back_callback_data),
            types.InlineKeyboardButton(text='📅 Показать расписание', callback_data=open_callback_data)
        )
    else:
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        keyboard.add(types.InlineKeyboardButton(text='⬅️ Назад', callback_data=back_callback_data))

    try:
        await callback_query.message.edit_text(detail_text, reply_markup=keyboard)
        await callback_query.answer()
    except Exception as send_error:
        await callback_query.answer('Не удалось показать подробности изменения.', show_alert=True)
        print(f'WARN: cannot send change details: {send_error}')


async def monitor_back_to_change_summary(callback_query: types.CallbackQuery):
    """Возвращает из подробностей к сообщению об изменении дня."""
    token = callback_query.data[len('mon_back_'):]
    payload = MONITOR_DIFF_CACHE.get(token)
    if not payload:
        await callback_query.answer('Сообщение об изменении уже недоступно.', show_alert=True)
        return

    summary_text = payload.get('summary_text') or 'Сообщение об изменении недоступно.'
    open_callback_data = payload.get('open_callback_data')
    diff_callback_data = f'mon_diff_{token}'

    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.row(
        types.InlineKeyboardButton(text='🔍 Что изменилось', callback_data=diff_callback_data),
        types.InlineKeyboardButton(
            text='📅 Показать расписание',
            callback_data=open_callback_data or 'cal_noop',
        ),
    )

    try:
        await callback_query.message.edit_text(summary_text, reply_markup=keyboard)
        await callback_query.answer()
    except Exception as send_error:
        await callback_query.answer('Не удалось вернуть сообщение об изменении.', show_alert=True)
        print(f'WARN: cannot restore changed day summary: {send_error}')


async def monitor_open_schedule(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает расписание по кнопке из уведомления мониторинга."""
    token = callback_query.data[len('mon_open_'):]
    payload = MONITOR_OPEN_CACHE.get(token)
    if not payload:
        await callback_query.answer('Ссылка устарела. Откройте расписание через меню.', show_alert=True)
        return

    course = payload.get('course')
    group_code = payload.get('group_code')
    week_label = payload.get('week_label')

    if not course or not group_code:
        await callback_query.answer('Недостаточно данных для открытия расписания.', show_alert=True)
        return

    if week_label:
        callback_query.data = f'show_week_{course}_{group_code}_{week_label}'
    else:
        callback_query.data = f'show_schedule_{course}_{group_code}'

    await client.show_schedule_day(callback_query, state)


def _ensure_day_state(group_days: dict, date_text: str, course_key: str, week_label: str, day_name: str) -> dict:
    """Создает или обновляет структуру хранения дня."""
    day_state = group_days.get(date_text)
    if day_state is None:
        day_state = {
            'course': _normalize_text(course_key),
            'week_label': _normalize_text(week_label),
            'day_name': _normalize_day_name(day_name),
            'lessons': {},
            'signatures': set(),
        }
        group_days[date_text] = day_state
        return day_state

    if not day_state.get('course'):
        day_state['course'] = _normalize_text(course_key)
    if not day_state.get('week_label'):
        day_state['week_label'] = _normalize_text(week_label)
    if not day_state.get('day_name'):
        day_state['day_name'] = _normalize_day_name(day_name)
    return day_state


def _build_group_day_snapshot():
    """Строит snapshot по группам и датам для сравнения изменений."""
    snapshot = {}

    for course_key in sorted(client.schedule_data.keys()):
        course_name = _course_name_from_key(course_key)
        groups = client.schedule_data.get(course_key, {})

        for group_code in sorted(groups.keys()):
            weeks = groups.get(group_code, {})
            group_days = snapshot.setdefault(group_code, {})

            for week_label in sorted(weeks.keys(), key=_week_sort_key):
                entries = weeks.get(week_label, [])
                week_days_map = client.week_days_info.get(course_key, {}).get(week_label, {}) or {}
                day_to_date = {}

                for day_name, date_value in week_days_map.items():
                    normalized_day = _normalize_day_name(day_name)
                    normalized_date = _normalize_date_text(date_value)
                    if not normalized_day or not normalized_date:
                        continue

                    day_to_date[normalized_day] = normalized_date
                    day_state = _ensure_day_state(
                        group_days=group_days,
                        date_text=normalized_date,
                        course_key=course_key,
                        week_label=week_label,
                        day_name=normalized_day,
                    )
                    day_state['signatures'].add(
                        f"{course_name}|{_normalize_text(week_label)}|{normalized_day}|DAY_MARKER"
                    )

                for entry in sorted(entries, key=lambda item: _time_sort_key(item.get('time'))):
                    day_name = _normalize_day_name(entry.get('day'))
                    date_text = _normalize_date_text(entry.get('date'))
                    if not date_text and day_name:
                        date_text = day_to_date.get(day_name, '')
                    if not date_text:
                        continue

                    time_text = _normalize_time_slot(entry.get('time'))
                    lesson_text = _clean_lesson_text(entry.get('lesson'))

                    day_state = _ensure_day_state(
                        group_days=group_days,
                        date_text=date_text,
                        course_key=course_key,
                        week_label=week_label,
                        day_name=day_name,
                    )

                    if time_text:
                        day_state['lessons'][time_text] = lesson_text

                    day_state['signatures'].add(
                        f"{course_name}|{_normalize_text(week_label)}|{day_name}|{time_text}|{lesson_text}"
                    )

    normalized_snapshot = {}
    for group_code, days in snapshot.items():
        normalized_snapshot[group_code] = {}
        for date_key, day_state in days.items():
            normalized_snapshot[group_code][date_key] = {
                'course': day_state.get('course'),
                'week_label': day_state.get('week_label'),
                'day_name': day_state.get('day_name'),
                'lessons': dict(
                    sorted(day_state.get('lessons', {}).items(), key=lambda item: _time_sort_key(item[0]))
                ),
                'signature': tuple(sorted(day_state.get('signatures', set()))),
            }

    return normalized_snapshot


def _collect_changed_group_days(old_snapshot, new_snapshot):
    """Возвращает изменившиеся и новые даты расписания по группам."""
    changed = {}

    all_groups = set(old_snapshot.keys()) | set(new_snapshot.keys())
    for group_code in all_groups:
        old_days = old_snapshot.get(group_code, {})
        new_days = new_snapshot.get(group_code, {})

        changed_dates = []
        new_dates = []

        for date_key in sorted(set(old_days.keys()) | set(new_days.keys()), key=_date_sort_key):
            old_day = old_days.get(date_key)
            new_day = new_days.get(date_key)

            if old_day is None and new_day is not None:
                new_dates.append(date_key)
                continue

            if new_day is None:
                changed_dates.append(date_key)
                continue

            if old_day.get('signature') != new_day.get('signature'):
                changed_dates.append(date_key)

        if changed_dates or new_dates:
            changed[group_code] = {
                'changed_dates': changed_dates,
                'new_dates': new_dates,
            }

    return changed


def _lesson_or_cross(lesson_text: str) -> str:
    """Возвращает текст занятия или символ отсутствия пары."""
    value = _clean_lesson_text(lesson_text)
    return value if value else '❌'


def _build_changed_day_message(group_code: str, date_text: str, day_state: dict | None) -> str:
    """Формирует уведомление об измененном дне."""
    day_name = (day_state or {}).get('day_name')
    date_caption = _format_date_with_weekday(date_text, day_name)
    lessons = (day_state or {}).get('lessons', {})

    lines = [
        f'👤 │ {group_code}',
        '',
        f'🔁 Изменилось расписание на {date_caption}:',
        '',
    ]

    pair_number = 1
    for time_slot in DEFAULT_TIME_SLOTS:
        lesson_value = _lesson_or_cross(lessons.get(time_slot))
        lines.append(f'{pair_number}. {time_slot} : {lesson_value}')
        pair_number += 1

    extra_time_slots = [slot for slot in lessons.keys() if slot not in DEFAULT_TIME_SLOTS]
    for time_slot in sorted(extra_time_slots, key=_time_sort_key):
        lesson_value = _lesson_or_cross(lessons.get(time_slot))
        lines.append(f'{pair_number}. {time_slot} : {lesson_value}')
        pair_number += 1

    return '\n'.join(lines)


def _build_changed_day_diff_message(group_code: str, date_text: str, old_day: dict | None, new_day: dict | None) -> str:
    """Формирует текст подробностей изменений по конкретному дню."""
    day_name = (new_day or old_day or {}).get('day_name')
    date_caption = _format_date_with_weekday(date_text, day_name)
    old_lessons = (old_day or {}).get('lessons', {})
    new_lessons = (new_day or {}).get('lessons', {})

    lines = [
        f'👤 │ {group_code}',
        '',
        f'🔎 Что изменилось на {date_caption}:',
        '',
    ]

    change_found = False
    all_slots = list(DEFAULT_TIME_SLOTS)
    extra_slots = sorted(
        (set(old_lessons.keys()) | set(new_lessons.keys())) - set(DEFAULT_TIME_SLOTS),
        key=_time_sort_key,
    )
    all_slots.extend(extra_slots)

    for time_slot in all_slots:
        old_text = _lesson_or_cross(old_lessons.get(time_slot))
        new_text = _lesson_or_cross(new_lessons.get(time_slot))
        if old_text == new_text:
            continue

        change_found = True
        lines.append(f'• {time_slot}: {old_text} → {new_text}')

    if not change_found:
        lines.append('• Изменились служебные данные дня, содержимое пар не изменилось.')

    return '\n'.join(lines)


def _build_new_dates_message(group_code: str, new_dates: list[str], new_snapshot: dict) -> str:
    """Формирует уведомление о появлении новых дат."""
    lines = ['❇️ Появилось расписание на новые даты:']
    group_days = new_snapshot.get(group_code, {})

    for date_text in new_dates:
        day_state = group_days.get(date_text, {})
        day_caption = _format_date_with_weekday(date_text, day_state.get('day_name'))
        lines.append(f'• {day_caption}')

    return '\n'.join(lines)


async def _notify_users_about_changes(changed_group_days, old_snapshot, new_snapshot):
    """Отправляет уведомления пользователям, для чьих групп есть изменения."""
    if not changed_group_days:
        return 0

    users = await db_commands.get_users_with_selected_groups(set(changed_group_days.keys()))
    if not users:
        return 0

    notified = 0
    for user in users:
        telegram_id = user.get('telegram_id')
        group_code = user.get('selected_group')
        group_changes = changed_group_days.get(group_code, {})
        changed_dates = group_changes.get('changed_dates', [])
        new_dates = group_changes.get('new_dates', [])

        if not telegram_id or not group_code or (not changed_dates and not new_dates):
            continue

        sent_any = False

        for date_text in changed_dates:
            old_day = old_snapshot.get(group_code, {}).get(date_text)
            new_day = new_snapshot.get(group_code, {}).get(date_text)
            display_day = new_day or {'day_name': (old_day or {}).get('day_name'), 'lessons': {}}

            open_source_day = new_day or old_day or {}
            open_callback_data = _store_open_callback(
                course_key=open_source_day.get('course'),
                group_code=group_code,
                week_label=open_source_day.get('week_label'),
            )

            diff_text = _build_changed_day_diff_message(
                group_code=group_code,
                date_text=date_text,
                old_day=old_day,
                new_day=new_day,
            )
            text = _build_changed_day_message(group_code, date_text, display_day)
            diff_callback_data = _store_diff_callback(diff_text, text, open_callback_data)

            keyboard = types.InlineKeyboardMarkup(row_width=2)
            keyboard.row(
                types.InlineKeyboardButton(text='🔍 Что изменилось', callback_data=diff_callback_data),
                types.InlineKeyboardButton(
                    text='📅 Показать расписание',
                    callback_data=open_callback_data or 'cal_noop',
                ),
            )

            try:
                await bot.send_message(telegram_id, text, reply_markup=keyboard)
                sent_any = True
            except Exception as send_error:
                print(f'WARN: cannot notify user {telegram_id} about changed day {date_text}: {send_error}')

        if new_dates:
            first_new_day = None
            for date_text in new_dates:
                day_state = new_snapshot.get(group_code, {}).get(date_text)
                if day_state:
                    first_new_day = day_state
                    break

            open_callback_data = None
            if first_new_day:
                open_callback_data = _store_open_callback(
                    course_key=first_new_day.get('course'),
                    group_code=group_code,
                    week_label=first_new_day.get('week_label'),
                )

            keyboard = types.InlineKeyboardMarkup(row_width=1)
            keyboard.add(
                types.InlineKeyboardButton(
                    text='📅 Показать расписание',
                    callback_data=open_callback_data or 'cal_noop',
                )
            )

            text = _build_new_dates_message(group_code, new_dates, new_snapshot)
            try:
                await bot.send_message(telegram_id, text, reply_markup=keyboard)
                sent_any = True
            except Exception as send_error:
                print(f'WARN: cannot notify user {telegram_id} about new dates: {send_error}')

        if sent_any:
            notified += 1

    return notified


async def _schedule_monitor_loop():
    """Фоновый цикл мониторинга сайта расписаний и синхронизации БД."""
    # Небольшая задержка, чтобы бот полностью поднялся.
    await asyncio.sleep(10)

    while True:
        try:
            report = await asyncio.to_thread(check_for_schedule_updates)

            if report.get('errors'):
                for error_text in report['errors']:
                    print(f'[MONITOR][ERR] {error_text}')

            has_file_changes = bool(report.get('new_files') or report.get('updated_files'))
            if has_file_changes:
                old_snapshot = _build_group_day_snapshot()

                client.init_schedule()
                db_stats = await client.persist_schedule_to_db()

                new_snapshot = _build_group_day_snapshot()
                changed_group_days = _collect_changed_group_days(old_snapshot, new_snapshot)
                notified = await _notify_users_about_changes(changed_group_days, old_snapshot, new_snapshot)

                print(
                    '[MONITOR] updates: new={new_count}, updated={updated_count}, '
                    'groups_changed={groups_changed}, notified={notified}, db_lessons={db_lessons}'.format(
                        new_count=len(report.get('new_files', [])),
                        updated_count=len(report.get('updated_files', [])),
                        groups_changed=len(changed_group_days),
                        notified=notified,
                        db_lessons=db_stats.get('lessons', 0),
                    )
                )

        except asyncio.CancelledError:
            raise
        except Exception as loop_error:
            print(f'[MONITOR][ERR] loop failed: {loop_error}')

        await asyncio.sleep(POLL_SECONDS)


async def on_startup(dispatcher):
    """Инициализация БД, расписания и фонового мониторинга при запуске."""
    global monitor_task

    await create_base()
    setup_handlers(dispatcher)

    print('Загрузка расписания из Excel...')
    client.init_schedule()

    total_courses = len(client.schedule_data)
    total_groups = sum(len(groups) for groups in client.schedule_data.values())
    print(f'Загружено расписание: {total_courses} курсов, {total_groups} групп')

    try:
        db_stats = await client.persist_schedule_to_db()
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

    if monitor_task is None or monitor_task.done():
        monitor_task = asyncio.create_task(_schedule_monitor_loop())
        print(f'✅ Монитор расписания запущен (интервал {POLL_SECONDS} сек)')

    print('Бот запущен!')


async def on_shutdown(dispatcher):
    """Останавливает фоновые задачи при завершении бота."""
    global monitor_task

    if monitor_task and not monitor_task.done():
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    executor.start_polling(dp, on_startup=on_startup, on_shutdown=on_shutdown)
