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
