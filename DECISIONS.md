# Architecture Decision Records

Decisions are recorded here as they are made. Each ADR captures **Context** (what forced the decision), **Decision** (what we chose), and **Consequences** (what it costs or enables).

Newest first.

---

## ADR index

| # | Decision | Status | Date |
|---|---|---|---|
| 1 | Use `uv` for dependency and Python version management | Accepted | 2026-09-13 |
| 2 | Use `hatchling` as the build backend | Accepted | 2026-09-13 |
| 3 | Run the container as non-root `appuser` (uid 10001) | Accepted | 2026-09-13 |
| 4 | Bridge structlog and stdlib logging via ProcessorFormatter | Accepted | 2026-09-15 |
| 5 | Pure ASGI middleware for request IDs (not BaseHTTPMiddleware) | Accepted | 2026-09-15 |
| 6 | Async SQLAlchemy with asyncpg and connection recycling | Accepted | 2026-09-20 |
| 7 | Alembic reads `DATABASE_URL` from Settings, not `alembic.ini` | Accepted | 2026-09-20 |
| 8 | Postgres is not published to the host | Accepted | 2026-09-20 |
| 9 | Health probes are registered at root, not under `/v1` | Accepted | 2026-09-20 |
| 10 | The initial migration is empty; it stamps `alembic_version` only | Accepted | 2026-09-22 |
| 11 | `Idempotency-Key` is required on `POST /v1/events` | Accepted | 2026-09-23 |

---

### ADR-011 — `Idempotency-Key` is required on `POST /v1/events`

**Status:** Accepted
**Date:** 2026-09-23

**Context**
A `POST /v1/events` request may be retried by the client at any time —
network timeout, 5xx, or an operator replaying a batch. Without an
idempotency key, every retry creates a new event and a new set of
deliveries. Two designs were considered: require the header, or accept
requests without it and skip deduplication.

**Decision**
`Idempotency-Key` is required. A request without it returns `422
Unprocessable Entity` with a Problem Details body naming the missing
header. There is no unprotected ingestion path.

**Consequences**
- Every accepted event carries a per-tenant deduplication key, so a client
  retry can never duplicate a delivery fan-out.
- Clients must generate and preserve a key across retries. This is a
  small cost; the same pattern is used by Stripe and Shopify.
- Bulk imports that do not have a natural key must synthesize one (e.g. a
  UUID per payload) — the server does not do this on the client's behalf.

**Alternatives considered**
- Optional header with no deduplication when absent — rejected: forces
  every downstream consumer to be idempotent, and hides the failure mode
  from the client.
- Server-generated key from a body hash — rejected: identical bodies with
  different intent would collide; the client's intent is authoritative.

---

### ADR-010 — The initial migration is empty; it stamps `alembic_version` only

**Status:** Accepted
**Date:** 2026-09-22

**Context**
`/health/ready` returns `503` unless `alembic_version` has a row. The
project needs an "at head" signal even before any domain table exists.
Two options: create the first domain table in the initial migration, or
ship an empty revision that only creates the version row.

**Decision**
The first Alembic revision has an empty `upgrade()` and `downgrade()`. Its
only effect is to insert the revision id into `alembic_version`, which
Alembic creates on first upgrade. Domain tables land in the migrations
that introduce their models.

**Consequences**
- Readiness flips to `200` on a schema with no domain tables — matches what
  "migrations at head" means.
- Each future migration corresponds to one feature.
- Autogenerate is wired; the next revision with real models produces real
  DDL.

**Alternatives considered**
- Create `tenants` in the initial migration — rejected: couples the
  readiness signal to later domain work.
- Skip the initial revision; rely on `alembic_version` existing when the
  first real migration runs — rejected: readiness stays `503` until the first domain migration lands.

---

### ADR-009 — Health probes are registered at root, not under `/v1`

**Status:** Accepted
**Date:** 2026-09-20

**Context**
The API versioning scheme mounts business resources under `/v1/*`. Health
probes are consumed by load balancers and orchestrators (Kubernetes, Fly,
Render) that are configured with a fixed path and do not participate in API
versioning. Placing probes under `/v1/health/...` couples infrastructure
lifecycle to API evolution.

**Decision**
`/health/live` and `/health/ready` are registered at the application root.
The `/v1` router aggregates only resource endpoints. `/health/ready` returns
`503` when the service cannot serve traffic; `200` otherwise.

**Consequences**
- Load balancers use a stable path independent of API versioning.
- `app/main.py` must register the health router separately from the `/v1`
  router. This is explicit in the code.
- A future `/v2` will not silently move the probe path.

**Alternatives considered**
- Mount probes under `/v1/health/*` — rejected: forces infrastructure to
  track API versions; breaks Kubernetes conventions.
- Use the root path but suffix with version — rejected: adds no value for
  a probe whose contract is already minimal.

---

### ADR-008 — Postgres is not published to the host

**Status:** Accepted
**Date:** 2026-09-20

**Context**
The first version of `docker-compose.yml` mapped container port 5432 to host
port 5432 for interactive debugging. On this VM that collided with the
system Postgres, causing `docker compose up` to fail. More importantly, a
published database port is a security surface: the port is reachable from
any interface on the host, and the container is meant to be deployed to
environments where Postgres should not be publicly exposed.

**Decision**
The `db` service does not publish a host port. Containers reach Postgres
over the compose bridge by service name (`db:5432`). Interactive debugging
is done with `docker compose exec db psql -U hookdaemon -d hookdaemon`.

**Consequences**
- No host port conflict on any developer machine.
- Database is not reachable from outside the compose network.
- GUI database clients cannot connect without adding a loopback-only
  binding on a non-standard port; not needed for this project.
- When a hosted database is used in production, its exposure is a
  provider-specific concern; the compose file teaches the correct
  default.

**Alternatives considered**
- Publish on `127.0.0.1:5432:5432` — rejected: still conflicts with a
  local Postgres, still exposes the port.
- Publish on a non-standard host port (`54320:5432`) — rejected: solves
  the collision but not the security concern; unnecessary given
  `docker compose exec`.

---

### ADR-007 — Alembic reads `DATABASE_URL` from Settings, not `alembic.ini`

**Status:** Accepted
**Date:** 2026-09-20

**Context**
Alembic's default template puts `sqlalchemy.url` in `alembic.ini`. That
file is committed to git. Any URL with a real password ends up in version
control. Alembic also produces migrations that must be runnable inside the
deployment image, not only on a developer's host.

**Decision**
`alembic.ini` omits `sqlalchemy.url`. `alembic/env.py` imports the
application `Settings` and calls
`config.set_main_option("sqlalchemy.url", get_settings().database_url)`,
escaping `%` as `%%` because Alembic's `Config` treats `%` as interpolation.

The Dockerfile copies `alembic.ini` and `alembic/` into the image so
`alembic upgrade head` can run as a separate deploy step inside the
deployed environment.

**Consequences**
- Only one source of truth for the connection string: `Settings` and `.env`.
- No credentials in the repository.
- Percent-encoded passwords work correctly (mitigated by escaping).
- Alembic is coupled to `app.config`, so `app` must be importable when
  `env.py` runs. Both host and image satisfy this.
- Migrations run in a single transaction with `NullPool` (one connection,
  closed after).

**Alternatives considered**
- Keep `sqlalchemy.url` in `alembic.ini` — rejected: credentials in VCS.
- Read the URL from an environment variable directly in `env.py` —
  rejected: duplicates the parsing logic that `Settings` already handles.
- Run migrations from the host only — rejected: the deployment must be
  self-contained.

---

### ADR-006 — Async SQLAlchemy with asyncpg and connection recycling

**Status:** Accepted
**Date:** 2026-09-20

**Context**
Every request to the API may need a database session. The API is async
(FastAPI + asyncio); blocking the event loop on database I/O would serialize
all concurrent requests. Postgres servers often close idle connections after
a configured interval, and a connection that has been idle long enough to
be closed server-side but not client-side produces errors on the next use.

**Decision**
Use SQLAlchemy 2.x with `create_async_engine` and the `asyncpg` driver.
Engine configuration:

- `pool_size=5`, `max_overflow=10` (from Settings)
- `pool_pre_ping=True` — validates a pooled connection before use
- `pool_recycle=1800` — recycles connections every 30 minutes so
  server-side idle timeouts do not invalidate in-use connections
- `echo=False`, `hide_parameters=True` — even if SQL echo is enabled for
  debugging, parameter values are masked

`SessionFactory` uses `expire_on_commit=False` (FastAPI recommendation so
instances remain usable after commit) and `autoflush=False` (explicit flush
control).

**Consequences**
- API remains non-blocking under concurrent load.
- `pool_pre_ping` interacts poorly with some asyncpg failure modes
  (InternalClientError on server-side session termination); `pool_recycle`
  mitigates by ensuring connections do not reach the idle timeout.
- `asyncpg` is required as a runtime dependency; the pure-Python drivers
  are not used.
- Session lifecycle is bound to the request via `get_session` dependency.

**Alternatives considered**
- Sync SQLAlchemy with `psycopg2` — rejected: blocks the event loop.
- `psycopg3` async — viable; chose `asyncpg` for maturity and performance
  at the current scale.
- No connection pooling — rejected: reconnect overhead per request.

---

### ADR-005 — Pure ASGI middleware for request IDs (not BaseHTTPMiddleware)

**Status:** Accepted
**Date:** 2026-09-15

**Context**
Every request needs a correlation id that (a) is echoed to the client, (b)
appears on every log line emitted during the request, and (c) is available
to the global exception handler for 500 responses. Starlette offers two
implementation options: the higher-level `BaseHTTPMiddleware` or a pure
ASGI callable.

**Decision**
Use a pure ASGI callable (`RequestIDMiddleware`) implementing
`__call__(scope, receive, send)`. It binds `request_id` into
`structlog.contextvars` at the start of each HTTP request and wraps the
response's `http.response.start` message to inject the `X-Request-ID`
response header.

**Consequences**
- No per-request anyio task-group overhead; the middleware runs in the same
  context as the endpoint, so `contextvars` propagate correctly.
- Streaming responses are not buffered.
- Direct access to the raw ASGI message, required to set the response
  header before the body is streamed.
- Client-supplied `X-Request-ID` values are sanitized against a strict
  character pattern before use, closing CWE-117 (log injection) and
  CWE-113 (HTTP response splitting).
- Slightly more code than the `BaseHTTPMiddleware` equivalent; the
  tradeoffs are documented inline.

**Alternatives considered**
- `BaseHTTPMiddleware` — rejected: runs each request in a separate task,
  disrupts `contextvars` propagation for downstream middleware, buffers
  streaming responses, and hides tracebacks. Confirmed in Starlette's own
  issue history and by production benchmarks from LiteLLM and IBM's
  MCP ContextForge.
- Echo the id via a FastAPI dependency — rejected: does not run for
  framework-level 4xx/5xx responses and does not cover middleware logs.

---

### ADR-004 — Bridge structlog and stdlib logging via ProcessorFormatter

**Status:** Accepted
**Date:** 2026-09-15

**Context**
We need a single JSON log stream from the whole process — our code plus
uvicorn, SQLAlchemy, httpx, and any future library. Two independent
loggers would produce interleaved JSON and plaintext, defeating structured
observability. We also need request-scoped fields (`request_id`, later
`event_id`, `delivery_id`) to appear on every line without threading them
through function arguments.

**Decision**
Use structlog with `structlog.stdlib.ProcessorFormatter`. Our loggers are
configured via `structlog.configure`; the root stdlib logger receives a
single handler whose formatter is a `ProcessorFormatter`. Both code paths
share the same processor chain and renderer. Request-scoped fields flow
through `structlog.contextvars`, cleared at the start of each request.

Redaction of sensitive keys runs as a processor before exception and stack
renderers, so any future processor cannot introduce an unredacted
sensitive field into the event dict.

**Consequences**
- One log pipeline for the whole process; third-party libraries get JSON
  for free.
- `contextvars` isolation prevents request-scoped fields from leaking
  between concurrent requests on the same worker.
- `structlog.reset_defaults()` must be called before `configure()` inside
  `setup_logging` so cached lazy proxies are re-evaluated. Tests depend
  on this.
- Redaction is key-based, not content-based; secrets embedded inside
  strings (e.g. inside an exception message) are not redacted. Documented
  in the module docstring.

**Alternatives considered**
- stdlib `logging` with a custom `Formatter` — rejected: no clean way to
  add structured fields without re-implementing a processor chain.
- structlog only, no stdlib bridge — rejected: uvicorn, SQLAlchemy, and
  httpx would remain plaintext.
- Denylist-based content scanning of every log message — rejected:
  false positives and never complete.

---

### ADR-003 — Run the container as non-root `appuser` (uid 10001)

**Status:** Accepted
**Date:** 2026-09-13

**Context**
Most managed container platforms and enterprise security baselines require
containers to run as non-root. Running as root also makes any RCE more
damaging because the attacker inherits a highly privileged UID inside the
namespace.

**Decision**
The Dockerfile creates a dedicated user `appuser` with uid `10001`, chowns
`/app` to it, and switches to it before installing dependencies and copying
the app code. The CMD runs as `appuser`.

**Consequences**
- Enables deployment on platforms that reject root containers.
- Blocks write access outside `/app`, so accidental writes surface early.
- Requires every `COPY` to use `--chown=appuser:appuser`.
- Forces layer ordering: user creation and `chown /app` must precede `uv sync`.

**Alternatives considered**
- Run as root — rejected: insecure and disallowed by most managed platforms.
- Use the base image's default user — rejected: `python:3.11-slim` runs as root.
- Use uid 1000 — rejected: collides with host users on bind mounts; 10001 avoids conflicts.

---

### ADR-002 — Use `hatchling` as the build backend

**Status:** Accepted
**Date:** 2026-09-13

**Context**
`uv` and modern PEP 517 tooling can build a project wheel with any backend.
Choices in common use: `setuptools` (legacy default), `poetry-core` (tied to
Poetry), `hatchling`, `flit`, `pdm-backend`.

**Decision**
Use `hatchling`. Minimal config block, first-class support for `packages = ["app"]`,
fast, and maintained by the Hatch team. `uv init` itself ships hatchling by default,
which reduces surprise for future contributors.

**Consequences**
- Build backend is fast and produces small wheels.
- Requires one line to declare the package location (`[tool.hatch.build.targets.wheel]`).
- Not tied to any specific dev workflow (unlike `poetry-core`).
- Non-standard for teams deeply invested in `setuptools` — a small learning cost.

**Alternatives considered**
- `setuptools` — rejected: more verbose config, legacy reputation.
- `poetry-core` — rejected: pulls in Poetry's worldview for a non-Poetry project.
- `flit` — rejected: less flexible for multi-file packages.

---

### ADR-001 — Use `uv` for dependency and Python version management

**Status:** Accepted
**Date:** 2026-09-13

**Context**
Python tooling needs a fast, reproducible way to install dependencies and pin
the interpreter. `pip` + `venv` + `pip-tools` is the traditional stack;
`poetry`, `pdm`, `hatch`, and `uv` are the modern alternatives.

**Decision**
Use `uv` for everything: interpreter installation (`uv python install`),
pinning (`uv python pin`), dependency resolution (`uv lock`), and sync
(`uv sync`). `uv.lock` is committed. CI and Docker both use `uv sync --frozen`.

**Consequences**
- Single tool covers what used to take three or four.
- Rust-based resolver: `uv sync` is typically 10–100× faster than `pip`.
- Lockfile is deterministic and portable.
- Team must know `uv`; not yet universal in older shops.

**Alternatives considered**
- `pip` + `requirements.txt` — rejected: no resolver guarantees, no lockfile standard.
- `poetry` — rejected: heavier, slower, opinionated beyond dependency management.
- `pdm` — rejected: viable but less tooling around interpreter management.

---

## Template

Copy this block for each new ADR.

### ADR-000N — <Short title>

**Status:** Proposed | Accepted | Superseded by ADR-XXXX
**Date:** YYYY-MM-DD

**Context**
What problem or constraint forced this decision? Two or three sentences.

**Decision**
What we chose. One paragraph.

**Consequences**
- What this enables.
- What this costs.
- What we're explicitly accepting as a tradeoff.

**Alternatives considered**
- Alternative A — rejected because …
- Alternative B — rejected because …
