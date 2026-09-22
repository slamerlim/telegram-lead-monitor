# Minimal BLOCKERS Fix Plan

**Based on:** Production Readiness, Lead Quality, and ML Readiness Audit — 2026-09-22  
**Status:** Approved roadmap (owner 2026-09-22). Implementation proceeds in order below.  
**Principle:** precision → persistence → labels → ML

## Objective

Move from technically operable / commercially NOT READY / ML NOT READY to:

1. Lead rows represent genuine commercial opportunities with materially higher precision
2. LOW/non-lead decisions retained as training data
3. Duplicate/broadcast inflation removed
4. Community-specific scoring controls work
5. Real labeling dataset can accumulate
6. Only then supervised ML becomes justified

## Phase 0 — Preserve ingestion (guardrail)

Do not change: Telegram collection, incremental cursor, Redis topology, message uniqueness, analyzer ACK semantics, Postgres volumes, Telegram session, deploy topology.

Acceptance: 6/6 Up; Redis lag 0; no duplicate message/lead IDs; cursor active; ~7–8k msgs/day steady-state.

## BLOCKER 1 — Stop broadcast / non-buyer leads

Require external buyer-side commercial action. Hard vetoes: MARKETING_BROADCAST, JOB_VACANCY, SUPPORT_REQUEST, SERVICE_AD, JOB_SEEKER, NEWS_DIGEST, OFF_DOMAIN (unless genuine buyer intent survives).

Acceptance: Bitunix ×75 family produces 0 leads; exchange promos/vacancies/job-seekers/vendor ads excluded; precision over volume.

## BLOCKER 2 — Deduplicate opportunities

Opportunity identity: `(text_hash, community_id)`. Keep `leads.message_id` UNIQUE. Do not global-merge all identical texts.

Acceptance: duplicate `(text, community)` opportunities = 0 after controlled reprocess; report lead rows vs distinct units separately.

## BLOCKER 3 — Restore `community_profiles`

Populate EXCHANGE_OFFICIAL / JOB_BOARD / DEV_COMMUNITY / TRADER_COMMUNITY / OTHER. Priority: high-FP communities.

Acceptance: `_profile()` populated; weights applied; exchange/job ≠ buyer communities.

## BLOCKER 4 — Persist negatives

Dedicated scored-message/decision table retaining LOW/non-leads. Target ≥5,000 negatives before ML.

## BLOCKER 5 — Labeling workflow

TRUE_LEAD / FALSE_POSITIVE / AMBIGUOUS + FP taxonomy. Binary gate: ≥500 reviewed, ≥150 TRUE_LEAD. Multiclass separate.

## BLOCKER 6 — Continuing lead stream

After 1–5: continuous discovery with better precision, not lower thresholds. Owner daily volume target remains UNKNOWN until defined.

## Phases 7–9

Controlled re-score slices → measure precision (READY ≥70%) → ML gate M1–M10; no LightGBM/Optuna/SHAP until pass.

## Do not yet

ML deps, semantic enable, lower thresholds, train on current 256 rows, treat agent census as gold, uncontrolled 8.5M reprocess.
