# DNP3 Master / Outstation Simulation

Docker-based simulation of a DNP3 SCADA **master station** and a **recloser outstation**, each with a web UI. The master polls live measurements from the outstation over DNP3/TCP and can send trip/close commands. The outstation simulates a 12 kV distribution recloser with voltage, current, and breaker status.

---

## Architecture

```
┌─────────────────────────┐       DNP3/TCP :20000        ┌──────────────────────────┐
│   Master Station        │ ◄────────────────────────────► │   Recloser Outstation    │
│   Container: master     │                                │   Container: outstation  │
│   Web UI    :8080       │                                │   Web UI    :8081        │
│   Master ID : 2         │                                │   Outstation ID : 1      │
└─────────────────────────┘                                └──────────────────────────┘
         │                                                            │
         └──────────────────── docker network: dnp3net ───────────────┘
```

| Component | Technology | Purpose |
|-----------|------------|---------|
| Master | Python 3.10, Flask, `dnp3-python` | Poll points, send CROB commands, SLD web UI |
| Outstation | Python 3.10, Flask, `dnp3-python` | Recloser simulation, DNP3 server, status web UI |
| Protocol | DNP3 over TCP (IEEE 1815) | Port 20000, opendnp3 stack via `dnp3-python` |

---

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine + Compose v2)
- ~500 MB disk for images
- Ports **8080**, **8081**, and **20000** available on the host

> **Apple Silicon (M1/M2/M3):** The `dnp3-python` package only publishes **linux/amd64** wheels. The compose file sets `platform: linux/amd64`, so containers run under emulation. Expect slightly slower startup on Mac.

---

## Project Structure

```
dnp3-simulation/
├── docker-compose.yml      # Orchestrates both containers
├── README.md
├── master/
│   ├── Dockerfile
│   ├── app.py              # Flask web UI + DNP3 master entry point
│   ├── dnp3_client.py      # Polling loop and command logic
│   ├── templates/index.html
│   └── static/style.css
└── outstation/
    ├── Dockerfile
    ├── app.py              # Flask web UI + DNP3 outstation entry point
    ├── recloser.py         # Recloser simulation and command handling
    ├── templates/index.html
    └── static/style.css
```

---

## How to Run

### 1. Start both containers (foreground)

From the project directory:

```bash
cd dnp3-simulation
docker compose up --build
```

Leave this terminal open to view logs. Press `Ctrl+C` to stop.

### 2. Start in background (detached)

```bash
docker compose up --build -d
```

### 3. View logs

```bash
# All services
docker compose logs -f

# Master only
docker compose logs -f master

# Outstation only
docker compose logs -f outstation
```

### 4. Check container status

```bash
docker compose ps
```

Expected output when healthy:

| Container | Status | Ports |
|-----------|--------|-------|
| `dnp3-recloser-outstation` | running (healthy) | 20000, 8081→8080 |
| `dnp3-master-station` | running | 8080 |

The master waits for the outstation health check before starting.

### 5. Rebuild after code changes

```bash
docker compose up --build -d
```

### 6. Stop and remove containers

```bash
docker compose down
```

Remove built images as well:

```bash
docker compose down --rmi local
```

---

## Accessing the Applications

### Web UIs

| Service | URL | Description |
|---------|-----|-------------|
| **Master Station** | http://localhost:8080 | Single line diagram, live SCADA data, trip/close controls |
| **Recloser Outstation** | http://localhost:8081 | Local recloser status, measurements, local trip/close buttons |

### DNP3 port (optional external access)

| Port | Protocol | Access |
|------|----------|--------|
| **20000** | DNP3/TCP | Exposed on host — outstation listens here. Master connects internally via Docker hostname `outstation`. |

---

## Web UI Features

### Master Station (http://localhost:8080)

- **Single line diagram (SLD)** — 132 kV bus → transformer → 12 kV feeder → recloser R-101 → load zone
- **Live overlay** — voltage, current, and power on the diagram
- **Recloser symbol** — green when closed, red when open; breaker arm animates with state
- **Status panel** — binary inputs (breaker closed/open, fault)
- **Control panel**
  - **Send OPEN Command** — DNP3 direct operate, trips the recloser
  - **Send CLOSE Command** — DNP3 direct operate, closes the recloser
- **Point database table** — last polled DNP3 values
- **Connection badge** — shows DNP3 link status and poll count

### Recloser Outstation (http://localhost:8081)

- **Breaker display** — large symbol with CLOSED / OPEN state
- **Measurements** — voltage (kV), current (A), active power (kW), updated every second
- **Binary input indicators** — BI-0 closed, BI-1 open, BI-2 fault
- **DNP3 point map table** — current database values
- **Local controls** — trip/close without using the master (useful for testing)
- **Connection badge** — shows whether a DNP3 master is connected

---

## DNP3 Configuration

| Parameter | Master | Outstation |
|-----------|--------|------------|
| Master address | 2 | expects master ID 2 |
| Outstation address | 1 | 1 |
| TCP port | connects to `outstation:20000` | listens on `0.0.0.0:20000` |
| Polling interval | ~1.5 s | — |
| Simulation update | — | ~1 s |

Environment variables (set in `docker-compose.yml`):

| Variable | Container | Default | Description |
|----------|-----------|---------|-------------|
| `OUTSTATION_HOST` | master | `outstation` | Docker hostname of outstation |
| `OUTSTATION_PORT` | master | `20000` | DNP3 TCP port |
| `DNP3_PORT` | outstation | `20000` | DNP3 listen port |
| `WEB_PORT` | both | `8080` | Internal Flask port |

---

## DNP3 Point Map

| Index | Group/Var | Type | Description |
|-------|-----------|------|-------------|
| 0 | 30/5 | Analog Input | Voltage (kV) |
| 1 | 30/1 | Analog Input | Current (A) |
| 2 | 30/1 | Analog Input | Active Power (kW) |
| 0 | 1/2 | Binary Input | Breaker Closed |
| 1 | 1/2 | Binary Input | Breaker Open |
| 2 | 1/2 | Binary Input | Fault |
| 0 | 12/1 | Binary Output | Trip / Close (CROB) |

### Control commands (from master)

| Action | DNP3 Object | Control Code | Effect |
|--------|-------------|--------------|--------|
| **OPEN / Trip** | BO-0 | `LATCH_OFF` (CROB) | Breaker opens, current → 0 A, power → 0 kW |
| **Close** | BO-0 | `LATCH_ON` (CROB) | Breaker closes, load current restored |

---

## REST API (for scripting / testing)

### Master — http://localhost:8080

```bash
# Get polled status
curl http://localhost:8080/api/status

# Send trip (open) command
curl -X POST http://localhost:8080/api/command/trip

# Send close command
curl -X POST http://localhost:8080/api/command/close
```

Example status response:

```json
{
  "voltage_kv": 11.89,
  "current_a": 190.0,
  "power_kw": 2.1,
  "breaker_closed": true,
  "breaker_open": false,
  "fault": false,
  "dnp3_connected": true,
  "last_poll": "2026-07-28 10:20:14 UTC",
  "last_command": "None",
  "poll_count": 4
}
```

### Outstation — http://localhost:8081

```bash
# Get local recloser status
curl http://localhost:8081/api/status

# Local trip (bypasses master)
curl -X POST http://localhost:8081/api/local-trip

# Local close
curl -X POST http://localhost:8081/api/local-close
```

---

## Typical Test Workflow

1. Start the stack: `docker compose up --build -d`
2. Open **master** UI at http://localhost:8080 — confirm **DNP3 Connected** badge is green
3. Observe live voltage (~12 kV) and current (~185 A) on the single line diagram
4. Click **Send OPEN Command** on the master — recloser turns red, current drops to 0
5. Open **outstation** UI at http://localhost:8081 — confirm **TRIP (DNP3 LATCH_OFF)** in last command
6. Click **Send CLOSE Command** on the master — feeder re-energizes, current returns

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Master shows **DNP3 Disconnected** | Outstation not ready | Wait 15–30 s; check `docker compose logs outstation` |
| Port already in use | Another service on 8080/8081/20000 | Stop conflicting service or change ports in `docker-compose.yml` |
| Slow startup on Mac | amd64 emulation | Normal on Apple Silicon; allow extra time for first build |
| Master starts before outstation | Health check failed | `docker compose restart master` |
| Commands have no effect | DNP3 not connected | Verify connection badge; restart both containers |

Check DNP3 link in master logs — look for `Connected to: outstation` and `channel state change: OPEN`.

---

## Dependencies

- [dnp3-python](https://pypi.org/project/dnp3-python/) 0.3.0b2 — Python bindings for opendnp3
- [Flask](https://flask.palletsprojects.com/) 3.0.3 — Web UI and REST API

---

## License / Notes

This is a **simulation/demo** project for learning and testing DNP3 SCADA workflows. It is not intended for production utility operations. The underlying opendnp3 library reached end-of-life in 2022; `dnp3-python` remains suitable for lab and demonstration use.
