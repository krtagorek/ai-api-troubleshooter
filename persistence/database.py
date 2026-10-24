"""Bounded SQLAlchemy async connections and explicit transaction scopes."""

from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.exc import SQLAlchemyError

from persistence.config import DatabaseSettings


class PersistenceError(RuntimeError):
    """Safe database failure; driver text and bound values are never exposed."""


class Database:
    def __init__(self, settings: DatabaseSettings):
        self.engine = create_async_engine(
            settings.database_url.get_secret_value(), echo=False, hide_parameters=True,
            pool_pre_ping=True, pool_size=5, max_overflow=0, pool_timeout=5,
            connect_args={"timeout": 5, "command_timeout": 15,
                          "server_settings": {"statement_timeout": "15000", "timezone": "UTC"}},
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction(self):
        try:
            async with self.sessions() as session:
                async with session.begin():
                    yield session
        except SQLAlchemyError:
            raise PersistenceError("Database operation failed; transaction rolled back") from None

    async def close(self):
        await self.engine.dispose()
