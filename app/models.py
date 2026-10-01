from datetime import datetime, timezone
from sqlalchemy import String, Float, Integer, Text, UniqueConstraint, ForeignKey, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(timezone.utc).timestamp()


class Base(DeclarativeBase):
    pass


class Vehicle(Base):
    __tablename__ = "vehicles"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant: Mapped[str] = mapped_column(String(60), index=True)
    vehicle_id: Mapped[str] = mapped_column(String(80))
    label: Mapped[str] = mapped_column(String(100))
    scenario: Mapped[str] = mapped_column(String(30), default="driving")
    powertrain: Mapped[str] = mapped_column(String(10), default="ICE")
    last_ts: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_ts: Mapped[float | None] = mapped_column(Float, nullable=True)
    litres: Mapped[float] = mapped_column(Float, default=0)
    last_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated: Mapped[int] = mapped_column(Integer, default=0)
    speed: Mapped[float | None] = mapped_column(Float, nullable=True)
    disposition: Mapped[str] = mapped_column(String(30), default="offline")
    __table_args__ = (UniqueConstraint("tenant", "vehicle_id"),)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant: Mapped[str] = mapped_column(String(60))
    event_id: Mapped[str] = mapped_column(String(160))
    vehicle_id: Mapped[str] = mapped_column(String(80))
    ts: Mapped[float] = mapped_column(Float)
    received: Mapped[float] = mapped_column(Float, default=now)
    disposition: Mapped[str] = mapped_column(String(30))
    payload: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("tenant", "event_id"), Index("event_vehicle_time", "tenant", "vehicle_id", "ts"))


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant: Mapped[str] = mapped_column(String(60), index=True)
    vehicle_id: Mapped[str] = mapped_column(String(80))
    start_ts: Mapped[float] = mapped_column(Float)
    end_ts: Mapped[float] = mapped_column(Float)
    litres: Mapped[float] = mapped_column(Float)
    estimated: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    closure: Mapped[str | None] = mapped_column(String(30), nullable=True)
    __table_args__ = (UniqueConstraint("tenant", "vehicle_id", "start_ts"),)


class Action(Base):
    __tablename__ = "actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant: Mapped[str] = mapped_column(String(60))
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id"))
    key: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(30))
    note: Mapped[str] = mapped_column(String(500))
    actor: Mapped[str] = mapped_column(String(100))
    ts: Mapped[float] = mapped_column(Float, default=now)
    __table_args__ = (UniqueConstraint("tenant", "key"),)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant: Mapped[str] = mapped_column(String(60), index=True)
    actor: Mapped[str] = mapped_column(String(100))
    operation: Mapped[str] = mapped_column(String(150))
    ts: Mapped[float] = mapped_column(Float, default=now)


class SessionToken(Base):
    __tablename__ = "sessions"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(60))
    actor: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(30))
    expires: Mapped[float] = mapped_column(Float, index=True)


