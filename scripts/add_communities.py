#!/usr/bin/env python3
"""Add communities using the local API.

Usage:
  python scripts/add_communities.py @cryptojobslist @laborxWeb3Jobs @BybitAPI
"""
import sys

import httpx


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Pass at least one Telegram username/link")
    for ref in sys.argv[1:]:
        response = httpx.post(
            "http://localhost:8000/communities",
            json={"telegram_ref": ref, "enabled": True},
            timeout=30,
        )
        response.raise_for_status()
        print(response.json())
