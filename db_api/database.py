from contextlib import asynccontextmanager

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from data.config import DB_DRIVER, DB_HOST, DB_NAME, DB_PASSWORD, DB_USERNAME

Base = declarative_base()

engine = create_async_engine(
    f'{DB_DRIVER}://{DB_USERNAME}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}',
    future=True,
    pool_pre_ping=True,
)


def _run_schema_migrations(sync_conn):
    """Добавляет недостающие колонки в существующие таблицы без отдельного мигратора."""
    inspector = inspect(sync_conn)
    table_names = set(inspector.get_table_names())

    if 'user_schedule_states' in table_names:
        existing_columns = {column['name'] for column in inspector.get_columns('user_schedule_states')}
        alter_statements = []

        if 'daily_digest_enabled' not in existing_columns:
            alter_statements.append(
                "ALTER TABLE user_schedule_states "
                "ADD COLUMN daily_digest_enabled BOOLEAN NOT NULL DEFAULT TRUE"
            )
        if 'selected_teacher_name' not in existing_columns:
            alter_statements.append(
                "ALTER TABLE user_schedule_states "
                "ADD COLUMN selected_teacher_name VARCHAR(255) NULL"
            )
        if 'daily_digest_hour' not in existing_columns:
            alter_statements.append(
                "ALTER TABLE user_schedule_states "
                "ADD COLUMN daily_digest_hour INTEGER NOT NULL DEFAULT 20"
            )
        if 'daily_digest_minute' not in existing_columns:
            alter_statements.append(
                "ALTER TABLE user_schedule_states "
                "ADD COLUMN daily_digest_minute INTEGER NOT NULL DEFAULT 0"
            )
        if 'event_notifications_enabled' not in existing_columns:
            alter_statements.append(
                "ALTER TABLE user_schedule_states "
                "ADD COLUMN event_notifications_enabled BOOLEAN NOT NULL DEFAULT TRUE"
            )
        if 'schedule_change_notifications_enabled' not in existing_columns:
            alter_statements.append(
                "ALTER TABLE user_schedule_states "
                "ADD COLUMN schedule_change_notifications_enabled BOOLEAN NOT NULL DEFAULT TRUE"
            )
        if 'student_lesson_reminder_minutes' not in existing_columns:
            alter_statements.append(
                "ALTER TABLE user_schedule_states "
                "ADD COLUMN student_lesson_reminder_minutes INTEGER NULL"
            )
        if 'teacher_lesson_reminder_minutes' not in existing_columns:
            alter_statements.append(
                "ALTER TABLE user_schedule_states "
                "ADD COLUMN teacher_lesson_reminder_minutes INTEGER NULL"
            )

        for statement in alter_statements:
            sync_conn.execute(text(statement))


async def create_base():
    """Создает таблицы, если они еще не существуют."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_run_schema_migrations)


def async_session_generator():
    """Фабрика асинхронных сессий SQLAlchemy."""
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def get_session():
    """Выдает асинхронную сессию с rollback при ошибках."""
    async_session = async_session_generator()
    async with async_session() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
