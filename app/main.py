"""Local MVP: one Python process, SQLite and a compiled React dashboard."""
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, Depends, HTTPException, Request, Response, Header, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, delete
from .models import Vehicle, Alert, Event, Audit, SessionToken
from .schemas import Batch, Login, ActionRequest
from .store import Store
from .simulator import Simulator


def create_app(database_url=None, *, testing=False):
    store=Store(database_url or os.getenv('DATABASE_URL','sqlite:///./idlewise.db'))
    simulator=Simulator(store)
    buckets=defaultdict(deque)
    password=os.getenv('DEMO_PASSWORD','idlewise-local')
    ingest_token=os.getenv('INGEST_TOKEN','local-ingest-token')
    users={'manager':('north','manager'),'viewer':('north','viewer'),'south':('south','manager')}
    @asynccontextmanager
    async def lifespan(app):
        if not testing:simulator.start()
        yield
        simulator.close();store.engine.dispose()
    app=FastAPI(title='IdleWise MVP',version='1.0.0',lifespan=lifespan)
    app.state.store=store;app.state.simulator=simulator

    @app.middleware('http')
    async def boundary(request,call_next):
        if request.method in {'POST','PUT','PATCH','DELETE'}:
            origin=request.headers.get('origin')
            if origin and urlsplit(origin).netloc!=request.headers.get('host'):
                return JSONResponse({'detail':'Cross-origin writes are not allowed'},status_code=403)
            if request.headers.get('content-type','').split(';')[0]!='application/json':
                return JSONResponse({'detail':'application/json required'},status_code=415)
            chunks=[];size=0
            async for chunk in request.stream():
                size+=len(chunk)
                if size>2_000_000:return JSONResponse({'detail':'Payload too large'},status_code=413)
                chunks.append(chunk)
            request._body=b''.join(chunks)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Content-Security-Policy']="default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        if request.url.path.startswith('/api/'):response.headers['Cache-Control']='no-store'
        return response

    def throttle(key,maximum):
        now=time.monotonic();bucket=buckets[key]
        while bucket and bucket[0]<now-60:bucket.popleft()
        if len(bucket)>=maximum:raise HTTPException(429,'Rate limit exceeded',headers={'Retry-After':'60'})
        bucket.append(now)

    def identity(request:Request):
        digest=hashlib.sha256(request.cookies.get('idlewise_session','').encode()).hexdigest()
        with store.sessions() as s:
            row=s.get(SessionToken,digest)
            if not row or row.expires<time.time():raise HTTPException(401,'Sign in required')
            user={'name':row.actor,'tenant':row.tenant,'role':row.role,'expires':row.expires}
        throttle(user['tenant']+':'+user['name'],600)
        if request.method=='GET':store.audit(user['tenant'],user['name'],'READ '+request.url.path)
        return user

    def manager(user=Depends(identity)):
        if user['role']!='manager':raise HTTPException(403,'Manager role required')
        return user

    @app.get('/health')
    def health():
        with store.sessions() as s:s.execute(select(1))
        return {'status':'ok','mode':'mvp','simulator_error':simulator.error}

    @app.post('/api/login')
    def login(body:Login,request:Request,response:Response):
        throttle('login:'+request.client.host,20)
        if body.username not in users or not hmac.compare_digest(body.password,password):raise HTTPException(401,'Invalid username or password')
        tenant,role=users[body.username];token=secrets.token_urlsafe(32);expires=time.time()+28800
        with store.sessions.begin() as s:
            s.execute(delete(SessionToken).where(SessionToken.expires<time.time()))
            s.add(SessionToken(digest=hashlib.sha256(token.encode()).hexdigest(),tenant=tenant,actor=body.username,role=role,expires=expires))
        response.set_cookie('idlewise_session',token,httponly=True,samesite='strict',max_age=28800)
        store.audit(tenant,body.username,'LOGIN')
        return {'name':body.username,'tenant':tenant,'role':role,'expires':expires}

    @app.post('/api/logout')
    def logout(request:Request,response:Response,user=Depends(identity)):
        with store.sessions.begin() as s:s.execute(delete(SessionToken).where(SessionToken.digest==hashlib.sha256(request.cookies.get('idlewise_session','').encode()).hexdigest()))
        response.delete_cookie('idlewise_session');return {'ok':True}

    @app.get('/api/me')
    def me(user=Depends(identity)):return user

    @app.get('/api/summary')
    def summary(user=Depends(identity)):return {**store.summary(user['tenant']),'simulating':user['tenant'] in simulator.enabled}

    @app.get('/api/vehicles')
    def vehicles(limit:int=Query(50,ge=1,le=100),after:int=Query(0,ge=0),user=Depends(identity)):
        return store.rows(Vehicle,user['tenant'],limit=limit,after=after)

    @app.get('/api/alerts')
    def alerts(limit:int=Query(50,ge=1,le=100),after:int=Query(0,ge=0),status:str|None=Query(None,pattern='^(OPEN|ACKNOWLEDGED|RESOLVED)$'),user=Depends(identity)):
        return store.rows(Alert,user['tenant'],limit=limit,after=after,status=status)

    @app.get('/api/vehicles/{vehicle_id}/episodes')
    def episodes(vehicle_id:str,limit:int=Query(50,ge=1,le=100),after:int=Query(0,ge=0),user=Depends(identity)):
        return store.rows(Alert,user['tenant'],limit=limit,after=after,vehicle=vehicle_id)

    @app.get('/api/alerts/{alert_id}')
    def detail(alert_id:int,user=Depends(identity)):
        with store.sessions() as s:
            a=s.scalar(select(Alert).where(Alert.id==alert_id,Alert.tenant==user['tenant']))
            if not a:raise HTTPException(404,'Alert not found')
            rows=list(s.scalars(select(Event).where(Event.tenant==user['tenant'],Event.vehicle_id==a.vehicle_id,Event.ts>=a.start_ts,Event.ts<=a.end_ts).order_by(Event.ts.desc()).limit(12)))
            return {**store.serialize(a),'evidence':[store.serialize(e) for e in reversed(rows)],
                    'rule':'Ignition on; below 3 km/h to enter, below 5 to remain; no PTO; 300 seconds; no gap over 10 seconds.',
                    'fuel_note':'Estimated consumed fuel, not verified savings. Missing rates use a 1.5 L/h assumption.'}

    @app.post('/api/alerts/{alert_id}/actions')
    def action(alert_id:int,body:ActionRequest,idempotency_key:str=Header(min_length=1,max_length=100),user=Depends(manager)):
        try:return store.action(user['tenant'],user['name'],alert_id,idempotency_key,body.kind,body.note)
        except LookupError as e:raise HTTPException(404,str(e))
        except ValueError as e:raise HTTPException(409,str(e))

    @app.get('/api/reports/daily')
    def daily(user=Depends(identity)):return {'items':store.daily(user['tenant'])}

    @app.get('/api/reports/export')
    def export(user=Depends(identity)):
        import csv,io
        buf=io.StringIO();writer=csv.DictWriter(buf,fieldnames=['date','episodes','idle_minutes','litres'])
        writer.writeheader();writer.writerows(store.daily(user['tenant']))
        return Response(buf.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="idlewise-daily.csv"'})

    @app.get('/api/audit')
    def audit(limit:int=Query(50,ge=1,le=100),after:int=Query(0,ge=0),user=Depends(manager)):
        return store.rows(Audit,user['tenant'],limit=limit,after=after)

    @app.post('/api/demo/seed')
    def seed(user=Depends(manager)):return simulator.seed(user['tenant'])

    @app.post('/api/demo/{command}')
    def demo(command:str,user=Depends(manager)):
        if command not in {'start','stop'}:raise HTTPException(404,'Unknown command')
        with simulator.lock:
            if command=='start':simulator.enabled.add(user['tenant'])
            else:simulator.enabled.discard(user['tenant'])
        return {'running':command=='start'}

    @app.post('/api/telemetry')
    def telemetry(body:Batch,authorization:str=Header(default='')):
        if not hmac.compare_digest(authorization,'Bearer '+ingest_token):raise HTTPException(401,'Invalid ingestion credential')
        try:return store.ingest('north',body.events)
        except ValueError as e:raise HTTPException(409,str(e))

    static=Path(__file__).resolve().parent.parent/'web'/'dist'
    if static.exists():app.mount('/assets',StaticFiles(directory=static/'assets'),name='assets')
    @app.get('/')
    def index():
        if (static/'index.html').exists():return FileResponse(static/'index.html')
        return {'message':'Build the dashboard with npm ci and npm run build in web/'}
    return app
