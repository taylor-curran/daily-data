"""MCP server exposing the accountability system.

Run locally:   python -m app.server            (streamable HTTP on :8000)
Connect from claude.ai as a custom connector at  https://<host>/<ACCESS_TOKEN>/mcp
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
from starlette.routing import Mount, Route

from .db import init_db, session
from .logic import active_outcome, build_review, ensure_day, suggest_variant, eligible_variants
from .models import CheckIn, Entry, Habit, Outcome, Project, Review
from .seed import load_config

INSTRUCTIONS = """
You are Taylor's accountability partner. You run short, spoken-friendly check-ins and weekly reviews
over a habit + project tracker. Rules of engagement:

CHECK-IN (call start_checkin first, every time):
1. Ask the day's mode (home / office / weekend / travel) and whether anything unusual was available
   or unavailable (piano, gym, kitchen...). Travel days are exempt — just log mode and move on.
2. Walk the habits in the order returned. For each, lead with the suggested variant
   ("Did you get to the rhythm drill? It's been 9 days.") and accept any eligible variant instead.
   If they didn't do the full thing, ask whether they did the minimum (maintenance variant).
   Skipped is fine; never scold.
3. Then ask about each active project: "Anything on AD today?" If yes, ask what, and classify the
   relation: advances (moved the current outcome), tangential (related but didn't move it),
   maintenance, exploring. Say your classification out loud so they can correct it.
4. Call log_entries ONCE with everything, then confirm in one sentence.

Keep every question short. One question at a time. Mirror their words, don't paraphrase into jargon.
If they say "skip today" or "don't count today", call skip_day and stop.

WEEKLY REVIEW (call weekly_review): read the flags, not the whole payload. Lead with the 2-3 things
that changed. For each active project ask the `ask` question and record the answer with
record_reflection. Never invent numbers; quote the ones returned.

Outcomes are binary: set with set_outcome, finish with complete_outcome. One active per target.
"""

TOKEN = os.environ.get("ACCESS_TOKEN") or ("" if os.environ.get("VERCEL") else "dev-token")
if not TOKEN:
    raise RuntimeError("ACCESS_TOKEN is not set. On Vercel: Settings -> Environment Variables, add it, then Redeploy.")
mcp = MCPServer("accountability", instructions=INSTRUCTIONS)


def _today() -> date:
    return date.fromisoformat(os.environ["FAKE_TODAY"]) if os.environ.get("FAKE_TODAY") else date.today()


def _parse(d: Optional[str]) -> date:
    return date.fromisoformat(d) if d else _today()


# ---------------------------------------------------------------- tools


@mcp.tool()
def start_checkin(day: Optional[str] = None, mode: Optional[str] = None, available: Optional[list[str]] = None) -> dict:
    """Begin a check-in for a day (default today). Pass mode once known; available = resources on hand
    (piano, gym, kitchen, trainer, metronome, climbing_gym). Returns the habits to ask about, each with a
    suggested variant and the other eligible variants, plus active projects with their current outcome."""
    cfg = load_config()
    d = _parse(day)
    with session() as s:
        dayrow = ensure_day(s, d)
        if mode:
            dayrow.mode = mode
            if mode == "travel":
                dayrow.exempt = True
                dayrow.exempt_reason = "travel"
        if available is not None:
            dayrow.available = available
        elif dayrow.mode and not dayrow.available:
            dayrow.available = cfg.get("default_available", {}).get(dayrow.mode, [])
        ci = CheckIn(day=d, started_at=datetime.utcnow())
        s.add(ci)
        s.flush()

        if not dayrow.mode:
            return {
                "checkin_id": ci.id,
                "day": d.isoformat(),
                "next": "Ask which mode today is: home, office, weekend, or travel. Then call start_checkin again with mode.",
                "modes": cfg.get("modes"),
            }
        if dayrow.exempt:
            return {"checkin_id": ci.id, "day": d.isoformat(), "mode": dayrow.mode, "exempt": True,
                    "next": "Travel day — exempt. Optionally ask only about supplements, then stop."}

        habits_out = []
        for h in s.scalars(select(Habit).where(Habit.active == True)).all():  # noqa: E712
            if h.modes and dayrow.mode not in h.modes:
                continue
            already = s.scalars(select(Entry).where(Entry.target_type == "habit", Entry.target_id == h.id, Entry.day == d)).first()
            sug = suggest_variant(s, h, dayrow.mode, dayrow.available, d)
            elig = eligible_variants(h, dayrow.available)
            oc = active_outcome(s, "habit", h.id)
            habits_out.append({
                "habit": h.id,
                "name": h.name,
                "cadence": h.cadence,
                "anchor": h.anchor,
                "already_logged": bool(already),
                "suggest": {"variant": sug["variant"].id, "name": sug["variant"].name, "why": sug["reason"]} if sug["variant"] else None,
                "eligible": [{"variant": v.id, "name": v.name, "kind": v.kind} for v in elig],
                "outcome": oc.title if oc else None,
            })
        # ask about progress-capable habits first, then quick binaries
        habits_out.sort(key=lambda h: (0 if any(e["kind"] == "progress" for e in h["eligible"]) else 1, h["habit"]))

        projects_out = []
        for p in s.scalars(select(Project).where(Project.status == "active")).all():
            oc = active_outcome(s, "project", p.id)
            projects_out.append({"project": p.id, "name": p.name, "priority": p.priority, "spirit": p.spirit,
                                 "outcome": {"id": oc.id, "title": oc.title, "horizon": oc.horizon} if oc else None})
        projects_out.sort(key=lambda p: {"top": 0, "medium": 1, "low": 2}.get(p["priority"] or "", 3))

        return {"checkin_id": ci.id, "day": d.isoformat(), "mode": dayrow.mode, "available": dayrow.available,
                "habits": habits_out, "projects": projects_out,
                "relations": ["advances", "tangential", "maintenance", "exploring"]}


@mcp.tool()
def log_entries(checkin_id: int, entries: list[dict], raw_notes: Optional[str] = None) -> dict:
    """Record everything from a check-in in one call. Each entry:
    {"target_type": "habit"|"project", "target_id": "piano", "variant_id": "piano:rhythm" (habits),
     "result": "done"|"skipped", "relation": "advances"|"tangential"|"maintenance"|"exploring" (projects),
     "note": "..."}. raw_notes = the gist of what was said, for later re-reading."""
    with session() as s:
        ci = s.get(CheckIn, checkin_id)
        if not ci:
            return {"error": f"no checkin {checkin_id}"}
        written = 0
        for e in entries:
            tt, tid = e["target_type"], e["target_id"]
            oc = active_outcome(s, tt, tid)
            # replace any earlier entry for the same target/day so re-logging is idempotent
            for old in s.scalars(select(Entry).where(Entry.day == ci.day, Entry.target_type == tt, Entry.target_id == tid)).all():
                s.delete(old)
            s.add(Entry(day=ci.day, checkin_id=ci.id, target_type=tt, target_id=tid,
                        variant_id=e.get("variant_id"), result=e.get("result", "done"),
                        relation=e.get("relation"), outcome_id=oc.id if oc and e.get("relation") == "advances" else None,
                        note=e.get("note")))
            written += 1
        ci.completed_at = datetime.utcnow()
        ci.raw_notes = raw_notes
        return {"ok": True, "day": ci.day.isoformat(), "entries": written}


@mcp.tool()
def skip_day(day: Optional[str] = None, reason: str = "not counting today") -> dict:
    """Mark a day exempt: it drops out of every rate calculation. Use for travel, sickness, or 'don't count today'."""
    d = _parse(day)
    with session() as s:
        dayrow = ensure_day(s, d)
        dayrow.exempt, dayrow.exempt_reason = True, reason
    return {"ok": True, "day": d.isoformat(), "exempt": True}


@mcp.tool()
def set_outcome(target_type: str, target_id: str, title: str, horizon: Optional[str] = None, notes: Optional[str] = None) -> dict:
    """Set the one active outcome for a project or habit (e.g. project AD: '2-minute pitch I can deliver cold', 'by Nov 1';
    habit piano: 'Clair de Lune'). Any previous active outcome is parked, not deleted."""
    with session() as s:
        prev = active_outcome(s, target_type, target_id)
        if prev:
            prev.status = "parked"
        oc = Outcome(target_type=target_type, target_id=target_id, title=title, horizon=horizon, notes=notes, set_on=_today())
        s.add(oc)
        s.flush()
        return {"ok": True, "outcome_id": oc.id, "parked_previous": prev.title if prev else None}


@mcp.tool()
def complete_outcome(outcome_id: int, note: Optional[str] = None) -> dict:
    """Mark an outcome done. Binary — they say it's mastered/finished, it is."""
    with session() as s:
        oc = s.get(Outcome, outcome_id)
        if not oc:
            return {"error": "no such outcome"}
        oc.status, oc.done_on = "done", _today()
        if note:
            oc.notes = (oc.notes or "") + f"\n[done] {note}"
        return {"ok": True, "title": oc.title, "done_on": oc.done_on.isoformat()}


@mcp.tool()
def set_project_status(project_id: str, status: str, note: Optional[str] = None) -> dict:
    """active | parked | killed | done. Parking is the honest alternative to silently dropping."""
    with session() as s:
        p = s.get(Project, project_id)
        if not p:
            return {"error": "no such project"}
        p.status = status
        return {"ok": True, "project": p.id, "status": status}


@mcp.tool()
def weekly_review(week_start: Optional[str] = None, window_weeks: int = 4) -> dict:
    """Build the review for the week starting Monday `week_start` (default: current week). Rates are over
    eligible days in a rolling window; exempt days are excluded. Read `drift`, `hanging_on`, `dropped`,
    `busy_not_moving` first, then each project's `ask`."""
    ws = _parse(week_start)
    ws = ws - __import__("datetime").timedelta(days=ws.weekday())
    with session() as s:
        payload = build_review(s, ws, window_weeks)
        r = s.get(Review, ws) or Review(week_start=ws, payload=payload, generated_at=datetime.utcnow())
        r.payload, r.generated_at = payload, datetime.utcnow()
        s.add(r)
    return payload


@mcp.tool()
def record_reflection(week_start: str, text: str) -> dict:
    """Store what Taylor said when asked whether each project still matters."""
    ws = date.fromisoformat(week_start)
    with session() as s:
        r = s.get(Review, ws)
        if not r:
            return {"error": "run weekly_review first"}
        r.reflection = ((r.reflection or "") + "\n" + text).strip()
    return {"ok": True}


@mcp.tool()
def recent_history(days: int = 14) -> dict:
    """Raw entries and check-in notes for the last N days, newest first. For 'what did I say about X'."""
    from datetime import timedelta
    start = _today() - timedelta(days=days)
    with session() as s:
        ents = s.scalars(select(Entry).where(Entry.day >= start).order_by(Entry.day.desc())).all()
        cis = s.scalars(select(CheckIn).where(CheckIn.day >= start).order_by(CheckIn.day.desc())).all()
        return {
            "entries": [{"day": e.day.isoformat(), "target": f"{e.target_type}:{e.target_id}", "variant": e.variant_id,
                         "result": e.result, "relation": e.relation, "note": e.note} for e in ents],
            "notes": [{"day": c.day.isoformat(), "raw": c.raw_notes} for c in cis if c.raw_notes],
        }


@mcp.tool()
def show_config() -> dict:
    """Current habits, variants, cadences, projects and outcomes."""
    with session() as s:
        habits = s.scalars(select(Habit).where(Habit.active == True)).all()  # noqa: E712
        projects = s.scalars(select(Project)).all()
        outcomes = s.scalars(select(Outcome).where(Outcome.status == "active")).all()
        return {
            "habits": [{"id": h.id, "cadence": h.cadence, "modes": h.modes,
                        "variants": [{"id": v.id, "requires": v.requires, "kind": v.kind, "focus": v.focus} for v in h.variants if v.active]}
                       for h in habits],
            "projects": [{"id": p.id, "status": p.status, "priority": p.priority, "spirit": p.spirit} for p in projects],
            "outcomes": [{"id": o.id, "target": f"{o.target_type}:{o.target_id}", "title": o.title, "horizon": o.horizon} for o in outcomes],
        }


# ---------------------------------------------------------------- app


def build_app() -> Starlette:
    init_db()
    from .seed import sync
    sync(load_config())
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    inner = mcp.streamable_http_app(streamable_http_path="/mcp", transport_security=security, stateless_http=True)

    async def health(_):
        return JSONResponse({"ok": True})

    static = os.path.join(os.path.dirname(__file__), "static")

    async def favicon(_):
        return FileResponse(os.path.join(static, "favicon.png"), media_type="image/png")

    async def icon(_):
        return FileResponse(os.path.join(static, "icon.svg"), media_type="image/svg+xml")

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

    class LazyStart:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                await ensure_started()
            await self.app(scope, receive, send)

    # Token lives in the URL path because claude.ai custom connectors can't set arbitrary headers.
    return Starlette(
        routes=[Route("/health", health), Route("/favicon.ico", favicon), Route("/favicon.png", favicon), Route("/icon.svg", icon), Mount(f"/{TOKEN}", app=LazyStart(inner)), Route("/{rest:path}", nope)],
        lifespan=lazy_lifespan,
    )


app = build_app()

if __name__ == "__main__":
    uvicorn.run("app.server:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
