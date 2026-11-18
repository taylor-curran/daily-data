import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

DATABASE_URL = (
    os.environ.get("DATABASE_URL")
    or os.environ.get("POSTGRES_URL")  # Vercel/Neon integration sometimes uses this name
    or ("" if os.environ.get("VERCEL") else "sqlite:///./accountability.db")
)
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. On Vercel: Storage -> connect a Postgres (Neon) database, then Redeploy. "
        "(SQLite can't work here: the filesystem is read-only.)"
    )
# Fly/Railway hand out postgres:// ; SQLAlchemy wants postgresql://
# Normalize to the driver we actually install (psycopg2). SQLAlchemy 2.1 maps bare
# postgresql:// to psycopg (v3), which isn't installed.
for prefix in ("postgres://", "postgresql://"):
    if DATABASE_URL.startswith(prefix):
        DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len(prefix):]
        break

from sqlalchemy.pool import NullPool  # noqa: E402

# NullPool: serverless (Vercel) spins workers up and down; a persistent pool just leaks connections.
engine = create_engine(DATABASE_URL, future=True, poolclass=NullPool, pool_pre_ping=False)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


BASELINE_REVISION = "0001"  # schema v4, the last version before Alembic
MIGRATION_LOCK = 724801  # arbitrary pg advisory-lock id


def _alembic_config(conn):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "migrations"))
    cfg.attributes["connection"] = conn
    return cfg


def init_db() -> None:
    """Apply any pending Alembic migrations (app/migrations). Runs on every cold start; never drops data.

    A database from before Alembic (has `schema_version` = 4, no `alembic_version`) is stamped at the
    baseline revision first. On Postgres an advisory lock stops concurrent cold starts from racing.
    """
    from alembic import command
    from sqlalchemy import inspect, text

    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("select pg_advisory_xact_lock(:k)"), {"k": MIGRATION_LOCK})
        cfg = _alembic_config(conn)
        tables = set(inspect(conn).get_table_names())
        if "alembic_version" not in tables and "schema_version" in tables:
            v = conn.execute(text("select max(version) from schema_version")).scalar()
            if v != 4:
                raise RuntimeError(f"DB is pre-Alembic schema v{v}; only v4 can be adopted automatically.")
            command.stamp(cfg, BASELINE_REVISION)
        command.upgrade(cfg, "head")


@contextmanager
def session() -> Session:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
