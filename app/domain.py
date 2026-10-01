"""Pure idling policy. No transport or storage imports."""
from dataclasses import dataclass


@dataclass
class IdleState:
    last_ts: float | None = None
    start_ts: float | None = None
    litres: float = 0
    last_rate: float | None = None
    estimated: bool = False


def advance(state: IdleState, *, ts: float, ignition: bool | None,
            speed: float | None, pto: bool | None, rate: float | None,
            powertrain: str = "ICE") -> tuple[IdleState, str]:
    """Returns new state and disposition. Old events never rewind live state.

    Previous valid rate integrates only contiguous candidate intervals. At a
    stop/gap the old episode is closed by the application, then state resets.
    """
    if state.last_ts is not None and ts <= state.last_ts:
        return state, "late"
    gap = state.last_ts is not None and ts - state.last_ts > 10
    known = ignition is not None and speed is not None and pto is not None
    speed_limit = 5 if state.start_ts is not None and not gap else 3
    eligible = known and ignition and speed < speed_limit and not pto and powertrain == "ICE"
    if not eligible:
        return IdleState(last_ts=ts), "uncertain" if not known else "inactive"
    if state.start_ts is None or gap:
        return IdleState(ts, ts, 0, rate, rate is None), "gap" if gap else "candidate"
    delta = ts - state.last_ts
    # A class default is explicitly flagged as estimated, including zero rates.
    litres = state.litres + (1.5 if state.last_rate is None else state.last_rate) * delta / 3600
    result = IdleState(ts, state.start_ts, litres, rate, state.estimated or rate is None)
    return result, "qualified" if ts - result.start_ts >= 300 else "candidate"
