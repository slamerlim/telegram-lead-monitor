#!/usr/bin/env python3
import json
import httpx


rows = httpx.get("http://localhost:8000/leads", params={"min_score": 75, "limit": 100}, timeout=30).json()
for lead in rows:
    print("=" * 100)
    print(f"{lead['score']:.0f} | {lead['tier']} | {lead.get('community_name')} | {lead.get('author_username')}")
    print("Message:", lead.get("message_url"))
    print("Author:", lead.get("author_url"))
    print(lead.get("author_bio") or "")
    print(lead["text"])
    print("Keywords:", ", ".join(lead["matched_keywords"]))
    print("Reasons:", "; ".join(lead["reasons"]))
