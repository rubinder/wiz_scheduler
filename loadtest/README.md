# WizScheduler Load Tests

Locust-based load tests simulating 10,000 managers each going through the full
workflow: register, set up company data, create shift templates, and generate
schedules.

## Setup

```bash
cd loadtest
pip install -r requirements.txt
```

## Run

```bash
# Web UI (recommended for first run)
locust -f locustfile.py --host http://localhost:8000

# Headless — 10,000 users, ramp 500/s, run 10 minutes
locust -f locustfile.py --host http://localhost:8000 \
  --headless -u 10000 -r 500 --run-time 10m

# Target a deployed environment
locust -f locustfile.py --host https://wizscheduler.com \
  --headless -u 10000 -r 500 --run-time 10m
```

## What it simulates

Each simulated user (manager) performs:

1. **Register** — creates a new account + company
2. **Create regions** — 1-3 regions
3. **Create locations** — 1-2 locations per region
4. **Create roles** — 3-6 roles
5. **Bulk-upload employees** — 10-50 employees per location with role assignments
6. **Set availability** — weekly availability for each employee
7. **Create shift templates** — one per location
8. **Generate schedules** — uses local scheduler (strategy: rotation), consumes NDJSON stream

After setup, each user loops on schedule generation with think-time pauses
to simulate ongoing usage.
