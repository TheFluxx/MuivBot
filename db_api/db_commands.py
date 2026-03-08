import re
from datetime import datetime
from typing import Any, Dict

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from db_api.database import get_session
from db_api.tables import (
    Users,
    UserScheduleState,
    UserDailyNotification,
    ScheduleCourse,
    ScheduleGroup,
    ScheduleLesson,
    SchedulePeriod,
    ScheduleWeek,
    ScheduleWeekDay,
)

DAY_ORDER_MAP = {
    'понедельник': 0,
    'вторник': 1,
    'среда': 2,
    'четверг': 3,
    'пятница': 4,
    'суббота': 5,
    'воскресенье': 6,
}

EMPTY_DATE_MARKERS = {'', 'не указана', 'none', 'null'}
DEFAULT_DAILY_DIGEST_ENABLED = True
DEFAULT_DAILY_DIGEST_HOUR = 20
DEFAULT_DAILY_DIGEST_MINUTE = 0
USER_SCHEDULE_FIELDS = (
    'selected_course',
    'selected_course_name',
    'selected_group',
    'selected_week_index',
    'selected_day_index',
    'daily_digest_enabled',
    'daily_digest_hour',
    'daily_digest_minute',
)


def _course_name_from_key(course_key: str) -> str:
    """Извлекает исходное имя курса из технического ключа."""
    if ':' in course_key:
        return course_key.split(':', 1)[1].strip()
    return course_key.strip()


def _week_sort_key(week_label: str) -> tuple[int, str]:
    """Возвращает стабильный ключ сортировки недель."""
    label = (week_label or '').strip()
    match = re.search(r'\d+', label)
    if match:
        return int(match.group(0)), label
    return 10**9, label


def _parse_iso_date(date_text: Any):
    """Преобразует текстовую дату формата YYYY-MM-DD в date."""
    if date_text is None:
        return None

    normalized = str(date_text).strip()
    if normalized.lower() in EMPTY_DATE_MARKERS:
        return None

    try:
        return datetime.strptime(normalized, '%Y-%m-%d').date()
    except ValueError:
        return None


def _normalize_daily_digest_enabled(value: Any) -> bool:
    """Нормализует флаг включения ежедневной рассылки."""
    if value is None:
        return DEFAULT_DAILY_DIGEST_ENABLED
    return bool(value)


def _normalize_daily_digest_hour(value: Any) -> int:
    """Нормализует час ежедневной рассылки."""
    try:
        hour = int(value)
    except (TypeError, ValueError):
        return DEFAULT_DAILY_DIGEST_HOUR
    return hour % 24


def _normalize_daily_digest_minute(value: Any) -> int:
    """Нормализует минуту ежедневной рассылки."""
    try:
        minute = int(value)
    except (TypeError, ValueError):
        return DEFAULT_DAILY_DIGEST_MINUTE
    return max(0, min(59, minute))


async def registration_check(telegram_id):
    async with get_session() as session:
        result = await session.execute(select(Users).filter_by(telegram_id=telegram_id))
        user = result.scalar_one_or_none()
        return user


async def register_user(telegram_id, username, referrer_id):
    async with get_session() as session:
        user = Users(
            telegram_id=telegram_id,
            username=username,
            referrer_id=referrer_id,
        )
        session.add(user)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()


async def get_user_schedule_state(telegram_id: int):
    """Возвращает сохраненные настройки расписания пользователя."""
    async with get_session() as session:
        result = await session.execute(
            select(UserScheduleState).where(UserScheduleState.telegram_id == telegram_id)
        )
        state_row = result.scalar_one_or_none()
        if state_row is None:
            return None

        return {
            'selected_course': state_row.selected_course,
            'selected_course_name': state_row.selected_course_name,
            'selected_group': state_row.selected_group,
            'selected_week_index': state_row.selected_week_index,
            'selected_day_index': state_row.selected_day_index,
            'daily_digest_enabled': _normalize_daily_digest_enabled(state_row.daily_digest_enabled),
            'daily_digest_hour': _normalize_daily_digest_hour(state_row.daily_digest_hour),
            'daily_digest_minute': _normalize_daily_digest_minute(state_row.daily_digest_minute),
        }


async def upsert_user_schedule_state(telegram_id: int, **kwargs):
    """Создает или обновляет сохраненные настройки расписания пользователя."""
    payload = {key: kwargs.get(key) for key in USER_SCHEDULE_FIELDS if key in kwargs}
    if not payload:
        return None

    if 'daily_digest_enabled' in payload:
        payload['daily_digest_enabled'] = _normalize_daily_digest_enabled(payload.get('daily_digest_enabled'))
    if 'daily_digest_hour' in payload:
        payload['daily_digest_hour'] = _normalize_daily_digest_hour(payload.get('daily_digest_hour'))
    if 'daily_digest_minute' in payload:
        payload['daily_digest_minute'] = _normalize_daily_digest_minute(payload.get('daily_digest_minute'))

    async with get_session() as session:
        result = await session.execute(
            select(UserScheduleState).where(UserScheduleState.telegram_id == telegram_id)
        )
        state_row = result.scalar_one_or_none()

        if state_row is None:
            state_row = UserScheduleState(telegram_id=telegram_id, **payload)
            session.add(state_row)
        else:
            for key, value in payload.items():
                setattr(state_row, key, value)
            state_row.updated_at = datetime.utcnow()

        await session.commit()

        return {
            'selected_course': state_row.selected_course,
            'selected_course_name': state_row.selected_course_name,
            'selected_group': state_row.selected_group,
            'selected_week_index': state_row.selected_week_index,
            'selected_day_index': state_row.selected_day_index,
            'daily_digest_enabled': _normalize_daily_digest_enabled(state_row.daily_digest_enabled),
            'daily_digest_hour': _normalize_daily_digest_hour(state_row.daily_digest_hour),
            'daily_digest_minute': _normalize_daily_digest_minute(state_row.daily_digest_minute),
        }



async def get_users_with_selected_groups(group_codes=None):
    """Возвращает пользователей с выбранной группой (опционально только для нужных групп)."""
    async with get_session() as session:
        query = select(
            UserScheduleState.telegram_id,
            UserScheduleState.selected_group,
            UserScheduleState.selected_course,
            UserScheduleState.selected_course_name,
            UserScheduleState.daily_digest_enabled,
            UserScheduleState.daily_digest_hour,
            UserScheduleState.daily_digest_minute,
        ).where(UserScheduleState.selected_group.is_not(None))

        if group_codes:
            normalized_groups = sorted({str(code).strip() for code in group_codes if code})
            if normalized_groups:
                query = query.where(UserScheduleState.selected_group.in_(normalized_groups))

        result = await session.execute(query)
        rows = result.all()

        users = []
        for row in rows:
            users.append(
                {
                    'telegram_id': row.telegram_id,
                    'selected_group': row.selected_group,
                    'selected_course': row.selected_course,
                    'selected_course_name': row.selected_course_name,
                    'daily_digest_enabled': _normalize_daily_digest_enabled(row.daily_digest_enabled),
                    'daily_digest_hour': _normalize_daily_digest_hour(row.daily_digest_hour),
                    'daily_digest_minute': _normalize_daily_digest_minute(row.daily_digest_minute),
                }
            )
        return users


async def was_daily_notification_sent(telegram_id: int, notification_type: str, target_date):
    """Проверяет, отправлялась ли уже ежедневная рассылка пользователю на дату."""
    async with get_session() as session:
        result = await session.execute(
            select(UserDailyNotification.id).where(
                UserDailyNotification.telegram_id == telegram_id,
                UserDailyNotification.notification_type == str(notification_type).strip(),
                UserDailyNotification.target_date == target_date,
            )
        )
        return result.scalar_one_or_none() is not None


async def mark_daily_notification_sent(telegram_id: int, notification_type: str, target_date):
    """Фиксирует успешную отправку ежедневной рассылки пользователю."""
    async with get_session() as session:
        session.add(
            UserDailyNotification(
                telegram_id=telegram_id,
                notification_type=str(notification_type).strip()[:64],
                target_date=target_date,
            )
        )
        try:
            await session.commit()
            return True
        except IntegrityError:
            await session.rollback()
            return False

async def replace_schedule_snapshot(
    schedule_data: Dict[str, Dict[str, Dict[str, list]]],
    group_info_data: Dict[str, Dict[str, list]],
    week_days_info: Dict[str, Dict[str, Dict[str, str]]],
    course_display_names: Dict[str, str],
    course_period_ids: Dict[str, str],
    period_id_to_label: Dict[str, str],
):
    """Полностью пересоздает расписание в БД из данных, загруженных в память."""
    stats = {
        'periods': 0,
        'courses': 0,
        'groups': 0,
        'weeks': 0,
        'days': 0,
        'lessons': 0,
    }

    async with get_session() as session:
        # Полный snapshot-подход: удаляем старое и загружаем новое.
        await session.execute(delete(ScheduleLesson))
        await session.execute(delete(ScheduleWeekDay))
        await session.execute(delete(ScheduleWeek))
        await session.execute(delete(ScheduleGroup))
        await session.execute(delete(ScheduleCourse))
        await session.execute(delete(SchedulePeriod))
        await session.flush()

        period_codes = sorted({code for code in course_period_ids.values() if code})
        period_objects: Dict[str, SchedulePeriod] = {}
        for period_code in period_codes:
            period_obj = SchedulePeriod(
                period_code=period_code,
                label=period_id_to_label.get(period_code, period_code),
            )
            session.add(period_obj)
            period_objects[period_code] = period_obj
        await session.flush()
        stats['periods'] = len(period_objects)

        course_objects: Dict[str, ScheduleCourse] = {}
        for course_key in sorted(schedule_data.keys()):
            period_code = course_period_ids.get(course_key)
            period_obj = period_objects.get(period_code)

            course_obj = ScheduleCourse(
                course_key=course_key,
                course_name=_course_name_from_key(course_key),
                display_name=course_display_names.get(course_key, course_key),
                period_id=period_obj.id if period_obj else None,
            )
            session.add(course_obj)
            course_objects[course_key] = course_obj
        await session.flush()
        stats['courses'] = len(course_objects)

        group_objects: Dict[tuple[str, str], ScheduleGroup] = {}
        for course_key, groups in schedule_data.items():
            course_obj = course_objects.get(course_key)
            if course_obj is None:
                continue

            for group_code in sorted(groups.keys()):
                direction = ''
                group_info = group_info_data.get(course_key, {}).get(group_code)
                if isinstance(group_info, list) and len(group_info) > 2 and group_info[2]:
                    direction = str(group_info[2]).strip()

                group_obj = ScheduleGroup(
                    course_id=course_obj.id,
                    group_code=group_code,
                    direction=direction,
                )
                session.add(group_obj)
                group_objects[(course_key, group_code)] = group_obj
        await session.flush()
        stats['groups'] = len(group_objects)

        week_objects: Dict[tuple[str, str, str], ScheduleWeek] = {}
        for (course_key, group_code), group_obj in group_objects.items():
            weeks_map = schedule_data.get(course_key, {}).get(group_code, {})
            sorted_week_labels = sorted(weeks_map.keys(), key=_week_sort_key)

            for order_index, week_label in enumerate(sorted_week_labels, start=1):
                week_obj = ScheduleWeek(
                    group_id=group_obj.id,
                    week_label=week_label,
                    week_sort=order_index,
                )
                session.add(week_obj)
                week_objects[(course_key, group_code, week_label)] = week_obj
        await session.flush()
        stats['weeks'] = len(week_objects)

        for (course_key, group_code, week_label), week_obj in week_objects.items():
            days_map = week_days_info.get(course_key, {}).get(week_label, {}) or {}
            for day_name, date_text in days_map.items():
                normalized_day = str(day_name).strip()
                normalized_date = str(date_text).strip() if date_text is not None else ''
                session.add(
                    ScheduleWeekDay(
                        week_id=week_obj.id,
                        day_name=normalized_day,
                        day_order=DAY_ORDER_MAP.get(normalized_day.lower(), 99),
                        date_value=_parse_iso_date(normalized_date),
                        date_text=normalized_date or None,
                    )
                )
                stats['days'] += 1

            entries = schedule_data.get(course_key, {}).get(group_code, {}).get(week_label, [])
            for lesson_index, entry in enumerate(entries, start=1):
                lesson_text = str(entry.get('lesson', '')).strip()
                if not lesson_text:
                    continue

                day_name = str(entry.get('day', '')).strip()
                date_text = str(entry.get('date', '')).strip()
                time_text = str(entry.get('time', '')).strip()

                session.add(
                    ScheduleLesson(
                        week_id=week_obj.id,
                        day_name=day_name,
                        date_value=_parse_iso_date(date_text),
                        date_text=date_text or None,
                        time_text=time_text or None,
                        lesson_text=lesson_text,
                        lesson_order=lesson_index,
                    )
                )
                stats['lessons'] += 1

        await session.commit()

    return stats
