"""Simulate ~2 weeks through the tool functions and check the review.
Run:  DATABASE_URL=sqlite:///./test.db python -m tests.test_e2e
"""

import os
from datetime import date, datetime, timedelta, timezone
from unittest import mock

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
if os.path.exists("test.db"):
    os.remove("test.db")

from sqlalchemy import select  # noqa: E402

from app import seed, server  # noqa: E402
from app.db import session  # noqa: E402
from app.models import ActionItem, Arc, Habit  # noqa: E402

START = date(2026, 11, 2)  # a Monday


def day(n):
    return START + timedelta(days=n)


def run_day(n, habits=None, measures=None):
    os.environ["FAKE_TODAY"] = day(n).isoformat()
    r = server.start_debrief()
    entries = []
    for h in r["habits"]:
        spec = (habits or {}).get(h["habit"])
        if spec is None:
            continue
        if spec == "skip":
            entries.append({"target_type": "habit", "target_id": h["habit"], "result": "skipped"})
        elif spec == "suggest":
            entries.append({"target_type": "habit", "target_id": h["habit"], "variant_id": h["suggest"]["variant"], "result": "done"})
        elif isinstance(spec, tuple):  # (variant, count)
            entries.append({"target_type": "habit", "target_id": h["habit"], "variant_id": spec[0], "result": "done", "value": spec[1]})
        else:
            entries.append({"target_type": "habit", "target_id": h["habit"], "variant_id": spec, "result": "done"})
    for mid, v in (measures or {}).items():
        e = {"target_type": "measure", "target_id": mid}
        e["label" if isinstance(v, str) else "value"] = v
        entries.append(e)
    res = server.log_entries(r["debrief_id"], entries, raw_notes="sim")
    assert res["ok"], res
    return r


# ---- seed sanity (re-seed dated to the sim clock so arc quiet-days are deterministic)
with session() as s_:
    for a in s_.scalars(select(Arc)):
        a.seeded = False
    for i in s_.scalars(select(ActionItem)):
        s_.delete(i)
seed.sync(seed.load_config(), today=START)
r0 = server.start_debrief(day=START.isoformat())
assert r0["open_with"].startswith("Hey Taylor")
assert {a["arc"] for a in r0["arcs"]} == {"AD", "WEDDING"}, r0["arcs"]
ad = next(a for a in r0["arcs"] if a["arc"] == "AD")
assert len(ad["open_items"]) == 5, "AD seed items"
assert "piano_piece" not in {h["habit"] for h in r0["habits"]}, "inactive habit must not be asked"
assert seed.sync(seed.load_config())["seeded_items"] == 0, "seed items only once"

good_h = {"piano_flashcards": "piano_flashcards:done", "piano_drills": "suggest", "piano_app": "piano_app:new_song",
          "ovasitol": "ovasitol:taken", "saline": "saline:rinse", "veggies": "veggies:had"}

# Week 1: good. picking high, phone "a lot". AD moves twice.
for n in range(7):
    h = dict(good_h)
    if day(n).weekday() in (0, 3):
        h["strength"] = "strength:trainer"
    if day(n).weekday() == 1:
        h["strength"] = "strength:solo_gym"
    if day(n).weekday() == 2:
        h["climbing"] = ("climbing:brigid", 1)
    run_day(n, h, {"picking": 4, "phone_video": "a lot"})
os.environ["FAKE_TODAY"] = day(1).isoformat()
first_item = ad["open_items"][0]["id"]
res = server.update_action_items("AD", done=[first_item], add=["Draft 2-minute pitch v1"], note="listened to 2 interviews")
assert res["ok"] and len(res["added"]) == 1, res
server.add_todos(["Order steel-toed boots for Noah", "Find a good place to order brisket"])
server.log_content([{"text": "CEO interview on competitive edge", "kind": "listened", "note": "the 'vertical integration' line"}])

# Week 2: Tue is a null day (no debrief); Wed is a thin day (flashcards + ovasitol only, on a trip);
# drills+app collapse; picking down; wedding untouched
for n in range(7, 14):
    if day(n).weekday() == 1:
        continue  # null day: no debrief at all
    if day(n).weekday() == 2:
        run_day(n, {"piano_flashcards": "piano_flashcards:done", "ovasitol": "ovasitol:taken"}, {"picking": 1, "phone_video": "some"})
        continue
    h = {"piano_flashcards": "piano_flashcards:done", "piano_drills": "skip", "piano_app": "skip",
         "ovasitol": "ovasitol:taken", "saline": "saline:rinse", "veggies": "veggies:had"}
    if day(n).weekday() == 0:
        h["strength"] = "strength:trainer"
    run_day(n, h, {"picking": 1, "phone_video": "none"})
os.environ["FAKE_TODAY"] = day(12).isoformat()
server.update_todos(done=[1])

# ---- rotation: drills should rotate focus across week 1
with session() as s_:
    drills = s_.get(Habit, "piano_drills")
    from app.logic import last_focus_dates
    assert set(last_focus_dates(s_, "piano_drills", day(7))) == {"fingerpower", "scales", "rhythm"}, "rotation covered all foci"

# ---- review for week 2
os.environ["FAKE_TODAY"] = day(13).isoformat()
rev = server.weekly_review(week_start=day(7).isoformat(), window_weeks=2)
print("off_this_week:", rev["off_this_week"], "drift:", rev["drift"])
print("measures better/worse:", rev["measures_better"], rev["measures_worse"])
print("arcs quiet / suggest backburner:", rev["arcs_quiet"], rev["arcs_suggest_backburner"])
for h in rev["habits"]:
    print(f"  {h['habit']:<17} elig={h['eligible_days']:<2} exp={h['expected']:<5} done={h['done_days']:<2} rate={h['rate']} wk={h['this_week']} {h['flags']}")
for m in rev["measures"]:
    print(f"  {m['measure']:<12} this={m['this_week']['avg']} ({m['this_week']['label']}) prior={m['prior_weeks']['avg']} -> {m['trend']}")
for a in rev["arcs"]:
    print(f"  {a['arc']:<8} {a['status']:<10} open={len(a['open_items'])} quiet={a['days_quiet']} {a['flags']}")
print("  todos open:", [t["text"] for t in rev["todos"]["open"]])

assert "piano_drills" in rev["off_this_week"] and "piano_app" in rev["off_this_week"], rev["off_this_week"]
assert "piano_drills" not in rev["drift"], "one bad week is not yet drift"
fc = next(h for h in rev["habits"] if h["habit"] == "piano_flashcards")
assert fc["eligible_days"] == 13 and fc["rate"] == 1.0, "a thin day still counts; a null day doesn't"
veg = next(h for h in rev["habits"] if h["habit"] == "veggies")
assert veg["done_days"] == 12 and veg["eligible_days"] == 13, "thin day counts against veggies (not logged = not done)"
assert "climbing" not in rev["drift"] + rev["off_this_week"], "count habits never drift"
assert rev["logged_days"] == 13 and rev["null_days"] == [day(8).isoformat()], (rev["logged_days"], rev["null_days"])
assert set(rev["measures_better"]) == {"picking", "phone_video"}
wedding = next(a for a in rev["arcs"] if a["arc"] == "WEDDING")
assert "quiet_week" in wedding["flags"], wedding
assert not any(a["arc"] == "NS" and a["status"] == "active" for a in rev["arcs"])
assert [t["text"] for t in rev["todos"]["open"]] == ["Find a good place to order brisket"]
hist = server.recent_history(days=14)
assert hist["content"] and hist["arc_notes"] and hist["action_items_changed"]

# ---- removed tools stay removed; config is the only writer of arc status
for name in ("set_outcome", "complete_outcome", "set_project_status", "skip_day"):
    assert not hasattr(server, name)
cfg = seed.load_config()
cfg["arcs"]["WEDDING"]["status"] = "backburner"
seed.sync(cfg)
with session() as s_:
    assert s_.get(Arc, "WEDDING").status == "backburner"

# ---- schema reset guard refuses when data exists
from app import db as dbmod
from sqlalchemy import text
with dbmod.engine.begin() as conn:
    conn.execute(text("drop table schema_version"))
try:
    dbmod.init_db()
    raise SystemExit("reset guard should have refused")
except RuntimeError as e:
    assert "refusing" in str(e)

# ---- local date: 10pm Central = 04:00 UTC next day -> still today; 1:30am -> still yesterday
os.environ.pop("FAKE_TODAY", None)
class FakeDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 10, 4, 0, tzinfo=timezone.utc).astimezone(tz)
with mock.patch.object(server, "datetime", FakeDT):
    assert server._today() == date(2026, 11, 9)
class LateNight(FakeDT):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 10, 7, 30, tzinfo=timezone.utc).astimezone(tz)
with mock.patch.object(server, "datetime", LateNight):
    assert server._today() == date(2026, 11, 9)

print("\nOK")
