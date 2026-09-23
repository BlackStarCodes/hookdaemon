# Roadmap

**Timeline:** 8 weeks part-time (~15–20 hrs/week).
**Deadline:** Week 8 = deployed and demoed.

**Current week:** 1 — API skeleton. Config, structured logging, request_id middleware, database engine, Alembic scaffolding, health endpoints, initial schema migration, and `/metrics` are all in place.



---

## Execution rule

If you slip behind at any weekly checkpoint, cut in this order:

1. Worker heartbeat
2. RFC 9457 error format → use FastAPI default
3. Cursor pagination → `?limit=` only
4. Structured log fields → plain JSON
5. Metrics depth → 3 metrics, not 7

**Never cut:** idempotency, retries, DLQ, HMAC, SSRF, tests, deploy.

---

## Week 0 — Environment (1–2 days)

**Goal:** Working VM, Docker, Remote-SSH, scaffolded repo pushed with hooks and CI.

- Ubuntu VM: 4 GB RAM, 2 vCPU, 40 GB disk.
- Install: `git`, `docker`, `docker compose`, `make`, `cloudflared`, `k6`, `uv`.
- `uv python pin 3.11` for the project interpreter.
- VS Code Remote-SSH from Windows.
- Repo scaffold: `pyproject.toml`, `uv.lock`, `README.md`, `ROADMAP.md`, `SPEC.md`, `DECISIONS.md`, `CHANGELOG.md`, `LICENSE`, `.gitignore`, `.editorconfig`, `.gitattributes`.
- Pre-commit hooks (ruff, mypy, detect-secrets, hygiene).
- FastAPI app with `/health/live` in a non-root Docker image.
- `Makefile` and GitHub Actions lint workflow.
- Dependabot for pip, actions, and docker.

**Done when:** `docker compose up` runs the API and `/health/live` responds from the Windows browser; CI is green on GitHub.

---

## Week 1 — API skeleton

**Goal:** FastAPI starts, migrations run, health endpoints respond.

- FastAPI app factory, `pydantic-settings` config.
- `structlog` JSON logging with `request_id` middleware.
- Postgres + SQLAlchemy async + `asyncpg` + Alembic.
- `/health/live`, `/health/ready`, `/metrics` (empty for now).
- Dockerfile + `docker-compose.yml` with API + Postgres.

**Done when:** app starts, migrations run, `/health/live` and `/health/ready` respond.

---

## Week 2 — Multi-tenant + endpoints + events

**Goal:** Tenant isolation works. Idempotency works.

- Tables: `tenants`, `api_keys` (hashed), `endpoints`, `events`, `idempotency_keys`.
- Auth: `Authorization: Bearer <api_key>` on all `/v1/*` routes.
- Endpoints: `POST/GET/PATCH/DELETE /v1/endpoints`.
- `POST /v1/events` with `Idempotency-Key` (unique per tenant).

**Done when:** same key twice returns the same event; missing auth returns 401.

---

## Week 3 — Deliveries + dispatcher + worker

**Goal:** End-to-end delivery works.

- Tables: `deliveries`, `delivery_attempts` (with lock columns and status enum).
- `POST /v1/events` writes event + delivery rows in **one transaction**, then best-effort `LPUSH`.
- Dispatcher process polls every 5s.
- Worker process claims via `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1`, sends HTTP POST via httpx, records attempt row.
- Integration test: run two workers concurrently, assert no duplicate delivery.


**Docs:** `docs/ARCHITECTURE.md`.

**Done when:** a mock endpoint receives a POST; killing Redis does not lose deliveries (dispatcher recovers them on next poll).

---

## Week 4 — Reliability

**Goal:** Retries, backoff, DLQ, replay all work.

- Retry on 5xx, timeout, connection error (not on 4xx except 408/429).
- Backoff: 10s, 30s, 2m, 10m, 30m, 1h, 6h + full jitter, 8 attempts max.
- `DEAD_LETTER` after max attempts.
- `POST /v1/deliveries/{id}/retry` requeues.
- Graceful shutdown on `SIGTERM`.
- Reaper process: returns stale `in_progress` deliveries to `pending` after 5 minutes. Adds `deliveries_reaped_total` metric.

**Done when:** 500→200 retries correctly; 8×500 → DLQ; manual retry works; `kill -TERM` does not lose in-flight work.

---

## Week 5 — Security

**Goal:** HMAC signing, SSRF protection, rate limiting all work.

- HMAC-SHA256 signing: `X-Webhook-ID`, `X-Webhook-Timestamp`, `X-Webhook-Signature`, `X-Webhook-Signature-Version: v1`.
- SSRF: DNS pinning, block RFC1918 / loopback / link-local / `169.254.169.254`, no redirects, 64 KB body cap.
- Rate limiting: per API key + per tenant, atomic Redis sliding window (Lua).
- Secrets encrypted at rest (Fernet).
- Retry-After respected on 429/503 (capped at `MAX_RETRY_AFTER_SECONDS`).



**Docs:** `docs/SECURITY.md`, `DECISIONS.md` (fill in real ADRs).

**Done when:** signature verifies with a sample receiver; private IP blocked; 101st request in 60s returns 429; DNS rebind test passes.

---

## Week 6 — Testing + CI

**Goal:** CI green, unit and integration tests running.

- `pytest` unit tests: backoff math, signature, SSRF check, state transitions.
- `pytest` integration: **DevDB** for ephemeral Postgres, docker-compose for Redis.
- Failure injection: 500→200, timeout, connection refused, duplicate key, max retries.
- `mypy --strict app/` in CI.
- GitHub Actions: `ruff` → `mypy` → unit → integration → docker build.
- Link FastAPI `/docs` (Swagger) from README.

**Docs:** `docs/TESTING.md`.

**Done when:** CI green on GitHub; unit and integration tests pass.

---

## Week 7 — Load test + observability

**Goal:** Real numbers in README, not promises.

- k6 against the **deployed** environment. Measure baseline throughput and latency.
- Identify the first bottleneck, fix it, re-measure. Document the before/after.
- Record: success rate, p50/p95/p99 latency, throughput, retry rate.
- Prometheus metrics: `events_ingested_total`, `deliveries_attempted_total`, `delivery_success_total`, `delivery_failure_total`, `retry_total`, `dlq_total`, `deliveries_reaped_total`, `delivery_latency_seconds`.
- README: architecture diagram, tradeoffs, real benchmark numbers.

**Docs:** `docs/OBSERVABILITY.md`, `docs/DEPLOYMENT.md`, `docs/OPERATIONS.md`.

**Done when:** README contains real numbers, not promises.

---

## Week 8 — Deploy + demo

**Goal:** A stranger can `curl` your live API.

- Deploy API + worker + dispatcher + Postgres + Redis on **Fly.io** (or Oracle Cloud free tier, or Hetzner CX22).
- HTTPS, public URL, daily Postgres backup.
- 2–3 minute demo video.
- Minimal receiver demo (`scripts/receiver.py`, ~50 lines) that verifies HMAC signatures — used in the demo video.
- Demo video walks through: register endpoint → send event → receiver verifies signature → inspect delivery history.

**Docs:** update `README.md` with real benchmark numbers.

**Done when:** a stranger can `curl` your live API and see a delivery succeed.

---

## Ongoing (after Week 8)

- Fix bugs from user feedback.
- Add items from SPEC §18 (Future Work) as priorities allow.
