"""Simulate ~2 weeks of check-ins through the tool functions and check the review flags.
Run:  DATABASE_URL=sqlite:///./test.db python -m tests.test_e2e
"""

import os
from datetime import date, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
if os.path.exists("test.db"):
    os.remove("test.db")

from app import seed, server  # noqa: E402

seed.init_db()
seed.sync(seed.load_config())

START = date(2026, 10, 5)  # a Monday
PATTERN = {0: "home", 1: "office", 2: "office", 3: "home", 4: "office", 5: "weekend", 6: "weekend"}


def day(n):
    return START + timedelta(days=n)


def run_day(n, logs, travel=False):
    os.environ["FAKE_TODAY"] = day(n).isoformat()
    mode = "travel" if travel else PATTERN[day(n).weekday()]
    r = server.start_checkin(mode=mode)
    if r.get("exempt"):
        return r
    entries = []
    for h in r["habits"]:
        spec = logs.get(h["habit"])
        if spec is None:
            continue
        if spec == "skip":
            entries.append({"target_type": "habit", "target_id": h["habit"], "result": "skipped"})
        elif spec == "suggest":
            entries.append({"target_type": "habit", "target_id": h["habit"], "variant_id": h["suggest"]["variant"], "result": "done"})
        else:
            entries.append({"target_type": "habit", "target_id": h["habit"], "variant_id": spec, "result": "done"})
    for pid, rel in logs.get("_projects", {}).items():
        entries.append({"target_type": "project", "target_id": pid, "result": "done", "relation": rel, "note": "sim"})
    server.log_entries(r["checkin_id"], entries, raw_notes="sim day")
    return r


os.environ["FAKE_TODAY"] = START.isoformat()
cfg = seed.load_config()
cfg["projects"]["AD"]["outcome"] = {"title": "2-minute TF pitch I can deliver cold", "horizon": "by Nov 1"}
cfg["habits"]["piano"]["outcome"] = "Current repertoire piece"
seed.sync(cfg)

# Week 1: good week. piano via rotation, strength on schedule, AD advances twice, NS tangential only
good = {"piano": "suggest", "supplements": "supplements:taken", "saline": "saline:rinse", "writing": "writing:three_lines",
        "veggies_fruit": "veggies_fruit:veggies", "skincare": "skincare:basics"}
for n in range(7):
    logs = dict(good)
    if day(n).weekday() in (0, 3):
        logs["strength"] = "strength:trainer"
    if day(n).weekday() == 2:
        logs["climbing"] = "climbing:brigid"
    if day(n).weekday() in (0, 4):
        logs["_projects"] = {"AD": "advances", "NS": "tangential"}
    run_day(n, logs)

# Week 2: travel Tue/Wed, piano only app lessons, writing collapses, no AD at all
for n in range(7, 14):
    if day(n).weekday() in (1, 2):
        run_day(n, {}, travel=True)
        continue
    logs = {"piano": "piano:app_lesson", "supplements": "supplements:taken", "saline": "saline:rinse",
            "writing": "skip", "veggies_fruit": "veggies_fruit:veggies", "skincare": "skincare:basics"}
    if day(n).weekday() == 0:
        logs["strength"] = "strength:trainer"
    if day(n).weekday() == 4:
        logs["_projects"] = {"NS": "tangential"}
    run_day(n, logs)

# Inspect rotation on the first home day of week 1 vs. what came after
os.environ["FAKE_TODAY"] = day(14).isoformat()
r = server.start_checkin(mode="home")
piano = next(h for h in r["habits"] if h["habit"] == "piano")
print("piano suggestion on day 15:", piano["suggest"])

rev = server.weekly_review(week_start=day(7).isoformat(), window_weeks=2)
print("\nlogged", rev["logged_days"], "exempt", rev["exempt_days"], "unlogged", rev["unlogged_days"])
print("drift:", rev["drift"])
print("hanging_on:", rev["hanging_on"])
print("dropped:", rev["dropped"])
print("busy_not_moving:", rev["busy_not_moving"])
for h in rev["habits"]:
    print(f"  {h['habit']:<14} elig={h['eligible_days']:<2} exp={h['expected']:<5} done={h['done_days']:<2} rate={h['rate']}  kinds={h['kinds']} focus={h['focus_coverage']} {h['flags']}")
for p in rev["projects"]:
    print(f"  {p['project']:<4} entries={p['entries']} rel={p['relations']} outcome={p['outcome'] and p['outcome']['title']} {p['flags']}")

# assertions
assert "writing" in rev["drift"], "writing should drift"
assert "piano" in rev["hanging_on"], "piano week 2 should read as hanging on (all maintenance)"
assert "NS" in rev["busy_not_moving"], "NS only tangential"
assert rev["exempt_days"] == 2
assert "AD" not in rev["dropped"], "AD had entries in window"

# --- config is the only writer of outcomes/status ---
from sqlalchemy import select
from app.db import session
from app.models import Outcome, Project
assert not hasattr(server, "set_outcome") and not hasattr(server, "set_project_status")
assert seed.sync(cfg)["outcomes_changed"] == 0, "re-sync with same config must be a no-op"
cfg["projects"]["AD"]["outcome"] = "Host one TF dinner"
cfg["projects"]["NS"]["status"] = "parked"
assert seed.sync(cfg)["outcomes_changed"] == 2  # old retired + new created
with session() as s_:
    ad = s_.scalars(select(Outcome).where(Outcome.target_id == "AD")).all()
    assert sorted(o.status for o in ad) == ["active", "replaced"], [(o.title, o.status) for o in ad]
    assert s_.get(Project, "NS").status == "parked"

# --- local date: 10pm Central on Nov 9 is 04:00 UTC Nov 10, must still log as Nov 9 ---
from datetime import datetime, timezone
from unittest import mock
os.environ.pop("FAKE_TODAY", None)
class FakeDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 10, 4, 0, tzinfo=timezone.utc).astimezone(tz)
with mock.patch.object(server, "datetime", FakeDT):
    assert server._today() == date(2026, 11, 9), server._today()
class LateNight(FakeDT):
    @classmethod
    def now(cls, tz=None):  # 1:30am Central Nov 10 -> still "Nov 9" (day starts at 4am)
        return datetime(2026, 11, 10, 7, 30, tzinfo=timezone.utc).astimezone(tz)
with mock.patch.object(server, "datetime", LateNight):
    assert server._today() == date(2026, 11, 9), server._today()

print("\nOK")
