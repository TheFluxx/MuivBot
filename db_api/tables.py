from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, BigInteger, DateTime, ARRAY

from db_api.database import Base


class Users(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, nullable=False)
    username = Column(String)
    referrer_id = Column(BigInteger)
