# oc_db_kyoo

Database Queue Manager for OpenCitations. Sits between the caching layer (Varnish/Redis) and the database backends, managing per-backend request queuing, concurrency limiting, and least-queue load balancing.

## Overview

**oc_db_kyoo** (pronounced like "queue") is an async HTTP reverse proxy that protects database backends from overload by:

- Limiting concurrent requests per backend
- Queuing excess requests with configurable depth and timeout
- Routing requests to the least-loaded backend (least-queue strategy)
- Returning a friendly "backend busy" page when all backends are saturated

Each instance of oc_db_kyoo manages **one type** of database (e.g., all Virtuoso instances, or all QLever instances). Each backend within that instance gets its own independent queue.

## Architecture

```
Service (oc-api, oc-sparql, oc-search, ...)
  → Varnish (HTTP cache)
    → Redis (secondary cache)
      → oc_db_kyoo (Virtuoso)  → [virtuoso-1, virtuoso-2, ...]
      → oc_db_kyoo (QLever)    → [qlever-1, qlever-2, ...]
```

## Configuration

### conf.json

```json
{
  "listen_port": 8080,
  "log_level": "info",
  "backends": [
    {
      "name": "virtuoso-1",
      "host": "virtuoso-1.default.svc.cluster.local",
      "port": 8890,
      "path": "/sparql"
    },
    {
      "name": "virtuoso-2",
      "host": "virtuoso-2.default.svc.cluster.local",
      "port": 8890,
      "path": "/sparql"
    }
  ],
  "max_concurrent_per_backend": 10,
  "max_queue_per_backend": 50,
  "queue_timeout": 120,
  "backend_timeout": 900
}
```

| Parameter | Description |
|---|---|
| `listen_port` | Port the service listens on |
| `log_level` | Logging level (debug, info, warning, error) |
| `backends` | List of database backends to manage |
| `max_concurrent_per_backend` | Max simultaneous requests sent to each backend |
| `max_queue_per_backend` | Max requests waiting in queue per backend |
| `queue_timeout` | Max seconds a request can wait in queue before being dropped |
| `backend_timeout` | Max seconds to wait for a backend response |

### Environment Variables

Environment variables override `conf.json` values (Docker/Kubernetes pattern):

```env
LISTEN_PORT=8080
LOG_LEVEL=info
MAX_CONCURRENT_PER_BACKEND=10
MAX_QUEUE_PER_BACKEND=50
QUEUE_TIMEOUT=120
BACKEND_TIMEOUT=900
```

Backends can be configured via individual environment variables:

```env
BACKEND_0_NAME=virtuoso-1
BACKEND_0_HOST=virtuoso-1.default.svc.cluster.local
BACKEND_0_PORT=8890
BACKEND_0_PATH=/sparql
BACKEND_1_NAME=virtuoso-2
BACKEND_1_HOST=virtuoso-2.default.svc.cluster.local
BACKEND_1_PORT=8890
BACKEND_1_PATH=/sparql
```

The service discovers backends by scanning `BACKEND_N_HOST` env vars (N=0,1,2,...). You can define more backends via env vars than exist in `conf.json` — env vars always take priority.

## Running

### Local development

```bash
pip install -r requirements.txt
python app.py
# or with custom port
python app.py --port 9090
```

### Docker

```bash
docker build -t opencitations/oc_db_kyoo:1.0.0 .
docker run -p 8080:8080 \
  -e MAX_CONCURRENT_PER_BACKEND=5 \
  -e BACKEND_0_NAME=db1 \
  -e BACKEND_0_HOST=localhost \
  -e BACKEND_0_PORT=8890 \
  -e BACKEND_0_PATH=/sparql \
  opencitations/oc_db_kyoo:1.0.0
```

### Kubernetes

See `manifests/oc-db-kyoo.yaml` and `.env.example` for deployment templates.

## Endpoints

| Endpoint | Description |
|---|---|
| `/{path}` | Proxy — all requests are forwarded to the least-loaded backend |
| `/health` | Health check (200 if at least one backend available, 503 if all overloaded) |
| `/status` | Detailed per-backend queue statistics (JSON) |

### /status response example

```json
{
  "status": "ok",
  "backends": [
    {
      "name": "virtuoso-1",
      "active_requests": 3,
      "queued_requests": 0,
      "total_requests": 1250,
      "total_completed": 1245,
      "total_errors": 2,
      "total_timeouts": 3,
      "total_rejected": 0,
      "avg_response_time_ms": 245.67
    },
    {
      "name": "virtuoso-2",
      "active_requests": 5,
      "queued_requests": 2,
      "total_requests": 1180,
      "total_completed": 1175,
      "total_errors": 1,
      "total_timeouts": 4,
      "total_rejected": 0,
      "avg_response_time_ms": 312.45
    }
  ]
}
```

## How it works

1. A request arrives at oc_db_kyoo
2. The **least-queue router** selects the backend with the lowest total load (active + queued)
3. If the backend has capacity, the request is forwarded immediately
4. If the backend is at max concurrency, the request enters the queue
5. If the queue is full, fallback backends are tried
6. If all backends are saturated, a **503 "Backend Busy"** page is returned
7. Queue timeout prevents requests from waiting indefinitely

## Tech Stack

- **FastAPI** + **uvicorn** — async HTTP framework
- **httpx** — async HTTP client for backend forwarding
- **asyncio.Semaphore** — concurrency control per backend
- **Pydantic** — configuration validation
