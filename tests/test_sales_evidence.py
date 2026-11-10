from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from shared.sales import alert_text, evidence, safe_url


def test_contact_comes_from_author_not_quoted_username():
    now = datetime.now(timezone.utc)
    lead = SimpleNamespace(id=1, budget_amount=3000, budget_currency="USD", lead_type="BOT_PURCHASE")
    message = SimpleNamespace(text="Please build my trading bot. Do not contact @unrelated. Deadline: Friday",
                              message_url="https://t.me/source/1", message_date=now - timedelta(hours=3))
    author = SimpleNamespace(username="actual_buyer", is_bot=False)
    item = evidence(lead, message, author, now=now)
    assert item["contact_url"] == "https://t.me/actual_buyer"
    assert item["age_hours"] == 3 and item["budget"] == "3000 USD"
    assert item["deadline"] == "Deadline: Friday"
    assert "@unrelated" in alert_text(lead, message, item, "Could we discuss the bot requirements?")
    assert safe_url("javascript:alert(1)") is None
    assert safe_url("https://user:pass@example.com") is None


def test_telegram_payload_unicode_bound_and_no_bot_contact():
    lead = SimpleNamespace(id=2, budget_amount=None, budget_currency=None, lead_type="BOT_REPAIR")
    message = SimpleNamespace(text="🚀" * 10000, message_url="https://t.me/source/2",
                              message_date=datetime.now(timezone.utc))
    item = evidence(lead, message, SimpleNamespace(username="post_bot", is_bot=True))
    assert item["contact_url"] == message.message_url
    text = alert_text(lead, message, item, "A draft")
    assert len(text.encode("utf-16-le")) <= 8000
    # Even long emoji requests must retain their actionable link and draft.
    assert "Source: https://t.me/source/2" in text
    assert "A draft" in text
