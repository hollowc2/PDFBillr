from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from flask import current_app

config = context.config
fileConfig(config.config_file_name, disable_existing_loggers=False)


def get_engine():
    return current_app.extensions["migrate"].db.engine


def get_engine_url() -> str:
    return str(get_engine().url).replace("%", "%%")


config.set_main_option("sqlalchemy.url", get_engine_url())
target_metadata = current_app.extensions["migrate"].db.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configure_args = current_app.extensions["migrate"].configure_args
    connectable = get_engine()

    with connectable.connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        sqlite_connection = None
        if sqlite:
            # Alembic's SQLite batch mode recreates parent tables. Enforced
            # foreign keys would reject the temporary DROP even though the
            # replacement preserves every key, so suspend checks only within
            # this dedicated migration connection.
            sqlite_connection = connection.connection.driver_connection
            sqlite_connection.execute("PRAGMA foreign_keys=OFF")
        try:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                **configure_args,
            )

            with context.begin_transaction():
                context.run_migrations()
        finally:
            if sqlite_connection is not None:
                violations = sqlite_connection.execute(
                    "PRAGMA foreign_key_check"
                ).fetchall()
                sqlite_connection.execute("PRAGMA foreign_keys=ON")
                if violations:
                    raise RuntimeError(
                        "Migration produced SQLite foreign-key violations"
                    )


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
