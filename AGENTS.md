# Agent instructions — telegram-lead-monitor

## Always

1. End every substantive turn with a **Turn Report** (see `.cursor/rules/end-of-turn-report.mdc`).
2. On implement/ship/finish/`commit-and-push`: **commit → push → deploy → runtime verify** on this host (see `.cursor/rules/push-deploy-verify.mdc`).
3. Work on the Ubuntu production host Docker/Postgres; do not substitute a Cloud Agent VM for live data.

## Never

- `docker compose down -v`, truncate/wipe Telegram data, destroy collector cursors
- Commit secrets (`.env`, `.env.validation`, `docker-secrets/*`, keys)
- Loosen scorer thresholds to inflate volume
- Claim ML GO until independent AI validation gates honestly pass
- Overwrite `human_labels` from AI/validation paths
- Count Phase D / `agent_*` / `audit_*` as independent validation
- Integrate third-party LLM APIs for validators (Cursor models/subagents only)

## AI validation pointers

- Design: `docs/audit/AI_VALIDATION_DESIGN_2026-09-23.md`
- Roster: `config/ai_validators.yaml`
- Orchestrator: `python -m services.validator.app.orchestrator --backend fake|sdk`
- Gates: `GET /validation/gates`, metrics: `GET /metrics`
