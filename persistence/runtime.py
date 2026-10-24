"""Resource ownership for separate application, checkpoint, and lock pools."""

from contextlib import asynccontextmanager
import hashlib

from psycopg.rows import dict_row
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import text

from persistence.checkpoints import SanitizedAsyncPostgresSaver
from persistence.database import Database


class InvestigationBusy(RuntimeError):
    pass


class DatabaseUnavailableError(RuntimeError):
    """Actionable startup failure with no DSN, credentials, or driver details."""


STARTUP_HELP = (
    "PostgreSQL is unavailable or persistence initialization failed. "
    "Start your PostgreSQL server and check the host, port, database, and credentials "
    "in DATABASE_URL (postgresql+asyncpg://) and CHECKPOINT_DATABASE_URL (postgresql://). "
    "Once connected, run Alembic upgrade head and python -m persistence.manage."
)


async def release_locks(connection):
    await connection.execute("SELECT pg_advisory_unlock_all()")


class PersistenceRuntime:
    def __init__(self, settings):
        self.database = Database(settings)
        options = dict(conninfo=settings.checkpoint_database_url.get_secret_value(),
            kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0,
                    "connect_timeout": 5,
                    "options": "-c search_path=langgraph -c statement_timeout=15000"},
            open=False, timeout=5)
        self.checkpoint_pool = AsyncConnectionPool(min_size=1, max_size=4, **options)
        # Separate lock pool avoids starving the saver while a graph is running.
        self.lock_pool = AsyncConnectionPool(min_size=0, max_size=4, reset=release_locks, **options)
        self.saver = SanitizedAsyncPostgresSaver(self.checkpoint_pool)

    async def setup_checkpoints(self):
        async with self.checkpoint_pool.connection() as connection:
            await connection.execute("SELECT pg_advisory_lock(805108)")
            try:
                await connection.execute("CREATE SCHEMA IF NOT EXISTS langgraph")
                await SanitizedAsyncPostgresSaver(connection).setup()
            finally:
                await connection.execute("SELECT pg_advisory_unlock(805108)")

    @asynccontextmanager
    async def thread_lock(self, thread_id):
        key = int.from_bytes(hashlib.sha256(thread_id.encode()).digest()[:8], "big", signed=True)
        async with self.lock_pool.connection() as connection:
            row = await (await connection.execute("SELECT pg_try_advisory_lock(%s) AS locked", (key,))).fetchone()
            if not row["locked"]:
                raise InvestigationBusy("Investigation is already running")
            try:
                yield
            finally:
                await connection.execute("SELECT pg_advisory_unlock(%s)", (key,))


@asynccontextmanager
async def open_persistence(settings, *, setup_checkpoints=False):
    runtime = PersistenceRuntime(settings)
    try:
        try:
            # Probe both environment-configured destinations before starting pool
            # workers: connection failures must not trigger repeated driver logs.
            async with runtime.database.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            async with await AsyncConnection.connect(
                settings.checkpoint_database_url.get_secret_value(),
                autocommit=True, connect_timeout=5,
            ) as connection:
                await connection.execute("SELECT 1")
            await runtime.checkpoint_pool.open(wait=True, timeout=5)
            await runtime.lock_pool.open(wait=True, timeout=5)
            if setup_checkpoints:
                await runtime.setup_checkpoints()
        except Exception:
            raise DatabaseUnavailableError(STARTUP_HELP) from None
        yield runtime
    finally:
        await runtime.lock_pool.close()
        await runtime.checkpoint_pool.close()
        await runtime.database.close()
