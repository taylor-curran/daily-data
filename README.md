# Accountability MCP server

Habit + project tracker that Claude voice mode talks to. You start a check-in by voice, Claude
walks your habits and projects, logs one batch of entries, and on Sundays builds a review that
says what drifted, what's hanging on, and what's busy-but-not-moving.

## Model in one paragraph

A **Day** has a mode (home / office / weekend / travel) and a list of what was available (piano, gym…).
A **Habit** has a cadence (`daily`, `every_2_days`, `3x/week`, `1x/2weeks`…), the modes it applies to,
and **Variants** — each with `requires` (resources), `kind` (maintenance | progress) and an optional
`focus` for rotation. A **Project** has a `spirit` sentence and a status. Projects *and* habits can
have one active **Outcome** (binary: active → done). An **Entry** records one habit variant or one
project touch per day; project entries carry a `relation` (advances | tangential | maintenance | exploring).
A **Review** rolls this up over a 4-week window of eligible days.

Signals the review emits:

| flag | meaning |
|---|---|
| `drift` | done-days < 60% of what the cadence expects over eligible days |
| `all_maintenance` ("hanging on") | did the habit this week but never a progress variant |
| `no_activity` ("dropped") | project had no entries in the window |
| `busy_not_moving` | project had entries, none `advances` |
| `stuck_outcome` | outcome ≥3 weeks old with no `advances` |
| `no_outcome` | active project with nothing concrete set |

Exempt days (travel, `skip_day`) are excluded from every denominator.

## Run locally

```bash
pip install -r requirements.txt
ACCESS_TOKEN=pick-something-long python -m app.server       # http://localhost:8000/mcp + Bearer header
```

SQLite by default (`accountability.db`). Config syncs from `config.yaml` on every boot;
`python -m app.seed` syncs without starting the server.

Test: `DATABASE_URL=sqlite:///./test.db python -m tests.test_e2e`

## Deploy (Fly)

```bash
fly launch --no-deploy            # accept fly.toml
fly postgres create && fly postgres attach <pg-app>   # sets DATABASE_URL
fly secrets set ACCESS_TOKEN=$(openssl rand -hex 24)
fly deploy
```

Railway works the same: add a Postgres service, set `ACCESS_TOKEN`, deploy from the Dockerfile.

## Connect to claude.ai

Settings → Connectors → Add custom connector:

- URL: `https://<your-app>.vercel.app/mcp`
- Authentication: **No sign-in**
- Request headers → Add header: `Authorization` = `Bearer <ACCESS_TOKEN>`

The URL itself isn't secret; requests without the header get a 401. (`X-Access-Token: <token>`
also works, for clients that can't send `Authorization`.) Once connected, voice mode can call the
tools; the server's `instructions` tell Claude how to run a check-in.

First check after connecting: ask Claude to list the connector's tools. You should see ten.
Then say "start my check-in" / "let's do the weekly review".

## Editing habits

Everything about habits lives in `config.yaml`. Add variants, change cadences, add resources,
re-deploy (or restart). Removed habits/variants are deactivated, not deleted — history stays.

Conventions:
- `requires: []` = can do anywhere (the travel-proof minimum)
- `kind: progress` + `focus:` = participates in `rotation: least_recent_focus`
- `default_by_mode: {home: progress}` = on home days suggest whichever progress focus is most overdue
- `default_by_mode: {office: app_lesson}` = on office days lead with that specific variant
- `modes:` = where the habit counts toward its rate; omit `travel` to make travel exempt for it

## Tools

`start_checkin`, `log_entries`, `skip_day`, `set_outcome`, `complete_outcome`, `set_project_status`,
`weekly_review`, `record_reflection`, `recent_history`, `show_config`. Docstrings in `app/server.py`.

## Not built (on purpose)

No dashboard, no reminders, no calendar integration, no per-piece sub-goals. Run four weekly reviews
first and see what you actually look at.

## Handoff notes for Devin

- `app/logic.py` is pure functions over SQLAlchemy sessions — easiest place to add signals.
- `FAKE_TODAY=YYYY-MM-DD` env var fakes the clock (used by the test).
- The server is stateless HTTP (`stateless_http=True`), so it scales to zero on Fly fine.
- Entries are idempotent per (day, target): re-logging replaces, so a corrected voice check-in is safe.
