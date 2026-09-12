.PHONY: up down logs test lint init-db scan

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

init-db:
	docker compose exec api python -m services.api.app.init_db

scan:
	curl -X POST 'http://localhost:8000/scans?days=30'

test:
	python -m pytest -q

lint:
	ruff check services shared tests
