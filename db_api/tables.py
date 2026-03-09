from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from db_api.database import Base


class Users(Base):
    """Пользователи бота."""

    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, nullable=False)
    username = Column(String)
    referrer_id = Column(BigInteger)

class BotAdminSession(Base):
    """Авторизованная админ-сессия в Telegram-боте."""

    __tablename__ = 'bot_admin_sessions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, nullable=False, unique=True, index=True)
    login = Column(String(128), nullable=False)
    authorized_at = Column(DateTime, nullable=False, default=datetime.utcnow)



class UserScheduleState(Base):
    """Сохраненное состояние выбора расписания для пользователя."""

    __tablename__ = 'user_schedule_states'

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, nullable=False, unique=True, index=True)
    selected_course = Column(String(255), nullable=True)
    selected_course_name = Column(String(255), nullable=True)
    selected_group = Column(String(64), nullable=True)
    selected_week_index = Column(Integer, nullable=True)
    selected_day_index = Column(Integer, nullable=True)
    daily_digest_enabled = Column(Boolean, nullable=False, default=True)
    daily_digest_hour = Column(Integer, nullable=False, default=20)
    daily_digest_minute = Column(Integer, nullable=False, default=0)
    event_notifications_enabled = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class UserDailyNotification(Base):
    """Факт отправки ежедневной рассылки пользователю."""

    __tablename__ = 'user_daily_notifications'
    __table_args__ = (
        UniqueConstraint(
            'telegram_id',
            'notification_type',
            'target_date',
            name='uq_user_daily_notification',
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, nullable=False, index=True)
    notification_type = Column(String(64), nullable=False, index=True)
    target_date = Column(Date, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BotCallbackPayload(Base):
    """Сохраненный payload callback-кнопки для переживания перезапуска бота."""

    __tablename__ = 'bot_callback_payloads'

    id = Column(Integer, primary_key=True, autoincrement=True)
    token = Column(String(32), nullable=False, unique=True, index=True)
    payload_type = Column(String(32), nullable=False, index=True)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BotEvent(Base):
    """Сохраненное мероприятие бота."""

    __tablename__ = 'bot_events'

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    event_at = Column(DateTime, nullable=False, index=True)
    attachment_type = Column(String(32), nullable=True, index=True)
    attachment_payload = Column(Text, nullable=True)
    created_by_telegram_id = Column(BigInteger, nullable=True, index=True)
    created_by_login = Column(String(128), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class SchedulePeriod(Base):
    """Период расписания (например, месяц/семестр из имени файла)."""

    __tablename__ = 'schedule_periods'

    id = Column(Integer, primary_key=True, autoincrement=True)
    period_code = Column(String(32), nullable=False, unique=True, index=True)
    label = Column(String(255), nullable=False)
    source_file = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    courses = relationship('ScheduleCourse', back_populates='period', cascade='all, delete-orphan')


class ScheduleCourse(Base):
    """Курс/лист Excel в рамках периода."""

    __tablename__ = 'schedule_courses'

    id = Column(Integer, primary_key=True, autoincrement=True)
    course_key = Column(String(255), nullable=False, unique=True, index=True)
    course_name = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=False)
    period_id = Column(Integer, ForeignKey('schedule_periods.id', ondelete='CASCADE'), nullable=True, index=True)

    period = relationship('SchedulePeriod', back_populates='courses')
    groups = relationship('ScheduleGroup', back_populates='course', cascade='all, delete-orphan')


class ScheduleGroup(Base):
    """Академическая группа внутри курса."""

    __tablename__ = 'schedule_groups'
    __table_args__ = (
        UniqueConstraint('course_id', 'group_code', name='uq_schedule_group_course_code'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    course_id = Column(Integer, ForeignKey('schedule_courses.id', ondelete='CASCADE'), nullable=False, index=True)
    group_code = Column(String(64), nullable=False, index=True)
    direction = Column(Text, nullable=True)

    course = relationship('ScheduleCourse', back_populates='groups')
    weeks = relationship('ScheduleWeek', back_populates='group', cascade='all, delete-orphan')


class ScheduleWeek(Base):
    """Неделя расписания для конкретной группы."""

    __tablename__ = 'schedule_weeks'
    __table_args__ = (
        UniqueConstraint('group_id', 'week_label', name='uq_schedule_week_group_label'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey('schedule_groups.id', ondelete='CASCADE'), nullable=False, index=True)
    week_label = Column(String(128), nullable=False)
    week_sort = Column(Integer, nullable=False, default=0)

    group = relationship('ScheduleGroup', back_populates='weeks')
    days = relationship('ScheduleWeekDay', back_populates='week', cascade='all, delete-orphan')
    lessons = relationship('ScheduleLesson', back_populates='week', cascade='all, delete-orphan')


class ScheduleWeekDay(Base):
    """День недели внутри недели (включая дату)."""

    __tablename__ = 'schedule_week_days'
    __table_args__ = (
        UniqueConstraint('week_id', 'day_name', name='uq_schedule_week_day_name'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    week_id = Column(Integer, ForeignKey('schedule_weeks.id', ondelete='CASCADE'), nullable=False, index=True)
    day_name = Column(String(32), nullable=False)
    day_order = Column(Integer, nullable=False, default=99)
    date_value = Column(Date, nullable=True)
    date_text = Column(String(64), nullable=True)

    week = relationship('ScheduleWeek', back_populates='days')


class ScheduleLesson(Base):
    """Учебная пара/занятие в рамках недели."""

    __tablename__ = 'schedule_lessons'

    id = Column(Integer, primary_key=True, autoincrement=True)
    week_id = Column(Integer, ForeignKey('schedule_weeks.id', ondelete='CASCADE'), nullable=False, index=True)
    day_name = Column(String(32), nullable=False)
    date_value = Column(Date, nullable=True)
    date_text = Column(String(64), nullable=True)
    time_text = Column(String(64), nullable=True)
    lesson_text = Column(Text, nullable=False)
    lesson_order = Column(Integer, nullable=False, default=0)

    week = relationship('ScheduleWeek', back_populates='lessons')

