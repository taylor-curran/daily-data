"""Sync config.yaml into the DB. Idempotent: upserts, deactivates what's gone, never deletes history."""

import os
import sys

import yaml
from sqlalchemy import select

from .db import init_db, session
from .models import Habit, Project, Variant

CONFIG_PATH = os.environ.get("CONFIG_PATH", os.path.join(os.path.dirname(__file__), "..", "config.yaml"))


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def sync(cfg: dict) -> dict:
    counts = {"habits": 0, "variants": 0, "projects": 0, "deactivated": 0}
    with session() as s:
        seen_habits, seen_variants = set(), set()
        for hid, h in (cfg.get("habits") or {}).items():
            seen_habits.add(hid)
            habit = s.get(Habit, hid) or Habit(id=hid)
            habit.name = h.get("name", hid)
            habit.cadence = h.get("cadence", "daily")
            habit.modes = h.get("modes") or []
            habit.rotation = h.get("rotation")
            habit.default_by_mode = h.get("default_by_mode") or {}
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
    return counts


if __name__ == "__main__":
    init_db()
    print(sync(load_config()), file=sys.stderr)
