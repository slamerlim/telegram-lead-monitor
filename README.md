# Telegram Lead Monitor

A self-hosted microservice system for discovering potential trading-bot / quantitative-development clients in Telegram communities.

## Architecture

- **api** — FastAPI REST API for communities, scans, leads, and exports.
- **collector** — Telethon worker. Reads Telegram message history from communities your account can access and publishes normalized message events to Redis Streams.
- **analyzer** — Consumes message events, scores intent/technical relevance, stores authors/messages/leads in PostgreSQL.
- **scheduler** — Periodically requests incremental scans through Redis Streams.
- **postgres** — Durable relational storage.
- **redis** — Service-to-service event bus and job queue.

The services are intentionally stateless except for the database and Telegram session volume.

## Telegram access model

The collector can only read communities that the authenticated Telegram account can legitimately access. For private groups/channels, the account must already be a member. It does not bypass Telegram privacy controls.

## Quick start

1. Copy `.env.example` to `.env`.
2. Create Telegram API credentials at https://my.telegram.org and put the API ID/hash plus your phone number into `.env`.
3. Start services:

```bash
docker compose up --build
```

4. Create the database schema:

```bash
docker compose exec api python -m app.init_db
```

5. Open Swagger UI:

`http://localhost:8000/docs`

6. Add communities through the API, then request a 30-day scan.

## First Telegram login

The first collector startup can require an interactive Telegram login. The container has a TTY-enabled session and persists the `.session` file in the `telegram_session` volume.

For a non-interactive production deployment, complete the initial login once, then keep the persisted session volume.

## Environment

See `.env.example` and `config/scoring.yaml`.

## API examples

Add a community:

```bash
curl -X POST http://localhost:8000/communities \\
  -H 'Content-Type: application/json' \\
  -d '{"telegram_ref":"@cryptojobslist","enabled":true}'
```

Request a 30-day scan:

```bash
curl -X POST 'http://localhost:8000/scans?days=30'
```

List high-value leads:

```bash
curl 'http://localhost:8000/leads?min_score=70&limit=50'
```

Export CSV:

```bash
curl -OJ 'http://localhost:8000/exports/leads.csv'
```

## Project layout

```text
telegram-lead-monitor/
├── config/
│   └── scoring.yaml
├── services/
│   ├── api/
│   ├── collector/
│   ├── analyzer/
│   └── scheduler/
├── shared/
├── scripts/
├── tests/
├── alembic/
├── docker-compose.yml
├── Dockerfile
├── Makefile
├── .env.example
├── pyproject.toml
└── README.md
```

## Search strategy

The default `config/scoring.yaml` includes the English and Russian bot/developer/API/trading/quant/DEX/copy-trading intent terms discussed for this project. Edit the YAML to add community-specific phrases without rebuilding the services.

The scheduler uses a small incremental window (default 2 days), while the API can request the full 30-day historical scan.

To enable semantic matching, install the optional `semantic` extra when building the image, for example with `pip install .[semantic]`. The baseline image keeps this disabled because the embedding stack is large.

## Production notes

- Use a real domain/reverse proxy and HTTPS for FastAPI.
- Do not commit `.env` or Telegram session files.
- Restrict the API with authentication before exposing it outside localhost/VPN.
- For large community sets, tune `COLLECTOR_CONCURRENCY`, Redis stream consumer counts, and PostgreSQL connection limits.
- Respect Telegram's terms, rate limits, and community rules.

## Seed communities

`config/communities.example.yaml` contains the candidate communities gathered for this project. It is intentionally a seed list, not an assertion that every entry is accessible to every Telegram account. Add communities through the API or `scripts/add_communities.py`.

## Typical workflow

```bash
cp .env.example .env
# edit .env with Telegram API credentials

docker compose up --build

docker compose exec api python -m services.api.app.init_db

python scripts/add_communities.py @cryptojobslist @laborxWeb3Jobs @BybitAPI @solana_dev_ru

curl -X POST 'http://localhost:8000/scans?days=30'
python scripts/print_high_value.py
```
