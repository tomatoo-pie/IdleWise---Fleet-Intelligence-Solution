import pytest
from app.domain import IdleState, advance


def step(s, ts, **kw):
    return advance(s, ts=ts, **dict(ignition=True, speed=0, pto=False, rate=1.5, **kw))


def test_five_minutes_and_fuel():
    state = IdleState()
    for ts in range(0,301,5): state, disposition = step(state, ts)
    assert disposition == "qualified"
    assert state.litres == pytest.approx(.125)


@pytest.mark.parametrize("ignition,speed,pto,powertrain", [(False,0,False,"ICE"),(True,40,False,"ICE"),(True,0,True,"ICE"),(True,0,False,"EV")])
def test_ineligible(ignition,speed,pto,powertrain):
    state, disposition = advance(IdleState(), ts=0, ignition=ignition,speed=speed,pto=pto,rate=1.5,powertrain=powertrain)
    assert state.start_ts is None


def test_missing_gap_and_late():
    state,_ = step(IdleState(), 100)
    new,d = step(state, 99)
    assert new == state and d == "late"
    new,d = step(state, 120)
    assert new.start_ts == 120 and d == "gap"
    new,d = advance(new,ts=125,ignition=True,speed=None,pto=False,rate=1.5)
    assert new.start_ts is None and d == "uncertain"


def test_hysteresis_and_zero_rate():
    s,_=advance(IdleState(),ts=0,ignition=True,speed=2,pto=False,rate=0)
    s,_=advance(s,ts=5,ignition=True,speed=4,pto=False,rate=0)
    assert s.start_ts==0 and s.litres==0
    s,_=advance(s,ts=10,ignition=True,speed=5,pto=False,rate=0)
    assert s.start_ts is None


def test_missing_rate_flagged():
    s,_=advance(IdleState(),ts=0,ignition=True,speed=0,pto=False,rate=None)
    s,_=advance(s,ts=5,ignition=True,speed=0,pto=False,rate=None)
    assert s.estimated and s.litres==pytest.approx(1.5*5/3600)
