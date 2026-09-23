# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Structured logging: JSON for production and CI, colored console for local
  development (`LOG_JSON=false`)
- Recursive, case- and dash-insensitive redaction of sensitive keys
  (passwords, tokens, API keys, auth headers) before log rendering
- `RequestIDMiddleware`: pure ASGI middleware assigns or honors
  `X-Request-ID`, echoes it on every response including 500s
- Structured per-request access log; skips `/health/live`, `/health/ready`,
  and `/metrics`
- Global exception handler returns JSON 500 with the request id echoed
- Shared test helpers module (`tests/helpers.py`)
- Postgres integration: async SQLAlchemy engine (`asyncpg`), session factory,
  and `get_session` FastAPI dependency
- Alembic configured to read the database URL from application Settings;
  migration files and config are included in the deployed image
- `/health/live` and `/health/ready` endpoints. Readiness returns `503`
  when Postgres is unreachable or migrations have not been applied, and
  distinguishes timeout, missing-table, transport, and empty-version
  failures
- Application settings via `pydantic-settings`, including `APP_NAME`,
  `APP_VERSION`, `ENVIRONMENT`, `LOG_LEVEL`, `LOG_JSON`, `DATABASE_URL`,
  `REDIS_URL`, and the DB pool tuning variables `DB_POOL_SIZE`,
  `DB_MAX_OVERFLOW`, `DB_POOL_PRE_PING`, `DB_POOL_RECYCLE`
- `make migrate` and `make migrate-new` targets
- Gitleaks pre-commit hook for pattern-based secret detection; allowlist
  covers placeholder credentials used in dev defaults
- `check-ast` pre-commit hook for Python syntax validation
- `--strict-markers` and `--strict-config` for pytest; explicit
  `asyncio_default_fixture_loop_scope`
- `[tool.uv] required-version` pin to prevent lockfile drift
- Declarative base (`app/models/base.py`) with a shared `TimestampMixin`
  providing database-set `created_at` and `updated_at`
- Alembic autogenerate wiring (`target_metadata = Base.metadata`) and the
  initial migration; `/health/ready` returns `200` once it is applied
- `/metrics` endpoint exposing Prometheus metrics in text exposition
  format (Python process and GC metrics from the default registry)

### Changed

- Uvicorn's plaintext access log disabled (`--no-access-log` in Makefile;
  `access_log=False` in the `python -m app` entrypoint); `RequestIDMiddleware`
  owns request logging
- `python -m app` runs uvicorn with `log_config=None`; `setup_logging`
  clears uvicorn's handlers so its records propagate to the structlog
  pipeline and render as JSON
- `mypy` now checks `tests/` in addition to `app/`
- `docker-compose.yml` adds a Postgres 17 service with a `pg_isready`
  healthcheck; the API waits for `service_healthy` before starting
- Postgres is not published to the host; containers reach it over the
  compose bridge by service name
- GitHub Actions uses an immutable `setup-uv` tag; the installed uv binary
  is pinned to 0.12.5 to match local development
- `make migrate` and `make migrate-new` now run inside the api container so
  Postgres (not published to the host, per ADR-008) is reachable

### Security

- Sanitize client-supplied `X-Request-ID` to prevent log and header
  injection (CWE-117, CWE-113)
- Sensitive-key redaction runs before exception and stack renderers
- Database credentials live only in `.env`; never in `alembic.ini` or the
  repository


[Unreleased]: https://github.com/BlackStarCodes/hookdaemon/commits/main
