API_URL ?= http://127.0.0.1:8010

.PHONY: up down logs test lint init-db migrate stamp-baseline scan reprocess reprocess-10k stats

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

init-db:
	docker compose exec api python -m services.api.app.init_db

migrate:
	docker compose exec api alembic upgrade head

stamp-baseline:
	docker compose exec api alembic stamp 0001_initial

scan:
	curl -X POST '$(API_URL)/scans?days=30'

reprocess:
	docker compose exec -T analyzer python scripts/reprocess_messages.py --max-messages 1000 --batch-size 250

reprocess-10k:
	docker compose exec -T analyzer python scripts/reprocess_messages.py --max-messages 10000 --batch-size 250

stats:
	curl '$(API_URL)/stats'

test:
	python -m pytest -q

lint:
	ruff check services shared tests scripts
