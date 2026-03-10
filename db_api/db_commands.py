import json
import re
from datetime import datetime
from typing import Any, Dict

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from db_api.database import get_session
from db_api.tables import (
    BotAdminSession,
    BotCallbackPayload,
    BotEvent,
    BotStarostaSession,
    GroupAttendanceMark,
    GroupStarostaAssignment,
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
    'event_notifications_enabled',
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


def _normalize_event_notifications_enabled(value: Any) -> bool:
    if value is None:
        return True
    return bool(value)


def _normalize_attendance_status(value: Any) -> str | None:
    normalized = str(value or '').strip().lower()
    if normalized in {'present', 'absent'}:
        return normalized
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


async def get_admin_session(telegram_id: int):
    """Возвращает админ-сессию пользователя, если она активна."""
    async with get_session() as session:
        result = await session.execute(
            select(BotAdminSession).where(BotAdminSession.telegram_id == telegram_id)
        )
        session_row = result.scalar_one_or_none()
        if session_row is None:
            return None

        return {
            'telegram_id': session_row.telegram_id,
            'login': session_row.login,
            'authorized_at': session_row.authorized_at,
        }


async def is_admin_authorized(telegram_id: int) -> bool:
    """Проверяет, авторизован ли пользователь в админ-панели."""
    return await get_admin_session(telegram_id) is not None


async def authorize_admin(telegram_id: int, login: str):
    """Создает или обновляет админ-сессию пользователя."""
    normalized_login = str(login).strip()

    async with get_session() as session:
        result = await session.execute(
            select(BotAdminSession).where(BotAdminSession.telegram_id == telegram_id)
        )
        session_row = result.scalar_one_or_none()

        if session_row is None:
            session_row = BotAdminSession(
                telegram_id=telegram_id,
                login=normalized_login,
                authorized_at=datetime.utcnow(),
            )
            session.add(session_row)
        else:
            session_row.login = normalized_login
            session_row.authorized_at = datetime.utcnow()

        await session.commit()

        return {
            'telegram_id': session_row.telegram_id,
            'login': session_row.login,
            'authorized_at': session_row.authorized_at,
        }


async def revoke_admin(telegram_id: int) -> bool:
    """Завершает админ-сессию пользователя."""
    async with get_session() as session:
        result = await session.execute(
            select(BotAdminSession).where(BotAdminSession.telegram_id == telegram_id)
        )
        session_row = result.scalar_one_or_none()
        if session_row is None:
            return False

        await session.delete(session_row)
        await session.commit()
        return True


async def get_starosta_session(telegram_id: int):
    """Возвращает активную сессию главной старосты."""
    async with get_session() as session:
        result = await session.execute(
            select(BotStarostaSession).where(BotStarostaSession.telegram_id == telegram_id)
        )
        session_row = result.scalar_one_or_none()
        if session_row is None:
            return None

        return {
            'telegram_id': session_row.telegram_id,
            'login': session_row.login,
            'authorized_at': session_row.authorized_at,
        }


async def is_super_starosta_authorized(telegram_id: int) -> bool:
    """Проверяет, вошел ли пользователь как главная староста."""
    return await get_starosta_session(telegram_id) is not None


async def authorize_starosta(telegram_id: int, login: str):
    """Создает или обновляет сессию главной старосты."""
    normalized_login = str(login).strip()

    async with get_session() as session:
        result = await session.execute(
            select(BotStarostaSession).where(BotStarostaSession.telegram_id == telegram_id)
        )
        session_row = result.scalar_one_or_none()

        if session_row is None:
            session_row = BotStarostaSession(
                telegram_id=telegram_id,
                login=normalized_login,
                authorized_at=datetime.utcnow(),
            )
            session.add(session_row)
        else:
            session_row.login = normalized_login
            session_row.authorized_at = datetime.utcnow()

        await session.commit()

        return {
            'telegram_id': session_row.telegram_id,
            'login': session_row.login,
            'authorized_at': session_row.authorized_at,
        }


async def revoke_starosta(telegram_id: int) -> bool:
    """Завершает сессию главной старосты."""
    async with get_session() as session:
        result = await session.execute(
            select(BotStarostaSession).where(BotStarostaSession.telegram_id == telegram_id)
        )
        session_row = result.scalar_one_or_none()
        if session_row is None:
            return False

        await session.delete(session_row)
        await session.commit()
        return True


async def get_user_starosta_groups(telegram_id: int):
    """Возвращает группы, для которых пользователь назначен старостой."""
    async with get_session() as session:
        result = await session.execute(
            select(GroupStarostaAssignment.group_code)
            .where(GroupStarostaAssignment.telegram_id == telegram_id)
            .order_by(GroupStarostaAssignment.group_code.asc())
        )
        rows = result.all()

    return [str(row.group_code).strip() for row in rows if str(row.group_code).strip()]


async def get_group_starosta(group_code: str):
    """Возвращает назначенного старосту конкретной группы."""
    normalized_group = str(group_code or '').strip()
    if not normalized_group:
        return None

    async with get_session() as session:
        result = await session.execute(
            select(
                GroupStarostaAssignment.group_code,
                GroupStarostaAssignment.telegram_id,
                Users.username,
            )
            .select_from(GroupStarostaAssignment)
            .outerjoin(Users, Users.telegram_id == GroupStarostaAssignment.telegram_id)
            .where(GroupStarostaAssignment.group_code == normalized_group)
        )
        row = result.one_or_none()

    if row is None:
        return None

    return {
        'group_code': str(row.group_code).strip(),
        'telegram_id': row.telegram_id,
        'username': str(row.username or '').strip() or None,
    }


async def assign_group_starosta(group_code: str, telegram_id: int, assigned_by_telegram_id: int | None = None):
    """Назначает пользователя старостой выбранной группы."""
    normalized_group = str(group_code or '').strip()
    if not normalized_group:
        return None

    async with get_session() as session:
        result = await session.execute(
            select(GroupStarostaAssignment).where(GroupStarostaAssignment.group_code == normalized_group)
        )
        row = result.scalar_one_or_none()

        if row is None:
            row = GroupStarostaAssignment(
                group_code=normalized_group,
                telegram_id=telegram_id,
                assigned_by_telegram_id=assigned_by_telegram_id,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(row)
        else:
            row.telegram_id = telegram_id
            row.assigned_by_telegram_id = assigned_by_telegram_id
            row.updated_at = datetime.utcnow()

        await session.commit()

    return await get_group_starosta(normalized_group)


async def clear_group_starosta(group_code: str) -> bool:
    """Снимает назначенного старосту с группы."""
    normalized_group = str(group_code or '').strip()
    if not normalized_group:
        return False

    async with get_session() as session:
        result = await session.execute(
            select(GroupStarostaAssignment).where(GroupStarostaAssignment.group_code == normalized_group)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False

        await session.delete(row)
        await session.commit()
        return True


async def get_starosta_access(telegram_id: int):
    """Возвращает права пользователя в панели старосты."""
    is_super = await is_super_starosta_authorized(telegram_id)
    groups = await get_user_starosta_groups(telegram_id)
    return {
        'is_super': is_super,
        'groups': groups,
        'has_access': is_super or bool(groups),
    }


async def get_admin_dashboard_stats():
    """Возвращает сводную статистику для админ-панели."""
    async with get_session() as session:
        total_users = await session.scalar(select(func.count(Users.id)))
        users_with_group = await session.scalar(
            select(func.count(UserScheduleState.id)).where(UserScheduleState.selected_group.is_not(None))
        )
        users_with_digest = await session.scalar(
            select(func.count(UserScheduleState.id)).where(UserScheduleState.daily_digest_enabled.is_(True))
        )
        active_admins = await session.scalar(select(func.count(BotAdminSession.id)))
        periods_count = await session.scalar(select(func.count(SchedulePeriod.id)))
        courses_count = await session.scalar(select(func.count(ScheduleCourse.id)))
        groups_count = await session.scalar(select(func.count(ScheduleGroup.id)))
        weeks_count = await session.scalar(select(func.count(ScheduleWeek.id)))
        days_count = await session.scalar(select(func.count(ScheduleWeekDay.id)))
        lessons_count = await session.scalar(select(func.count(ScheduleLesson.id)))
        events_count = await session.scalar(select(func.count(BotEvent.id)))

        return {
            'total_users': int(total_users or 0),
            'users_with_group': int(users_with_group or 0),
            'users_with_digest': int(users_with_digest or 0),
            'active_admins': int(active_admins or 0),
            'periods_count': int(periods_count or 0),
            'courses_count': int(courses_count or 0),
            'groups_count': int(groups_count or 0),
            'weeks_count': int(weeks_count or 0),
            'days_count': int(days_count or 0),
            'lessons_count': int(lessons_count or 0),
            'events_count': int(events_count or 0),
        }


async def get_admin_user_course_filters():
    """Извлекает исходное имя курса из технического ключа."""
    async with get_session() as session:
        result = await session.execute(
            select(
                UserScheduleState.selected_course,
                UserScheduleState.selected_course_name,
            ).where(UserScheduleState.selected_course.is_not(None))
        )
        rows = result.all()

    courses: dict[str, str] = {}
    for row in rows:
        course_key = str(row.selected_course or '').strip()
        if not course_key:
            continue

        course_label = str(row.selected_course_name or '').strip() or _course_name_from_key(course_key)
        if course_key not in courses:
            courses[course_key] = course_label

    return [
        {'value': course_key, 'label': courses[course_key]}
        for course_key in sorted(courses.keys(), key=lambda item: courses[item].casefold())
    ]


async def get_admin_user_group_filters(course_key: str | None = None):
    """Извлекает исходное имя курса из технического ключа."""
    normalized_course = str(course_key or '').strip() or None

    async with get_session() as session:
        query = select(UserScheduleState.selected_group).where(UserScheduleState.selected_group.is_not(None))
        if normalized_course:
            query = query.where(UserScheduleState.selected_course == normalized_course)

        result = await session.execute(query)
        rows = result.all()

    groups = sorted(
        {
            str(row.selected_group or '').strip()
            for row in rows
            if str(row.selected_group or '').strip()
        },
        key=str.casefold,
    )
    return groups


async def get_admin_users_page(
    course_key: str | None = None,
    group_code: str | None = None,
    page: int = 0,
    page_size: int = 8,
    allowed_group_codes: list[str] | None = None,
    starosta_only: bool = False,
):
    normalized_course = str(course_key or '').strip() or None
    normalized_group = str(group_code or '').strip() or None
    normalized_allowed_groups = sorted(
        {
            str(group).strip()
            for group in (allowed_group_codes or [])
            if str(group).strip()
        },
        key=str.casefold,
    )

    try:
        page = max(0, int(page))
    except (TypeError, ValueError):
        page = 0

    try:
        page_size = max(1, int(page_size))
    except (TypeError, ValueError):
        page_size = 8

    async with get_session() as session:
        starosta_subquery = select(GroupStarostaAssignment.telegram_id)
        count_query = (
            select(func.count(Users.id))
            .select_from(Users)
            .outerjoin(UserScheduleState, UserScheduleState.telegram_id == Users.telegram_id)
        )
        data_query = (
            select(
                Users.telegram_id,
                Users.username,
                UserScheduleState.selected_course,
                UserScheduleState.selected_course_name,
                UserScheduleState.selected_group,
                UserScheduleState.daily_digest_enabled,
                UserScheduleState.daily_digest_hour,
                UserScheduleState.daily_digest_minute,
                UserScheduleState.event_notifications_enabled,
            )
            .select_from(Users)
            .outerjoin(UserScheduleState, UserScheduleState.telegram_id == Users.telegram_id)
        )

        if normalized_course:
            count_query = count_query.where(UserScheduleState.selected_course == normalized_course)
            data_query = data_query.where(UserScheduleState.selected_course == normalized_course)

        if normalized_group:
            count_query = count_query.where(UserScheduleState.selected_group == normalized_group)
            data_query = data_query.where(UserScheduleState.selected_group == normalized_group)

        if normalized_allowed_groups:
            count_query = count_query.where(UserScheduleState.selected_group.in_(normalized_allowed_groups))
            data_query = data_query.where(UserScheduleState.selected_group.in_(normalized_allowed_groups))

        if starosta_only:
            count_query = count_query.where(Users.telegram_id.in_(starosta_subquery))
            data_query = data_query.where(Users.telegram_id.in_(starosta_subquery))

        total = int(await session.scalar(count_query) or 0)
        total_pages = max(1, (total + page_size - 1) // page_size)
        if page >= total_pages:
            page = total_pages - 1

        result = await session.execute(
            data_query.order_by(Users.id.desc()).offset(page * page_size).limit(page_size)
        )
        rows = result.all()

        telegram_ids = [row.telegram_id for row in rows]
        assignment_map: dict[int, list[str]] = {}
        if telegram_ids:
            assignment_rows = (
                await session.execute(
                    select(GroupStarostaAssignment.telegram_id, GroupStarostaAssignment.group_code).where(
                        GroupStarostaAssignment.telegram_id.in_(telegram_ids)
                    )
                )
            ).all()
            for assignment_row in assignment_rows:
                assignment_map.setdefault(int(assignment_row.telegram_id), []).append(
                    str(assignment_row.group_code).strip()
                )

    items = []
    for row in rows:
        course_value = str(row.selected_course or '').strip() or None
        course_label = str(row.selected_course_name or '').strip()
        if course_value and not course_label:
            course_label = _course_name_from_key(course_value)

        items.append(
            {
                'telegram_id': row.telegram_id,
                'username': str(row.username or '').strip(),
                'selected_course': course_value,
                'selected_course_name': course_label or None,
                'selected_group': str(row.selected_group or '').strip() or None,
                'daily_digest_enabled': _normalize_daily_digest_enabled(row.daily_digest_enabled),
                'daily_digest_hour': _normalize_daily_digest_hour(row.daily_digest_hour),
                'daily_digest_minute': _normalize_daily_digest_minute(row.daily_digest_minute),
                'event_notifications_enabled': _normalize_event_notifications_enabled(
                    row.event_notifications_enabled
                ),
                'starosta_groups': sorted(assignment_map.get(int(row.telegram_id), []), key=str.casefold),
            }
        )

    return {
        'total': total,
        'page': page,
        'page_size': page_size,
        'total_pages': total_pages,
        'items': items,
    }


async def get_admin_user_details(telegram_id: int):
    """Возвращает подробную информацию о пользователе для админ-панели."""
    async with get_session() as session:
        result = await session.execute(
            select(
                Users.telegram_id,
                Users.username,
                Users.referrer_id,
                UserScheduleState.selected_course,
                UserScheduleState.selected_course_name,
                UserScheduleState.selected_group,
                UserScheduleState.daily_digest_enabled,
                UserScheduleState.daily_digest_hour,
                UserScheduleState.daily_digest_minute,
                UserScheduleState.event_notifications_enabled,
                UserScheduleState.updated_at,
            )
            .select_from(Users)
            .outerjoin(UserScheduleState, UserScheduleState.telegram_id == Users.telegram_id)
            .where(Users.telegram_id == telegram_id)
        )
        row = result.one_or_none()

        assignment_rows = (
            await session.execute(
                select(GroupStarostaAssignment.group_code)
                .where(GroupStarostaAssignment.telegram_id == telegram_id)
                .order_by(GroupStarostaAssignment.group_code.asc())
            )
        ).all()

    if row is None:
        return None

    course_value = str(row.selected_course or '').strip() or None
    course_label = str(row.selected_course_name or '').strip()
    if course_value and not course_label:
        course_label = _course_name_from_key(course_value)

    return {
        'telegram_id': row.telegram_id,
        'username': str(row.username or '').strip() or None,
        'referrer_id': row.referrer_id,
        'selected_course': course_value,
        'selected_course_name': course_label or None,
        'selected_group': str(row.selected_group or '').strip() or None,
        'daily_digest_enabled': _normalize_daily_digest_enabled(row.daily_digest_enabled),
        'daily_digest_hour': _normalize_daily_digest_hour(row.daily_digest_hour),
        'daily_digest_minute': _normalize_daily_digest_minute(row.daily_digest_minute),
        'event_notifications_enabled': _normalize_event_notifications_enabled(
            row.event_notifications_enabled
        ),
        'starosta_groups': [str(item.group_code).strip() for item in assignment_rows if str(item.group_code).strip()],
        'updated_at': row.updated_at,
    }


async def get_admin_message_recipients(
    course_key: str | None = None,
    group_code: str | None = None,
    allowed_group_codes: list[str] | None = None,
):
    """Возвращает получателей для админ-сообщения с учетом фильтров курса и группы."""
    normalized_course = str(course_key or '').strip() or None
    normalized_group = str(group_code or '').strip() or None
    normalized_allowed_groups = sorted(
        {
            str(group).strip()
            for group in (allowed_group_codes or [])
            if str(group).strip()
        },
        key=str.casefold,
    )

    async with get_session() as session:
        query = (
            select(
                Users.telegram_id,
                Users.username,
                UserScheduleState.selected_course,
                UserScheduleState.selected_course_name,
                UserScheduleState.selected_group,
            )
            .select_from(Users)
            .outerjoin(UserScheduleState, UserScheduleState.telegram_id == Users.telegram_id)
        )

        if normalized_course:
            query = query.where(UserScheduleState.selected_course == normalized_course)

        if normalized_group:
            query = query.where(UserScheduleState.selected_group == normalized_group)

        if normalized_allowed_groups:
            query = query.where(UserScheduleState.selected_group.in_(normalized_allowed_groups))

        result = await session.execute(query.order_by(Users.id.desc()))
        rows = result.all()

    recipients = []
    for row in rows:
        course_value = str(row.selected_course or '').strip() or None
        course_label = str(row.selected_course_name or '').strip()
        if course_value and not course_label:
            course_label = _course_name_from_key(course_value)

        recipients.append(
            {
                'telegram_id': row.telegram_id,
                'username': str(row.username or '').strip() or None,
                'selected_course': course_value,
                'selected_course_name': course_label or None,
                'selected_group': str(row.selected_group or '').strip() or None,
            }
        )

    return recipients


async def get_group_students(group_code: str, allowed_group_codes: list[str] | None = None):
    """Возвращает пользователей выбранной группы для панели старосты."""
    normalized_group = str(group_code or '').strip() or None
    normalized_allowed_groups = {
        str(group).strip()
        for group in (allowed_group_codes or [])
        if str(group).strip()
    }

    if not normalized_group:
        return []

    if normalized_allowed_groups and normalized_group not in normalized_allowed_groups:
        return []

    async with get_session() as session:
        result = await session.execute(
            select(
                Users.telegram_id,
                Users.username,
            )
            .select_from(Users)
            .join(UserScheduleState, UserScheduleState.telegram_id == Users.telegram_id)
            .where(UserScheduleState.selected_group == normalized_group)
        )
        rows = result.all()

    students = [
        {
            'telegram_id': row.telegram_id,
            'username': str(row.username or '').strip() or None,
            'selected_group': normalized_group,
        }
        for row in rows
    ]
    students.sort(
        key=lambda item: (
            (item.get('username') or '').casefold() if item.get('username') else '~~~',
            int(item.get('telegram_id') or 0),
        )
    )
    return students


async def get_group_attendance_marks(group_code: str, target_date, lesson_index: int):
    """Возвращает отметки посещаемости по ученикам для конкретной пары."""
    normalized_group = str(group_code or '').strip() or None
    if not normalized_group or target_date is None:
        return {}

    try:
        normalized_lesson_index = int(lesson_index)
    except (TypeError, ValueError):
        return {}

    async with get_session() as session:
        result = await session.execute(
            select(
                GroupAttendanceMark.student_telegram_id,
                GroupAttendanceMark.status,
            ).where(
                GroupAttendanceMark.group_code == normalized_group,
                GroupAttendanceMark.target_date == target_date,
                GroupAttendanceMark.lesson_index == normalized_lesson_index,
            )
        )
        rows = result.all()

    return {
        int(row.student_telegram_id): str(row.status).strip()
        for row in rows
        if row.student_telegram_id is not None and str(row.status).strip()
    }


async def upsert_group_attendance_mark(
    *,
    group_code: str,
    target_date,
    lesson_index: int,
    lesson_time: str,
    lesson_title: str | None,
    student_telegram_id: int,
    status: str | None,
    marked_by_telegram_id: int | None = None,
):
    """Создает, обновляет или удаляет отметку посещаемости ученика."""
    normalized_group = str(group_code or '').strip() or None
    normalized_status = _normalize_attendance_status(status)
    normalized_time = str(lesson_time or '').strip()
    normalized_title = str(lesson_title or '').strip() or None

    if not normalized_group or target_date is None or not normalized_time:
        return None

    try:
        normalized_lesson_index = int(lesson_index)
        normalized_student_id = int(student_telegram_id)
    except (TypeError, ValueError):
        return None

    async with get_session() as session:
        result = await session.execute(
            select(GroupAttendanceMark).where(
                GroupAttendanceMark.group_code == normalized_group,
                GroupAttendanceMark.target_date == target_date,
                GroupAttendanceMark.lesson_index == normalized_lesson_index,
                GroupAttendanceMark.student_telegram_id == normalized_student_id,
            )
        )
        row = result.scalar_one_or_none()

        if normalized_status is None:
            if row is not None:
                await session.delete(row)
                await session.commit()
            return None

        if row is None:
            row = GroupAttendanceMark(
                group_code=normalized_group,
                target_date=target_date,
                lesson_index=normalized_lesson_index,
                lesson_time=normalized_time[:64],
                lesson_title=normalized_title,
                student_telegram_id=normalized_student_id,
                status=normalized_status,
                marked_by_telegram_id=marked_by_telegram_id,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(row)
        else:
            row.lesson_time = normalized_time[:64]
            row.lesson_title = normalized_title
            row.status = normalized_status
            row.marked_by_telegram_id = marked_by_telegram_id
            row.updated_at = datetime.utcnow()

        await session.commit()

        return {
            'group_code': row.group_code,
            'target_date': row.target_date,
            'lesson_index': row.lesson_index,
            'lesson_time': row.lesson_time,
            'lesson_title': row.lesson_title,
            'student_telegram_id': row.student_telegram_id,
            'status': row.status,
            'marked_by_telegram_id': row.marked_by_telegram_id,
            'updated_at': row.updated_at,
        }


def _event_row_to_dict(event_row: BotEvent | None):
    if event_row is None:
        return None

    try:
        attachment_payload = json.loads(event_row.attachment_payload) if event_row.attachment_payload else None
    except json.JSONDecodeError:
        attachment_payload = None

    return {
        'id': event_row.id,
        'title': event_row.title,
        'description': event_row.description,
        'event_at': event_row.event_at,
        'attachment_type': event_row.attachment_type,
        'attachment_payload': attachment_payload,
        'created_by_telegram_id': event_row.created_by_telegram_id,
        'created_by_login': event_row.created_by_login,
        'created_at': event_row.created_at,
    }


async def create_bot_event(
    *,
    title: str,
    description: str,
    event_at: datetime,
    attachment_type: str | None = None,
    attachment_payload: dict | None = None,
    created_by_telegram_id: int | None = None,
    created_by_login: str | None = None,
):
    attachment_json = None
    if attachment_payload:
        attachment_json = json.dumps(attachment_payload, ensure_ascii=False, separators=(',', ':'))

    async with get_session() as session:
        row = BotEvent(
            title=str(title).strip()[:255] or 'Событие',
            description=str(description).strip(),
            event_at=event_at,
            attachment_type=str(attachment_type).strip()[:32] if attachment_type else None,
            attachment_payload=attachment_json,
            created_by_telegram_id=created_by_telegram_id,
            created_by_login=str(created_by_login).strip()[:128] if created_by_login else None,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _event_row_to_dict(row)


async def get_bot_event(event_id: int):
    async with get_session() as session:
        result = await session.execute(select(BotEvent).where(BotEvent.id == int(event_id)))
        row = result.scalar_one_or_none()
    return _event_row_to_dict(row)


async def get_bot_events():
    async with get_session() as session:
        result = await session.execute(select(BotEvent).order_by(BotEvent.event_at.desc(), BotEvent.id.desc()))
        rows = result.scalars().all()
    return [_event_row_to_dict(row) for row in rows]


async def get_event_notification_recipients():
    async with get_session() as session:
        result = await session.execute(
            select(
                Users.telegram_id,
                Users.username,
            )
            .select_from(Users)
            .outerjoin(UserScheduleState, UserScheduleState.telegram_id == Users.telegram_id)
            .where(
                (UserScheduleState.id.is_(None))
                | (UserScheduleState.event_notifications_enabled.is_(True))
            )
            .order_by(Users.id.desc())
        )
        rows = result.all()

    return [
        {
            'telegram_id': row.telegram_id,
            'username': str(row.username or '').strip() or None,
        }
        for row in rows
    ]

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
            'event_notifications_enabled': _normalize_event_notifications_enabled(
                state_row.event_notifications_enabled
            ),
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
    if 'event_notifications_enabled' in payload:
        payload['event_notifications_enabled'] = _normalize_event_notifications_enabled(
            payload.get('event_notifications_enabled')
        )

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
            'event_notifications_enabled': _normalize_event_notifications_enabled(
                state_row.event_notifications_enabled
            ),
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


async def save_bot_callback_payload(token: str, payload_type: str, payload: dict):
    """Сохраняет payload callback-кнопки в базе данных."""
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), sort_keys=True)

    async with get_session() as session:
        result = await session.execute(
            select(BotCallbackPayload).where(BotCallbackPayload.token == str(token).strip())
        )
        row = result.scalar_one_or_none()

        if row is None:
            row = BotCallbackPayload(
                token=str(token).strip(),
                payload_type=str(payload_type).strip()[:32],
                payload_json=payload_json,
            )
            session.add(row)
        else:
            row.payload_type = str(payload_type).strip()[:32]
            row.payload_json = payload_json
            row.created_at = datetime.utcnow()

        await session.commit()


async def get_bot_callback_payload(token: str, payload_type: str | None = None):
    """Возвращает сохраненный payload callback-кнопки из базы данных."""
    async with get_session() as session:
        query = select(BotCallbackPayload).where(BotCallbackPayload.token == str(token).strip())
        if payload_type:
            query = query.where(BotCallbackPayload.payload_type == str(payload_type).strip())

        result = await session.execute(query)
        row = result.scalar_one_or_none()
        if row is None:
            return None

        try:
            return json.loads(row.payload_json)
        except json.JSONDecodeError:
            return None

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

