# AI Independent Validation Design — 2026-09-23

## Goal

**Cursor SDK-mediated multi-model validation** (SDK orchestrator + in-chat Task subagents): orchestration runs through the Cursor SDK; distinct model families (e.g. Anthropic / OpenAI / xAI) execute via Cursor; this application does **not** call Anthropic/OpenAI/xAI HTTP APIs directly. Decides commercial-lead status under `config/scoring.yaml` objectives — fail-closed, blind, non-circular.

## Reuse

| Asset | Action |
|-------|--------|
| `label_review_samples` | Candidate membership |
| `label_reviews` | Extended for AI opinions (`validator_kind=ai`, `AI_*` labels) |
| Human blind HMAC | Pattern → v2 task tokens for AI queue |
| Human gates | Unchanged; exclude `validator_kind=ai` |
| Scorer | Unchanged thresholds; hidden from validators |

## Companion tables

- `ai_validation_attempts` — append-only Cursor-SDK call log (provider field=`cursor`, plus model/family)
- `validation_consensus` — append-only consensus snapshots (gate re-derives)

## Independence

Distinct `model` + `model_family` across modes A/B/C (recorded on each attempt). Three prompts on one model = fail closed. `provider` is always `cursor` (SDK mediation).

## Commercial definition

Exactly the six objectives in `scoring.yaml` header. Positive `lead_type` ∈ commercial types matching `LeadScorer._COMMERCIAL_TYPES`.

AI consensus is multi-model evidence — **not** human ground truth, sales qualification, or ML GO.
