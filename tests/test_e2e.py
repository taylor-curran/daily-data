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
server.set_outcome("project", "AD", "2-minute TF pitch I can deliver cold", "by Nov 1")
server.set_outcome("habit", "piano", "Current repertoire piece")

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
print("\nOK")
