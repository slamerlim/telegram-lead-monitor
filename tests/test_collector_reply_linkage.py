"""Collector reply linkage extraction safety."""

from __future__ import annotations

from types import SimpleNamespace


def test_reply_to_extraction_tolerant():
    """Mirrors collector logic: missing/invalid reply_to_msg_id must not raise."""

    def extract(message):
        reply_to_id = None
        try:
            raw_reply = getattr(message, "reply_to_msg_id", None)
            if raw_reply is not None:
                reply_to_id = int(raw_reply)
        except (TypeError, ValueError):
            reply_to_id = None
        return reply_to_id

    assert extract(SimpleNamespace(reply_to_msg_id=42)) == 42
    assert extract(SimpleNamespace()) is None
    assert extract(SimpleNamespace(reply_to_msg_id="bad")) is None
    assert extract(SimpleNamespace(reply_to_msg_id=None)) is None
