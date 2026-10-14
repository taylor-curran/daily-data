"""Pure-ish logic: cadence evaluation, variant eligibility, rotation, review signals."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Day, Entry, Habit, Outcome, Project, Variant

# ---------- cadence ----------

_CADENCE_RE = re.compile(r"^(\d+)x/(week|2weeks|month)$")


def expected_per_day(cadence: str) -> float:
    """How many 'done' days a cadence implies per eligible day."""
    if cadence == "daily":
        return 1.0
    m = re.match(r"^every_(\d+)_days$", cadence)
    if m:
        return 1.0 / int(m.group(1))
    m = _CADENCE_RE.match(cadence)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return n / {"week": 7, "2weeks": 14, "month": 30}[unit]
    raise ValueError(f"unknown cadence: {cadence}")


# ---------- days ----------


def ensure_day(s: Session, d: date) -> Day:
    day = s.get(Day, d)
    if not day:
        day = Day(date=d, available=[])
        s.add(day)
        s.flush()
    return day


def days_in_window(s: Session, start: date, end: date) -> dict[date, Day]:
    rows = s.scalars(select(Day).where(Day.date >= start, Day.date <= end)).all()
    return {r.date: r for r in rows}


# ---------- variants ----------


def eligible_variants(habit: Habit, available: list[str]) -> list[Variant]:
    avail = set(available)
    return [v for v in habit.variants if v.active and set(v.requires) <= avail]


def last_focus_dates(s: Session, habit_id: str, before: date) -> dict[str, date]:
    """Most recent done-date per focus for a habit."""
    rows = s.execute(
        select(Entry.variant_id, Entry.day)
        .where(Entry.target_type == "habit", Entry.target_id == habit_id, Entry.result == "done", Entry.day < before)
        .order_by(Entry.day.desc())
    ).all()
    focus_of = {v.id: v.focus for v in s.scalars(select(Variant).where(Variant.habit_id == habit_id))}
    out: dict[str, date] = {}
    for vid, d in rows:
        f = focus_of.get(vid)
        if f and f not in out:
            out[f] = d
    return out


def suggest_variant(s: Session, habit: Habit, mode: str | None, available: list[str], today: date) -> dict:
    """Pick what the check-in should ask about first."""
    elig = eligible_variants(habit, available)
    if not elig:
        return {"variant": None, "reason": "nothing eligible with what's available"}
    pref = (habit.default_by_mode or {}).get(mode or "", None)
    progress = [v for v in elig if v.kind == "progress"]
    if pref == "progress" or (pref is None and progress and habit.rotation):
        if progress:
            if habit.rotation == "least_recent_focus":
                last = last_focus_dates(s, habit.id, today)
                never = [v for v in progress if v.focus and v.focus not in last]
                if never:
                    v = never[0]
                    return {"variant": v, "reason": f"{v.focus} never done"}
                v = min(progress, key=lambda v: last.get(v.focus, date.min))
                gap = (today - last[v.focus]).days if v.focus in last else None
                return {"variant": v, "reason": f"{gap} days since {v.focus}" if gap is not None else "rotation"}
            return {"variant": progress[0], "reason": "progress variant"}
    if pref:
        for v in elig:
            if v.key == pref:
                return {"variant": v, "reason": f"default for {mode}"}
    # fall back: first eligible maintenance, else anything
    maint = [v for v in elig if v.kind == "maintenance"]
    v = maint[0] if maint else elig[0]
    return {"variant": v, "reason": "fallback"}


# ---------- outcomes ----------


def active_outcome(s: Session, target_type: str, target_id: str) -> Outcome | None:
    return s.scalars(
        select(Outcome).where(
            Outcome.target_type == target_type, Outcome.target_id == target_id, Outcome.status == "active"
        )
    ).first()


# ---------- review ----------


def habit_report(s: Session, habit: Habit, start: date, end: date, days: dict[date, Day], week_start: date | None = None) -> dict:
    eligible = [
        d for d in days.values()
        if not d.exempt and d.mode and (not habit.modes or d.mode in habit.modes)
    ]
    n_elig = len(eligible)
    entries = s.scalars(
        select(Entry).where(
            Entry.target_type == "habit", Entry.target_id == habit.id, Entry.day >= start, Entry.day <= end
        )
    ).all()
    done = [e for e in entries if e.result == "done"]
    done_days = {e.day for e in done}
    expected = expected_per_day(habit.cadence) * n_elig
    variants = {v.id: v for v in habit.variants}
    kinds = Counter(variants[e.variant_id].kind if e.variant_id in variants else "unknown" for e in done)
    focus = Counter(variants[e.variant_id].focus for e in done if e.variant_id in variants and variants[e.variant_id].focus)
    rate = min(len(done_days) / expected, 1.0) if expected else None
    # "hanging on" is judged on the review week only, so a good earlier week can't mask a thin one
    ws = week_start or start
    week_done = [e for e in done if e.day >= ws]
    week_kinds = Counter(variants[e.variant_id].kind if e.variant_id in variants else "unknown" for e in week_done)
    flags = []
    if expected and rate is not None and rate < 0.6:
        flags.append("drift")
    if week_done and week_kinds.get("progress", 0) == 0 and any(v.kind == "progress" for v in habit.variants):
        flags.append("all_maintenance")
    if n_elig == 0:
        flags.append("no_eligible_days")
    return {
        "habit": habit.id,
        "name": habit.name,
        "cadence": habit.cadence,
        "eligible_days": n_elig,
        "expected": round(expected, 1),
        "done_days": len(done_days),
        "rate": round(rate, 2) if rate is not None else None,
        "kinds": dict(kinds),
        "this_week_kinds": dict(week_kinds),
        "focus_coverage": dict(focus),
        "flags": flags,
    }


def project_report(s: Session, proj: Project, start: date, end: date, prior_reviews: int = 2) -> dict:
    entries = s.scalars(
        select(Entry).where(
            Entry.target_type == "project", Entry.target_id == proj.id, Entry.day >= start, Entry.day <= end
        )
    ).all()
    rel = Counter(e.relation or "unspecified" for e in entries)
    outcome = active_outcome(s, "project", proj.id)
    flags = []
    if not entries:
        flags.append("no_activity")  # actually dropped?
    elif rel.get("advances", 0) == 0:
        flags.append("busy_not_moving")  # ER-style
    if outcome:
        age_weeks = (end - outcome.set_on).days // 7
        if age_weeks >= 3 and rel.get("advances", 0) == 0:
            flags.append("stuck_outcome")
    else:
        flags.append("no_outcome")
    last_entry = max((e.day for e in entries), default=None)
    return {
        "project": proj.id,
        "name": proj.name,
        "priority": proj.priority,
        "spirit": proj.spirit,
        "outcome": {"title": outcome.title, "horizon": outcome.horizon, "set_on": outcome.set_on.isoformat()} if outcome else None,
        "entries": len(entries),
        "relations": dict(rel),
        "last_entry": last_entry.isoformat() if last_entry else None,
        "flags": flags,
        "ask": f"Does '{proj.name}' still matter the way you set it up? ({proj.spirit})",
    }


def build_review(s: Session, week_start: date, window_weeks: int = 4) -> dict:
    end = week_start + timedelta(days=6)
    start = end - timedelta(days=7 * window_weeks - 1)
    days = days_in_window(s, start, end)
    habits = s.scalars(select(Habit).where(Habit.active == True)).all()  # noqa: E712
    projects = s.scalars(select(Project).where(Project.status == "active")).all()
    exempt_days = sum(1 for d in days.values() if d.exempt)
    unlogged = [
        (start + timedelta(days=i)).isoformat()
        for i in range((end - start).days + 1)
        if (start + timedelta(days=i)) not in days
    ]
    habit_reports = [habit_report(s, h, start, end, days, week_start) for h in habits]
    project_reports = [project_report(s, p, start, end) for p in projects]
    return {
        "week_start": week_start.isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat(), "weeks": window_weeks},
        "logged_days": len(days),
        "exempt_days": exempt_days,
        "unlogged_days": unlogged,
        "habits": habit_reports,
        "projects": project_reports,
        "drift": [h["habit"] for h in habit_reports if "drift" in h["flags"]],
        "hanging_on": [h["habit"] for h in habit_reports if "all_maintenance" in h["flags"]],
        "dropped": [p["project"] for p in project_reports if "no_activity" in p["flags"]],
        "busy_not_moving": [p["project"] for p in project_reports if "busy_not_moving" in p["flags"]],
    }
