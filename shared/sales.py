"""Actionable sales evidence and owner alerts. Never sends to prospective buyers."""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from telethon import functions, types
from telethon.errors import FloodWaitError

from shared.models import Author, CommercialAIReview, Lead, LeadEvent, LeadNotification, Message
from shared.opportunity import text_hash
from shared.outbox import enqueue
from shared.redis_bus import RetryLater
from shared.settings import get_settings

OWNER_ALERT_STREAM = "telegram:owner_alerts"
OWNER_ALERT_GROUP = "owner-alert-workers"

SERVICE_NAMES = {
    "BOT_PURCHASE": "Trading bot development", "BOT_REPAIR": "Trading bot repair",
    "BOT_CUSTOMIZATION": "Trading bot customization", "STRATEGY_IMPLEMENTATION": "Strategy automation",
    "TRADING_SYSTEM_CONTRACT": "Trading system engineering", "QUANT_ENGINEERING_CONTRACT": "Quant engineering",
    "ML_AI_ENGINEERING_CONTRACT": "ML / AI engineering", "ARBITRAGE_PROJECT": "Arbitrage system",
    "COPY_TRADING_PROJECT": "Copy trading system", "MARKET_MAKING_PROJECT": "Market making system",
    "SOLANA_DEX_BOT_PROJECT": "Solana / DEX bot",
}


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def safe_url(value: str | None) -> str | None:
    try:
        parsed = urlparse(value or "")
        return value if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username else None
    except ValueError:
        return None


def evidence(lead: Lead, message: Message, author: Author | None, *, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    age = (now - utc(message.message_date)).total_seconds() / 3600
    contact = None
    label = "No verified contact route"
    # A quoted @username is not necessarily the buyer. Prefer actual author metadata.
    if author and author.username and not author.is_bot and re.fullmatch(r"[A-Za-z0-9_]{4,32}", author.username):
        contact, label = f"https://t.me/{author.username}", "Author profile (buyer identity requires review)"
    if not contact:
        # Marketplace application links can reach a buyer; channel posters usually cannot.
        urls = re.findall(r"https?://[^\s<>]+", message.text)
        for url in urls:
            url = url.rstrip(".,);]")
            parsed = urlparse(url)
            if safe_url(url) and parsed.hostname in {"laborx.com", "www.laborx.com", "www.upwork.com", "upwork.com"} and re.search(r"/(?:jobs?|job|projects?)/", parsed.path):
                contact, label = url, "Application link (unverified)"
                break
    if not contact and safe_url(message.message_url):
        contact, label = message.message_url, "Original thread (buyer identity unverified)"
    deadline = re.search(r"(?:deadline|deliver\s+by|needed\s+by|срок(?:и)?|дедлайн)\s*[:—-]?\s*[^\n.!?]{1,100}", message.text, re.I)
    # Budget is a parser result, not an AI-invented price; the original request remains visible.
    budget = f"{lead.budget_amount:g} {lead.budget_currency or ''}".strip() if lead.budget_amount is not None else None
    return {"age_hours": round(max(age, 0), 2), "stale": age > get_settings().hot_lead_max_age_hours,
            "future_dated": age < -5 / 60, "service": SERVICE_NAMES.get(lead.lead_type, lead.lead_type),
            "budget": budget, "deadline": deadline.group(0) if deadline else None,
            "contact_url": contact, "contact_label": label}


def alert_text(lead, message, details, draft: str | None) -> str:
    # UTF-16 bound is enforced below; source link + inbox preserve the full request.
    original = message.text[:1700] + ("\n[Request shortened; open source for full text]" if len(message.text) > 1700 else "")
    lines = [f"HOT LEAD #{lead.id} · {details['service']}",
             f"Posted: {utc(message.message_date).isoformat()} · {details['age_hours']:g}h old when queued",
             f"Stated budget: {details['budget'] or 'Not stated'}",
             f"Deadline: {details['deadline'] or 'Not stated'}", "", "Original request:", original,
             "", f"Source: {safe_url(message.message_url) or 'Unavailable'}",
             f"Contact: {details['contact_url'] or 'Unavailable'}", details['contact_label'],
             "", "Outreach draft — edit and send manually:", (draft or "No verified draft available.")[:600]]
    text = "\n".join(lines)
    # Telegram limits use UTF-16 code units; don't split a surrogate pair.
    return text.encode("utf-16-le")[:8000].decode("utf-16-le", errors="ignore")


async def queue_owner_alert(session, lead: Lead, message: Message) -> LeadNotification | None:
    cfg = get_settings()
    if not cfg.owner_notifications_enabled or lead.status != "AI_CONFIRMED":
        return None
    author = await session.get(Author, message.author_id) if message.author_id else None
    details = evidence(lead, message, author)
    if details["stale"] or details["future_dated"] or not details["contact_url"]:
        return None
    draft = await session.scalar(select(CommercialAIReview.draft_text).where(
        CommercialAIReview.lead_id == lead.id, CommercialAIReview.row_kind == "DRAFT",
        CommercialAIReview.draft_valid.is_(True)).order_by(CommercialAIReview.id.desc()).limit(1))
    # Exact reposts across communities receive one owner alert. No fuzzy buyer merging.
    result = await session.execute(insert(LeadNotification).values(
        lead_id=lead.id, dedup_key=text_hash(message.text), target=cfg.owner_telegram_target,
        random_id=secrets.randbits(63) or 1, status="PENDING",
        payload=alert_text(lead, message, details, draft), created_at=datetime.now(timezone.utc),
    ).on_conflict_do_nothing().returning(LeadNotification.id))
    alert_id = result.scalar_one_or_none()
    if alert_id is None:
        return None
    await enqueue(session, OWNER_ALERT_STREAM, {"notification_id": alert_id}, key=f"owner-alert:{alert_id}")
    return await session.get(LeadNotification, alert_id)


async def send_owner_alert(session, client, payload: dict) -> None:
    cfg = get_settings()
    if not cfg.owner_notifications_enabled:
        raise RetryLater("owner alerts disabled")
    alert = await session.get(LeadNotification, int(payload["notification_id"]), with_for_update=True)
    if not alert or alert.status != "PENDING":
        return
    lead = await session.get(Lead, alert.lead_id)
    message = await session.get(Message, lead.message_id) if lead else None
    now = datetime.now(timezone.utc)
    if alert.retry_after and utc(alert.retry_after) > now:
        raise RetryLater("telegram_retry_after")
    feedback = await session.scalar(select(LeadEvent.event_type).where(
        LeadEvent.lead_id == alert.lead_id, LeadEvent.event_type.in_(("OWNER_ACCEPTED", "OWNER_REJECTED")))
        .order_by(LeadEvent.id.desc()).limit(1))
    if not message or lead.status not in {"AI_CONFIRMED", "QUALIFIED"} or feedback == "OWNER_REJECTED":
        alert.status, alert.reason = "SUPPRESSED", "lead_no_longer_actionable"
    elif now - utc(message.message_date) > timedelta(hours=cfg.hot_lead_max_age_hours):
        alert.status, alert.reason = "SUPPRESSED", "stale_before_delivery"
    else:
        # The stored target is immutable on retry. Never resolve a recipient from lead text.
        target = int(alert.target) if alert.target.lstrip("-").isdigit() else alert.target
        peer = await client.get_input_entity(target)
        try:
            result = await client(functions.messages.SendMessageRequest(
                peer=peer, message=alert.payload, random_id=alert.random_id, no_webpage=True,
            ))
        except FloodWaitError as exc:
            alert.retry_after = now + timedelta(seconds=exc.seconds + 5)
            await session.commit()
            raise RetryLater(f"telegram_flood_wait_{exc.seconds}s") from None
        except (ConnectionError, TimeoutError, OSError):
            raise RetryLater("telegram_connection_unavailable") from None
        alert.status, alert.sent_at = "SENT", datetime.now(timezone.utc)
        if isinstance(result, types.UpdateShortSentMessage):
            alert.telegram_message_id = result.id
        else:
            for update in getattr(result, "updates", []):
                if isinstance(update, types.UpdateMessageID) and update.random_id == alert.random_id:
                    alert.telegram_message_id = update.id
    await session.commit()
