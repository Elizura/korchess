.PHONY: up down start stop logs build restart ps clean \
        up-prod down-prod start-prod stop-prod logs-prod build-prod \
        logs-backend logs-frontend logs-db logs-celery \
        shell-backend shell-frontend

# Compose file combinations
DC_DEV = docker compose -f docker-compose.yml -f docker-compose.dev.yml
DC_PROD = docker compose -f docker-compose.yml

# =============================================================================
# Dev Commands (default)
# =============================================================================

up:
	$(DC_DEV) up --build -d

down:
	$(DC_DEV) down

start:
	$(DC_DEV) start

stop:
	$(DC_DEV) stop

logs:
	$(DC_DEV) logs -f

build:
	$(DC_DEV) build

restart:
	$(DC_DEV) restart

# =============================================================================
# Prod Commands
# =============================================================================

up-prod:
	$(DC_PROD) up --build -d

down-prod:
	$(DC_PROD) down

start-prod:
	$(DC_PROD) start

stop-prod:
	$(DC_PROD) stop

logs-prod:
	$(DC_PROD) logs -f

build-prod:
	$(DC_PROD) build

# =============================================================================
# Per-Service Logs (dev)
# =============================================================================

logs-backend:
	$(DC_DEV) logs -f backend

logs-frontend:
	$(DC_DEV) logs -f frontend

logs-db:
	$(DC_DEV) logs -f db

logs-celery:
	$(DC_DEV) logs -f celery-worker

# =============================================================================
# Utilities
# =============================================================================

ps:
	$(DC_DEV) ps

clean:
	$(DC_DEV) down -v

shell-backend:
	$(DC_DEV) exec backend /bin/sh

shell-frontend:
	$(DC_DEV) exec frontend /bin/sh
