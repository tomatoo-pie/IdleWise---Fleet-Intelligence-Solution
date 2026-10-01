"""Deterministic synthetic scenarios and a bounded local demonstration loop."""
import threading
from datetime import datetime, timezone
from sqlalchemy import select
from .models import Vehicle, now
from .schemas import Telemetry

SCENARIOS = ["idling", "driving", "parked", "pto", "missing", "ev"]


def event(vehicle, ts, scenario, prefix="sim"):
    return Telemetry(event_id=f"{prefix}:{vehicle}:{int(ts * 1000)}", vehicle_id=vehicle,
                     event_time=datetime.fromtimestamp(ts, timezone.utc),
                     ignition=scenario != "parked", speed_kmh=48 if scenario == "driving" else None if scenario == "missing" else 0,
                     pto_active=scenario == "pto", fuel_rate_lph=1.5 if scenario != "ev" else 0,
                     powertrain="EV" if scenario == "ev" else "ICE")


class Simulator:
    def __init__(self, store):
        self.store = store
        self.enabled = set()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.error = None

    def seed(self, tenant, count=24):
        with self.store.lock, self.store.sessions.begin() as s:
            exists = s.scalar(select(Vehicle.id).where(Vehicle.tenant == tenant).limit(1))
            if exists: return {"seeded": 0, "message": "Fleet already contains vehicles"}
            for i in range(count):
                scenario = SCENARIOS[i % len(SCENARIOS)]
                s.add(Vehicle(tenant=tenant, vehicle_id=f"IW-{i+1:04}", label=f"Fleet vehicle {i+1:02}", scenario=scenario,
                              powertrain="EV" if scenario == "ev" else "ICE"))
        end = int(now()) - 1
        # Historical backfill uses actual five-minute rule, never a shortened demo threshold.
        events = [event(f"IW-{i+1:04}", end - offset, SCENARIOS[i % 6])
                  for offset in range(360, -1, -5) for i in range(count)]
        for i in range(0, len(events), 500): self.store.ingest(tenant, events[i:i+500])
        return {"seeded": count, "events": len(events)}

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name="synthetic-stream")
        self.thread.start()

    def run(self):
        while not self.stop.wait(1):
            with self.lock: tenants = list(self.enabled)
            for tenant in tenants:
                try:
                    with self.store.sessions() as s:
                        vehicles = list(s.scalars(select(Vehicle).where(Vehicle.tenant == tenant).limit(100)))
                    stamp = now()
                    self.store.ingest(tenant, [event(v.vehicle_id, stamp, v.scenario) for v in vehicles])
                    self.error = None
                except Exception as exc:
                    self.error = type(exc).__name__

    def close(self):
        self.stop.set()
        if self.thread: self.thread.join(timeout=10)
