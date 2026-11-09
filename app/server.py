"""MCP server exposing the accountability system.

Run locally:   python -m app.server            (streamable HTTP on :8000)
Connect from claude.ai as a custom connector at  https://<host>/mcp
with request header  Authorization: Bearer <ACCESS_TOKEN>
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Optional

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from .db import init_db, session
from .logic import arc_snapshot, build_review, eligible_variants, ensure_day, suggest_variant
from .models import ActionItem, Arc, ArcNote, Debrief, ContentLog, Entry, Habit, Measure, Review, Todo
from .seed import load_config

INSTRUCTIONS = """
You are Taylor's accountability partner over five kinds of things: habits (yes/no against a cadence),
measures (a daily number to watch), arcs (a static objective with rotating action items), todos
(one-off housekeeping), and content (what she read/listened to that stuck). Two modes of conversation:

DAILY DEBRIEF — short, spoken-friendly, under 5 minutes. Call start_debrief first, every time.
1. Mode: home / office / weekend / travel. Anything unusual on hand or missing (piano, gym)?
   Travel days are exempt for most habits; the response says which still apply.
2. Habits, in the order returned. Skip anything marked already_logged unless she wants to change it
   (a day can have several debriefs; re-logging a habit replaces the earlier answer). Lead with the
   suggested variant ("Drills — scales today? it's been
   8 days"). Any eligible variant counts. Skipped is fine; never scold. Count habits (climbing): just
   ask how many times.
3. Measures (skip already_logged ones too): ask for the number or the level ("picking — how many times today?", "phone video —
   none, some, or a lot?").
4. Arcs, one line each, active arcs only: "Anything move on AD today?" If yes: which open item, or a
   new one? Finished anything? Keep it to logging; strategy is a separate conversation (below).
5. Todos: "Anything to add to the list, or anything you knocked out?"
6. Content: "Anything you read or listened to today that stuck with you?" One question, no follow-up.
7. Call log_entries ONCE for habits+measures, plus update_action_items / add_todos / log_content as
   needed, then confirm in one sentence. If she says "skip today" / "don't count today": skip_day, stop.

STRATEGY SESSION — when she says "let's talk AD" / "help me think about the wedding". Call
get_arc first. This is rubber-ducking, not tracking: read back the objective and open items, ask
what's actually blocking, help her sequence or cut. If the conversation starts circling (same two
options going back and forth), say so and offer: "want to take some time to write this down and
come back?" Two endings, both fine:
  - She writes it out (paper or wherever): end now; save a one-line add_arc_note(kind="strategy")
    like "deciding between X and Y — Taylor writing it out", so the next session knows where she
    left off. If she later pastes the text or shares a photo in a text chat, save it verbatim as
    add_arc_note(kind="writeout"). On-paper-only is fine; then only what she decided gets saved.
  - She'd rather hash it out here: keep going, and end with new/changed items via
    update_action_items and a short gist as add_arc_note(kind="strategy").
Don't invent items. Don't push her to hand over the writing.

WEEKLY REVIEW — call weekly_review. Read the flags, not the whole payload. Lead with the 2-3 things
that changed: drift, measures that moved, arcs that went quiet. For each active arc ask the `ask`
question and record the answer with record_reflection. Quote returned numbers; never invent any.

CONFIG IS READ-ONLY HERE. Which habits exist, their cadences, which measures, which arcs and their
status all live in config.yaml in the repo. If Taylor asks to change those ("make drills 5x a week",
"put NS back on", "add omega-3"), don't pretend to save it: say it needs a config edit and offer to
note it (add_todo) so it gets applied in a text chat. Action items, todos, notes and content ARE
yours to write.

Keep every question short. One question at a time. Mirror her words.
"""

TOKEN = os.environ.get("ACCESS_TOKEN") or ("" if os.environ.get("VERCEL") else "dev-token")
if not TOKEN:
    raise RuntimeError("ACCESS_TOKEN is not set. On Vercel: Settings -> Environment Variables, add it, then Redeploy.")
mcp = MCPServer("daily-data", instructions=INSTRUCTIONS)

LOCAL_TZ = os.environ.get("TZ_NAME", "America/Chicago")
DAY_START_HOUR = int(os.environ.get("DAY_START_HOUR", "4"))  # a 1am debrief still counts as "yesterday"


def _today() -> date:
    """Taylor's local date, not the server's (Vercel runs in UTC)."""
    if os.environ.get("FAKE_TODAY"):
        return date.fromisoformat(os.environ["FAKE_TODAY"])
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    return (datetime.now(ZoneInfo(LOCAL_TZ)) - timedelta(hours=DAY_START_HOUR)).date()


def _parse(d: Optional[str]) -> date:
    return date.fromisoformat(d) if d else _today()


# ---------------------------------------------------------------- tools


@mcp.tool()
def start_debrief(day: Optional[str] = None, mode: Optional[str] = None, available: Optional[list[str]] = None) -> dict:
    """Begin the daily debrief (default: today, Taylor's local date). Pass mode once known; `available` =
    resources on hand (piano, gym, trainer), else the mode's defaults apply. Returns, in asking
    order: habits (each with a suggested variant), measures, active arcs with open items, open todos."""
    cfg = load_config()
    d = _parse(day)
    with session() as s:
        dayrow = ensure_day(s, d)
        if mode:
            dayrow.mode = mode
            if mode == "travel":
                dayrow.exempt, dayrow.exempt_reason = True, "travel"
        if available is not None:
            dayrow.available = available
        elif dayrow.mode and not dayrow.available:
            dayrow.available = cfg.get("default_available", {}).get(dayrow.mode, [])
        ci = Debrief(day=d, started_at=datetime.utcnow())
        s.add(ci)
        s.flush()
        if not dayrow.mode:
            return {"debrief_id": ci.id, "day": d.isoformat(), "modes": cfg.get("modes"),
                    "next": "Ask which mode today was, then call start_debrief again with mode."}

        logged = {(e.target_type, e.target_id) for e in s.scalars(select(Entry).where(Entry.day == d))}

        habits_out = []
        for h in s.scalars(select(Habit).where(Habit.active == True)).all():  # noqa: E712
            if h.modes and dayrow.mode not in h.modes:
                continue
            sug = suggest_variant(s, h, dayrow.available, d)
            elig = eligible_variants(h, dayrow.available)
            habits_out.append({
                "habit": h.id, "name": h.name, "group": h.group, "cadence": h.cadence, "track": h.track,
                "target": h.target, "anchor": h.anchor, "already_logged": ("habit", h.id) in logged,
                "suggest": {"variant": sug["variant"].id, "name": sug["variant"].name, "why": sug["reason"]} if sug["variant"] else None,
                "eligible": [{"variant": v.id, "name": v.name} for v in elig],
            })
        habits_out.sort(key=lambda h: (h["group"] or "zz", h["habit"]))

        measures_out = [
            {"measure": m.id, "name": m.name, "kind": m.kind, "levels": m.levels, "unit": m.unit,
             "already_logged": ("measure", m.id) in logged}
            for m in s.scalars(select(Measure).where(Measure.active == True)).all()  # noqa: E712
            if not m.modes or dayrow.mode in m.modes
        ]

        arcs_out = []
        if not dayrow.exempt:
            for a in s.scalars(select(Arc).where(Arc.status == "active")).all():
                snap = arc_snapshot(s, a, d)
                arcs_out.append({"arc": a.id, "name": a.name, "priority": a.priority,
                                 "open_items": snap["open_items"], "days_quiet": snap["days_quiet"]})
            arcs_out.sort(key=lambda a: {"top": 0, "high": 1}.get(a["priority"] or "", 2))

        todos = s.scalars(select(Todo).where(Todo.status == "open").order_by(Todo.added_on)).all()
        return {
            "debrief_id": ci.id, "day": d.isoformat(), "mode": dayrow.mode, "exempt": dayrow.exempt,
            "available": dayrow.available,
            "habits": habits_out, "measures": measures_out, "arcs": arcs_out,
            "open_todos": [{"id": t.id, "text": t.text} for t in todos],
            "ask_content": not dayrow.exempt,
        }


@mcp.tool()
def log_entries(debrief_id: int, entries: list[dict], raw_notes: Optional[str] = None) -> dict:
    """Record habits and measures from a debrief in one call. Each entry is one of:
      habit:   {"target_type":"habit","target_id":"piano_drills","variant_id":"piano_drills:scales","result":"done"|"skipped","note":"..."}
      count:   same, plus "value": 2   (e.g. climbed twice)
      measure: {"target_type":"measure","target_id":"picking","value":3}  or  {"target_type":"measure","target_id":"phone_video","label":"some"}
    raw_notes = the gist of what she said, for later re-reading. Re-logging the same target replaces."""
    with session() as s:
        ci = s.get(Debrief, debrief_id)
        if not ci:
            return {"error": f"no debrief {debrief_id}"}
        measures = {m.id: m for m in s.scalars(select(Measure)).all()}
        written, problems = 0, []
        for e in entries:
            tt, tid = e.get("target_type"), e.get("target_id")
            if tt not in ("habit", "measure") or not tid:
                problems.append(f"bad entry: {e}")
                continue
            value = e.get("value")
            if tt == "measure":
                m = measures.get(tid)
                if not m:
                    problems.append(f"unknown measure {tid}")
                    continue
                if value is None and e.get("label") is not None:
                    try:
                        value = m.levels.index(str(e["label"]).lower())
                    except ValueError:
                        problems.append(f"{tid}: label must be one of {m.levels}")
                        continue
                if value is None:
                    problems.append(f"{tid}: needs value or label")
                    continue
            for old in s.scalars(select(Entry).where(Entry.day == ci.day, Entry.target_type == tt, Entry.target_id == tid)).all():
                s.delete(old)
            s.add(Entry(day=ci.day, debrief_id=ci.id, target_type=tt, target_id=tid,
                        variant_id=e.get("variant_id"), result=e.get("result", "done") if tt == "habit" else None,
                        value=float(value) if value is not None else None, note=e.get("note")))
            written += 1
        ci.completed_at = datetime.utcnow()
        if raw_notes:
            ci.raw_notes = raw_notes
        return {"ok": not problems, "day": ci.day.isoformat(), "entries": written, "problems": problems}


@mcp.tool()
def skip_day(day: Optional[str] = None, reason: str = "not counting today") -> dict:
    """Mark a day exempt: it drops out of every rate. For travel, sickness, or 'don't count today'."""
    d = _parse(day)
    with session() as s:
        dayrow = ensure_day(s, d)
        dayrow.exempt, dayrow.exempt_reason = True, reason
    return {"ok": True, "day": d.isoformat(), "exempt": True}


# ---- arcs


@mcp.tool()
def get_arc(arc_id: str) -> dict:
    """Everything needed for a strategy session on one arc: objective, open items, recent done items, recent notes."""
    with session() as s:
        arc = s.get(Arc, arc_id)
        if not arc:
            return {"error": f"no arc {arc_id}", "arcs": [a.id for a in s.scalars(select(Arc)).all()]}
        return arc_snapshot(s, arc, _today(), recent_days=60)


@mcp.tool()
def update_action_items(arc_id: str, add: Optional[list[str]] = None, done: Optional[list[int]] = None,
                        dropped: Optional[list[int]] = None, note: Optional[str] = None, day: Optional[str] = None) -> dict:
    """Add new action items (texts) and/or close existing ones (ids) as done or dropped. Optional note is
    saved as an arc update for the day. Use ids from start_debrief / get_arc; never invent them."""
    d = _parse(day)
    with session() as s:
        arc = s.get(Arc, arc_id)
        if not arc:
            return {"error": f"no arc {arc_id}"}
        added = []
        for text in add or []:
            item = ActionItem(arc_id=arc_id, text=text, status="open", added_on=d)
            s.add(item)
            s.flush()
            added.append({"id": item.id, "text": text})
        closed, missing = [], []
        for status, ids in (("done", done or []), ("dropped", dropped or [])):
            for iid in ids:
                item = s.get(ActionItem, iid)
                if not item or item.arc_id != arc_id:
                    missing.append(iid)
                    continue
                item.status, item.closed_on = status, d
                closed.append({"id": iid, "text": item.text, "status": status})
        if note:
            s.add(ArcNote(arc_id=arc_id, day=d, kind="update", text=note))
        return {"ok": not missing, "added": added, "closed": closed, "unknown_ids": missing}


@mcp.tool()
def add_arc_note(arc_id: str, text: str, kind: str = "strategy", day: Optional[str] = None) -> dict:
    """Save the gist of a strategy session or a write-out on an arc. kind: strategy | writeout | update."""
    d = _parse(day)
    with session() as s:
        if not s.get(Arc, arc_id):
            return {"error": f"no arc {arc_id}"}
        s.add(ArcNote(arc_id=arc_id, day=d, kind=kind, text=text))
    return {"ok": True, "arc": arc_id, "day": d.isoformat()}


# ---- todos


@mcp.tool()
def add_todos(items: list[str], day: Optional[str] = None) -> dict:
    """Add one-off housekeeping todos (boots for Noah, car paperwork, order brisket). Not tracked for adherence."""
    d = _parse(day)
    with session() as s:
        out = []
        for text in items:
            t = Todo(text=text, status="open", added_on=d)
            s.add(t)
            s.flush()
            out.append({"id": t.id, "text": text})
    return {"ok": True, "added": out}


@mcp.tool()
def update_todos(done: Optional[list[int]] = None, dropped: Optional[list[int]] = None, day: Optional[str] = None) -> dict:
    """Close todos by id as done or dropped."""
    d = _parse(day)
    with session() as s:
        closed, missing = [], []
        for status, ids in (("done", done or []), ("dropped", dropped or [])):
            for tid in ids:
                t = s.get(Todo, tid)
                if not t:
                    missing.append(tid)
                    continue
                t.status, t.closed_on = status, d
                closed.append({"id": tid, "text": t.text, "status": status})
        return {"ok": not missing, "closed": closed, "unknown_ids": missing}


@mcp.tool()
def list_todos(include_closed_days: int = 0) -> dict:
    """Open todos (oldest first), optionally plus ones closed in the last N days."""
    today = _today()
    with session() as s:
        rows = s.scalars(select(Todo).order_by(Todo.added_on)).all()
        return {
            "open": [{"id": t.id, "text": t.text, "age_days": (today - t.added_on).days} for t in rows if t.status == "open"],
            "recently_closed": [{"id": t.id, "text": t.text, "status": t.status, "closed_on": t.closed_on.isoformat()}
                                for t in rows if t.status != "open" and t.closed_on
                                and (today - t.closed_on).days <= include_closed_days],
        }


# ---- content


@mcp.tool()
def log_content(items: list[dict], day: Optional[str] = None) -> dict:
    """Store what she read / listened to / watched that stuck. Each item: {"text": "...", "kind": "read"|"listened"|"watched"|"other", "note": "what stuck"}."""
    d = _parse(day)
    with session() as s:
        n = 0
        for it in items:
            if not it.get("text"):
                continue
            s.add(ContentLog(day=d, kind=it.get("kind", "other"), text=it["text"], note=it.get("note")))
            n += 1
    return {"ok": True, "day": d.isoformat(), "stored": n}


# ---- review & history


@mcp.tool()
def weekly_review(week_start: Optional[str] = None, window_weeks: int = 4) -> dict:
    """Build the review for the week starting Monday `week_start` (default: current week). Habit rates are over
    eligible days in a rolling window (exempt days excluded); measures compare this week to the prior 3; arcs
    flag quiet weeks and suggest backburner after 3. Read `off_this_week`, `drift`, `measures_worse/better`, `arcs_quiet`,
    `arcs_suggest_backburner`, then each arc's `ask`."""
    from datetime import timedelta
    ws = _parse(week_start)
    ws = ws - timedelta(days=ws.weekday())
    with session() as s:
        payload = build_review(s, ws, _today(), window_weeks)
        r = s.get(Review, ws) or Review(week_start=ws, payload=payload, generated_at=datetime.utcnow())
        r.payload, r.generated_at = payload, datetime.utcnow()
        s.add(r)
    return payload


@mcp.tool()
def record_reflection(week_start: str, text: str) -> dict:
    """Store what Taylor said in the weekly review (does each arc still matter, what she wants to change)."""
    ws = date.fromisoformat(week_start)
    with session() as s:
        r = s.get(Review, ws)
        if not r:
            return {"error": "run weekly_review first"}
        r.reflection = ((r.reflection or "") + "\n" + text).strip()
    return {"ok": True}


@mcp.tool()
def recent_history(days: int = 14, include: Optional[list[str]] = None) -> dict:
    """Raw history for the last N days, newest first. include = any of
    ["entries", "notes", "arc_notes", "action_items", "todos", "content"]; default all."""
    from datetime import timedelta
    start = _today() - timedelta(days=days)
    want = set(include or ["entries", "notes", "arc_notes", "action_items", "todos", "content"])
    out: dict = {}
    with session() as s:
        if "entries" in want:
            out["entries"] = [{"day": e.day.isoformat(), "target": f"{e.target_type}:{e.target_id}", "variant": e.variant_id,
                               "result": e.result, "value": e.value, "note": e.note}
                              for e in s.scalars(select(Entry).where(Entry.day >= start).order_by(Entry.day.desc()))]
        if "notes" in want:
            out["debrief_notes"] = [{"day": c.day.isoformat(), "raw": c.raw_notes}
                                    for c in s.scalars(select(Debrief).where(Debrief.day >= start).order_by(Debrief.day.desc())) if c.raw_notes]
        if "arc_notes" in want:
            out["arc_notes"] = [{"day": n.day.isoformat(), "arc": n.arc_id, "kind": n.kind, "text": n.text}
                                for n in s.scalars(select(ArcNote).where(ArcNote.day >= start).order_by(ArcNote.day.desc()))]
        if "action_items" in want:
            out["action_items_changed"] = [{"arc": i.arc_id, "id": i.id, "text": i.text, "status": i.status,
                                            "added_on": i.added_on.isoformat(), "closed_on": i.closed_on.isoformat() if i.closed_on else None}
                                           for i in s.scalars(select(ActionItem)) if i.added_on >= start or (i.closed_on and i.closed_on >= start)]
        if "todos" in want:
            out["todos_changed"] = [{"id": t.id, "text": t.text, "status": t.status, "added_on": t.added_on.isoformat()}
                                    for t in s.scalars(select(Todo)) if t.added_on >= start or (t.closed_on and t.closed_on >= start)]
        if "content" in want:
            out["content"] = [{"day": c.day.isoformat(), "kind": c.kind, "text": c.text, "note": c.note}
                              for c in s.scalars(select(ContentLog).where(ContentLog.day >= start).order_by(ContentLog.day.desc()))]
    return out


@mcp.tool()
def show_config() -> dict:
    """Current habits, measures and arcs as configured (read-only here; edit config.yaml to change)."""
    with session() as s:
        return {
            "habits": [{"id": h.id, "name": h.name, "group": h.group, "cadence": h.cadence, "track": h.track, "modes": h.modes,
                        "target": h.target,
                        "variants": [{"id": v.id, "requires": v.requires, "kind": v.kind, "focus": v.focus} for v in h.variants if v.active]}
                       for h in s.scalars(select(Habit).where(Habit.active == True)).all()],  # noqa: E712
            "measures": [{"id": m.id, "name": m.name, "kind": m.kind, "levels": m.levels, "unit": m.unit, "direction": m.direction}
                         for m in s.scalars(select(Measure).where(Measure.active == True)).all()],  # noqa: E712
            "arcs": [{"id": a.id, "name": a.name, "status": a.status, "priority": a.priority, "objective": a.objective}
                     for a in s.scalars(select(Arc)).all()],
        }


# ---------------------------------------------------------------- app


def build_app() -> Starlette:
    init_db()
    from .seed import sync
    sync(load_config(), today=_today())
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    inner = mcp.streamable_http_app(streamable_http_path="/mcp", transport_security=security, stateless_http=True)

    async def health(_):
        return JSONResponse({"ok": True})

    static = os.path.join(os.path.dirname(__file__), "static")

    async def favicon(_):
        return FileResponse(os.path.join(static, "favicon.png"), media_type="image/png")

    async def icon(_):
        return FileResponse(os.path.join(static, "icon.svg"), media_type="image/svg+xml")

    async def index(_):
        return FileResponse(os.path.join(static, "index.html"), media_type="text/html")

    async def nope(_):
        return JSONResponse({"error": "not found"}, status_code=404)

    # The session manager normally starts in the ASGI lifespan. Serverless hosts (Vercel) may never
    # send lifespan events, so start it lazily on the first request and leave it running.
    import asyncio

    state: dict = {"task": None, "ready": None}

    async def _runner(ready: asyncio.Event):
        # The task group inside session_manager.run() must live in its own task, not a request's.
        async with mcp.session_manager.run():
            ready.set()
            await asyncio.Event().wait()  # run forever

    async def ensure_started():
        if state["task"] is None or state["task"].done():
            state["ready"] = asyncio.Event()
            state["task"] = asyncio.create_task(_runner(state["ready"]))
        await state["ready"].wait()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lazy_lifespan(app):
        await ensure_started()
        yield

    import hmac

    def authorized(scope) -> bool:
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        supplied = auth[7:].strip() if auth.lower().startswith("bearer ") else headers.get("x-access-token", "")
        return bool(supplied) and hmac.compare_digest(supplied, TOKEN)

    class Guarded:
        """Requires the token in a header, then lazily starts the MCP session manager."""

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                if not authorized(scope):
                    await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                    return
                await ensure_started()
            await self.app(scope, receive, send)

    return Starlette(
        routes=[
            Route("/", index),
            Route("/health", health),
            Route("/favicon.ico", favicon),
            Route("/favicon.png", favicon),
            Route("/icon.svg", icon),
            Route("/mcp", endpoint=Guarded(inner), methods=["GET", "POST", "DELETE"]),
            Route("/{rest:path}", nope, methods=["GET", "POST", "PUT", "DELETE", "PATCH"]),
        ],
        lifespan=lazy_lifespan,
    )


app = build_app()

if __name__ == "__main__":
    uvicorn.run("app.server:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
