# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Configuration via `pydantic-settings` (`app/config.py`); all settings read
  from environment or `.env`, cached singleton
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

### Changed

- Uvicorn's plaintext access log disabled in `Makefile` and `Dockerfile`
  (`--no-access-log`); `RequestIDMiddleware` owns request logging
- `mypy` now checks `tests/` in addition to `app/`

### Security

- Sanitize client-supplied `X-Request-ID` to prevent log and header
  injection (CWE-117, CWE-113)
- Sensitive-key redaction runs before exception and stack renderers

[Unreleased]: https://github.com/BlackStarCodes/hookdaemon/commits/main
