from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from data.config import DB_DRIVER, DB_HOST, DB_NAME, DB_PASSWORD, DB_USERNAME

Base = declarative_base()

engine = create_async_engine(
    f'{DB_DRIVER}://{DB_USERNAME}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}',
    future=True,
    pool_pre_ping=True,
)


async def create_base():
    """Создает таблицы, если они еще не существуют."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


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
