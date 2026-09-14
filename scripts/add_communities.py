#!/usr/bin/env python3
"""Add communities using the local API.

Usage:
  python scripts/add_communities.py @cryptojobslist @laborxWeb3Jobs @BybitAPI

Set TLM_API_URL when the API is not on the default local port.
"""
import os
import sys

import httpx


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Pass at least one Telegram username/link")
    api_url = os.getenv("TLM_API_URL", "http://127.0.0.1:8010").rstrip("/")
    for ref in sys.argv[1:]:
        response = httpx.post(
            f"{api_url}/communities",
            json={"telegram_ref": ref, "enabled": True},
            timeout=30,
        )
        response.raise_for_status()
        print(response.json())
