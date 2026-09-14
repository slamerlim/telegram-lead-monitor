#!/usr/bin/env python3
import os
import httpx


api_url = os.getenv("TLM_API_URL", "http://127.0.0.1:8010").rstrip("/")
rows = httpx.get(
    f"{api_url}/leads",
    params={"min_score": 50, "limit": 100},
    timeout=30,
).json()
for lead in rows:
    print("=" * 120)
    print(
        f"{lead['score']:.0f} | {lead['tier']} | {lead['lead_type']} | {lead['buyer_type']} "
        f"| {lead.get('community_name')} | {lead.get('author_username')}"
    )
    print("Message:", lead.get("message_url"))
    print("Author:", lead.get("author_url"))
    print("Contacts:", ", ".join(lead.get("contact_usernames") or []))
    print("Budget:", lead.get("budget_amount"), lead.get("budget_currency"))
    print("Bio:", lead.get("author_bio") or "")
    print(lead["text"])
    print("Keywords:", ", ".join(lead["matched_keywords"]))
    print("Reasons:", "; ".join(lead["reasons"]))
