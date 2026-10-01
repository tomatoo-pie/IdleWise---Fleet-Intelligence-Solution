# IdleWise MVP

A local demo for finding avoidable vehicle idling. Built with React, FastAPI and SQLite.

## Run on Windows

1. Install Python 3.11 or newer (tested with Python 3.13), with Python on PATH.
2. Extract the ZIP and double-click **start.bat**. The first run needs internet to install Python dependencies.
3. Keep its terminal open and visit http://127.0.0.1:8000.
4. Select **North fleet · Manager** and enter **idlewise-local**.

The compiled dashboard is included, so Node.js is not required to run the app. Stop the server with Ctrl+C. Your data stays in `idlewise.db`; the simulator starts paused after a restart.

## Two-minute demo

1. Click **Load demo fleet** to add 24 synthetic vehicles. Six minutes of historical signals create four idle alerts immediately.
2. Click **Start stream** to see incoming events and current vehicle states.
3. Inspect an alert, review its signal evidence, enter a note and click **Acknowledge** or **Resolve alert**.
4. Open **Vehicles** to compare idling, driving, parked, PTO, missing-signal and EV scenarios.
5. Open **Reports** and export the daily totals as CSV. Pause the stream when finished.

## MVP scope

- Persistent local vehicle data, idling detection and fuel estimates.
- Dashboard, vehicle list, searchable alert queue and alert evidence.
- Acknowledge/resolve actions and daily CSV reports.
- Manager and viewer demo accounts; separate North and South fleets.
- Synthetic telemetry generator and a JSON ingestion endpoint.

The rule requires ignition on and speed below 3 km/h to enter idling, below 5 km/h to stay idle, no PTO exemption and 300 seconds of continuity. Missing required signals or a gap over 10 seconds interrupts continuity. EVs are excluded. Fuel integrates reported rates, with a marked 1.5 L/h assumption for missing rates. Figures represent estimated consumed fuel, not verified savings. Daily fuel is apportioned by episode duration across UTC dates.

Late events are retained but do not revise earlier alerts. Duplicate event IDs are ignored if their payload matches; conflicting reuse is rejected. Resolving an alert records a review action; it does not control the vehicle or stop an ongoing episode.

This is a single-process local MVP using synthetic data. Cloud deployment, Kafka, Kubernetes, real OEM integration, enterprise sign-in and large-scale processing are outside its scope. Demo credentials are intended for local use.

## Development

From the project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests -q
```

To rebuild the dashboard (Node.js required):

```powershell
cd web
npm ci
npm run build
```

Start the backend with `.\.venv\Scripts\python.exe run.py` from the project directory. `DEMO_PASSWORD`, `INGEST_TOKEN` and `PORT` can be set as environment variables. The default ingestion token is `local-ingest-token`; send it as a Bearer token to `POST /api/telemetry`. The request body contains an `events` array matching `app/schemas.py`; ingestion is scoped to the North fleet.

## Code map

- `app/domain.py`: idling state machine and fuel calculation.
- `app/store.py`, `app/models.py`: SQLite persistence and actions.
- `app/main.py`: HTTP endpoints and local session login.
- `app/simulator.py`: synthetic vehicle signals.
- `web/src/main.jsx`, `web/src/style.css`: dashboard.
- `tests/`: detection, ingestion, persistence, access and workflow checks.

Validation: 17 automated tests passed; production dashboard build succeeded.
