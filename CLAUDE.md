# daily-data

- Schema changes need an Alembic migration in the same commit. After editing `app/models.py`, run
  `alembic revision --autogenerate -m "what changed"` and read the generated file in
  `app/migrations/versions/`. Autogenerate writes a renamed column/table as drop + add, which loses data:
  rewrite those as `op.alter_column(..., new_column_name=...)` / `op.rename_table(...)`. New non-null
  columns on tables with rows need a `server_default` or a backfill.
- Never drop or recreate tables in `init_db`; it only runs `alembic upgrade head` on startup.
- Run the tests before pushing: `DATABASE_URL=sqlite:///./test.db python -m tests.test_e2e`.
  CI also runs `alembic check`, which fails if a model change has no migration.
