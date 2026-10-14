from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Day(Base):
    __tablename__ = "days"
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    mode: Mapped[Optional[str]] = mapped_column(String(16))
    exempt: Mapped[bool] = mapped_column(Boolean, default=False)  # travel / "don't count today"
    exempt_reason: Mapped[Optional[str]] = mapped_column(String(200))
    available: Mapped[list] = mapped_column(JSON, default=list)  # resources on hand


class Habit(Base):
    __tablename__ = "habits"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    cadence: Mapped[str] = mapped_column(String(20))  # daily | every_N_days | Nx/week | Nx/2weeks | Nx/month
    modes: Mapped[list] = mapped_column(JSON, default=list)  # modes where this habit is expected
    rotation: Mapped[Optional[str]] = mapped_column(String(30))
    default_by_mode: Mapped[dict] = mapped_column(JSON, default=dict)
    anchor: Mapped[Optional[str]] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    variants: Mapped[list[Variant]] = relationship(back_populates="habit", cascade="all, delete-orphan")


class Variant(Base):
    __tablename__ = "variants"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # "<habit>:<variant>"
    habit_id: Mapped[str] = mapped_column(ForeignKey("habits.id"))
    key: Mapped[str] = mapped_column(String(40))  # short id within the habit
    name: Mapped[str] = mapped_column(String(100))
    requires: Mapped[list] = mapped_column(JSON, default=list)
    kind: Mapped[str] = mapped_column(String(12))  # maintenance | progress
    focus: Mapped[Optional[str]] = mapped_column(String(40))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    habit: Mapped[Habit] = relationship(back_populates="variants")


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    priority: Mapped[Optional[str]] = mapped_column(String(12))
    spirit: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active | parked | killed | done


class Outcome(Base):
    """The one concrete next thing for a project or a habit. One active per target."""

    __tablename__ = "outcomes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_type: Mapped[str] = mapped_column(String(8))  # project | habit
    target_id: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    horizon: Mapped[Optional[str]] = mapped_column(String(60))  # free text, e.g. "by Nov 1"
    status: Mapped[str] = mapped_column(String(12), default="active")  # active | done | parked
    set_on: Mapped[date] = mapped_column(Date)
    done_on: Mapped[Optional[date]] = mapped_column(Date)
    notes: Mapped[Optional[str]] = mapped_column(Text)


class CheckIn(Base):
    __tablename__ = "checkins"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(ForeignKey("days.date"))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    raw_notes: Mapped[Optional[str]] = mapped_column(Text)


class Entry(Base):
    __tablename__ = "entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(ForeignKey("days.date"))
    checkin_id: Mapped[Optional[int]] = mapped_column(ForeignKey("checkins.id"))
    target_type: Mapped[str] = mapped_column(String(8))  # habit | project
    target_id: Mapped[str] = mapped_column(String(40))
    variant_id: Mapped[Optional[str]] = mapped_column(String(40))  # habits only
    result: Mapped[str] = mapped_column(String(12))  # done | skipped
    relation: Mapped[Optional[str]] = mapped_column(String(12))  # advances | tangential | maintenance | exploring
    outcome_id: Mapped[Optional[int]] = mapped_column(ForeignKey("outcomes.id"))
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Review(Base):
    __tablename__ = "reviews"
    week_start: Mapped[date] = mapped_column(Date, primary_key=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime)
    payload: Mapped[dict] = mapped_column(JSON)
    reflection: Mapped[Optional[str]] = mapped_column(Text)  # what you said when asked "does this still matter?"
