# Contributing

## Development setup

```bash
git clone https://github.com/BlackStarCodes/hookdaemon
cd hookdaemon
uv sync
uv run pre-commit install
cp .env.example .env
docker compose up -d
make migrate
```

## Workflow
- Branch off `main`. Name branches `feat/<topic>`, `fix/<topic>`,
`docs/<topic>`, or `chore/<topic>`.
- Commit messages follow Conventional Commits. Types in use: `feat`,
`fix`, `docs`, `chore`, `build`, `ci`, `refactor`, `test`.
- One logical change per commit. Atomic, reviewable.
- Open a pull request. Wait for CI to pass before merging.

## Quality gates
Every commit runs pre-commit (ruff, ruff format, mypy, detect-secrets,
gitleaks, hygiene hooks). CI runs the same gates plus pytest on push
and pull request.

To run the full gate locally:

```bash
uv run pre-commit run --all-files
uv run pytest tests/unit -q
```

## Documentation
Documentation ships with the change:

- `README.md` — setup, configuration, or API changes.
- `CHANGELOG.md` — user-visible additions, changes, removals.
- `DECISIONS.md` — new architectural decisions.
- `SPEC.md` — changes to the design contract.
