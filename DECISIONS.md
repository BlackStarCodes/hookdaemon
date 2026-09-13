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
| 4 | Postgres as the source of truth; Redis as a wake-up signal | Proposed | Week 3 |

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
