from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

from domain.model_gateway.contracts import ModelRequestContext


SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "jwt",
    "password",
    "prompt",
    "messages",
    "content",
    "document",
    "report",
    "token",
}


def redact_sensitive(value: Any, key: str = "") -> Any:
    normalized = key.lower().replace("-", "_")
    if any(item in normalized for item in SENSITIVE_KEYS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): redact_sensitive(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str) and len(value) > 500:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return {"sha256": digest, "length": len(value)}
    return value


class AuditRepository:
    def __init__(self, database, enabled: bool = True) -> None:
        self._database = database
        self._enabled = enabled

    async def write(
        self,
        context: ModelRequestContext,
        event_type: str,
        result_code: str,
        metadata: dict[str, Any],
        severity: str = "info",
        conn=None,
    ) -> None:
        if not self._enabled or self._database.pool is None:
            return
        safe_metadata = redact_sensitive(metadata)
        if conn is not None:
            # Use a savepoint when called from a business transaction so an
            # audit insert failure cannot abort the surrounding transaction.
            async with conn.transaction():
                await self._insert(
                    conn,
                    context,
                    event_type,
                    result_code,
                    safe_metadata,
                    severity,
                )
            return
        async with self._database.pool.acquire() as acquired:
            await self._insert(
                acquired,
                context,
                event_type,
                result_code,
                safe_metadata,
                severity,
            )

    @staticmethod
    async def _insert(
        conn,
        context: ModelRequestContext,
        event_type: str,
        result_code: str,
        metadata: dict[str, Any],
        severity: str,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO audit_events (
                id, owner_id, conversation_id, research_task_id,
                mode, event_type, result_code, metadata,
                request_id, severity
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10)
            """,
            uuid4(),
            context.owner_id,
            context.conversation_id,
            context.research_task_id,
            context.mode,
            event_type,
            result_code,
            metadata,
            context.request_id,
            severity,
        )
