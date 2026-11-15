import asyncio
from collections import Counter

import structlog

from app.redaction.engine import RedactionEngine, RedactionResult

log = structlog.get_logger()


async def preview(engine: RedactionEngine, text: str) -> RedactionResult:
    """Redact one text. The model is CPU-bound, so it runs off the event loop."""
    result = await asyncio.to_thread(engine.redact, text)
    # Counts only: neither the input nor the detected values may reach the logs.
    log.info(
        "redaction_preview",
        characters=len(text),
        entities=dict(Counter(entity.type.value for entity in result.entities)),
    )
    return result
