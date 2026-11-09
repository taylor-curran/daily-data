# daily-data

A personal tracker that Claude voice mode talks to. One short debrief a day, a weekly review, and
separate strategy conversations for the bigger threads. Lives at `https://daily-data-one.vercel.app`.

## The model

Five kinds of things. Two sources of truth.

| Kind | What | Defined in | Written by |
|---|---|---|---|
| **Habit** | yes/no against a cadence (flashcards daily, drills 3x/week) | `config.yaml` | debrief → `entries` |
| **Measure** | a daily number you want to see trend (picking count, phone video level) | `config.yaml` | debrief → `entries.value` |
| **Arc** | a static objective with rotating action items (AD, Wedding) | objective + status in `config.yaml` | conversations → `action_items`, `arc_notes` |
| **Todo** | one-off housekeeping (boots for Noah, order brisket) | — | conversations → `todos` |
| **Content** | what you read / listened to that stuck | — | debrief → `content_log` |

Config is the *shape*: it only changes by editing `config.yaml` and pushing (Vercel redeploys and
syncs on boot). Everything that *happens* is data and is written by conversations. Conversations
never write config; if asked to, Claude says so and adds a todo.

There is no notion of where you were. A day counts if it had a debrief; a day with no debrief is a
null day and counts against nothing. A thin day (flashcards only, on a trip) is just a thin day.

## Conversations

- **Daily debrief** ("let's do my debrief"): opens broad — *talk me through today: what was on your
  mind, what did you get to, what did you want to and didn't* — and Claude maps the story onto arcs,
  todos, habits and content as it goes. Then one light line on any arc that didn't come up, content
  if it didn't, and a batched rapid-fire sweep of whatever habits and measures are still missing.
  One-line readback. Under five minutes.
- **Strategy session** ("let's talk AD"): `get_arc`, then rubber-ducking — what's blocking, what to
  sequence or cut, "want to write it out?" — saved via `update_action_items` and `add_arc_note`.
- **Weekly review** (`weekly_review`): leads with `off_this_week`, `drift`, measures that moved,
  arcs that went quiet; asks per arc whether it still matters → `record_reflection`.

Review signals:

| flag | meaning |
|---|---|
| `off_this_week` | this week's done-days < 50% of what the cadence expects, over days that had a debrief (fast signal) |
| `drift` | 4-week rate < 60% (slow signal) |
| measure `trend` | this week's average vs the prior 3 weeks, in the configured `direction` |
| `quiet_week` | active arc with no item added/closed and no note for 7+ days |
| `suggest_backburner` | 21+ days quiet — you decide, in config |
| `no_open_items` | active arc with nothing on the list |
| todo `stale` | open 30+ days |

Count habits (climbing) report the weekly number and never flag drift.

## Run locally

```bash
pip install -r requirements.txt
ACCESS_TOKEN=pick-something-long python -m app.server     # http://localhost:8000/mcp
DATABASE_URL=sqlite:///./test.db python -m tests.test_e2e  # simulates 2 weeks, checks every flag
```

SQLite by default. Dates are Taylor's local date (`TZ_NAME`, default `America/Chicago`) and a day
runs until 4am (`DAY_START_HOUR`). `FAKE_TODAY=YYYY-MM-DD` fakes the clock.

## Deploy

Vercel, Python runtime, Neon Postgres via the Storage tab (`DATABASE_URL`), `ACCESS_TOKEN` as a
secret env var. Push to `main` deploys. `Dockerfile` / `fly.toml` remain for a non-serverless host.

**Schema changes:** there is no migration tooling yet. `init_db` does a one-time guarded reset from
the v1 schema (it refuses if any entries exist). The next schema change after data exists needs
Alembic.

## Connect to claude.ai

Settings → Connectors → Add custom connector:

- URL: `https://daily-data-one.vercel.app/mcp`
- Authentication: **No sign-in**
- Request headers → `Authorization` = `Bearer <ACCESS_TOKEN>`

Requests without the header get a 401. First check after connecting: ask Claude to list the
connector's tools — there are 13.

## Reading your data

Neon console → SQL Editor, or any Postgres client with the connection string from the Storage page:
`entries`, `days`, `debriefs`, `action_items`, `arc_notes`, `todos`, `content_log`, `reviews`.
It's yours; `pg_dump` takes all of it.

## Not built (on purpose)

No dashboard, no reminders, no calendar integration, no per-exercise strength logging. Run a few
weekly reviews first and see what you actually look at.
