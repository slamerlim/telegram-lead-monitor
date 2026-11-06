# Native production on Ubuntu

Owner instruction, 6 November 2026: start a fresh database without Docker. The old Docker database/session were deleted after disk exhaustion. This supersedes the Compose deployment workflow. Existing backup files are preserved; the September 23 dump contains selected lead/validation tables, not the message archive, and is not a live dataset.

State lives at `~/.local/state/telegram-lead-monitor/`, outside the repository. PostgreSQL binaries from Ubuntu packages live at `~/.local/opt/tlm-postgresql/`. Services run as the existing Ubuntu user, with systemd user lingering enabled for reboot persistence. No sudo or Docker is needed on this host.

Install or redeploy after tests, commit, and push:

```bash
.venv/bin/python scripts/native_setup.py
curl -fsS http://127.0.0.1:8010/health
systemctl --user --no-pager status tlm-api tlm-dispatcher tlm-analyzer
```

The installer refuses to initialize a nonempty database directory and preserves the previous `.env` in the private state/config directory. Secrets remain in `.env` / `.env.validation`; never commit either. Application services use these files. PostgreSQL listens on loopback port 55432, Redis on 56379, and the API on 8010.

Authenticate the monitoring account interactively once:

```bash
.venv/bin/python scripts/telegram_login.py
```

Enter Telegram's login code and, if enabled, its 2FA password in the terminal. The code/password are not logged or saved. The session file is private. Successful login starts the collector and scheduler. Background workers never initiate another interactive login or repeatedly send login codes. No real collection is verified before this step succeeds and accessible communities have been verified and added.

For ordinary code updates:

```bash
.venv/bin/python -m alembic upgrade head
systemctl --user restart tlm-api tlm-analyzer tlm-collector tlm-scheduler tlm-dispatcher tlm-commercial-ai tlm-commercial-discovery
curl -fsS http://127.0.0.1:8010/health
curl -fsS http://127.0.0.1:8010/validation/gates
```

Service diagnostics:

```bash
systemctl --user list-units 'tlm-*'
journalctl --user -u tlm-collector -n 60 --no-pager
journalctl --user -u tlm-commercial-ai -n 60 --no-pager
redis-cli -p 56379 XINFO GROUPS telegram:messages
redis-cli -p 56379 XPENDING telegram:messages analyzer-workers
```

The collector scans history oldest-first in bounded pages and checkpoints only published pages. Initial scans are limited to two days. Existing cursors drain their backlog even after longer outages. Failed/incomplete pages are replayable; never reset cursors to recover history.

PostgreSQL's `dispatch_outbox` commits downstream jobs with message/lead/scan changes. `tlm-dispatcher` publishes them with retry/backoff. Publication is at-least-once: consumers must remain idempotent. Redis consumers reclaim abandoned jobs after 60 seconds, heartbeat active work, and defer rate-limited reviews without acknowledging them. Five actual handler failures preserve the payload in `<stream>:dead_letter`; inspect and explicitly replay after correcting the cause. Capacity deferrals do not consume this failure budget. Never blindly acknowledge pending events.

Disk controls pause ingestion instead of deleting data:

- Reserve at least 10 GiB free disk; pause at an 8 GiB application database budget.
- Pause intake at 384 MiB Redis memory. Redis has a 512 MiB ceiling with eviction disabled.
- The dispatcher trims only acknowledged transport copies, respecting every consumer group's pending/undelivered entries. PostgreSQL messages, labels, and cursors are retained.
- Redis AOF fsyncs each publication and rewrites at a 64 MiB minimum. PostgreSQL's WAL target is 512 MiB; this is not a hard disk cap.
- Systemd rate-limits service journal output to 100 lines per 30 seconds. Inspect host journal/disk growth as part of operations.

An intake pause requires inspecting the source mix and storage growth. Do not increase budgets blindly or wipe data. Backups should be added with an explicit size/retention budget and verified restore procedure; the present installer does not create an automatic backup schedule.

Collector freshness, successful AI review, and delivered owner alerts must be verified separately from `/health`, which only checks database and Redis connectivity. An empty healthy database is infrastructure readiness, not business success. Independent ML gates stay closed until their existing evidence requirements are satisfied.

Native integration tests use disposable PostgreSQL/Redis processes and synthetic fixtures on ephemeral ports. They never connect to the configured application database:

```bash
.venv/bin/python -m pytest -q tests/test_delivery_integration.py tests/test_collector_backlog.py tests/test_capacity.py
```
