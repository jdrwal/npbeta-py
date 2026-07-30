# Container runtime: defaults to `docker compose`, override with podman locally, e.g.
#   make up COMPOSE="podman compose"
COMPOSE ?= docker compose

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

.PHONY: up
up: ## Start the dev stack (build if needed)
	$(COMPOSE) up --build

.PHONY: down
down: ## Stop the stack
	$(COMPOSE) down

.PHONY: logs
logs: ## Tail logs
	$(COMPOSE) logs -f

.PHONY: migrate
migrate: ## Apply DB migrations
	$(COMPOSE) run --rm web python manage.py migrate

.PHONY: makemigrations
makemigrations: ## Generate migrations
	$(COMPOSE) run --rm web python manage.py makemigrations

.PHONY: superuser
superuser: ## Create an admin user
	$(COMPOSE) run --rm web python manage.py createsuperuser

.PHONY: shell
shell: ## Django shell
	$(COMPOSE) run --rm web python manage.py shell

.PHONY: test
test: ## Run lint, type-check and tests
	$(COMPOSE) run --rm web ruff check .
	$(COMPOSE) run --rm web mypy .
	$(COMPOSE) run --rm web pytest

.PHONY: lint
lint: ## Run ruff
	$(COMPOSE) run --rm web ruff check .

.PHONY: fmt
fmt: ## Auto-format with ruff
	$(COMPOSE) run --rm web ruff check --fix .

.PHONY: pytest
pytest: ## Run pytest only
	$(COMPOSE) run --rm web pytest

TAILWIND_URL = https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-macos-arm64

bin/tailwindcss: ## Download the Tailwind standalone CLI (macOS arm64)
	mkdir -p bin && curl -sL -o bin/tailwindcss $(TAILWIND_URL) && chmod +x bin/tailwindcss

.PHONY: css
css: bin/tailwindcss ## Build the Tailwind CSS bundle
	./bin/tailwindcss -i static/src/input.css -o static/css/app.css --minify

.PHONY: css-watch
css-watch: bin/tailwindcss ## Rebuild CSS on template changes
	./bin/tailwindcss -i static/src/input.css -o static/css/app.css --watch
