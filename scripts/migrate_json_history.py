import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from infrastructure.config.settings import get_settings
from infrastructure.conversation.pg_repository import PgConversationRepository
from infrastructure.database.postgres import PostgresDatabase


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("history-migration")
USERS_DIR = ROOT / "data" / "users"
IMPORT_TITLE = "[导入] 旧 JSON 历史"


async def migrate_one(database, path: Path) -> tuple[int, int]:
    if not path.name == "chat_history.json":
        return 0, 0
    if not path.parent.name.startswith("user_"):
        return 0, 0
    owner_id = path.parent.name.removeprefix("user_")
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("跳过损坏文件 %s: %s", path, exc)
        return 0, 1
    if not isinstance(items, list):
        logger.error("跳过非数组历史文件 %s", path)
        return 0, 1

    repo = PgConversationRepository(database, owner_id)
    existing = await repo.list(limit=100)
    if any(row["title"] == IMPORT_TITLE for row in existing):
        logger.info("已导入，跳过 %s", owner_id)
        return 0, 0

    conversation_id = await repo.create(IMPORT_TITLE)
    imported = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            # system 是旧会话的提示词，不是用户可见聊天消息，故不迁移。
            continue
        await repo.add_message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            requested_mode="normal",
            resolved_mode="normal",
        )
        imported += 1
    logger.info("用户 %s 导入 %s 条", owner_id, imported)
    return imported, 0


async def main() -> None:
    settings = get_settings()
    pg = PostgresDatabase(settings.pg_dsn)
    await pg.connect()
    total = 0
    errors = 0
    try:
        for path in sorted(USERS_DIR.glob("user_*/chat_history.json")):
            count, failed = await migrate_one(pg, path)
            total += count
            errors += failed
    finally:
        await pg.close()
    logger.info("迁移结束：导入=%s，失败文件=%s", total, errors)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
