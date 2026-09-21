.PHONY: help install dev up down logs migrate migrate-new test lint fmt typecheck pre-commit

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "%-12s %s\n", $$1, $$2}'

install:  ## Install Python dependencies
	uv sync

dev:  ## Run API locally with reload
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 --no-access-log

up:  ## Start stack in Docker
	docker compose up -d --build

down:  ## Stop stack
	docker compose down

logs:  ## Tail API logs
	docker compose logs -f api

migrate: ## Apply all pending Alembic migrations
	docker compose exec api alembic upgrade head

migrate-new: ## Autogenerate a new migration (usage: make migrate-new M="msg")
	docker compose exec api alembic revision --autogenerate -m "$(M)"

test:  ## Run tests
	uv run pytest

lint:  ## Run linter
	uv run ruff check .

fmt:  ## Format code
	uv run ruff format .

typecheck:  ## Run mypy
	uv run mypy app/ tests/

pre-commit: ## Run all pre-commit hooks on all files
	uv run pre-commit run --all-files
