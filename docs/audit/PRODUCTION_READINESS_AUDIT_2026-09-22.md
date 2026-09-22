# Production Readiness, Lead Quality, and ML Readiness Audit

**Date:** 2026-09-22  
**Host:** Ubuntu production `/home/slamer-lim/telegram-lead-monitor`  
**Git:** `main` @ `91ef77b` (PR #2 merged)  
**Mode:** Read-only measurement (amendments A–I applied). No code/YAML/DB/Redis mutations.  
**Evidence root:** `docs/audit/evidence/`

**Label taxonomy for claims:** `VERIFIED` = measured this run · `HISTORICAL` = prior session · `UNKNOWN` = cannot measure with current instrumentation.

**Human-audit limitation:** Labels on all 157 `(text_hash, community_id)` units were produced by the coding agent applying the documented business objective rules (not external annotators). Treat as structured expert review, not independent human ground truth.

---

## Executive conclusion

| Question | Verdict |
|----------|---------|
| Deterministic pipeline **technically operable**? | **READY WITH CONDITIONS** — services healthy, Redis lag 0, UNIQUE constraints intact, incremental collector active, remote-verify previously green |
| CRM **commercially useful** for selling trading/quant engineering services? | **NOT READY** — lead table is dominated by false positives |
| Dataset ready for supervised ML? | **NOT READY** |
| LightGBM + Optuna + SHAP specifically? | **NOT READY** |
| Other ML families now? | **NOT READY** (same label/noise blockers) |

### Funnel (VERIFIED this run)

```text
8,511,557 messages
        ↓  (~0.003% become lead rows)
256 lead rows  (HIGH 196 / MEDIUM 60)
        ↓  dedup
144 distinct texts · 157 (text, community) units · 94 distinct authors
        ↓  agent business-rule census
6 human-confirmed target units (7 rows)  ≈ 2.7% of lead rows
        ↓  recent / contactable
≈ 0.2 actionable confirmed opportunities / day
        ↓
quality sufficient for ML?  NO
```

### Main strengths
- Production stack is up; Postgres/Redis healthy; volumes intact.
- DB uniqueness on messages and leads is real and currently clean (0 duplicate `message_id`).
- Incremental collector + success-only Redis ACK are in place on `main`.
- Bounded remote-verify tooling exists and previously passed production gates (`HISTORICAL` earlier same day).

### Main blockers (commercial)
1. **~92%+ of lead rows are false positives** under business rules (exchange promo, job boards, vendor outreach, commentary).
2. **Broadcast inflation:** one Bitunix promo occupies **75 identical lead rows** (score 100 / HIGH / CLIENT / `TRADING_SYSTEM_CONTRACT`).
3. **`community_profiles` absent** from live `config/scoring.yaml` → community dampening never fires.
4. **Zero human CRM labels** (`status=NEW` for all 256); LOW negatives are deleted (no persisted negative class).

---

## Current system facts

| Metric | Value | Tag | Source |
|--------|-------|-----|--------|
| Messages | 8,511,557 | VERIFIED | `evidence/02-volumes-dedup-growth.txt`, `/stats` |
| Leads | 256 | VERIFIED | same |
| Communities / enabled | 136 / 136 | VERIFIED | same |
| Authors | 187,871 | VERIFIED | same |
| Services | 6/6 Up; restart counts 0 this boot | VERIFIED | `01-platform-health.txt` |
| Redis analyzer pending/lag | 0 / 0 | VERIFIED | `01-platform-health.txt` |
| Redis collector pending/lag | 0 / 0 | VERIFIED | same |
| Disk | 115G, ~33% used, ~74G free | VERIFIED | host `df` |
| Volumes | postgres_data, redis_data, telegram_session present | VERIFIED | `docker volume ls` |
| UNIQUE messages | `uq_message_community_message` | VERIFIED | `02-…` |
| UNIQUE leads | `leads_message_id_key` | VERIFIED | same |
| Dup lead `message_id` | 0 | VERIFIED | same |
| Scan runs 24h | 680 scans; seen 7,823; published 7,393 | VERIFIED | `02-…` |
| Analyzer throughput | ~1.6–21 msg/s (session-average drifts as idle periods grow) | VERIFIED | `08-throughput-logs.txt` |
| 24h error grep | api/collector/analyzer/scheduler ≈ 0 | VERIFIED | `01-…` |
| Prior remote-verify | `HOST_CLASS=production_like`, `DUP_CHECK_*=0`, `PRODUCTION_VERIFY_OK` | HISTORICAL | earlier session logs |

### Ingestion / capacity (`created_at`, amendment A)

| Window | Messages inserted (`created_at`) | Interpretation |
|--------|----------------------------------|----------------|
| 2026-09-14 | 1,668,727 | Bulk backfill |
| 2026-09-15 | 6,831,379 | Bulk backfill |
| 2026-09-16 | 1,499 | Quiet day |
| 2026-09-22 (partial) | ~7,904 | Steady-state-ish incremental |
| Last 24h published (scan_runs) | 7,393 | Steady-state collector output |

**VERIFIED:** Naïve `msgs_per_day_30d ≈ 283k` is **misleading** because nearly the entire corpus was inserted on Sep 14–15.  
**Steady-state ingestion ≈ 7–8k messages/day** after backfill (VERIFIED Sep 22 + scan_runs 24h).  
`message_date` spans 2025-09-19 → 2026-09-22 (source content age; diagnostic only).

Near-empty texts (`length < 20`): **2,760,955 / 8,511,557 ≈ 32%** (VERIFIED) — mostly noise for scoring.

---

## Business relevance

### Identity metrics (amendment B/F) — VERIFIED

| Term | Count |
|------|------:|
| Lead rows | 256 |
| Distinct text hashes | 144 |
| Distinct `(text, community)` | 157 |
| Distinct authors | 94 |
| Distinct `(author, text)` | 163 |
| Human-confirmed target units | **6** |
| Human-confirmed target rows | **7** |
| Ambiguous units | 13 (14 rows) |
| False-positive units | 138 (235 rows) |

**256 database rows ≠ 256 sales opportunities.**

### System-reported taxonomy (not business truth)

- Tier: HIGH 196, MEDIUM 60 (LOW not persisted)
- All 256 `lead_type` values are in the commercial taxonomy list
- `buyer_type`: CLIENT 216, RECRUITER 40
- `status`: **all NEW** → no CRM workflow usage

### Agent census precision (157/157 units reviewed)

| Metric | Value |
|--------|------:|
| Row-weighted target rate | **7/256 = 2.7%** |
| Unit-weighted target rate | **6/157 = 3.8%** |
| Row-weighted FP rate | **235/256 = 91.8%** |
| Ambiguous share (rows) | 14/256 = 5.5% |

### Confirmed TRUE_LEAD examples (VERIFIED review)

1. LaborX freelance: fix Polymarket trading bot ($1500 / $500) → `BOT_REPAIR`
2. LaborX freelance: Python crypto trading bot + GUI ($800 / $200) → `TRADING_SYSTEM_CONTRACT`
3. LaborX freelance: real-time crypto trading dashboard ($3000) → `TRADING_SYSTEM_CONTRACT`
4. `@solana_dev_ru`: buyer needs Solana copy-trading bot (0–1 block) → `COPY_TRADING_PROJECT`

### Dominant false-positive classes (rows driven)

| Class | Approx unit count | Example |
|-------|------------------:|---------|
| Exchange channel / no buyer | 42 | Bitunix / BloFin / Bybit product posts |
| Job board / vacancy | 34 | Ethena hiring, JobStash employment |
| No commercial buyer evidence | 18 | Weak keyword hits |
| Job seeker / vendor | 10+ | “Ищу задачи…”, cold outreach |
| Exchange product promo | 5+ | Futures Grid PnL Challenge (**×75 rows**) |
| Market commentary | 3 | MEXC/Bitget editorial |

Detail: `evidence/09-human-audit-summary.txt`, `09-human-audit-labels.tsv`.

### Actionable leads/day (amendment C)

Proxy: confirmed target units with ingest in last 30d / 30 ≈ **0.2/day**.  
**Gate: UNKNOWN** — no owner-defined threshold (e.g. ≥X qualified prospects/day).

All lead rows were created on Sep 14–15 (bulk rescore/ingest window); there is **no ongoing drip of new lead rows** in the last week (VERIFIED `07-recent-leads.txt`).

---

## English vs Russian (amendment D)

### Script heuristic (not language ID)

**Corpus messages (VERIFIED `05-script-heuristic.txt`):**

| Script class | Messages | Share |
|--------------|---------:|------:|
| LATIN_SCRIPT | 8,385,495 | 98.5% |
| OTHER | 98,422 | 1.2% |
| MIXED | 14,924 | 0.18% |
| CYRILLIC_SCRIPT | 12,716 | 0.15% |

**Leads:** LATIN 223 rows / MIXED 33 / pure CYRILLIC among leads ≈ 0 in this heuristic split (RU often lands in MIXED).

Do **not** call LATIN_SCRIPT “English” or CYRILLIC “Russian” without calibration.

### Quality by script (census)

| | LATIN units | MIXED units |
|--|------------:|------------:|
| TRUE_LEAD | 5 | 1 |
| FALSE_POSITIVE | 113 | 25 |
| AMBIGUOUS | 10 | 3 |

Lead-share vs corpus-share ratio is a **diagnostic only**: LATIN dominates both corpus and leads; that does **not** prove LATIN classification quality. Primary evidence is the census above.

RU-relevant TRUE example exists (`@solana_dev_ru` copy-trading RFQ). YAML asymmetry (EN-heavy patterns, `job_aggregator` EN-only) remains a **suspected** RU weakness (`HISTORICAL` code research); corpus has very little pure Cyrillic, so RU precision/recall is **under-powered**.

---

## Collection quality

| Topic | Finding | Tag |
|-------|---------|-----|
| Incremental cursor | `min_id=last_message_id`; update after successful iteration | VERIFIED (code) |
| Communities with cursor | 126/136 | VERIFIED |
| Steady-state new msgs | ~7.4k published / 24h | VERIFIED |
| Zero-message scans | Common in live collector logs (cursor caught up) | VERIFIED |
| Dedup layers | Telegram cursor + analyzer upsert + UNIQUE | VERIFIED |
| Mid-scan timeout republish | Possible; DB-idempotent | VERIFIED (code) |
| Edited old messages | Not re-fetched (trade-off) | VERIFIED (code) |
| Historical 99.85% Bitget replay | Not re-run | HISTORICAL |

---

## Scoring quality

| Topic | Finding | Tag |
|-------|---------|-----|
| Commercial gate + vetoes | Present in `scoring.py` | VERIFIED |
| Duplicate veto helpers | Fixed in active file (only in backups) | VERIFIED |
| Semantic layer | Disabled | VERIFIED |
| `community_profiles` | **Absent** from live YAML → `_profile()` always `{}` | VERIFIED |
| System marks all 256 as “commercial types” | True, but **business FP rate ~92%** | VERIFIED |
| Precision | ~2.7% row-weighted target (agent census) | VERIFIED (with label limitation) |
| Dominant bug | Exchange/job content scored as CLIENT commercial contracts | VERIFIED |

---

## Performance

| Area | Evidence | Severity |
|------|----------|----------|
| Analyzer | ~2–20 msg/s logged; per-message N+1 queries | IMPORTANT (not blocking ops at current lag 0) |
| Collector | Steady ~7k/day; many 0/0 scans when caught up | OK |
| Reprocess | Prior bounded 1k≈2s, 10k≈27s | HISTORICAL |
| DB size / disk | ~7GB volumes; 74G free | OK |
| Full-corpus regex FN search | Too slow on 8.5M rows without `message_date` bound | Observability gap |

---

## Reliability

| Area | Status |
|------|--------|
| Service health | READY |
| Redis backlog | READY (pending 0) |
| Data integrity (UNIQUE) | READY |
| Scheduler | READY now (restarts=0); prior OOM 137 is HISTORICAL |
| Security/secrets | READY WITH CONDITIONS (`.env` on host; not printed) |
| Observability | READY WITH CONDITIONS (structlog + `/stats`; no Prometheus/lag API) |
| Test coverage | READY WITH CONDITIONS (scoring unit tests only; EN-centric) |

---

## ML readiness (amendment E / I)

### 1) Deterministic rules for production **use of output**
**NOT READY** for commercial CRM decisions until FP classes are fixed (see blockers).  
**READY WITH CONDITIONS** as a data-collection + scoring **engine** (ops healthy).

### 2) Supervised ML dataset readiness
**NOT READY**

| Criterion | Evidence |
|-----------|----------|
| Human labels | 0 CRM `status` labels; agent census is not gold training data |
| Positives | Only ~6 confirmed target units in entire lead table |
| Negatives | LOW deleted; must reconstruct from `messages` LEFT JOIN |
| Noise | Training on current `lead_type`/`score` = learning the broken heuristic |
| Leakage | Subscores/matched_* are functions of the same rules |

### 3a) Binary commercial ML (commercial vs not)
**NOT READY.** Need a labeled set with hundreds of audited positives/negatives spanning communities/scripts. Current confirmed positives ≪ any serious binary threshold.

### 3b) Multiclass lead_type ML (11 classes)
**NOT READY.** Even worse: several classes have 0–2 system rows and ~0 confirmed true examples (e.g. `BOT_PURCHASE` absent among confirmed). Per-class counts insufficient.

### LightGBM + Optuna + SHAP
**NOT READY.** Libraries not installed; no training scripts; no leakage-safe splits; no stable objective tied to business precision; SHAP would explain a noisy pseudo-label model.

### Other model families
Same gate: **not now**. Deterministic fixes + labeling first. Logistic regression / LightGBM later as a **reranker on structured features**, not a replacement for a broken taxonomy.

---

## False negatives (bounded)

Search: messages with `message_date` last 30d, no lead, commercial-ish regex (`evidence/06-fn-candidates.txt`).

- EN hits: mostly **employment vacancies** (JobStash quant roles) or “fix the API for trading bots?” support — **not clear missed RFQs**.
- RU hits: mostly vacancies / off-topic; one educational reply about Rust vs Solana.

**Corpus-wide FN rate: UNKNOWN** (full 8.5M regex scan not completed; expensive).  
**Recent-window FN of true buyer RFQs: low in this sample**, but under-powered.

Notable: the Solana copy-trading RFQ **was** captured as a lead (TRUE in census) — not an FN.

---

## Production readiness gates (dimensions)

| Dimension | Gate |
|-----------|------|
| Data integrity | READY |
| Collection reliability | READY WITH CONDITIONS (timeout republish) |
| Deduplication | READY |
| Classification quality | **NOT READY** |
| Lead relevance | **NOT READY** |
| English support | READY WITH CONDITIONS (LATIN dominates; quality = FP-heavy) |
| Russian support | READY WITH CONDITIONS / under-powered sample |
| Performance | READY WITH CONDITIONS |
| Observability | READY WITH CONDITIONS |
| Error recovery | READY (success-only ACK) |
| Security/configuration | READY WITH CONDITIONS |
| Database health | READY |
| Deployment reproducibility | READY WITH CONDITIONS |
| Test coverage | READY WITH CONDITIONS |
| Operational maintainability | READY WITH CONDITIONS |
| **Actionable leads/day business target** | **UNKNOWN** (no owner threshold) |
| **Binary ML** | **NOT READY** |
| **Multiclass ML** | **NOT READY** |
| **Optuna/SHAP/LightGBM** | **NOT READY** |

---

## Required fixes before production *use*

### BLOCKERS
1. Stop scoring exchange marketing / challenges as CLIENT commercial contracts (Bitunix ×75).
2. Restore or replace **`community_profiles`** dampening (currently dead).
3. Strengthen job-board / employment / job-seeker / vendor vetoes until census target rate is acceptable.
4. Define owner metric for actionable leads/day; until then do not claim volume readiness.

### IMPORTANT
5. Persist negatives or export `messages LEFT JOIN leads` for evaluation/ML.
6. Add integration tests for commercial FP regressions (promo, job digest, vendor).
7. Improve `/stats` (by lead_type, community, script) and Redis lag metrics.
8. RU pattern parity for commercial + job_aggregator vetoes.
9. Analyzer N+1 query batching (throughput).

### NICE
10. Clean unused YAML keys; remove backup scoring files from deploy path.
11. Document edited-message recheck strategy (optional separate job).

---

## Recommended sequence

```text
1. Deterministic FP fixes + community_profiles (offline replay on 157-unit set first)
2. Disable or heavily dampen worst communities (bitunixglobal, pure job boards) after measuring delta
3. External human labeling of census + stratified FN sample → gold set
4. Bilingual veto/pattern tests
5. Observability for commercial KPI funnel
6. Only then: binary weak-supervision / LightGBM experiments
7. Multiclass only after per-class label budgets exist
```

---

## Answers to Definition-of-Done questions

| Question | Answer |
|----------|--------|
| How many messages? | **8,511,557** VERIFIED |
| How many lead rows? | **256** VERIFIED |
| Distinct opportunities? | **144 texts / 157 text×community / 94 authors** VERIFIED |
| Genuinely relevant? | **~6 units / 7 rows** under agent business-rule census VERIFIED (limitation: not external humans) |
| Audited commercial usefulness? | **~2.7% of lead rows** |
| Dominant FPs? | Exchange promo/channels, job boards/vacancies, job seekers, vendors |
| Dominant FNs? | UNKNOWN corpus-wide; recent window sample mostly non-RFQ |
| English (LATIN) quality? | High volume, **poor commercial precision** |
| Russian quality? | Sparse corpus; 1 confirmed TRUE; under-powered |
| Collector new-message rate? | **~7.4k published/day** steady-state VERIFIED |
| Duplicate rate? | Lead text duplication **severe** (75-row group); message UNIQUE clean |
| Performance bottlenecks? | Analyzer N+1; heavy regex FN scans; not blocking lag today |
| Active reliability blockers? | None acute (lag 0); commercial quality is the blocker |
| Operationally safe? | Yes for running; **not safe to trust leads for outreach without filtering** |
| Ready for supervised ML? | **NO** |
| Optuna/SHAP/LightGBM? | **NO** |
| Other models justified now? | **NO** |
| Must fix before production use? | FP scoring + community profiles + define volume KPI |
| Safe to improve later? | Throughput, observability polish, ML after labels |

---

## Evidence index

| File | Contents |
|------|----------|
| `evidence/01-platform-health.txt` | Restarts, Redis, 24h errors |
| `evidence/02-volumes-dedup-growth.txt` | Counts, UNIQUE, identity metrics, `created_at` growth |
| `evidence/02b-created-at-reality.txt` | Bulk vs steady-state ingest |
| `evidence/03-communities-and-dup-texts.txt` | Top communities, duplicate texts |
| `evidence/04-audit-units.tsv` | All `(text,community)` units |
| `evidence/05-script-heuristic.txt` | Script mixes |
| `evidence/06-fn-candidates.txt` | Bounded FN search |
| `evidence/07-recent-leads.txt` | Lead creation days |
| `evidence/08-throughput-logs.txt` | Analyzer/collector logs |
| `evidence/09-human-audit-*.tsv/txt` | Census labels + summary |
| `evidence/10-config-ack.txt` | Missing community_profiles; ACK lines |

Plan + amendments: `docs/audit/AUDIT_EXECUTION_PLAN.md`
