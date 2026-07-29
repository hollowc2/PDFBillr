PYTHON ?= .venv/bin/python
FLASK = $(PYTHON) -m flask --app app
COMPOSE ?= docker compose

.DEFAULT_GOAL := help

.PHONY: help test lint format-check check db-bootstrap up

help: ## Show available commands.
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*?##/ {printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

test: ## Run the automated test suite.
	$(PYTHON) -m pytest

lint: ## Run Ruff lint checks.
	$(PYTHON) -m ruff check .

format-check: ## Check formatting without changing files.
	$(PYTHON) -m ruff format --check .

check: lint format-check test ## Run all local quality checks.

db-bootstrap: ## Apply database migrations (requires environment variables).
	$(FLASK) db-bootstrap

up: ## Build and run the local Docker stack (requires SECRET_KEY).
	@test -n "$(SECRET_KEY)" || (echo "Set SECRET_KEY before running make up."; exit 1)
	$(COMPOSE) up --build
