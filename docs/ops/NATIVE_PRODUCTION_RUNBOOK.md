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

## Sales inbox and owner alerts

Open `http://127.0.0.1:8010/ops/inbox/` on this host (or through your existing SSH forwarding). Show the owner credentials in your interactive terminal with `.venv/bin/python scripts/inbox_access.py`. The command refuses to print a token into captured output. Credentials are held only in browser memory; disconnect clears them. All lead data and CRM actions require the operator token.

`OWNER_TELEGRAM_TARGET=me` sends to the monitoring account's Saved Messages. `OWNER_NOTIFICATIONS_ENABLED=true` enables owner delivery after successful commercial review. No prospect messages are sent. The collector owns the Telegram session and delivers owner alerts through the same client. Notifications persist with the review/draft transaction; retries reuse a stored Telegram random ID. Exact whitespace-normalized reposts across communities receive one alert and appear under Duplicates. This is not semantic/paraphrase deduplication.

Only confirmed leads at most `HOT_LEAD_MAX_AGE_HOURS=48` hours old qualify for owner alerts. Delivery rechecks age, workflow status, and owner rejection. Older opportunities remain in the inbox. Contacts are labelled by evidence; quoted usernames are not assumed to be the buyer. Drafts are editable and copied for manual sending.

Owner acceptance/rejection and proposals are `lead_events`, never `human_labels`. The pilot starts at the first successful owner notification and measures its first seven days. Acceptance is accepted / (accepted + rejected), with delivered and reviewed counts exposed separately. The quality target requires at least 20 delivered leads, feedback on all of them, and at least 80% acceptance after seven days. Replies/wins require operator-recorded evidence. Alert latency runs from Telegram posting to successful owner delivery. Source attribution uses the canonical delivered opportunity; duplicates do not inflate the delivery denominator.

## Cursor SDK access check

On 10 November, all three configured SDK model probes were rejected with `plan_required`: the configured account did not have Cloud Agent plan access. This occurs before model execution. Model availability and successful commercial adjudication remain unverified. Enable the required access or privately update `CURSOR_API_KEY` in `.env.validation` to an eligible account, then run:

```bash
.venv/bin/python scripts/sdk_preflight.py
systemctl --user restart tlm-commercial-ai
```

The preflight uses an empty temporary workspace and a synthetic JSON connectivity prompt. It writes only a private SDK health snapshot, not leads or validation records. It prints status categories, never credentials or raw SDK errors. A changed key invalidates the snapshot. The inbox shows the last check for up to 24 hours. Runtime provider failures defer reviews with five-minute global backoff; rate-limit/provider deferrals do not consume the poison-message budget. Check `/health`, authenticated inbox health, and actual reviewed/delivered counts separately.

The Cursor credential exposed by the earlier test failure should be rotated. Settings representations now redact secrets, and tests disable production dotenv loading.

## Remaining live acceptance work

Do not reimport the entire old source list. After login, verify 5–10 accessible communities and inspect recent buyer requests/contact routes. Start with a bounded two-day scan and measure source yield. Raw messages, including LOW/unscored messages, need a recent retrieval audit. Discovery episodes still have no production CRM bridge; shadow evaluation is disabled until real contextual examples and buyer attribution can be tested. Neither an empty healthy service nor synthetic test success proves commercial recall, precision, or revenue.
