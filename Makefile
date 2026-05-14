.PHONY: up down start stop logs build restart ps clean \
        up-prod down-prod start-prod stop-prod logs-prod build-prod \
        logs-backend restart-backend build-backend start-backend stop-backend up-backend \
        logs-frontend restart-frontend build-frontend start-frontend stop-frontend up-frontend \
        logs-db restart-db start-db stop-db \
        logs-redis restart-redis start-redis stop-redis \
        logs-celery restart-celery build-celery start-celery stop-celery \
        shell-backend shell-frontend db-clean

# Compose file combinations
DC_DEV = docker compose -f docker-compose.yml -f docker-compose.dev.yml
DC_PROD = docker compose -f docker-compose.yml -f docker-compose.prod.yml

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
# Per-Service Commands (dev)
# =============================================================================

# Backend
logs-backend:
	$(DC_DEV) logs -f backend

restart-backend:
	$(DC_DEV) restart backend

build-backend:
	$(DC_DEV) build backend

start-backend:
	$(DC_DEV) start backend

stop-backend:
	$(DC_DEV) stop backend

up-backend:
	$(DC_DEV) up --build -d backend

# Frontend
logs-frontend:
	$(DC_DEV) logs -f frontend

restart-frontend:
	$(DC_DEV) restart frontend

build-frontend:
	$(DC_DEV) build frontend

start-frontend:
	$(DC_DEV) start frontend

stop-frontend:
	$(DC_DEV) stop frontend

up-frontend:
	$(DC_DEV) up --build -d frontend

# Database
logs-db:
	$(DC_DEV) logs -f db

restart-db:
	$(DC_DEV) restart db

start-db:
	$(DC_DEV) start db

stop-db:
	$(DC_DEV) stop db

# Redis
logs-redis:
	$(DC_DEV) logs -f redis

restart-redis:
	$(DC_DEV) restart redis

start-redis:
	$(DC_DEV) start redis

stop-redis:
	$(DC_DEV) stop redis

# Celery
logs-celery:
	$(DC_DEV) logs -f celery-worker

restart-celery:
	$(DC_DEV) restart celery-worker

build-celery:
	$(DC_DEV) build celery-worker

start-celery:
	$(DC_DEV) start celery-worker

stop-celery:
	$(DC_DEV) stop celery-worker

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

db-clean:
	$(DC_DEV) exec backend python repository/clean_db.py postgresql://postgres:postgres@db:5432/korchess
