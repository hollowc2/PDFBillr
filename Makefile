PYTHON ?= .venv/bin/python
FLASK = $(PYTHON) -m flask --app app
COMPOSE ?= docker compose

# Tailwind standalone CLI, pinned and checksum-verified (no Node required).
TAILWIND_VERSION := v3.4.17
TAILWIND := bin/tailwindcss
TAILWIND_OS := $(shell uname -s | sed 's/Darwin/macos/;s/Linux/linux/')
TAILWIND_ARCH := $(shell uname -m | sed 's/x86_64/x64/;s/aarch64/arm64/')
TAILWIND_ASSET := tailwindcss-$(TAILWIND_OS)-$(TAILWIND_ARCH)
TAILWIND_SHA256_linux-x64 := 7d24f7fa191d2193b78cd5f5a42a6093e14409521908529f42d80b11fde1f1d4
TAILWIND_SHA256_linux-arm64 := 69b1378b8133192d7d2feb12a116fa12d035594f58db3eff215879e4ad8cf39b
TAILWIND_SHA256_macos-x64 := 6cbdad74be776c087ffa5e9a057512c54898f9fe8828d3362212dfe32fc933a3
TAILWIND_SHA256_macos-arm64 := a1d0c7985759accca0bf12e51ac1dcbf0f6cf2fffb62e6e0f62d091c477a10a3
CSS_SRC := static/css/src.css
CSS_OUT := static/css/app.css

.DEFAULT_GOAL := help

.PHONY: help test lint format-check check db-bootstrap up tailwind css css-check sample-invoice

help: ## Show available commands.
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*?##/ {printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

test: ## Run the automated test suite.
	$(PYTHON) -m pytest

lint: ## Run Ruff lint checks.
	$(PYTHON) -m ruff check .

format-check: ## Check formatting without changing files.
	$(PYTHON) -m ruff format --check .

check: lint format-check css-check test ## Run all local quality checks.

db-bootstrap: ## Apply database migrations (requires environment variables).
	$(FLASK) db-bootstrap

up: ## Build and run the local Docker stack (requires SECRET_KEY).
	@test -n "$(SECRET_KEY)" || (echo "Set SECRET_KEY before running make up."; exit 1)
	$(COMPOSE) up --build

tailwind: $(TAILWIND) ## Download the pinned Tailwind CLI into bin/.

$(TAILWIND):
	@mkdir -p bin
	curl -fsSL -o $@.tmp https://github.com/tailwindlabs/tailwindcss/releases/download/$(TAILWIND_VERSION)/$(TAILWIND_ASSET)
	@echo "$(TAILWIND_SHA256_$(TAILWIND_OS)-$(TAILWIND_ARCH))  $@.tmp" | shasum -a 256 -c -
	@chmod +x $@.tmp && mv $@.tmp $@

css: $(TAILWIND) ## Rebuild static/css/app.css from templates and static/js.
	$(TAILWIND) -c tailwind.config.js -i $(CSS_SRC) -o $(CSS_OUT) --minify

css-check: css ## Fail if the committed app.css is out of date.
	@git diff --exit-code --stat -- $(CSS_OUT) || (echo "static/css/app.css is stale: run make css and commit it."; exit 1)

sample-invoice: ## Re-render static/img/sample-invoice.png from the real PDF template (needs pdftoppm).
	$(PYTHON) scripts/render_sample_invoice.py
