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


def init_db() -> None:
    """Create tables. One-time guarded reset from schema v1 (projects/outcomes) to v2.

    There is no migration tooling yet. v1 never held logged data, so if the DB is at v1 and
    has no entries, drop it all and recreate. If it DOES have entries, refuse loudly rather
    than lose anything — that's the point at which to add Alembic.
    """
    from sqlalchemy import inspect, text

    from .models import SCHEMA_VERSION, SchemaVersion

    insp = inspect(engine)
    tables = set(insp.get_table_names())
    current = None
    if "schema_version" in tables:
        with engine.begin() as conn:
            current = conn.execute(text("select max(version) from schema_version")).scalar()
    if tables and current != SCHEMA_VERSION:
        n = 0
        with engine.begin() as conn:
            if "entries" in tables:
                n = conn.execute(text("select count(*) from entries")).scalar()
        if n:
            raise RuntimeError(
                f"DB is schema v{current} with {n} logged entries; refusing to auto-reset. Add a migration."
            )
        legacy = ("entries", "outcomes", "projects", "checkins", "debriefs", "reviews", "variants", "habits",
                  "days", "measures", "arcs", "action_items", "arc_notes", "todos", "content_log", "schema_version")
        with engine.begin() as conn:
            for t in legacy:
                conn.execute(text(f"drop table if exists {t} cascade" if engine.dialect.name == "postgresql"
                                  else f"drop table if exists {t}"))
    Base.metadata.create_all(engine)
    with session() as s:
        if not s.get(SchemaVersion, SCHEMA_VERSION):
            s.add(SchemaVersion(version=SCHEMA_VERSION))


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
