# Evidence 119c: Mode-2 RETRIEVED FP root-cause

- Generated: `2026-09-27T10:16:46.911327+00:00` HEAD `95eb42b`
- Isolation: human_labels=1524 AI_CONFIRMED=4
- Auth: owner approved Mode-2 investigation + minimal hardening
- Locks: no path_b/c reopen; no scoring.yaml loosen; no human_labels mutation

## Findings

### mid=60620
- Hypothesis: LaborX NEW PROJECT listing reaches path E (v6_budget_deliverable) despite marketplace shape; JOB_VACANCY FP if eligible.
- v4 eligible=False; v6 eligible=False trigger=None paths=[]
- Signals: ownership=False aggregator=True carve=False
- Vetoes: ['job_aggregator', 'corporate_employment']
- CURRENT CODE already vetoes via job_aggregator — freeze RETRIEVED may be stale or race; still harden listing veto for defense-in-depth

### mid=4780480
- Hypothesis: MEXC FOMO commentary reaches path E via ownership_signal on bare 'my strategy' + domain; OFF_DOMAIN FP if eligible.
- v4 eligible=False; v6 eligible=False trigger=None paths=[]
- Signals: ownership=False aggregator=False carve=False
- Vetoes: []
- CURRENT CODE already ineligible — still narrow path-E ownership away from bare strategy advice

## Post-harden (applied)

| mid | v6 eligible | first_loss | note |
|-----|-------------|------------|------|
| 60620 | false | VETO_job_aggregator | `_FO_LISTING_RX` no longer includes NEW PROJECT ON LABORX |
| 4780480 | false | NO_PATH_MATCH | `_OWNERSHIP_RX` no longer matches bare `my strategy` |

pytest: 43 passed. path_b/c frozen. scoring.yaml untouched.
