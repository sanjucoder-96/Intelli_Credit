# ------------------------------------------------------------------------------
# IntelliCredit — developer entrypoints.
#
# NOTE (Windows): GNU Make is not bundled with Windows. Run these targets from
# Git Bash or WSL (both understand `make`), or copy the underlying commands
# into PowerShell by hand.
# ------------------------------------------------------------------------------

COMPOSE ?= docker compose

.PHONY: up down logs ps migrate test lint fmt typecheck demo-seed

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=200

ps:
	$(COMPOSE) ps

migrate:
	$(COMPOSE) exec api alembic upgrade head

test:
	cd backend && pytest -m "not integration"

test-integration:
	cd backend && IC_INTEGRATION_TESTS=1 pytest -m integration

seed:
	$(COMPOSE) exec api python -m app.seeds.demo

lint:
	cd backend && ruff check app tests && ruff format --check app tests && lint-imports

fmt:
	cd backend && ruff format app tests && ruff check --fix app tests

typecheck:
	cd backend && mypy app

demo-seed:
	@echo "demo seed ships in Phase 9 (ROADMAP.md)"
	@exit 1
