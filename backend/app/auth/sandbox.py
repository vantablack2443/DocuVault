"""Sample history for guest sandboxes, so the dashboard is not empty on first visit."""

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.db.models import RequestLog, Tenant, UsageDaily

# (days ago, requests, cache hits, redactions, prompt tokens, completion tokens)
_USAGE = [
    (6, 14, 3, 31, 5200, 3900),
    (5, 22, 6, 54, 8100, 6400),
    (4, 9, 2, 17, 3300, 2500),
    (3, 31, 11, 76, 11800, 9100),
    (2, 27, 8, 61, 9900, 7700),
    (1, 18, 5, 40, 6700, 5200),
]
# (minutes ago, provider, status, stream, cache hit, latency ms, redaction counts)
_REQUESTS: list[tuple[int, str, str, bool, bool, int, dict[str, int]]] = [
    (190, "gemini", "ok", True, False, 840, {"PERSON": 2, "IBAN": 1}),
    (150, "gemini", "ok", False, True, 12, {"PERSON": 2, "IBAN": 1}),
    (95, "groq", "ok", True, False, 410, {"EMAIL": 1, "PHONE": 1}),
    (40, "gemini", "ok", False, False, 910, {"PERSON": 1, "ADDRESS": 1, "POSTAL_CODE": 1}),
    (12, "replay", "ok", True, False, 35, {"STEUER_ID": 1}),
]


async def seed_sandbox(session: AsyncSession, tenant: Tenant, now: datetime) -> None:
    """Clearly synthetic numbers; real activity of the visitor is added on top."""
    for days_ago, requests, cache_hits, redactions, prompt_tokens, completion_tokens in _USAGE:
        session.add(
            UsageDaily(
                tenant_id=tenant.id,
                day=(now - timedelta(days=days_ago)).date(),
                requests=requests,
                cache_hits=cache_hits,
                redactions=redactions,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        )
    for minutes_ago, provider, status, stream, cache_hit, latency_ms, counts in _REQUESTS:
        session.add(
            RequestLog(
                tenant_id=tenant.id,
                model="docuvault-default",
                provider=provider,
                status=status,
                stream=stream,
                cache_hit=cache_hit,
                latency_ms=latency_ms,
                prompt_tokens=0,
                completion_tokens=0,
                redaction_counts=counts,
                created_at=now - timedelta(minutes=minutes_ago),
            )
        )
    await audit.append(
        session, tenant.id, actor="system", action="sandbox.created", details={"sample_data": True}
    )
