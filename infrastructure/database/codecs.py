"""Shared asyncpg codecs for structured PostgreSQL values."""

import json


def _json_encode(value):
    """Encode Python JSON-compatible values for asyncpg json/jsonb columns."""
    return json.dumps(value, ensure_ascii=False, default=str)


async def register_json_codecs(conn) -> None:
    """Register consistent json/jsonb codecs on one asyncpg connection."""
    for type_name in ("json", "jsonb"):
        await conn.set_type_codec(
            type_name,
            encoder=_json_encode,
            decoder=json.loads,
            schema="pg_catalog",
        )
