"""Data model.

Five kinds of things, two sources of truth:

  CONFIG (config.yaml, synced on boot)      DATA (written by conversations)
  ------------------------------------      --------------------------------
  Habit, Variant   yes/no adherence          Day, CheckIn, Entry
  Measure          a daily number            Entry (with value)
  Arc              objective + status        ActionItem, ArcNote
                                             Todo
                                             ContentLog
                                             Review (+ reflection)
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

SCHEMA_VERSION = 2


class Base(DeclarativeBase):
    pass


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)


# ---------------------------------------------------------------- config-backed


class Habit(Base):
    __tablename__ = "habits"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    cadence: Mapped[str] = mapped_column(String(20))  # daily | every_N_days | Nx/week | Nx/2weeks | Nx/month
    track: Mapped[str] = mapped_column(String(12), default="adherence")  # adherence | count
    modes: Mapped[list] = mapped_column(JSON, default=list)  # modes where this habit is expected
    rotation: Mapped[Optional[str]] = mapped_column(String(30))
    default_variant: Mapped[Optional[str]] = mapped_column(String(40))  # variant key, or "progress"
    target: Mapped[Optional[str]] = mapped_column(String(200))  # e.g. the piece being learned
    anchor: Mapped[Optional[str]] = mapped_column(String(200))
    group: Mapped[Optional[str]] = mapped_column(String(40))  # e.g. "piano", for display ordering
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    variants: Mapped[list[Variant]] = relationship(back_populates="habit", cascade="all, delete-orphan")


class Variant(Base):
    __tablename__ = "variants"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # "<habit>:<variant>"
    habit_id: Mapped[str] = mapped_column(ForeignKey("habits.id"))
    key: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(100))
    requires: Mapped[list] = mapped_column(JSON, default=list)
    kind: Mapped[str] = mapped_column(String(12))  # maintenance | progress
    focus: Mapped[Optional[str]] = mapped_column(String(40))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    habit: Mapped[Habit] = relationship(back_populates="variants")


class Measure(Base):
    """A daily number you want to see trend, not hit."""

    __tablename__ = "measures"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(12))  # count | scale
    levels: Mapped[list] = mapped_column(JSON, default=list)  # scale only: ["none", "some", "a lot"]
    unit: Mapped[Optional[str]] = mapped_column(String(30))
    direction: Mapped[str] = mapped_column(String(8), default="down")  # which way is good
    modes: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Arc(Base):
    """A strategic thread: static objective, rotating action items."""

    __tablename__ = "arcs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    objective: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active | backburner | done
    priority: Mapped[Optional[str]] = mapped_column(String(12))
    seeded: Mapped[bool] = mapped_column(Boolean, default=False)  # seed_items inserted once


# ---------------------------------------------------------------- data


class Day(Base):
    __tablename__ = "days"
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    mode: Mapped[Optional[str]] = mapped_column(String(16))
    exempt: Mapped[bool] = mapped_column(Boolean, default=False)
    exempt_reason: Mapped[Optional[str]] = mapped_column(String(200))
    available: Mapped[list] = mapped_column(JSON, default=list)


class CheckIn(Base):
    __tablename__ = "checkins"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(ForeignKey("days.date"))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    raw_notes: Mapped[Optional[str]] = mapped_column(Text)


class Entry(Base):
    """One habit or measure, one day. Re-logging the same (day, target) replaces."""

    __tablename__ = "entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(ForeignKey("days.date"))
    checkin_id: Mapped[Optional[int]] = mapped_column(ForeignKey("checkins.id"))
    target_type: Mapped[str] = mapped_column(String(8))  # habit | measure
    target_id: Mapped[str] = mapped_column(String(40))
    variant_id: Mapped[Optional[str]] = mapped_column(String(40))  # habits only
    result: Mapped[Optional[str]] = mapped_column(String(12))  # habits: done | skipped
    value: Mapped[Optional[float]] = mapped_column(Float)  # measures, and count habits (default 1)
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ActionItem(Base):
    __tablename__ = "action_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    arc_id: Mapped[str] = mapped_column(ForeignKey("arcs.id"))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | done | dropped
    added_on: Mapped[date] = mapped_column(Date)
    closed_on: Mapped[Optional[date]] = mapped_column(Date)
    note: Mapped[Optional[str]] = mapped_column(Text)


class ArcNote(Base):
    """What happened or what you thought about an arc on a day: progress updates, strategy, write-outs."""

    __tablename__ = "arc_notes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    arc_id: Mapped[str] = mapped_column(ForeignKey("arcs.id"))
    day: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(12), default="update")  # update | strategy | writeout
    text: Mapped[str] = mapped_column(Text)


class Todo(Base):
    __tablename__ = "todos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | done | dropped
    added_on: Mapped[date] = mapped_column(Date)
    closed_on: Mapped[Optional[date]] = mapped_column(Date)
    note: Mapped[Optional[str]] = mapped_column(Text)


class ContentLog(Base):
    """What you read / listened to / watched that stuck with you. Long-term storage, not a habit."""

    __tablename__ = "content_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(12), default="other")  # read | listened | watched | other
    text: Mapped[str] = mapped_column(Text)
    note: Mapped[Optional[str]] = mapped_column(Text)  # what stuck


class Review(Base):
    __tablename__ = "reviews"
    week_start: Mapped[date] = mapped_column(Date, primary_key=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime)
    payload: Mapped[dict] = mapped_column(JSON)
    reflection: Mapped[Optional[str]] = mapped_column(Text)
