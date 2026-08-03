"""
Alembic migration environment.

Reads the database URL from application settings (not from alembic.ini) so that
credentials stay out of version control.

Supports two modes:
- Offline: generates SQL scripts without a live DB connection
- Online:  runs migrations directly against the configured database
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from service.config import settings
from service.db.base import Base

# Import all models so Alembic can detect schema changes.
# Add new model modules here as they are created.
import service.models  # noqa: F401

alembic_cfg = context.config

# Wire in the live database URL from app settings
alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)

if alembic_cfg.config_file_name is not None:
    fileConfig(alembic_cfg.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL migration scripts without a live DB connection."""
    url = alembic_cfg.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations directly to the configured database."""
    connectable = engine_from_config(
        alembic_cfg.get_section(alembic_cfg.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
