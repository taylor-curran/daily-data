"""Sync config.yaml into the DB. Idempotent: upserts, deactivates what's gone, never deletes history.

config.yaml is the single source of truth for habits, variants, projects (incl. status) and each
target's current outcome. Nothing in a conversation writes config.
"""

import os
import sys

import yaml
from sqlalchemy import select

from .db import init_db, session
from datetime import date

from .models import Habit, Outcome, Project, Variant

CONFIG_PATH = os.environ.get("CONFIG_PATH", os.path.join(os.path.dirname(__file__), "..", "config.yaml"))


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def sync(cfg: dict) -> dict:
    counts = {"habits": 0, "variants": 0, "projects": 0, "deactivated": 0, "outcomes_changed": 0}
    with session() as s:
        seen_habits, seen_variants = set(), set()
        for hid, h in (cfg.get("habits") or {}).items():
            seen_habits.add(hid)
            habit = s.get(Habit, hid) or Habit(id=hid)
            habit.name = h.get("name", hid)
            habit.cadence = h.get("cadence", "daily")
            habit.modes = h.get("modes") or []
            habit.rotation = h.get("rotation")
            dbm = dict(h.get("default_by_mode") or {})
            if h.get("default"):
                dbm["*"] = h["default"]  # applies to every mode unless a mode overrides it
            habit.default_by_mode = dbm
            habit.anchor = h.get("anchor")
            habit.active = True
            s.add(habit)
            counts["habits"] += 1
            for v in h.get("variants") or []:
                vid = f"{hid}:{v['id']}"
                seen_variants.add(vid)
                var = s.get(Variant, vid) or Variant(id=vid, habit_id=hid, key=v["id"])
                var.name = v.get("name", v["id"])
                var.requires = v.get("requires") or []
                var.kind = v.get("kind", "maintenance")
                var.focus = v.get("focus")
                var.active = True
                s.add(var)
                counts["variants"] += 1
        for habit in s.scalars(select(Habit)):
            if habit.id not in seen_habits and habit.active:
                habit.active = False
                counts["deactivated"] += 1
        for var in s.scalars(select(Variant)):
            if var.id not in seen_variants and var.active:
                var.active = False
                counts["deactivated"] += 1

        for pid, p in (cfg.get("projects") or {}).items():
            proj = s.get(Project, pid) or Project(id=pid)
            proj.name = p.get("name", pid)
            proj.priority = p.get("priority")
            proj.spirit = p.get("spirit")
            proj.status = p.get("status", "active")
            s.add(proj)
            counts["projects"] += 1
        for tt, items in (("habit", cfg.get("habits") or {}), ("project", cfg.get("projects") or {})):
            for tid, item in items.items():
                counts["outcomes_changed"] += _sync_outcome(s, tt, tid, item.get("outcome"))
    return counts


def _sync_outcome(s, target_type: str, target_id: str, spec) -> int:
    """Make the DB's active outcome match config. Changing the title retires the old one (kept as history)."""
    if isinstance(spec, str):
        spec = {"title": spec}
    title = (spec or {}).get("title")
    active = s.scalars(
        select(Outcome).where(Outcome.target_type == target_type, Outcome.target_id == target_id, Outcome.status == "active")
    ).first()
    if active and active.title == title:
        active.horizon = (spec or {}).get("horizon")
        return 0
    changed = 0
    if active:
        active.status, active.done_on = "replaced", date.today()
        changed += 1
    if title:
        s.add(Outcome(target_type=target_type, target_id=target_id, title=title,
                      horizon=(spec or {}).get("horizon"), set_on=date.today()))
        changed += 1
    return changed


if __name__ == "__main__":
    init_db()
    print(sync(load_config()), file=sys.stderr)
