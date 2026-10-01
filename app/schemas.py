from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator
import math


class Telemetry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    vehicle_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    event_time: datetime
    ignition: bool | None
    speed_kmh: float | None = Field(default=None, ge=0, le=300)
    fuel_rate_lph: float | None = Field(default=None, ge=0, le=100)
    pto_active: bool | None = None
    powertrain: Literal["ICE", "EV"] = "ICE"

    @field_validator("event_time")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("event_time must include a timezone")
        return value.astimezone(timezone.utc)

    @field_validator("speed_kmh", "fuel_rate_lph")
    @classmethod
    def finite(cls, value):
        if value is not None and not math.isfinite(value):
            raise ValueError("value must be finite")
        return value


class Batch(BaseModel):
    events: list[Telemetry] = Field(min_length=1, max_length=1000)


class Login(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)


class ActionRequest(BaseModel):
    kind: Literal["ACKNOWLEDGE", "RESOLVE"]
    note: str = Field(default="", max_length=500)
