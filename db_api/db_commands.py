import re
from datetime import datetime
from typing import Any, Dict

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from db_api.database import get_session
from db_api.tables import (
    Users,
    UserScheduleState,
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
USER_SCHEDULE_FIELDS = (
    'selected_course',
    'selected_course_name',
    'selected_group',
    'selected_week_index',
    'selected_day_index',
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
        }


async def upsert_user_schedule_state(telegram_id: int, **kwargs):
    """Создает или обновляет сохраненные настройки расписания пользователя."""
    payload = {key: kwargs.get(key) for key in USER_SCHEDULE_FIELDS if key in kwargs}
    if not payload:
        return None

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
        }


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
