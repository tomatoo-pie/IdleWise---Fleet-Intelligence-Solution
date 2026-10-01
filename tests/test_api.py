import time
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.simulator import event
from app.schemas import Telemetry


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('DEMO_PASSWORD','test-password')
    monkeypatch.setenv('INGEST_TOKEN','test-token')
    return create_app(f"sqlite:///{tmp_path / 'test.db'}",testing=True)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        assert c.post('/api/login',json={'username':'manager','password':'test-password'}).status_code==200
        yield c


def batch():
    start=int(time.time())-400
    return [event('V1',start+t,'idling','test') for t in range(0,306,5)]


def inject(client, events):
    return client.post('/api/telemetry',headers={'Authorization':'Bearer test-token'},json={'events':[e.model_dump(mode='json') for e in events]})


def test_ingestion_idempotency_and_actions(client):
    events=batch()
    assert inject(client,events).json()['accepted']==62
    assert inject(client,events).json()['duplicate']==62
    alerts=client.get('/api/alerts').json()['items']
    assert len(alerts)==1
    aid=alerts[0]['id']
    assert alerts[0]['litres']==pytest.approx(1.5*305/3600)
    body={'kind':'ACKNOWLEDGE','note':'Reviewed'}
    a=client.post(f'/api/alerts/{aid}/actions',headers={'Idempotency-Key':'k1'},json=body)
    b=client.post(f'/api/alerts/{aid}/actions',headers={'Idempotency-Key':'k1'},json=body)
    assert a.status_code==200 and a.json()['id']==b.json()['id']
    assert client.post(f'/api/alerts/{aid}/actions',headers={'Idempotency-Key':'k1'},json={'kind':'RESOLVE'}).status_code==409
    assert client.get(f'/api/alerts/{aid}').json()['evidence']
    assert client.get('/api/reports/daily').json()['items']
    assert client.get('/api/audit').json()['items']


def test_tenant_and_role_isolation(client):
    inject(client,batch())
    aid=client.get('/api/alerts').json()['items'][0]['id']
    client.post('/api/login',json={'username':'south','password':'test-password'})
    assert client.get('/api/alerts').json()['items']==[]
    assert client.get(f'/api/alerts/{aid}').status_code==404
    assert client.post(f'/api/alerts/{aid}/actions',headers={'Idempotency-Key':'x'},json={'kind':'RESOLVE'}).status_code==404
    client.post('/api/login',json={'username':'viewer','password':'test-password'})
    assert client.post('/api/demo/seed',json={}).status_code==403
    assert client.post(f'/api/alerts/{aid}/actions',headers={'Idempotency-Key':'y'},json={'kind':'RESOLVE'}).status_code==403


def test_validation_and_auth(client):
    assert client.post('/api/telemetry',json={'events':[batch()[0].model_dump(mode='json')]}).status_code==401
    e=batch()[0].model_dump(mode='json');e['tenant']='south'
    assert client.post('/api/telemetry',headers={'Authorization':'Bearer test-token'},json={'events':[e]}).status_code==422
    assert client.post('/api/demo/start',headers={'Origin':'https://evil.test'},json={}).status_code==403
    assert client.post('/api/demo/start',content='{}').status_code==415
    assert client.get('/api/vehicles?limit=1000').status_code==422
    client.post('/api/logout',json={})
    assert client.get('/api/summary').status_code==401


def test_atomic_rollback_for_conflicting_id(client):
    events=batch()
    inject(client,[events[0]])
    bad=events[0].model_copy(update={'speed_kmh':20})
    assert inject(client,[events[1],bad]).status_code==409
    assert client.get('/api/summary').json()['events']==1


def test_restart_replay_persistence(app,client):
    events=batch();inject(client,events)
    from app.store import Store
    other=Store(str(app.state.store.engine.url))
    assert other.ingest('north',events)['duplicate']==62
    assert other.rows(__import__('app.models',fromlist=['Alert']).Alert,'north')['items'][0]['active']==1
    other.engine.dispose()


def test_late_and_gap(client):
    events=batch();inject(client,events)
    old=events[1].model_copy(update={'event_id':'late-1'})
    assert inject(client,[old]).json()['late']==1
    ts=events[-1].event_time.timestamp()+20
    inject(client,[event('V1',ts,'idling')])
    a=client.get('/api/alerts').json()['items'][0]
    assert a['active']==0 and a['closure']=='gap'
    assert client.get('/api/summary').json()['late_events']==1


def test_seed_controls_and_pagination(client):
    assert client.post('/api/demo/seed',json={}).json()['seeded']==24
    assert client.post('/api/demo/seed',json={}).json()['seeded']==0
    first=client.get('/api/vehicles?limit=5').json()
    assert len(first['items'])==5 and first['next_cursor']
    second=client.get('/api/vehicles?limit=5&after='+str(first['next_cursor'])).json()
    assert not ({x['id'] for x in first['items']} & {x['id'] for x in second['items']})
    assert client.post('/api/demo/start',json={}).json()['running']
    assert not client.post('/api/demo/stop',json={}).json()['running']
    assert client.post('/api/demo/wrong',json={}).status_code==404
    assert client.get('/health').json()['status']=='ok'
    assert client.get('/api/me').json()['tenant']=='north'


def test_future_time_and_naive_time(client):
    e=event('V2',time.time()+100,'idling')
    assert inject(client,[e]).status_code==409
    data=e.model_dump(mode='json');data['event_time']='2026-01-01T00:00:00'
    with pytest.raises(ValueError):Telemetry(**data)


def test_resolve_then_ack_conflict(client):
    inject(client,batch());aid=client.get('/api/alerts').json()['items'][0]['id']
    assert client.post(f'/api/alerts/{aid}/actions',headers={'Idempotency-Key':'r'},json={'kind':'RESOLVE'}).status_code==200
    assert client.post(f'/api/alerts/{aid}/actions',headers={'Idempotency-Key':'a'},json={'kind':'ACKNOWLEDGE'}).status_code==409
