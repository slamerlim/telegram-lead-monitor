# Commercial Production Pilot — AI-Agentic

## Why this system exists

Identify genuine commercial buyers of trading-bot / trading-system / quant / ML
engineering services from Telegram, **qualify them with commercial AI**, rank them,
prepare outreach, and track real business outcomes.

## Architecture separation

| Plane | Role |
|-------|------|
| Independent AI validation (0007) | Research / ML readiness / unanimous 3×TRUE / 100/30 gate |
| Commercial ops audit (0008) | `lead_events` FSM |
| Commercial AI adjudication (0009) | Autonomous AI_CONFIRMED decisions |

Commercial AI **does not** require `ai_validation_gate_ready`.

## Who decides

**AI (autonomous):** commercial candidate evaluation, lead-type classification,
AI_CONFIRMED / AI_CANDIDATE / AI_UNCERTAIN / AI_REJECTED, ranking, outreach draft.

**Human operator:** send/approve external outreach, record CONTACT/RESPONSE/WON/LOST,
optional override/reject. **NOT** a mandatory first-line lead reviewer.

## Pipeline

```
Telegram → scorer → Redis commercial_ai_review → A/B/C Cursor SDK
→ commercial_v1 policy → AI_CONFIRMED (+ draft)
→ operator sends → CONTACT_ATTEMPT → RESPONSE → WON/LOST
```

## First-25 milestone

`first_25_ai_confirmed` — first 25 **AI_CONFIRMED** opportunities.
Not 25 human dispositions.

## Absolute rules

- No ML enablement; no 100/30 loosening; no auto-DM
- AI cannot fabricate RESPONSE/WON
- Commercial AI never writes independent validation / human_labels gate numerators
- Kill switches: `COMMERCIAL_AI_ENABLED`, `COMMERCIAL_AI_AUTO_PROMOTE`, `COMMERCIAL_OPS_ENABLED`

## Commercial episode discovery (0010) — experiment plane

High-recall retrieval from LOW messages → commercial episodes (bounded multi-message
context) → paired A/B shadow reviews (`commercial_ai_v1` message-only vs
`commercial_ai_v2_ep` contextual).

**NO OUTREACH IN THIS PHASE.** Episode `AI_CONFIRMED` does **not** create CRM leads,
AI_PROMOTE, CONTACT_ATTEMPT, RESPONSE, or auto-DM. Existing production AI_CONFIRMED
leads remain untouched. Discovery/episode tables are isolated from 0007 validation
and `human_labels`.

Kill switches (default false): `COMMERCIAL_DISCOVERY_ENABLED`,
`COMMERCIAL_EPISODE_SHADOW_ENABLED`.
