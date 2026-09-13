# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Repository scaffold: `pyproject.toml`, `uv.lock`, `README.md`, `ROADMAP.md`, `SPEC.md`, `DECISIONS.md`, `CHANGELOG.md`
- Initial architecture decision records (`DECISIONS.md`) covering uv, hatchling, and non-root container
- Pre-commit hooks: ruff, mypy, detect-secrets, hygiene checks
- Cross-editor consistency: `.editorconfig`, `.gitattributes`
- FastAPI app with `/health/live` liveness endpoint
- Non-root `Dockerfile` and `docker-compose.yml` with healthcheck
- `Makefile` with common developer commands
- GitHub Actions lint workflow
- Dependabot for pip, GitHub Actions, and Docker

[Unreleased]: https://github.com/BlackStarCodes/hookdaemon/commits/main
