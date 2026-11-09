"""Pure-ish logic over SQLAlchemy sessions: eligibility, rotation, and the weekly review."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, timedelta
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ActionItem, Arc, ArcNote, Day, Entry, Habit, Measure, Todo, Variant

# ---------- cadence ----------

_CADENCE_RE = re.compile(r"^(\d+)x/(week|2weeks|month)$")


def expected_per_day(cadence: str) -> float:
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
        day = Day(date=d)
        s.add(day)
        s.flush()
    return day


def days_in_window(s: Session, start: date, end: date) -> dict[date, Day]:
    return {r.date: r for r in s.scalars(select(Day).where(Day.date >= start, Day.date <= end))}


# ---------- variants ----------


def active_variants(habit: Habit) -> list[Variant]:
    return [v for v in habit.variants if v.active]


def last_focus_dates(s: Session, habit_id: str, before: date) -> dict[str, date]:
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


def suggest_variant(s: Session, habit: Habit, today: date) -> dict:
    """Which variant to lead with, if any. Only meaningful for habits with a rotation or a default."""
    elig = active_variants(habit)
    if not elig:
        return {"variant": None, "reason": "no variants"}
    if len(elig) == 1:
        return {"variant": elig[0], "reason": "only option"}
    pref = habit.default_variant
    progress = [v for v in elig if v.kind == "progress"]
    if pref == "progress" and progress:
        if habit.rotation == "least_recent_focus":
            last = last_focus_dates(s, habit.id, today)
            never = [v for v in progress if v.focus and v.focus not in last]
            if never:
                return {"variant": never[0], "reason": f"{never[0].focus} not done yet"}
            v = min(progress, key=lambda v: last.get(v.focus, date.min))
            gap = (today - last[v.focus]).days if v.focus in last else None
            return {"variant": v, "reason": f"{gap} days since {v.focus}" if gap is not None else "rotation"}
        return {"variant": progress[0], "reason": "progress variant"}
    if pref:
        for v in elig:
            if v.key == pref:
                return {"variant": v, "reason": "default"}
    return {"variant": elig[0], "reason": "first eligible"}


# ---------- arcs ----------


def arc_snapshot(s: Session, arc: Arc, today: date, recent_days: int = 14) -> dict:
    items = s.scalars(select(ActionItem).where(ActionItem.arc_id == arc.id).order_by(ActionItem.added_on)).all()
    notes = s.scalars(
        select(ArcNote).where(ArcNote.arc_id == arc.id, ArcNote.day >= today - timedelta(days=recent_days))
        .order_by(ArcNote.day.desc())
    ).all()
    touches = [i.closed_on for i in items if i.closed_on] + [i.added_on for i in items] + [n.day for n in notes]
    last_touch = max(touches) if touches else None
    return {
        "arc": arc.id,
        "name": arc.name,
        "status": arc.status,
        "priority": arc.priority,
        "objective": arc.objective,
        "open_items": [{"id": i.id, "text": i.text, "added_on": i.added_on.isoformat()} for i in items if i.status == "open"],
        "recent_done": [{"id": i.id, "text": i.text, "closed_on": i.closed_on.isoformat()} for i in items
                        if i.status == "done" and i.closed_on and i.closed_on >= today - timedelta(days=recent_days)],
        "recent_notes": [{"day": n.day.isoformat(), "kind": n.kind, "text": n.text} for n in notes[:5]],
        "last_touch": last_touch.isoformat() if last_touch else None,
        "days_quiet": (today - last_touch).days if last_touch else None,
    }


# ---------- review ----------


def habit_report(s: Session, habit: Habit, start: date, end: date, days: dict[date, Day], week_start: date) -> dict:
    # A day counts iff it had a debrief. Thin days (flashcards only on a trip) count fully — that's the truth.
    eligible = list(days.values())
    n_elig = len(eligible)
    n_elig_week = sum(1 for d in eligible if d.date >= week_start)
    entries = s.scalars(select(Entry).where(
        Entry.target_type == "habit", Entry.target_id == habit.id, Entry.day >= start, Entry.day <= end)).all()
    done = [e for e in entries if e.result == "done"]
    done_days = {e.day for e in done}
    week_count = sum(int(e.value or 1) for e in done if e.day >= week_start)
    expected = expected_per_day(habit.cadence) * n_elig
    variants = {v.id: v for v in habit.variants}
    focus = Counter(variants[e.variant_id].focus for e in done if e.variant_id in variants and variants[e.variant_id].focus)
    rate = min(len(done_days) / expected, 1.0) if expected else None
    week_done_days = len({e.day for e in done if e.day >= week_start})
    week_expected = expected_per_day(habit.cadence) * n_elig_week
    week_rate = min(week_done_days / week_expected, 1.0) if week_expected else None
    flags = []
    if habit.track == "adherence":
        if expected and rate is not None and rate < 0.6:
            flags.append("drift")  # rolling window, the slow signal
        if week_expected and week_rate is not None and week_rate < 0.5:
            flags.append("off_this_week")  # the fast signal
    if n_elig == 0:
        flags.append("no_eligible_days")
    return {
        "habit": habit.id, "name": habit.name, "group": habit.group, "cadence": habit.cadence, "track": habit.track,
        "eligible_days": n_elig, "expected": round(expected, 1), "done_days": len(done_days),
        "rate": round(rate, 2) if rate is not None else None,
        "this_week": week_count,
        "this_week_rate": round(week_rate, 2) if week_rate is not None else None,
        "focus_coverage": dict(focus),
        "flags": flags,
    }


def measure_report(s: Session, meas: Measure, week_start: date, weeks_back: int = 3) -> dict:
    """This week's average vs the average of the prior N weeks."""
    end = week_start + timedelta(days=6)
    prior_start = week_start - timedelta(days=7 * weeks_back)
    rows = s.scalars(select(Entry).where(
        Entry.target_type == "measure", Entry.target_id == meas.id, Entry.day >= prior_start, Entry.day <= end)).all()
    this = [e.value for e in rows if e.day >= week_start and e.value is not None]
    prior = [e.value for e in rows if e.day < week_start and e.value is not None]
    this_avg = round(mean(this), 2) if this else None
    prior_avg = round(mean(prior), 2) if prior else None
    trend = None
    if this_avg is not None and prior_avg is not None:
        delta = this_avg - prior_avg
        good = (delta < 0) if meas.direction == "down" else (delta > 0)
        trend = "better" if good and abs(delta) > 0.1 else ("worse" if abs(delta) > 0.1 else "flat")

    def label(v):
        if v is None:
            return None
        if meas.kind == "scale" and meas.levels:
            return meas.levels[min(int(round(v)), len(meas.levels) - 1)]
        return v

    return {
        "measure": meas.id, "name": meas.name, "kind": meas.kind, "unit": meas.unit, "direction": meas.direction,
        "this_week": {"days_logged": len(this), "avg": this_avg, "label": label(this_avg),
                      "values": [e.value for e in sorted(rows, key=lambda e: e.day) if e.day >= week_start]},
        "prior_weeks": {"days_logged": len(prior), "avg": prior_avg, "label": label(prior_avg)},
        "trend": trend,
    }


def arc_report(s: Session, arc: Arc, week_start: date, today: date) -> dict:
    snap = arc_snapshot(s, arc, today)
    items = s.scalars(select(ActionItem).where(ActionItem.arc_id == arc.id)).all()
    done_this_week = [i for i in items if i.status == "done" and i.closed_on and i.closed_on >= week_start]
    added_this_week = [i for i in items if i.added_on >= week_start]
    flags = []
    if arc.status == "active":
        if snap["days_quiet"] is None or snap["days_quiet"] >= 21:
            flags.append("suggest_backburner")
        elif snap["days_quiet"] >= 7:
            flags.append("quiet_week")
        if not snap["open_items"]:
            flags.append("no_open_items")
    snap.update({
        "done_this_week": [i.text for i in done_this_week],
        "added_this_week": [i.text for i in added_this_week],
        "flags": flags,
        "ask": f"Is '{arc.name}' still what you want it to be? ({arc.objective})",
    })
    return snap


def todo_report(s: Session, today: date) -> dict:
    todos = s.scalars(select(Todo).where(Todo.status == "open").order_by(Todo.added_on)).all()
    return {
        "open": [{"id": t.id, "text": t.text, "age_days": (today - t.added_on).days} for t in todos],
        "stale": [t.text for t in todos if (today - t.added_on).days >= 30],
    }


def build_review(s: Session, week_start: date, today: date, window_weeks: int = 4) -> dict:
    end = week_start + timedelta(days=6)
    start = end - timedelta(days=7 * window_weeks - 1)
    days = days_in_window(s, start, end)
    habits = s.scalars(select(Habit).where(Habit.active == True)).all()  # noqa: E712
    measures = s.scalars(select(Measure).where(Measure.active == True)).all()  # noqa: E712
    arcs = s.scalars(select(Arc).where(Arc.status != "done")).all()
    habit_reports = [habit_report(s, h, start, end, days, week_start) for h in habits]
    measure_reports = [measure_report(s, m, week_start) for m in measures]
    arc_reports = [arc_report(s, a, week_start, today) for a in arcs]
    unlogged = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)
                if (start + timedelta(days=i)) not in days and start + timedelta(days=i) <= today]
    return {
        "week_start": week_start.isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat(), "weeks": window_weeks},
        "logged_days": len(days),
        "null_days": unlogged,
        "habits": habit_reports,
        "measures": measure_reports,
        "arcs": arc_reports,
        "todos": todo_report(s, today),
        "drift": [h["habit"] for h in habit_reports if "drift" in h["flags"]],
        "off_this_week": [h["habit"] for h in habit_reports if "off_this_week" in h["flags"]],
        "measures_worse": [m["measure"] for m in measure_reports if m["trend"] == "worse"],
        "measures_better": [m["measure"] for m in measure_reports if m["trend"] == "better"],
        "arcs_quiet": [a["arc"] for a in arc_reports if "quiet_week" in a["flags"]],
        "arcs_suggest_backburner": [a["arc"] for a in arc_reports if "suggest_backburner" in a["flags"]],
    }
