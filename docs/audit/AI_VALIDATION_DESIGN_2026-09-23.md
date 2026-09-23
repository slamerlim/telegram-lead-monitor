# AI Independent Validation Design — 2026-09-23

## Goal

Autonomous multi-AI validation using **Cursor models only** (SDK orchestrator + in-chat Task subagents), deciding commercial-lead status under `config/scoring.yaml` objectives — fail-closed, blind, non-circular.

## Reuse

| Asset | Action |
|-------|--------|
| `label_review_samples` | Candidate membership |
| `label_reviews` | Extended for AI opinions (`validator_kind=ai`, `AI_*` labels) |
| Human blind HMAC | Pattern → v2 task tokens for AI queue |
| Human gates | Unchanged; exclude `validator_kind=ai` |
| Scorer | Unchanged thresholds; hidden from validators |

## Companion tables

- `ai_validation_attempts` — append-only provider (Cursor) call log
- `validation_consensus` — append-only consensus snapshots (gate re-derives)

## Independence

Distinct Cursor `model` + `model_family` across modes A/B/C. Three prompts on one model = fail closed.

## Commercial definition

Exactly the six objectives in `scoring.yaml` header. Positive `lead_type` ∈ commercial types matching `LeadScorer._COMMERCIAL_TYPES`.
