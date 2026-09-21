# Telegram Lead Monitor

Self-hosted FastAPI microservice system for discovering potential customers for trading-bot, algorithmic-trading, exchange-API, execution, arbitrage, copy-trading, Solana/DEX and quantitative-development services.

## Architecture

- **api** — FastAPI REST API for communities, scans, leads, lead-status updates and exports.
- **collector** — Telethon worker. Reads message history from communities your Telegram account can legitimately access and publishes normalized message events to Redis Streams.
- **analyzer** — Consumes message events, classifies buyer/provider/job-seeker intent, detects technical problems, extracts contacts/budgets and stores scored leads in PostgreSQL.
- **scheduler** — Periodically requests incremental scans.
- **postgres** — Durable application database.
- **redis** — Service-to-service event bus and job queue.

The services are stateless except for PostgreSQL/Redis data and the persistent Telegram session volume.

## Lead model

The analyzer is optimized for commercial prospecting rather than raw keyword counts. It distinguishes:

- `CLIENT` — likely buyer asking for custom work or help.
- `SERVICE_PROVIDER` — another developer/freelancer advertising services.
- `JOB_SEEKER` — person advertising themselves for work.
- `RECRUITER` — organization/person posting a vacancy.
- `UNKNOWN` — insufficient evidence.

Lead types include `BOT_REPAIR`, `EXECUTION_PROBLEM`, `TRADING_SYSTEM_BUILD`, `ARBITRAGE_PROJECT`, `COPY_TRADING_PROJECT`, `SOLANA_DEX_PROJECT`, `QUANT_ML_PROJECT`, `JOB_VACANCY`, `SERVICE_ADVERTISEMENT`, `JOB_SEEKER`, `TECHNICAL_QUESTION` and `NOISE`.

## Port

FastAPI uses **8010** by default. Change `API_PORT` in `.env` if necessary.

The default Compose mapping binds FastAPI to `127.0.0.1:8010`, so it is not exposed directly to the public Internet. Use an SSH tunnel or a reverse proxy for remote/browser access.

## Telegram access

The collector can only read communities that the authenticated Telegram account can legitimately access. For private groups/channels, the account must already be a member. It does not bypass Telegram privacy controls.

The production Telegram DC is selected by Telegram/Telethon. Do not hard-code a production DC IP into the collector unless you deliberately need a specialized connection setup.

## Secrets

Never commit `.env`, Telethon `*.session` files, SSH private keys, tokens or passwords.

Recommended production setup:

1. Keep `.env` on the Ubuntu host with `chmod 600`.
2. Keep the Telethon session in the persistent `telegram_session` Docker volume.
3. Use a dedicated Telegram monitoring account rather than a personal account.
4. Keep PostgreSQL and Redis unexposed to the Internet.
5. Put FastAPI behind HTTPS and authentication before public exposure.

## First deployment

```bash
cp .env.example .env
chmod 600 .env
# edit .env

docker compose up -d --build postgres redis api
```

For a brand-new database:

```bash
docker compose exec api alembic upgrade head
```

For the original v1 database created with `init_db.py`, mark the existing schema as the baseline and then apply the new migration:

```bash
docker compose exec api alembic stamp 0001_initial
docker compose exec api alembic upgrade head
```

## First Telegram login

```bash
docker compose run --rm collector
```

Complete the interactive Telegram login. The session is persisted in the Docker `telegram_session` volume.

Then start the complete stack:

```bash
docker compose up -d
```

## Add communities

The helper script defaults to `http://127.0.0.1:8010`. Override with `TLM_API_URL` when necessary.

```bash
python scripts/add_communities.py \
  @cryptojobslist \
  @laborxWeb3Jobs \
  @laborx \
  @cryptotradebots \
  @Community_3Commas \
  @BybitAPI \
  @solana_dev_ru \
  @solanadev \
  @solana_jobs \
  @workingincrypto \
  @cryptoheadhunter \
  @careers_crypto \
  @web3hiring \
  @remoteweb3jobs
```

## Initial scan

Run the first 30-day scan:

```bash
curl -X POST 'http://127.0.0.1:8010/scans?days=30'
```

Monitor:

```bash
docker compose logs -f collector
docker compose logs -f analyzer
```

## Reprocess existing data

After changing `config/scoring.yaml`, do not rescan Telegram unnecessarily. Re-score already stored messages:

```bash
docker compose exec api python scripts/reprocess_messages.py
```

Only recent messages:

```bash
docker compose exec api python scripts/reprocess_messages.py --since-days 30
```

## Useful API calls

Health:

```bash
curl http://127.0.0.1:8010/health
```

Communities:

```bash
curl http://127.0.0.1:8010/communities
```

High-value likely-client leads:

```bash
curl 'http://127.0.0.1:8010/leads?min_score=70&buyer_type=CLIENT&limit=100'
```

Bot repair leads:

```bash
curl 'http://127.0.0.1:8010/leads?lead_type=BOT_REPAIR&min_score=50&limit=100'
```

New leads:

```bash
curl 'http://127.0.0.1:8010/leads?status=NEW&min_score=50&limit=100'
```

Update a lead status:

```bash
curl -X PATCH \
  'http://127.0.0.1:8010/leads/123/status?status=CONTACTED'
```

Stats:

```bash
curl http://127.0.0.1:8010/stats
```

Exports:

```bash
curl -OJ 'http://127.0.0.1:8010/exports/leads.csv'
curl -OJ 'http://127.0.0.1:8010/exports/leads.xlsx'
```

## Existing v1 database migration

If PostgreSQL already contains the original project schema and the original `init_db.py` was used, do not run `alembic upgrade head` immediately. First stamp the existing schema as `0001_initial`:

```bash
docker compose exec api alembic stamp 0001_initial
docker compose exec api alembic upgrade head
```

This applies `0002_lead_intent` without dropping the existing 2,558 messages.

## Production workflow

Update code:

```bash
git pull
docker compose up -d --build
```

Check services:

```bash
docker compose ps
```

Check resource usage:

```bash
docker stats --no-stream
```

View logs:

```bash
docker compose logs --tail=200
```
