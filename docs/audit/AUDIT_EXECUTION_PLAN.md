# Production-Readiness / Lead-Quality / ML-Readiness Audit — EXECUTION PLAN

**Status:** PLAN ONLY. No implementation. Read-only until the report is delivered.
**Target host:** Ubuntu production, `/home/slamer-lim/telegram-lead-monitor`
**Planner run:** 2026-09-22 ~12:57 UTC+3 (facts below are VERIFIED-at-plan-time; re-verify at audit start)
**Consumer:** parent audit agent

---

## 0. Evidence taxonomy (use these labels in every claim)

| Label | Meaning | Required proof |
|---|---|---|
| `VERIFIED` | Re-measured during this audit run | Command + raw output in evidence dir |
| `HISTORICAL` | Measured earlier, not re-run now | Source + timestamp, explicitly flagged as stale |
| `UNKNOWN` | Cannot be measured with current instrumentation | Statement of what is missing and what would be needed |

Never upgrade `HISTORICAL` to `VERIFIED` without re-running. Never state a precision/FP number without the sample size and the sampling frame.

---

## 1. Starting facts measured by the planner (re-confirm in Phase 1–2)

These were measured live on the host at plan time. Treat as `HISTORICAL` at audit start; the audit must re-run each one. They are listed so the agent knows what to expect and can flag drift.

### Platform
- Git: `main @ 91ef77b` ("Merge pull request #2"), working tree clean.
- Compose services: `postgres` Up 8 days (healthy), `redis` Up 8 days (healthy), `api`/`collector`/`analyzer` Up ~53m, `scheduler` Up ~52m. All 6 Up.
- Redis streams: `telegram:messages`, `telegram:scan_requests` (from `shared/redis_bus.py`).

### Corpus
- `communities` = **136** (all 136 `enabled=true`); only **123** have any messages (13 communities produce nothing).
- `messages` = **8,511,088**
- `authors` = **187,868**
- `scan_runs` = **1,187**
- Message date range: **2025-09-19 → 2026-09-22** (~12 months).

### Lead inventory
- `leads` = **256** total → lead yield ≈ **0.0030%** of messages.
- Tier: `HIGH` = 196 (score 75–100), `MEDIUM` = 60 (score 61–73), `LOW` = 0 *(by design — LOW deletes the lead row)*.
- `buyer_type`: `CLIENT` = 216, `RECRUITER` = 40.
- `status`: `NEW` = 256 → **zero human labels exist**.
- `lead_type`: `TRADING_SYSTEM_CONTRACT` 143, `SOLANA_DEX_BOT_PROJECT` 66, `ML_AI_ENGINEERING_CONTRACT` 17, `COPY_TRADING_PROJECT` 12, `QUANT_ENGINEERING_CONTRACT` 6, `MARKET_MAKING_PROJECT` 5, `BOT_CUSTOMIZATION` 3, `STRATEGY_IMPLEMENTATION` 2, `ARBITRAGE_PROJECT` 1, `BOT_REPAIR` 1.

### Three red flags the planner already observed (audit must confirm or refute)

**RF-1 — Duplicate inflation.** 256 lead rows collapse to **144 distinct `md5(text)`**. 21 duplicate groups cover 133 rows. ⇒ ~44% of the lead table is repeated text. The largest single duplicate group is **75 rows of one identical text** — i.e. one message accounts for 29% of the entire lead table.

**RF-2 — Single-channel domination by an exchange marketing feed.** `bitunixglobal` = **89 leads (34.8% of all leads) from only 4 distinct texts**. Spot check of its top-scoring rows: `score=100`, `tier=HIGH`, `lead_type=TRADING_SYSTEM_CONTRACT`, `buyer_type=CLIENT`, text = `"🚀 Futures Grid PnL Challenge is LIVE! …"` — an exchange promotional broadcast, not a buyer commissioning a trading system. Prima facie **false positive**. Other top channels are also exchange/job feeds: `jobstash` 18, `MEXCEnglish` 15, `solana_jobs` 14, `BybitEnglish` 10, `BybitAPI` 8, `LaborXWeb3Jobs` 8, `rabotaweb3` 7, `Kucoin_Exchange` 7, `blockchain_job` 7.

**RF-3 — `community_profiles` is dead code in production.** `LeadScorer.__init__` reads `data.get("community_profiles", {})` and `_profile()` consumes `job_weight` / `support_weight` / `commercial_boost`. The key is **absent** from live `config/scoring.yaml`, from all three backups, *and* from `/app/config/scoring.yaml` inside the analyzer container. ⇒ `_profile()` always returns `{}`; the community-level dampening intended to suppress exchange-support and job channels **never fires**. This is a plausible mechanistic cause of RF-2.

> RF-3 is a config/code gap, not a scoring-semantics disagreement. Under the "freeze scoring semantics unless evidence proves a defect" rule, the audit may **document** it and quantify its impact by simulation, but must not change YAML or code before the report.

### ML surface
- `pyproject.toml` dependencies contain **no** `lightgbm`, `optuna`, `shap`, `xgboost`, `catboost`, `scikit-learn`. Optional extras: `semantic` (`sentence-transformers`, `torch`), `dev` (`pytest`, `pytest-asyncio`, `ruff`).
- `pip list` inside the analyzer container: **none** of lightgbm/optuna/shap/xgboost/catboost/scikit/torch/sentence-transformers installed.
- No language-detection library anywhere in the repo (`langdetect`/`langid`/`fasttext`/`pycld`/`lingua` all absent). The only hits for "language" are human-readable reason strings in `scoring.py` and the `semantic_model` setting name.
- `SEMANTIC_ENABLED=false`; `semantic.py` is an optional, disabled layer.

---

## 2. Standard command patterns (use verbatim; no secrets printed)

**SQL — always use this heredoc form.** It reads credentials from the container's own environment, so nothing sensitive is echoed, and it avoids shell quoting errors with Cyrillic/regex literals. Validated by the planner.

```bash
cd /home/slamer-lim/telegram-lead-monitor && \
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -P pager=off' <<'SQL'
-- statements here
SQL
```

Add `-A -F$'\t'` for machine-readable output when saving evidence. Never pass `POSTGRES_PASSWORD` on a command line. Never `cat .env`.

**Evidence capture.** Every command's raw output goes to `docs/audit/evidence/<phase>-<slug>.txt`, appended with the command line and a UTC timestamp. The report cites these filenames.

```bash
mkdir -p docs/audit/evidence
```

---

## 3. Phase structure

### Phase 1 — Platform & pipeline integrity (VERIFY the baseline)
*Answers: technical readiness, reliability, bottlenecks (part 1).*

1. **Git + drift**
   ```bash
   git log --oneline -3 && git status --porcelain && git fetch --dry-run origin 2>&1 | head
   ```
   Acceptance: `main @ 91ef77b`, clean tree, no divergence from `origin/main`. Any deviation → record and re-baseline before continuing.

2. **Service health & uptime**
   ```bash
   docker compose ps --format 'table {{.Service}}\t{{.Status}}\t{{.Health}}'
   curl -sS http://127.0.0.1:8010/health
   curl -sS http://127.0.0.1:8010/stats
   ```
   Acceptance: 6/6 Up; `/health` = `ok`; `/stats` agrees with SQL counts (cross-check).

3. **Restart-loop check (reliability evidence, not just "Up")**
   ```bash
   docker compose ps -a --format '{{.Service}} {{.Status}}'
   for s in api collector analyzer scheduler; do
     echo "== $s"; docker inspect -f '{{.RestartCount}} {{.State.StartedAt}}' "$(docker compose ps -q $s)"
   done
   ```
   Acceptance: `RestartCount` is the reliability signal. A high count with a recent `StartedAt` means crash-looping masked by `restart: unless-stopped`. Record exact numbers.

4. **Error/exception rate in logs (bounded window)**
   ```bash
   for s in api collector analyzer scheduler; do
     echo "=== $s"; docker compose logs --since 24h --no-color "$s" 2>&1 \
       | grep -ciE 'traceback|error|exception|flood|timeout|critical' ;
   done
   for s in collector analyzer; do
     echo "=== $s samples"; docker compose logs --since 24h --no-color "$s" 2>&1 \
       | grep -iE 'traceback|error|exception|flood|timeout' | tail -25
   done
   ```
   Acceptance: classify each distinct error signature; count occurrences. FloodWait is expected-and-handled; tracebacks are not.

5. **Redis backlog / consumer lag**
   ```bash
   docker compose exec -T redis redis-cli XINFO STREAM telegram:messages
   docker compose exec -T redis redis-cli XINFO GROUPS telegram:messages
   docker compose exec -T redis redis-cli XINFO GROUPS telegram:scan_requests
   docker compose exec -T redis redis-cli INFO memory | grep -E 'used_memory_human|maxmemory'
   ```
   Acceptance: `pending` and `lag` at 0 for both groups ⇒ consumers keep up. Non-zero and growing ⇒ throughput bottleneck. Also record stream `length` — an unbounded stream is a capacity risk even at lag 0.

6. **Capacity headroom**
   ```bash
   df -h / && docker system df
   ```
   Acceptance: free space ≥ 40G and volume growth rate sustainable for ≥ 90 days at observed message rate (compute rate in Phase 2).

7. **Schema & migration state**
   ```bash
   docker compose exec -T api alembic current
   ```
   ```sql
   SELECT tablename, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC;
   SELECT pg_size_pretty(pg_total_relation_size('messages')) AS messages_size,
          pg_size_pretty(pg_total_relation_size('leads'))    AS leads_size,
          pg_size_pretty(pg_database_size(current_database())) AS db_size;
   ```
   Acceptance: alembic at head (`0002_lead_intent_and_operational_fields`); no unexpected tables.

8. **Integrity invariants (the DUP_CHECK family, as SQL)**
   ```sql
   SELECT count(*) AS dup_leads_per_message FROM (
     SELECT message_id FROM leads GROUP BY message_id HAVING count(*) > 1) t;
   SELECT count(*) AS dup_messages FROM (
     SELECT community_id, telegram_message_id FROM messages
     GROUP BY 1,2 HAVING count(*) > 1) t;
   SELECT count(*) AS orphan_leads FROM leads l
     LEFT JOIN messages m ON m.id = l.message_id WHERE m.id IS NULL;
   SELECT count(*) AS low_tier_rows FROM leads WHERE tier = 'LOW';
   ```
   Acceptance: first three = 0. `low_tier_rows` = 0 is *expected* (LOW deletes) — record it as designed behaviour, and note the consequence for ML in Phase 5.

9. **Collector cursor sanity**
   ```sql
   SELECT count(*) FILTER (WHERE last_message_id IS NULL) AS no_cursor,
          count(*) FILTER (WHERE last_scanned_at IS NULL) AS never_scanned,
          count(*) FILTER (WHERE resolve_status <> 'ok')  AS unresolved,
          count(*) AS total
   FROM communities WHERE enabled;
   SELECT resolve_status, count(*) FROM communities GROUP BY 1 ORDER BY 2 DESC;
   SELECT id, telegram_ref, resolve_status, left(coalesce(last_error,''),120) AS err
   FROM communities WHERE last_error IS NOT NULL ORDER BY id LIMIT 20;
   ```
   Acceptance: quantify the 13 message-less communities — are they unresolved, private, or empty? This is a *coverage* defect, not a scoring one.

10. **Scan-run outcome distribution (reliability)**
    ```sql
    SELECT status, count(*) FROM scan_runs GROUP BY 1 ORDER BY 2 DESC;
    SELECT date_trunc('day', requested_at) d, count(*) n,
           count(*) FILTER (WHERE status='failed') failed
    FROM scan_runs WHERE requested_at > now() - interval '14 days'
    GROUP BY 1 ORDER BY 1 DESC;
    SELECT id, community_id, status, left(coalesce(error,''),160) AS error
    FROM scan_runs WHERE error IS NOT NULL ORDER BY id DESC LIMIT 20;
    SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM finished_at-started_at)) AS p50_sec,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM finished_at-started_at)) AS p95_sec
    FROM scan_runs WHERE finished_at IS NOT NULL AND started_at IS NOT NULL;
    ```
    Acceptance: failure rate < 5% of recent runs; stuck `running` rows older than 2× p95 are a defect.

---

### Phase 2 — Corpus suitability & business fit
*Answers: is the collected data business-suitable; EN/RU; bottlenecks (part 2).*

1. **Volume, rate, growth**
   ```sql
   SELECT count(*) AS messages, min(message_date), max(message_date),
          count(DISTINCT community_id) AS comms_with_messages FROM messages;
   SELECT date_trunc('week', message_date) w, count(*) n
   FROM messages WHERE message_date > now() - interval '90 days'
   GROUP BY 1 ORDER BY 1 DESC;
   ```
   Derive messages/day → project storage growth for the Phase 1 headroom check.

2. **Community mix — is the corpus pointed at buyers or at broadcasters?**
   ```sql
   SELECT c.id, c.username, c.name, c.kind, count(m.id) AS msgs,
          max(m.message_date) AS last_msg
   FROM communities c LEFT JOIN messages m ON m.community_id = c.id
   GROUP BY 1,2,3,4 ORDER BY msgs DESC LIMIT 40;
   ```
   Then classify the **top 40 communities by message volume** into: `EXCHANGE_OFFICIAL` (announcement/support), `JOB_BOARD`, `DEV_COMMUNITY`, `TRADER_COMMUNITY`, `OTHER`. This is a manual judgement recorded in `docs/audit/evidence/phase2-community-taxonomy.tsv`.
   Acceptance criterion for "business-suitable data": the share of corpus volume in `DEV_COMMUNITY` + `TRADER_COMMUNITY` (where buyers actually ask for work) vs `EXCHANGE_OFFICIAL` + `JOB_BOARD` (where they structurally do not). A corpus dominated by the latter caps achievable precision regardless of scoring quality.

3. **Broadcast vs conversation** — buyers post in conversations; broadcast channels emit marketing.
   ```sql
   SELECT c.username,
          count(*) AS msgs,
          count(DISTINCT m.author_id) AS distinct_authors,
          round(100.0*count(*) FILTER (WHERE m.author_id IS NULL)/count(*),1) AS pct_no_author,
          round(100.0*count(*) FILTER (WHERE m.is_reply)/count(*),1) AS pct_reply
   FROM messages m JOIN communities c ON c.id=m.community_id
   GROUP BY 1 HAVING count(*) > 5000
   ORDER BY msgs DESC LIMIT 40;
   ```
   Interpretation: `distinct_authors` ≈ 1 or very high `pct_no_author` ⇒ broadcast channel. Record which of these produce leads.

4. **Duplicate / near-duplicate pressure in the corpus**
   ```sql
   SELECT count(*) AS msgs, count(DISTINCT md5(text)) AS distinct_texts
   FROM messages TABLESAMPLE SYSTEM (1);
   ```
   Use `TABLESAMPLE` — a full `count(DISTINCT md5(text))` over 8.5M rows is an unnecessary load. Report as an estimate with the sample fraction stated.

5. **EN/RU composition — see §5 for the method.**

6. **Empty/degenerate text**
   ```sql
   SELECT count(*) FILTER (WHERE text IS NULL OR btrim(text)='') AS empty_text,
          count(*) FILTER (WHERE length(text) < 20)  AS very_short,
          percentile_cont(0.5)  WITHIN GROUP (ORDER BY length(text)) AS p50_len,
          percentile_cont(0.95) WITHIN GROUP (ORDER BY length(text)) AS p95_len
   FROM messages TABLESAMPLE SYSTEM (2);
   ```

---

### Phase 3 — Lead inventory & genuine commercial relevance
*Answers: lead counts, genuine commercial relevance, precision.*

1. **Re-run the full inventory** (tier / lead_type / buyer_type / status distributions from §1) and record drift vs the planner's numbers.

2. **Deduplicated inventory — the number that actually matters commercially**
   ```sql
   SELECT count(*) AS lead_rows,
          count(DISTINCT md5(m.text)) AS distinct_texts,
          count(DISTINCT m.author_id) AS distinct_authors
   FROM leads l JOIN messages m ON m.id = l.message_id;

   SELECT c.username, count(*) AS lead_rows,
          count(DISTINCT md5(m.text)) AS distinct_texts
   FROM leads l JOIN messages m ON m.id=l.message_id
   JOIN communities c ON c.id=m.community_id
   GROUP BY 1 ORDER BY 2 DESC;

   -- the duplicate groups themselves
   SELECT count(*) AS n, left(regexp_replace(m.text,E'[\n\r]+',' ','g'),110) AS snippet
   FROM leads l JOIN messages m ON m.id=l.message_id
   GROUP BY m.text HAVING count(*)>1 ORDER BY n DESC LIMIT 25;
   ```
   **Report both** `lead_rows` and `distinct_texts`. The business-facing "how many leads do we have" answer is the deduplicated, non-FP count, not 256.

3. **Recency** — a 12-month-old lead is not actionable.
   ```sql
   SELECT count(*) FILTER (WHERE m.message_date > now() - interval '7 days')  AS last_7d,
          count(*) FILTER (WHERE m.message_date > now() - interval '30 days') AS last_30d,
          count(*) FILTER (WHERE m.message_date > now() - interval '90 days') AS last_90d,
          count(*) AS all_time
   FROM leads l JOIN messages m ON m.id=l.message_id;
   ```
   Acceptance: compute **actionable lead rate = distinct, non-FP leads in last 30d / 30**. This is the single most business-relevant throughput number.

4. **Contactability** — a lead with no reachable author is not a lead.
   ```sql
   SELECT count(*) FILTER (WHERE a.username IS NOT NULL) AS author_contactable,
          count(*) FILTER (WHERE l.contact_usernames <> '[]') AS has_contact_in_text,
          count(*) FILTER (WHERE a.username IS NULL AND l.contact_usernames = '[]') AS uncontactable,
          count(*) AS total
   FROM leads l JOIN messages m ON m.id=l.message_id
   LEFT JOIN authors a ON a.id = m.author_id;
   ```

5. **Budget signal**
   ```sql
   SELECT count(*) FILTER (WHERE budget_amount IS NOT NULL) AS with_budget,
          budget_currency, count(*) FROM leads GROUP BY budget_currency ORDER BY 3 DESC;
   ```

6. **Human audit sample — see §4 for the full design.** This is the core precision measurement.

---

### Phase 4 — Precision, false positives, false negatives, language
*Answers: precision, FP/FN, EN/RU.*

1. **Precision** from the Phase 3 stratified sample (§4). Report overall precision **and** per-stratum precision with Wilson 95% confidence intervals. With n≈100 the CI half-width is roughly ±8–10pp — state this explicitly rather than implying a point estimate is exact.

2. **FP hunt — targeted queries** (§6).

3. **FN hunt — targeted queries** (§6). FN measurement is necessarily a *lower bound* (you can only find the ones you search for); label it as such.

4. **Language split** (§5) applied to: corpus, leads, FP set, FN set. The business question is whether RU buyers are being systematically missed because the YAML is EN-heavy (104/36 terms, 63/32 patterns; `job_aggregator` EN-only).

---

### Phase 5 — ML readiness
*Answers: Optuna/SHAP/LightGBM readiness, other models.*

1. **Label availability** (the binding constraint)
   ```sql
   SELECT status, count(*) FROM leads GROUP BY 1;
   SELECT count(*) AS leads_ever_updated FROM leads WHERE updated_at > created_at;
   ```
   At plan time: 256/256 `NEW`, i.e. **zero human labels**. Confirm.

2. **Negative-class availability**
   - `LOW`-tier survivors are deleted from `leads`, so the negative class is **not persisted**. Verify by reading `scripts/reprocess_messages.py` delete path and confirming `SELECT count(*) FROM leads WHERE tier='LOW'` = 0.
   - Consequence: a supervised model has ~144 distinct positives (many of them FPs) and **no stored negatives** out of 8.5M messages. Negatives would have to be regenerated by re-scoring, which is a design decision, not an existing asset.

3. **Dependency reality**
   ```bash
   grep -nE 'lightgbm|optuna|shap|scikit|xgboost|catboost' pyproject.toml requirements.txt || echo ABSENT
   docker compose exec -T analyzer pip list 2>/dev/null | grep -iE 'lightgbm|optuna|shap|scikit|torch' || echo "NONE INSTALLED"
   ls -la scripts/ | grep -iE 'train|model|tune' || echo "NO TRAINING SCRIPTS"
   ```

4. **Feature-store readiness** — the numeric sub-scores (`intent_score`, `technical_score`, `commercial_score`, `promotion_score`, `semantic_score`) plus `matched_categories` exist *only on rows that became leads*. There is no persisted feature vector for the 8.5M non-leads. State this as the key architectural gap.

5. Apply the **ML go/no-go checklist** in §8.

---

### Phase 6 — Synthesis (report) → then, and only then, optional fixes

1. Fill the **gate rubric** (§7) for every dimension.
2. Write the report to the path in §9.
3. **Stop.** Present the report. Only after the user accepts it, propose the **minimal fix backlog** (§11). No code, YAML, or data changes before that point.

---

## 4. Stratified human-audit sample design

**Frame:** all 256 lead rows (re-count at audit time).
**Unit:** the *distinct message text* — dedupe by `md5(text)` first, so one marketing broadcast repeated 40× consumes one review slot, not 40. Record the row multiplicity alongside each sampled item so population-level FP impact can be weighted back up.

**Target:** review **100 distinct lead texts**. Since there are only ~144 distinct texts, this is a ~70% sample — near-census, which is the right call at this scale and removes most sampling error. If distinct texts ≤ 120 at audit time, **review all of them (census)** and report precision without a sampling CI.

**Strata** (allocate proportionally, then top up minority cells to a floor of 10 where they exist):

| Stratum | Cells | Floor |
|---|---|---|
| Tier | HIGH, MEDIUM | 20 each |
| Buyer type | CLIENT, RECRUITER | 15 each |
| Lead type | the 10 observed types | 3 each (all rows if fewer) |
| Language | EN, RU, MIXED, UNKNOWN (§5) | 10 for RU if available |
| Community class | EXCHANGE_OFFICIAL, JOB_BOARD, DEV/TRADER, OTHER | 15 each |
| Suspicious | `is_duplicate_text`, `score=100`, `author_id IS NULL` (broadcast), `contact_usernames='[]'` | 10 each |

**Sampling SQL (reproducible — fixed seed):**
```sql
SELECT setseed(0.42);
WITH dedup AS (
  SELECT DISTINCT ON (md5(m.text))
         l.id AS lead_id, l.score, l.tier, l.lead_type, l.buyer_type,
         c.username AS community, m.author_id,
         (m.text ~ '[А-Яа-яЁё]') AS has_cyrillic,
         count(*) OVER (PARTITION BY md5(m.text)) AS row_multiplicity,
         m.message_url,
         left(regexp_replace(m.text, E'[\n\r]+', ' ', 'g'), 400) AS snippet
  FROM leads l
  JOIN messages m ON m.id = l.message_id
  JOIN communities c ON c.id = m.community_id
  ORDER BY md5(m.text), l.id
)
SELECT * FROM dedup ORDER BY random();
```
Export full rows to `docs/audit/evidence/phase3-audit-sample.tsv` (use `-A -F$'\t'`). **Read the full message text, not the snippet, before judging** — pull it with `SELECT text FROM messages WHERE id=…`.

**Verdict schema** — one row per reviewed text in `docs/audit/evidence/phase3-audit-verdicts.tsv`:

| Column | Values |
|---|---|
| `lead_id` | int |
| `verdict` | `TRUE_LEAD` / `FALSE_POSITIVE` / `AMBIGUOUS` |
| `fp_reason` | `MARKETING_BROADCAST`, `JOB_VACANCY`, `SUPPORT_REQUEST`, `SERVICE_AD`, `JOB_SEEKER`, `NEWS_DIGEST`, `OFF_DOMAIN`, `DUPLICATE`, `n/a` |
| `commercially_actionable` | yes/no — *would a sales rep contact this person today?* |
| `language` | EN/RU/MIXED/OTHER/UNKNOWN |
| `row_multiplicity` | int (for weighting) |
| `note` | free text |

**Decision rule for `TRUE_LEAD`:** an identifiable party is expressing a want for paid work on a trading/quant/ML system that the business could deliver, and is reachable. Exchange promos, vacancy ads, CVs, support tickets, and agency self-promotion are all `FALSE_POSITIVE` regardless of score.

**Two precision figures must be reported:**
- **Row-weighted precision** = Σ(`row_multiplicity` of TRUE_LEAD) / Σ(all `row_multiplicity`) — what the lead table actually looks like to a user.
- **Unweighted distinct-text precision** — what the scorer's discriminative quality is.

`AMBIGUOUS` items are reported separately and excluded from the numerator; report precision as a range (strict = ambiguous counted as FP; lenient = counted as true).

---

## 5. Language estimation without a detector

**There is no language detector in the codebase** (`VERIFIED`: no `langdetect`/`langid`/`fasttext`/`pycld`/`lingua`). Do **not** install one — that is a code change and out of scope for a read-only audit.

**Use a documented script-based heuristic instead, and label it as a heuristic everywhere it appears.**

```sql
-- Corpus-level (sampled: 8.5M rows makes a full scan wasteful)
SELECT count(*) AS sampled,
       count(*) FILTER (WHERE text ~ '[А-Яа-яЁё]')                     AS any_cyrillic,
       count(*) FILTER (WHERE text ~ '[A-Za-z]')                       AS any_latin,
       count(*) FILTER (WHERE text ~ '[А-Яа-яЁё]' AND text ~ '[A-Za-z]') AS both,
       count(*) FILTER (WHERE text !~ '[А-Яа-яЁё]' AND text !~ '[A-Za-z]') AS neither
FROM messages TABLESAMPLE SYSTEM (2);

-- Lead-level (full scan, only 256 rows)
SELECT CASE
         WHEN m.text ~ '[А-Яа-яЁё]' AND m.text ~ '[A-Za-z]' THEN 'MIXED'
         WHEN m.text ~ '[А-Яа-яЁё]'                          THEN 'RU_SCRIPT'
         WHEN m.text ~ '[A-Za-z]'                            THEN 'LATIN_SCRIPT'
         ELSE 'UNKNOWN' END AS lang_bucket,
       count(*)
FROM leads l JOIN messages m ON m.id=l.message_id GROUP BY 1 ORDER BY 2 DESC;
```

**Refinement for MIXED** (a mostly-English post with one Russian word is not a Russian lead) — use Cyrillic character *density*:
```sql
SELECT l.id,
       round(100.0 * (length(m.text) - length(regexp_replace(m.text,'[А-Яа-яЁё]','','g')))
             / nullif(length(m.text),0), 1) AS pct_cyrillic
FROM leads l JOIN messages m ON m.id=l.message_id ORDER BY pct_cyrillic DESC;
```
Rule: `pct_cyrillic ≥ 20` → RU; `1–20` → MIXED; `0` with Latin → EN-script; else UNKNOWN.

**Mandatory caveats to state in the report:**
- Latin script ≠ English (Spanish/Turkish/Indonesian all land in `LATIN_SCRIPT`). So the "EN" figure is an **upper bound on English**, and must be written as `LATIN_SCRIPT`, not `EN`, in tables.
- Cyrillic script ≠ Russian (Ukrainian, Bulgarian, Serbian). Upper bound on Russian.
- Emoji/CJK-only and empty texts → `UNKNOWN`.
- The human audit (§4) assigns a *true* language label to the ~100 reviewed texts; use that to report the observed error rate of the heuristic on that subset. That is the only calibrated language number available.

Planner's measurement for context (`HISTORICAL`): 33/256 leads (12.9%) contain Cyrillic. Both `'[А-Яа-яЁё]'` and `'[\u0400-\u04FF]'` returned identical counts on a 2% sample, but **prefer the literal-character form** — it is unambiguous across PostgreSQL escape-string settings.

---

## 6. FP / FN search queries (EN + RU)

### 6.1 False positives — confirm the suspected classes

```sql
-- FP-A: marketing/promo broadcast scored as a commercial lead
SELECT l.id, l.score, l.tier, l.lead_type, c.username,
       left(regexp_replace(m.text,E'[\n\r]+',' ','g'),160) AS snippet
FROM leads l JOIN messages m ON m.id=l.message_id JOIN communities c ON c.id=m.community_id
WHERE m.text ~* '(giveaway|airdrop|campaign|challenge|prize pool|reward|bonus|listing|promo|
                  win |claim now|join now|trading competition|розыгрыш|конкурс|бонус|акци)'
ORDER BY l.score DESC;

-- FP-B: leads from broadcast-only channels (no real author)
SELECT c.username, count(*) FROM leads l
JOIN messages m ON m.id=l.message_id JOIN communities c ON c.id=m.community_id
WHERE m.author_id IS NULL GROUP BY 1 ORDER BY 2 DESC;

-- FP-C: vacancy language surviving as a commercial contract type
SELECT l.id,l.score,l.lead_type,l.buyer_type,c.username,
       left(regexp_replace(m.text,E'[\n\r]+',' ','g'),160) AS snippet
FROM leads l JOIN messages m ON m.id=l.message_id JOIN communities c ON c.id=m.community_id
WHERE m.text ~* '(#vacancy|#hiring|#job|we are hiring|apply now|send your cv|resume|
                  salary|full-time|remote position|вакансия|резюме|зарплат|полная занятость)'
ORDER BY l.score DESC;

-- FP-D: service-provider ads that beat the non-buyer veto
SELECT l.id,l.score,l.lead_type,l.buyer_type,
       left(regexp_replace(m.text,E'[\n\r]+',' ','g'),160) AS snippet
FROM leads l JOIN messages m ON m.id=l.message_id
WHERE m.text ~* '(our team|we develop|we build|portfolio|our services|dm me|contact us|
                  our company|мы разрабатываем|наша команда|наши услуги|пишите в лс)'
ORDER BY l.score DESC;

-- FP-E: exchange support disguised as a lead
SELECT l.id,l.score,l.lead_type,c.username,
       left(regexp_replace(m.text,E'[\n\r]+',' ','g'),160) AS snippet
FROM leads l JOIN messages m ON m.id=l.message_id JOIN communities c ON c.id=m.community_id
WHERE m.text ~* '(withdraw|deposit|kyc|my account|ticket|support team|frozen|locked|
                  не могу вывести|пополнен|поддержк|заблокирован)'
ORDER BY l.score DESC;
```

### 6.2 False negatives — buyers the pipeline did *not* turn into leads

The FN frame is `messages` with **no** `leads` row. Always bound with a date filter and `LIMIT`; never scan 8.5M rows unbounded.

```sql
-- FN-EN: explicit commissioning language, no lead row
SELECT m.id, c.username, m.message_date,
       left(regexp_replace(m.text,E'[\n\r]+',' ','g'),200) AS snippet
FROM messages m
LEFT JOIN leads l ON l.message_id = m.id
JOIN communities c ON c.id = m.community_id
WHERE l.id IS NULL
  AND m.message_date > now() - interval '90 days'
  AND m.text ~* '(need (a )?(dev|developer|programmer|coder)|looking for (a )?(dev|developer|quant)|
                  who can (build|make|write|code)|can (you|someone) (build|make|write)|
                  i want to (buy|order|commission)|ready to pay|will pay|paid (task|project|gig)|
                  hire (a )?(dev|developer|freelancer)|budget for)'
  AND m.text ~* '(bot|trading|strategy|algo|arbitrage|market ?mak|exchange|api|solana|quant|hft)'
ORDER BY m.message_date DESC LIMIT 100;

-- FN-RU: same, Russian
SELECT m.id, c.username, m.message_date,
       left(regexp_replace(m.text,E'[\n\r]+',' ','g'),200) AS snippet
FROM messages m
LEFT JOIN leads l ON l.message_id = m.id
JOIN communities c ON c.id = m.community_id
WHERE l.id IS NULL
  AND m.message_date > now() - interval '90 days'
  AND m.text ~* '(нужен (разработчик|программист|кодер|специалист)|ищу (разработчика|программиста|кванта)|
                  кто может (написать|сделать|разработать)|готов (оплатить|заплатить)|
                  заказать (бота|разработку)|требуется (разработчик|программист)|
                  оплачу|за деньги|бюджет)'
  AND m.text ~* '(бот|торгов|стратеги|алго|арбитраж|маркет.?мейк|бирж|api|солана|квант)'
ORDER BY m.message_date DESC LIMIT 100;

-- FN-BUDGET: explicit money + build verb, no lead
SELECT m.id, c.username, m.message_date,
       left(regexp_replace(m.text,E'[\n\r]+',' ','g'),200) AS snippet
FROM messages m
LEFT JOIN leads l ON l.message_id=m.id JOIN communities c ON c.id=m.community_id
WHERE l.id IS NULL
  AND m.message_date > now() - interval '90 days'
  AND m.text ~* '(\$[0-9]{3,}|[0-9]{3,}\s*(usd|usdt|\$)|[0-9]{4,}\s*(руб|₽))'
  AND m.text ~* '(bot|trading|strategy|бот|торгов|стратеги)'
ORDER BY m.message_date DESC LIMIT 100;
```

**FN review protocol:** take up to **60 distinct FN candidates** (20 from each query, deduped), read full text, label `TRUE_MISSED_LEAD` / `CORRECTLY_IGNORED`. Record in `docs/audit/evidence/phase4-fn-verdicts.tsv`.

**Report FN as a lower bound.** You cannot compute true recall without labelling a random sample of the 8.5M non-leads, which is out of scope. State this. Optionally, for a weak recall proxy, label a **random 200-message sample** from the last 30 days (`TABLESAMPLE` + `ORDER BY random() LIMIT 200`) and report how many true buyer requests it contains — at the observed ~0.003% lead rate this will almost certainly contain zero, which is itself a reportable result about base rate.

---

## 7. Gate rubric

Apply per dimension. Every rating needs a cited evidence file.

| Rating | Meaning |
|---|---|
| `READY` | Measured, meets the acceptance criterion, no known defect affecting it |
| `READY WITH CONDITIONS` | Works, but a named, bounded fix or an operational caveat is required; state the condition and its owner |
| `NOT READY` | Measured and fails the acceptance criterion, or a defect blocks business use |
| `UNKNOWN` | Not measurable with current instrumentation; state what is missing |

| # | Dimension | Acceptance criterion for `READY` |
|---|---|---|
| 1 | Pipeline liveness | 6/6 Up, `/health` ok, RestartCount stable over 24h |
| 2 | Throughput / lag | Redis pending = 0, lag = 0, stream length not growing unboundedly |
| 3 | Data integrity | dup leads/message = 0, dup messages = 0, orphans = 0 |
| 4 | Coverage | ≥ 95% of enabled communities have a fresh cursor and recent messages |
| 5 | Capacity | ≥ 90 days headroom at observed growth |
| 6 | Corpus suitability | Majority of volume in communities where buyers actually post |
| 7 | Lead volume | Actionable (distinct, non-FP, ≤30d) leads/day meets the business need |
| 8 | Precision | Row-weighted precision ≥ 0.70 → READY; 0.40–0.70 → CONDITIONS; < 0.40 → NOT READY |
| 9 | FP profile | No single FP class > 20% of the lead table |
| 10 | FN profile | < 20% of reviewed FN candidates are true missed leads |
| 11 | EN/RU parity | RU lead share within 2× of RU corpus share, or gap explained |
| 12 | Scoring config integrity | No dead config paths; live YAML supplies every key the code reads |
| 13 | Reliability | Scan failure rate < 5%; no stuck runs |
| 14 | Test coverage | Scoring unit tests pass; integration gap documented |
| 15 | ML readiness | §8 checklist |

**Pre-registered expectation (planner):** on RF-1/RF-2/RF-3, dimensions 8, 9 and 12 look likely to land at `NOT READY`. The audit must confirm with measurement, not inherit this assumption.

---

## 8. ML go/no-go criteria

**Decision:** `GO` / `GO WITH CONDITIONS` / `NO-GO` for building a LightGBM + Optuna + SHAP layer now.

**Evidence checklist — every item must be answered with VERIFIED evidence:**

| # | Question | Threshold for GO | Planner's expectation |
|---|---|---|---|
| M1 | How many human-labelled leads exist? | ≥ 500 labelled, ≥ 150 positive | 0 labels (`status` all NEW) → fails |
| M2 | Is there a persisted negative class? | Yes, ≥ 5,000 stored negatives | LOW rows are deleted → fails |
| M3 | Are features persisted for non-leads? | Yes, for all scored messages | Sub-scores exist only on lead rows → fails |
| M4 | Is the positive class clean? | Precision ≥ 0.70 pre-ML | RF-2 suggests otherwise |
| M5 | Are positives independent? | Duplicate rate < 10% | 256→144 distinct = ~44% dup → fails |
| M6 | Class balance workable? | Positive rate ≥ 0.1% | ~0.003% → extreme imbalance |
| M7 | Are the libraries available? | In `pyproject.toml` + installed | Absent from both |
| M8 | Is there a train/eval harness? | Train script + held-out split + metric | No training scripts |
| M9 | Is there a labelling workflow? | `PATCH /leads/{id}/status` used in practice | Endpoint exists, 0 rows used |
| M10 | Is there drift/versioning infra? | Model registry + reproducible config | None |

**Rule:** `NO-GO` if **any** of M1, M2, M3, M5 fails — these are data-asset problems that no amount of hyperparameter tuning fixes. Optuna optimises a model you cannot yet train; SHAP explains a model that does not exist.

**If NO-GO, the report must state the ordered prerequisites** (not implement them):
1. Stop deleting LOW rows *or* persist a scored-message feature table → creates the negative class.
2. Deduplicate leads by text/author → removes the 44% duplicate inflation.
3. Fix precision first (RF-2/RF-3) → a model trained on today's labels would learn "exchange marketing = lead".
4. Run a labelling campaign via the existing `status` field → target ≥ 500 labels.
5. Only then add `lightgbm`/`optuna`/`shap` and a training harness.

**"Other models" question — assess and recommend, do not build:**
- **Rules-only (status quo, repaired):** cheapest, fully explainable, no labels needed. Likely the correct near-term answer.
- **Multilingual embeddings + threshold** (`semantic.py` already scaffolded, `SEMANTIC_ENABLED=false`): addresses the EN/RU keyword asymmetry without labels; cost = model download + CPU latency per message at 8.5M scale — quantify before recommending.
- **Zero/few-shot LLM classifier** on the ~300 candidates/day that pass a cheap pre-filter: no training labels needed, doubles as a labelling engine for a future LightGBM. Cost-per-message must be estimated against observed daily volume.
- **LightGBM:** correct *eventual* target once M1–M3 are satisfied; on tabular sub-scores + categorical features it would be a good fit.
- Evaluate each on: label requirement, latency at observed throughput, explainability, and cost. Present as a comparison, with a recommended sequence.

---

## 9. Report artifact

**Path:** `/home/slamer-lim/telegram-lead-monitor/docs/audit/PRODUCTION_READINESS_AUDIT_2026-09-22.md`
**Evidence dir:** `/home/slamer-lim/telegram-lead-monitor/docs/audit/evidence/`
(These are the **only** files the audit may create. Nothing under `services/`, `shared/`, `config/`, `scripts/`, `alembic/` may be touched.)

**Required sections, in order:**
1. **Executive summary** — overall verdict, the 3–5 findings that matter, and the single most important number (actionable leads/day at measured precision).
2. **Evidence & method** — what was measured, when, how; VERIFIED/HISTORICAL/UNKNOWN legend; sampling frames and their limits.
3. **Gate scorecard** — the §7 table, all 15 dimensions rated, each with an evidence citation.
4. **Answers to the 15 business questions** — one subsection each, explicitly labelled, in the master prompt's order:
   technical readiness · business-suitable data · lead counts · genuine commercial relevance · precision · FP/FN · EN/RU · bottlenecks · reliability · Optuna/SHAP/LightGBM readiness · other models · blockers · deferrable items · roadmap · (15th per master prompt).
5. **Lead-quality deep dive** — inventory, dedup, per-community concentration, human-audit results with both precision figures + CIs, FP taxonomy with counts, FN lower bound.
6. **Language analysis** — heuristic definition, caveats, corpus vs lead vs FP/FN splits, calibration against human labels.
7. **ML readiness** — M1–M10 checklist, go/no-go, ordered prerequisites, model-option comparison.
8. **Risks & blockers** — severity-ranked, with business impact stated in lead terms.
9. **Roadmap** — sequenced, with the measurement that would confirm each step worked.
10. **Appendix** — full command list and evidence-file index.

**Writing rules:** every quantitative claim carries its `n`, its date, and its evidence filename. No claim that a thing "works" without the command that showed it. Distinguish "no errors observed in a 24h window" from "reliable".

---

## 10. What NOT to do during the audit

**Destructive — never, under any circumstance:**
- `docker compose down -v`, `docker volume rm`, `docker system prune`
- `TRUNCATE`, `DROP`, `DELETE FROM`, `UPDATE` on any table — the audit is `SELECT`-only
- Deleting or rotating the Telegram session volume (re-auth requires a phone code and would take the collector offline)
- `git checkout`/`reset`/`rebase`/`commit`/`push`, or editing tracked files

**Out of scope until the report is accepted:**
- Editing `config/scoring.yaml` — **including adding the missing `community_profiles` key.** RF-3 is a finding to document and quantify, not to fix mid-audit. Changing it invalidates every precision number measured before the change.
- Editing `services/analyzer/app/scoring.py` or any scoring semantics
- Installing packages (`pip install`, `uv add`), including a language detector or any ML library
- Enabling `SEMANTIC_ENABLED`
- Rebuilding images (`docker compose build`, `make up`)

**Operationally risky:**
- Full-table `count(DISTINCT md5(text))` over 8.5M rows, or any unbounded `ILIKE '%…%'` without a date filter and `LIMIT` — use `TABLESAMPLE` and bounded windows
- `POST /scans` — do not trigger new collection; it perturbs the corpus mid-measurement
- Restarting services (changes uptime/RestartCount evidence and can drop in-flight work)
- Re-running the 10k reprocess (see §11 bounds)
- Printing `.env`, `POSTGRES_PASSWORD`, `TELEGRAM_API_HASH`, or session files into logs or the report

**Analytical:**
- Do not report `256` as the lead count without the dedup and FP qualifiers
- Do not call Latin script "English"
- Do not present a precision point estimate without its interval and sample size
- Do not let the planner's red flags substitute for measurement

---

## 11. Bounded experiments

Default: **no experiments.** All 15 questions are answerable from read-only queries plus human review. Run an experiment only if a specific question cannot otherwise be answered, and state the justification in the report.

**Permitted, if justified:**

- **E1 — 1k reprocess, idempotency check.** `make reprocess` (`--max-messages 1000 --batch-size 250`).
  *Justify only if* Phase 1 integrity checks are inconclusive about scorer/DB idempotency.
  Pre-record `SELECT count(*), sum(score) FROM leads;` and the per-tier counts; re-run after; diff. Expect `created=0 updated=0 deleted=0 unchanged=1000` on a stable corpus. Any lead delete must be captured (id + text) **before** it happens — so snapshot candidate ids first.
  Note: this **writes** to `leads` (it can delete LOW rows). It is the one sanctioned write, it is bounded, and it must be explicitly flagged in the report.

- **E2 — Offline scorer replay (preferred, zero-write).** Re-score sampled texts **in a throwaway Python process** that imports `LeadScorer` and prints results, without touching the DB. This is the correct tool for "what would the score be if…" questions and for quantifying RF-3 impact:
  ```bash
  docker compose exec -T analyzer python - <<'PY'
  from services.analyzer.app.scoring import LeadScorer
  s = LeadScorer("/app/config/scoring.yaml")
  print(s.score("<text under test>", community_username="bitunixglobal").tier)
  PY
  ```
  Use this to show *why* a specific FP scored 100 (inspect `.reasons`) — strong, cheap report evidence.

**Not permitted:** repeating the full 10k reprocess (the 1k run answers the same question at 1/10 the write surface), any unbounded reprocess, any reprocess without `--max-messages`.

---

## 12. Post-report fix backlog (draft only after the report is accepted)

Do not write this section until the report is delivered and the user approves proceeding. Structure:

**BLOCKERS** — prevent business use of the output. Expected candidates based on planner observations, subject to confirmation:
- Exchange marketing broadcasts scoring as HIGH commercial leads (RF-2)
- `community_profiles` absent from live YAML, making community dampening inert (RF-3)
- Duplicate texts inflating the lead table ~44% (RF-1)

**IMPORTANT** — materially degrade quality but have workarounds:
- EN/RU asymmetry in `scoring.yaml`; `job_aggregator` patterns EN-only
- No negative-class persistence (blocks ML and any recall measurement)
- No integration tests for API/worker/DB

**NICE** — hygiene:
- Unused `buyer_intent` / `budget_context` YAML sections
- `scoring.yaml.*` backup files in `config/` (drift risk — a future reader may edit the wrong one)
- N+1 queries per message in the analyzer

Each item: one-line problem, the evidence citation, smallest safe fix, the measurement that would prove it worked, and a rollback note. **Every scoring change must be validated by an E2 offline replay against the audit sample before any live change**, so the precision delta is known in advance.

---

## 13. Suggested execution order & effort

| Step | Phase | Effort | Gate to proceed |
|---|---|---|---|
| 1 | Phase 1 (1–10) | ~30 min | Baseline matches §1 or drift is explained |
| 2 | Phase 2 (1–6) | ~45 min | Community taxonomy complete |
| 3 | Phase 3 (1–5) | ~20 min | Dedup inventory established |
| 4 | Phase 3.6 human audit | ~2–3 h | ~100 distinct texts reviewed (census if ≤120) |
| 5 | Phase 4 FP/FN | ~1.5 h | FP taxonomy + 60 FN candidates reviewed |
| 6 | Phase 5 ML | ~30 min | M1–M10 answered |
| 7 | Phase 6 report | ~1.5 h | All 15 questions answered, every claim cited |

The human audit is the long pole and the highest-value step — it is the only thing that converts "256 leads" into a defensible business answer. Do not compress it.

---

# AUDIT PLAN AMENDMENTS (owner-approved 2026-09-22)

Apply without expanding scope. Audit remains read-only.

## A. Capacity calculations
Use `messages.created_at` (not `message_date`) for messages/day, DB growth, storage headroom, recent ingestion throughput. Report `message_date` only as source-content age.

## B. Lead deduplication
Report: lead rows; distinct text hashes; distinct `(text_hash, community_id)`; distinct authors; distinct `(author_id, text_hash)`; multiplicity. Primary human-audit unit: `(md5(text), community_id)` with community/author context preserved. Also report pure text-level duplication separately. Distinguish text / broadcast / same-author repeat / separate opportunities.

## C. Actionable-lead business target
Measure `distinct non-FP leads in last 30 days / 30` but do **not** invent a READY threshold. Gate = `UNKNOWN` until owner defines required daily qualified volume.

## D. Language quality
RU/lead-share-vs-corpus-share is diagnostic only. Primary evidence: human-audited precision / FP / FN by language + heuristic calibration. Do not call LATIN_SCRIPT "English" or CYRILLIC "Russian" except as upper-bound heuristic.

## E. ML readiness split
Separate gates: (1) binary commercial vs non-commercial; (2) multiclass lead_type. Binary label thresholds are insufficient for multiclass; report per-class counts and rare classes.

## F. Business lead-count terminology
Never equate lead rows with sales opportunities. Use: lead rows; distinct texts; distinct authors; human-confirmed target leads; actionable leads (recent, confirmed, contactable).

## G. Historical vs verified
Tag every quantitative conclusion VERIFIED / HISTORICAL / UNKNOWN. Planner values are context only.

## H. No implementation during audit
Only create `docs/audit/PRODUCTION_READINESS_AUDIT_2026-09-22.md` and `docs/audit/evidence/*`. No code/YAML/schema/data/Redis mutations; no rebuilds; no full reprocess; no package installs; no semantic enablement.

## I. Final ML conclusion
Answer separately: (1) deterministic rules ready for production use? (2) dataset ready for supervised ML? (3) sufficient for LightGBM+Optuna+SHAP vs other models?

## First-class funnel metric
```
messages → business-relevant corpus % → candidate lead rows → deduplicated candidates
→ human-confirmed commercial opportunities → contactable recent opportunities
→ actionable leads/day → quality sufficient for ML?
```
