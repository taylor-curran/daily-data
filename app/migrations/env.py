from alembic import context

from app.db import engine
from app.models import Base

target_metadata = Base.metadata


def run() -> None:
    # init_db passes its own connection (holding the migration lock); the CLI opens one.
    conn = context.config.attributes.get("connection")
    if conn is not None:
        _migrate(conn)
    else:
        with engine.begin() as c:
            _migrate(c)


def _migrate(conn) -> None:
    context.configure(
        connection=conn,
        target_metadata=target_metadata,
        render_as_batch=conn.dialect.name == "sqlite",  # sqlite can't ALTER most things in place
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


run()
