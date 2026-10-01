import threading
import os
from dataclasses import asdict
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from .domain import IdleState, advance
from .models import Base, Vehicle, Event, Alert, Audit, Action, now


class Store:
    """Local transactional processing with persistent vehicle state."""
    def __init__(self, url):
        self.engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.lock = threading.RLock()

    def audit(self, tenant, actor, operation):
        with self.sessions.begin() as s:
            s.add(Audit(tenant=tenant, actor=actor, operation=operation))

    def ingest(self, tenant, events):
        counts = {"accepted": 0, "duplicate": 0, "late": 0}
        with self.lock, self.sessions.begin() as s:
            vehicle_ids = sorted({e.vehicle_id for e in events})
            event_ids = [e.event_id for e in events]
            existing_events = {row.event_id: row.payload for row in s.scalars(select(Event).where(Event.tenant == tenant, Event.event_id.in_(event_ids)))}
            vehicles = {v.vehicle_id: v for v in s.scalars(select(Vehicle).where(Vehicle.tenant == tenant, Vehicle.vehicle_id.in_(vehicle_ids)))}
            alerts = {(a.vehicle_id, a.start_ts): a for a in s.scalars(select(Alert).where(Alert.tenant == tenant, Alert.vehicle_id.in_(vehicle_ids), Alert.active == 1))}
            for e in events:
                existing = existing_events.get(e.event_id)
                payload = e.model_dump_json()
                if existing:
                    if existing != payload:
                        raise ValueError("Event ID already exists with a different payload")
                    counts["duplicate"] += 1
                    continue
                ts = e.event_time.timestamp()
                if ts < now() - int(os.getenv("EVENT_RETENTION_DAYS", "30")) * 86400:
                    raise ValueError("Event is older than the configured online replay horizon")
                if ts > now() + 10:
                    raise ValueError("Event time exceeds the allowed 10-second future clock skew")
                v = vehicles.get(e.vehicle_id)
                if not v:
                    v = Vehicle(tenant=tenant, vehicle_id=e.vehicle_id, label=e.vehicle_id, powertrain=e.powertrain,
                                litres=0, estimated=0)
                    s.add(v); vehicles[e.vehicle_id] = v
                old = IdleState(v.last_ts, v.start_ts, v.litres, v.last_rate, bool(v.estimated))
                state, disposition = advance(old, ts=ts, ignition=e.ignition, speed=e.speed_kmh,
                                             pto=e.pto_active, rate=e.fuel_rate_lph, powertrain=e.powertrain)
                if old.start_ts is not None and state.start_ts != old.start_ts:
                    alert = alerts.get((v.vehicle_id, old.start_ts))
                    if alert:
                        alert.active = 0
                        alert.closure = disposition
                if disposition != "late":
                    for k, value in asdict(state).items():
                        setattr(v, k, int(value) if k == "estimated" else value)
                    v.speed = e.speed_kmh; v.disposition = disposition; v.powertrain = e.powertrain
                    if state.start_ts is not None and ts - state.start_ts >= 300:
                        alert = alerts.get((v.vehicle_id, state.start_ts))
                        if not alert:
                            alert = Alert(tenant=tenant, vehicle_id=v.vehicle_id, start_ts=state.start_ts,
                                          end_ts=ts, litres=state.litres, estimated=int(state.estimated))
                            s.add(alert)
                            alerts[(v.vehicle_id, state.start_ts)] = alert
                        else:
                            alert.end_ts = ts; alert.litres = state.litres; alert.estimated = int(state.estimated)
                else:
                    counts["late"] += 1
                s.add(Event(tenant=tenant, event_id=e.event_id, vehicle_id=e.vehicle_id, ts=ts,
                            disposition=disposition, payload=payload))
                counts["accepted"] += 1
                existing_events[e.event_id] = payload
        return counts

    @staticmethod
    def serialize(row):
        return {c.name: getattr(row, c.name) for c in row.__table__.columns}

    def rows(self, model, tenant, *, limit=50, after=0, vehicle=None, status=None):
        with self.sessions() as s:
            stmt = select(model).where(model.tenant == tenant, model.id > after)
            if vehicle is not None: stmt = stmt.where(model.vehicle_id == vehicle)
            if status: stmt = stmt.where(model.status == status)
            data = list(s.scalars(stmt.order_by(model.id).limit(limit + 1)))
            return {"items": [self.serialize(x) for x in data[:limit]],
                    "next_cursor": data[limit-1].id if len(data) > limit else None}

    def summary(self, tenant):
        with self.sessions() as s:
            def count(model, *conditions):
                return s.scalar(select(func.count()).select_from(model).where(model.tenant == tenant, *conditions))
            recent = now() - 15
            return {"vehicles": count(Vehicle), "online": count(Vehicle, Vehicle.last_ts >= recent),
                    "open_alerts": count(Alert, Alert.status == "OPEN"),
                    "active_episodes": count(Vehicle, Vehicle.start_ts.is_not(None), Vehicle.last_ts >= recent),
                    "fuel_litres": s.scalar(select(func.coalesce(func.sum(Alert.litres), 0)).where(Alert.tenant == tenant)),
                    "events": count(Event), "late_events": count(Event, Event.disposition == "late"),
                    "last_received": s.scalar(select(func.max(Event.received)).where(Event.tenant == tenant)),
                    "server_time": now()}

    def action(self, tenant, actor, alert_id, key, kind, note):
        with self.lock, self.sessions.begin() as s:
            alert = s.scalar(select(Alert).where(Alert.id == alert_id, Alert.tenant == tenant))
            if not alert: raise LookupError("Alert not found")
            s.refresh(alert)
            existing = s.scalar(select(Action).where(Action.tenant == tenant, Action.key == key))
            if existing:
                if (existing.alert_id, existing.kind, existing.note) != (alert_id, kind, note):
                    raise ValueError("Idempotency key reused for another action")
                return self.serialize(existing)
            if alert.status == "RESOLVED" and kind == "ACKNOWLEDGE":
                raise ValueError("A resolved alert cannot be acknowledged")
            alert.status = "ACKNOWLEDGED" if kind == "ACKNOWLEDGE" else "RESOLVED"
            action = Action(tenant=tenant, actor=actor, alert_id=alert_id, key=key, kind=kind, note=note)
            s.add(action); s.add(Audit(tenant=tenant, actor=actor, operation=f"{kind} alert:{alert_id}")); s.flush()
            return self.serialize(action)

    def daily(self, tenant):
        # UTC split of qualified-episode totals, rather than DB-specific date functions.
        from datetime import datetime, timezone
        from collections import defaultdict
        days = defaultdict(lambda: {"episodes": 0, "idle_minutes": 0.0, "litres": 0.0})
        with self.sessions() as s:
            for a in s.scalars(select(Alert).where(Alert.tenant == tenant)):
                start = a.start_ts; duration = a.end_ts - start
                while start < a.end_ts:
                    dt = datetime.fromtimestamp(start, timezone.utc)
                    boundary = (int(start) // 86400 + 1) * 86400
                    segment = min(a.end_ts, boundary) - start
                    item = days[dt.date().isoformat()]
                    item["episodes"] += 1; item["idle_minutes"] += segment / 60
                    item["litres"] += a.litres * segment / duration if duration else 0
                    start += segment
        return [{"date": k, **v} for k, v in sorted(days.items(), reverse=True)]

