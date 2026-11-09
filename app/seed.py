"""Sync config.yaml into the DB. Idempotent: upserts, deactivates what's gone, never deletes history.

config.yaml is the single source of truth for habits, variants, measures and arcs (name, objective,
status). Everything that happens over time is data and is never touched here — except an arc's
`seed_items`, inserted exactly once when the arc first appears.
"""

import os
import sys
from datetime import date

import yaml
from sqlalchemy import select

from .db import init_db, session
from .models import ActionItem, Arc, Habit, Measure, Variant

CONFIG_PATH = os.environ.get("CONFIG_PATH", os.path.join(os.path.dirname(__file__), "..", "config.yaml"))


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def sync(cfg: dict, today: date | None = None) -> dict:
    today = today or date.today()
    counts = {"habits": 0, "variants": 0, "measures": 0, "arcs": 0, "seeded_items": 0, "deactivated": 0}
    with session() as s:
        # ---- habits + variants
        seen_h, seen_v = set(), set()
        for hid, h in (cfg.get("habits") or {}).items():
            seen_h.add(hid)
            habit = s.get(Habit, hid) or Habit(id=hid)
            habit.name = h.get("name", hid)
            habit.cadence = h.get("cadence", "daily")
            habit.track = h.get("track", "adherence")
            habit.modes = h.get("modes") or []
            habit.rotation = h.get("rotation")
            habit.default_variant = h.get("default")
            habit.target = h.get("target") or None
            habit.anchor = h.get("anchor")
            habit.group = h.get("group")
            habit.active = bool(h.get("active", True))
            s.add(habit)
            counts["habits"] += 1
            for v in h.get("variants") or []:
                vid = f"{hid}:{v['id']}"
                seen_v.add(vid)
                var = s.get(Variant, vid) or Variant(id=vid, habit_id=hid, key=v["id"])
                var.name = v.get("name", v["id"])
                var.requires = v.get("requires") or []
                var.kind = v.get("kind", "maintenance")
                var.focus = v.get("focus")
                var.active = True
                s.add(var)
                counts["variants"] += 1
        for habit in s.scalars(select(Habit)):
            if habit.id not in seen_h and habit.active:
                habit.active = False
                counts["deactivated"] += 1
        for var in s.scalars(select(Variant)):
            if var.id not in seen_v and var.active:
                var.active = False
                counts["deactivated"] += 1

        # ---- measures
        seen_m = set()
        for mid, m in (cfg.get("measures") or {}).items():
            seen_m.add(mid)
            meas = s.get(Measure, mid) or Measure(id=mid)
            meas.name = m.get("name", mid)
            meas.kind = m.get("kind", "count")
            meas.levels = m.get("levels") or []
            meas.unit = m.get("unit")
            meas.direction = m.get("direction", "down")
            meas.modes = m.get("modes") or []
            meas.active = True
            s.add(meas)
            counts["measures"] += 1
        for meas in s.scalars(select(Measure)):
            if meas.id not in seen_m and meas.active:
                meas.active = False
                counts["deactivated"] += 1

        # ---- arcs (+ one-time seed items)
        for aid, a in (cfg.get("arcs") or {}).items():
            arc = s.get(Arc, aid) or Arc(id=aid, seeded=False)
            arc.name = a.get("name", aid)
            arc.objective = a.get("objective")
            arc.status = a.get("status", "active")
            arc.priority = a.get("priority")
            s.add(arc)
            counts["arcs"] += 1
            if not arc.seeded:
                for text in a.get("seed_items") or []:
                    s.add(ActionItem(arc_id=aid, text=text, status="open", added_on=today))
                    counts["seeded_items"] += 1
                arc.seeded = True
    return counts


if __name__ == "__main__":
    init_db()
    print(sync(load_config()), file=sys.stderr)
