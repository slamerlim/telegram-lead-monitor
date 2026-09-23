# Commercial Production Pilot

## Why this system exists

Identify genuine commercial buyers of trading-bot / trading-system / quant / ML
engineering services from Telegram communities, qualify them with a human
operator, contact them manually, and track real business outcomes.

AI validation is **advisory**. The AI gate being closed does **not** block this pilot.

## Who counts as a target customer

Someone who shows buyer / project / hiring intent for:

1. Buying or commissioning a trading bot
2. Repairing an existing trading bot
3. Custom trading-system development
4. Contract quantitative engineering
5. Contract ML/AI engineering
6. Implementing or automating a trading strategy

## What is NOT a lead

Exchange support, API docs questions, account issues, generic crypto chat,
job seekers looking for work, unrelated recruiters, vendor self-promotion,
purely educational discussion, generic Python/ML mentions without buyer intent.

## What counts as a real customer outcome

Only an operator-recorded event with evidence:

| Outcome | Meaning |
|---------|---------|
| CONTACTED | Operator actually messaged the prospect |
| RESPONDED | Prospect replied (evidence_ref required) |
| QUALIFIED | Real project discussion underway |
| WON | Signed agreement, deposit, paid project, or equivalent commitment |
| LOST | Closed without win (include reason_code; use `nurture` when appropriate) |

Do **not** claim WON or revenue without a real-world event.

## Operator path

1. Enable `COMMERCIAL_OPS_ENABLED=true` with `LABEL_WRITE_TOKEN` + `HUMAN_LABEL_REVIEWER_IDS`
2. `GET /ops/candidates` — triage queue
3. Human disposition: promote + QUALIFIED / REJECTED via events
4. Open contact URLs manually — **no auto-DM**
5. `POST /ops/leads/{id}/events` with CONTACT_ATTEMPT
6. On reply: RESPONSE (+ evidence_ref)
7. Advance to WON/LOST with evidence when appropriate
8. Track funnel: `GET /ops/metrics/funnel` and `/ops/metrics/milestone`

## First-25 procedure

Target **25 human dispositions** (CONFIRMED/REJECTED/UNCERTAIN), not 25 wins.

Sources (in order): scorer HIGH/MEDIUM NEW → agent_provisional TRUE_LEAD → operator search.  
AI hints may reorder priority only.

## Seven-day pilot classification

A no useful leads · B leads but no replies · C real conversations · D proposals ·  
E first commercial outcome · F first paid/won outcome

Honest labeling only.

## Absolute rules

- No ML enablement
- No AI gate / threshold loosening
- No fabricated outcomes
- No automated outreach
- Diagnostic AI batches never inflate production gates
