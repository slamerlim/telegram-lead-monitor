#!/usr/bin/env python3
"""Add Telegram communities from arguments or a file.

File format: one Telegram community URL/username per line. Blank lines and comments are ignored.
A message URL such as https://t.me/s/channel/123 is normalized to @channel. Invite links are
preserved and can only resolve if the authenticated Telegram account can access them.
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

_MESSAGE_PATH_RE = re.compile(r"^/s/([^/]+)/\d+$", re.IGNORECASE)
_USERNAME_PATH_RE = re.compile(r"^/([^/+][^/]*)/?$", re.IGNORECASE)


def normalize_ref(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("empty community reference")
    if value.startswith(("https://t.me/", "http://t.me/")):
        parsed = urlparse(value)
        path = parsed.path.rstrip("/")
        if path.startswith("/s/"):
            match = _MESSAGE_PATH_RE.match(path)
            if match:
                return f"@{match.group(1)}"
        if path.startswith("/joinchat/") or path.startswith("/+"):
            return f"https://t.me{path}"
        match = _USERNAME_PATH_RE.match(path)
        if match:
            return f"@{match.group(1)}"
        raise ValueError(f"unsupported Telegram URL: {value}")
    if value.startswith("@"): return value
    if value.startswith("+"): return f"https://t.me/{value}"
    return f"@{value}"


def load_refs(path: str) -> list[str]:
    refs: list[str] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for token in line.split():
            try:
                refs.append(normalize_ref(token))
            except ValueError as exc:
                print(f"SKIP {token}: {exc}")
    # Telegram usernames are case-insensitive; dedupe on a case-folded key.
    unique: dict[str, str] = {}
    for ref in refs:
        unique.setdefault(ref.casefold(), ref)
    return list(unique.values())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("refs", nargs="*", help="Telegram @username or URL")
    parser.add_argument("--file", dest="file", help="file containing Telegram URLs/usernames")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.refs and not args.file:
        raise SystemExit("Pass Telegram refs/URLs or --file <path>")

    refs = [normalize_ref(value) for value in args.refs]
    if args.file:
        refs.extend(load_refs(args.file))
    unique: dict[str, str] = {}
    for ref in refs:
        unique.setdefault(ref.casefold(), ref)
    refs = list(unique.values())

    api_url = os.getenv("TLM_API_URL", "http://127.0.0.1:8010").rstrip("/")
    if args.dry_run:
        for ref in refs:
            print(ref)
        print(f"TOTAL={len(refs)}")
        return

    with httpx.Client(timeout=30) as client:
        existing_rows = client.get(f"{api_url}/communities").json()
        existing = {str(row["telegram_ref"]).casefold() for row in existing_rows}
        added = skipped = 0
        for ref in refs:
            if ref.casefold() in existing:
                print(f"SKIP EXISTING {ref}")
                skipped += 1
                continue
            response = client.post(
                f"{api_url}/communities",
                json={"telegram_ref": ref, "enabled": True},
            )
            response.raise_for_status()
            print(response.json())
            added += 1
            existing.add(ref.casefold())
    print(f"Added {added}; skipped existing {skipped}; total input {len(refs)}")


if __name__ == "__main__":
    main()
