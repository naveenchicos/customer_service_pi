"""
Alembic environment configuration.

Reads DATABASE_URL from the application settings so migrations always
use the same database as the running service.

For async engines (asyncpg), we wrap the migration in run_sync() because
Alembic itself is synchronous.

Usage:
  # Generate a new migration from model changes
  alembic revision --autogenerate -m "description"

  # Apply all pending migrations
  alembic upgrade head

  # Rollback one migration
  alembic downgrade -1

  # Show current revision
  alembic current
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Alembic Config object — provides access to alembic.ini values
alembic_config = context.config

# Set up loggers from alembic.ini
if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name)

# Import Base and all models so autogenerate can detect schema changes.
# Adding a new model? Import it here.
from src.db import Base  # noqa: E402
from src.models.account import Account  # noqa: E402, F401
from src.config import get_settings  # noqa: E402

# Override sqlalchemy.url with the value from application settings
# so we never need to duplicate credentials in alembic.ini
settings = get_settings()
alembic_config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode (generates SQL without connecting).
    Useful for generating SQL scripts to review before applying.
    """
    url = alembic_config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,  # detect column type changes
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations using the async engine (required for asyncpg)."""
    engine = async_engine_from_config(
        alembic_config.get_section(alembic_config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live database."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
