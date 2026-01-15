from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from db_api.database import get_session
from db_api.tables import Users
from sqlalchemy import func
from sqlalchemy import delete
from sqlalchemy import or_, and_



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
            referrer_id=referrer_id
        )
        session.add(user)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
