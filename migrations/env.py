"""Async migrations own only app.*. Checkpoint setup is a separate command."""

import asyncio
import os

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine

from persistence.tables import Base

url = os.environ.get("DATABASE_URL")
if not url or not url.startswith("postgresql+asyncpg://"):
    raise ValueError("DATABASE_URL must use postgresql+asyncpg")


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name == "app"
    return parent_names.get("schema_name", "app") == "app"


def configure(connection=None):
    context.configure(connection=connection, url=url if connection is None else None,
        target_metadata=Base.metadata, include_schemas=True, include_name=include_name,
        version_table_schema="app", compare_type=True,
        literal_binds=connection is None, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


async def online():
    engine = create_async_engine(url, poolclass=pool.NullPool, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("CREATE SCHEMA IF NOT EXISTS app"))
            await connection.commit()
            await connection.run_sync(configure)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(url=url, literal_binds=True, version_table_schema="app")
    context.execute("CREATE SCHEMA IF NOT EXISTS app")
    configure()
else:
    asyncio.run(online())
