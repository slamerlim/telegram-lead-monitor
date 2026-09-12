import csv
import io
import json
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db import engine, get_session
from shared.logging import configure_logging
from shared.models import Author, Community, Lead, Message, ScanRun
from shared.redis_bus import RedisBus, SCAN_STREAM
from shared.settings import get_settings
from services.api.app.schemas import CommunityCreate, CommunityOut, HealthOut, LeadOut, ScanOut, StatsOut


settings = get_settings()
configure_logging(settings.log_level)
app = FastAPI(title="Telegram Lead Monitor API", version="0.1.0")


@app.on_event("shutdown")
async def shutdown() -> None:
    await engine.dispose()


@app.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    postgres = "ok"
    redis_status = "ok"
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        postgres = "error"
    bus = RedisBus(settings.redis_url)
    try:
        await bus.redis.ping()
    except Exception:
        redis_status = "error"
    finally:
        await bus.close()
    status = "ok" if postgres == redis_status == "ok" else "degraded"
    return HealthOut(status=status, postgres=postgres, redis=redis_status)


@app.post("/communities", response_model=CommunityOut, status_code=201)
async def add_community(payload: CommunityCreate, session: AsyncSession = Depends(get_session)) -> CommunityOut:
    existing = await session.scalar(select(Community).where(Community.telegram_ref == payload.telegram_ref))
    if existing:
        existing.enabled = payload.enabled
        await session.commit()
        await session.refresh(existing)
        return CommunityOut.model_validate(existing)
    community = Community(telegram_ref=payload.telegram_ref, enabled=payload.enabled)
    session.add(community)
    await session.commit()
    await session.refresh(community)
    return CommunityOut.model_validate(community)


@app.get("/communities", response_model=list[CommunityOut])
async def list_communities(session: AsyncSession = Depends(get_session)) -> list[CommunityOut]:
    rows = (await session.scalars(select(Community).order_by(Community.id))).all()
    return [CommunityOut.model_validate(row) for row in rows]


@app.patch("/communities/{community_id}/enabled", response_model=CommunityOut)
async def set_community_enabled(community_id: int, enabled: bool, session: AsyncSession = Depends(get_session)) -> CommunityOut:
    community = await session.get(Community, community_id)
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")
    community.enabled = enabled
    await session.commit()
    await session.refresh(community)
    return CommunityOut.model_validate(community)


@app.post("/scans", response_model=ScanOut, status_code=202)
async def request_scan(
    days: int = Query(default=settings.scan_default_days, ge=1, le=365),
    community_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> ScanOut:
    if community_id is not None and not await session.get(Community, community_id):
        raise HTTPException(status_code=404, detail="Community not found")
    query = select(func.count(Community.id)).where(Community.enabled.is_(True))
    if community_id:
        query = query.where(Community.id == community_id)
    count = int((await session.scalar(query)) or 0)
    if count == 0:
        raise HTTPException(status_code=400, detail="No enabled communities selected")

    run = ScanRun(community_id=community_id, days=days, status="queued")
    session.add(run)
    await session.commit()
    await session.refresh(run)
    bus = RedisBus(settings.redis_url)
    try:
        await bus.publish(SCAN_STREAM, {"run_id": run.id, "community_id": community_id, "days": days})
    finally:
        await bus.close()
    return ScanOut(run_id=run.id, status=run.status, communities=count, days=days)


@app.get("/scans")
async def list_scans(limit: int = Query(default=50, ge=1, le=500), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(ScanRun).order_by(ScanRun.id.desc()).limit(limit))).all()
    return [
        {
            "id": row.id,
            "community_id": row.community_id,
            "days": row.days,
            "status": row.status,
            "requested_at": row.requested_at,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "messages_seen": row.messages_seen,
            "messages_published": row.messages_published,
            "error": row.error,
        }
        for row in rows
    ]


@app.get("/leads", response_model=list[LeadOut])
async def list_leads(
    min_score: float = Query(default=50, ge=0, le=100),
    tier: str | None = Query(default=None),
    since_days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> list[LeadOut]:
    since = datetime.now(timezone.utc) - timedelta(days=since_days)
    stmt = (
        select(Lead, Message, Community, Author)
        .join(Message, Lead.message_id == Message.id)
        .join(Community, Message.community_id == Community.id)
        .outerjoin(Author, Message.author_id == Author.id)
        .where(Lead.score >= min_score, Message.message_date >= since)
        .order_by(Lead.score.desc(), Message.message_date.desc())
        .limit(limit)
    )
    if tier:
        stmt = stmt.where(Lead.tier == tier.upper())
    results = (await session.execute(stmt)).all()
    return [_lead_out(lead, message, community, author) for lead, message, community, author in results]


def _lead_out(lead: Lead, message: Message, community: Community, author: Author | None) -> LeadOut:
    return LeadOut(
        id=lead.id,
        score=lead.score,
        tier=lead.tier,
        matched_keywords=json.loads(lead.matched_keywords or "[]"),
        matched_categories=json.loads(lead.matched_categories or "[]"),
        reasons=json.loads(lead.reasons or "[]"),
        semantic_score=lead.semantic_score,
        message_id=message.telegram_message_id,
        message_url=message.message_url,
        message_date=message.message_date,
        text=message.text,
        community_name=community.name,
        community_username=community.username,
        community_url=community.url,
        author_id=author.telegram_id if author else None,
        author_username=author.username if author else None,
        author_url=(f"https://t.me/{author.username}" if author and author.username else None),
        author_name=author.name if author else None,
        author_bio=author.bio if author else None,
    )


@app.get("/stats", response_model=StatsOut)
async def stats(session: AsyncSession = Depends(get_session)) -> StatsOut:
    communities = int((await session.scalar(select(func.count(Community.id)))) or 0)
    messages = int((await session.scalar(select(func.count(Message.id)))) or 0)
    leads = int((await session.scalar(select(func.count(Lead.id)))) or 0)
    high = int((await session.scalar(select(func.count(Lead.id)).where(Lead.tier == "HIGH"))) or 0)
    return StatsOut(communities=communities, messages=messages, leads=leads, high_leads=high)


@app.get("/search")
async def search_messages(
    q: str = Query(min_length=2, max_length=200),
    limit: int = Query(default=100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(Message, Community, Author)
        .join(Community, Message.community_id == Community.id)
        .outerjoin(Author, Message.author_id == Author.id)
        .where(Message.text.ilike(f"%{q}%"))
        .order_by(Message.message_date.desc())
        .limit(limit)
    )
    results = (await session.execute(stmt)).all()
    return [
        {
            "message_id": m.telegram_message_id,
            "message_url": m.message_url,
            "message_date": m.message_date,
            "text": m.text,
            "community": c.name,
            "community_url": c.url,
            "author_id": a.telegram_id if a else None,
            "author_username": a.username if a else None,
            "author_url": (f"https://t.me/{a.username}" if a and a.username else None),
            "author_name": a.name if a else None,
            "author_bio": a.bio if a else None,
        }
        for m, c, a in results
    ]


@app.get("/exports/leads.xlsx")
async def export_xlsx(
    min_score: float = Query(default=50, ge=0, le=100),
    since_days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
):
    import pandas as pd

    leads = await list_leads(min_score=min_score, since_days=since_days, limit=1000, session=session)
    rows = [lead.model_dump() for lead in leads]
    frame = pd.DataFrame(rows)
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="Leads")
    out.seek(0)
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=telegram_leads.xlsx"},
    )


@app.get("/exports/leads.csv")
async def export_csv(
    min_score: float = Query(default=50, ge=0, le=100),
    since_days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
):
    leads = await list_leads(min_score=min_score, since_days=since_days, limit=1000, session=session)
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=[
            "score", "tier", "matched_keywords", "matched_categories", "reasons",
            "semantic_score", "message_date", "message_url", "community_name",
            "community_username", "community_url", "author_id", "author_username",
            "author_name", "author_bio", "text",
        ],
    )
    writer.writeheader()
    for lead in leads:
        writer.writerow(lead.model_dump())
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=telegram_leads.csv"},
    )
