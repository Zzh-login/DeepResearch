import asyncio
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from uuid import uuid4

from infrastructure.config.settings import get_settings
from infrastructure.database.postgres import PostgresDatabase
from infrastructure.research.pg_repository import PgResearchRepository


async def main():
    settings = get_settings()
    db = PostgresDatabase(settings.pg_dsn)
    await db.connect()
    repo = PgResearchRepository(db)
    owner = "verify-cancel-overwrite"
    created_ids = []  # 所有测试任务 id，供 finally 统一清理（close 之前）
    try:
        # 场景1：verifying + 已请求取消 -> 应置为 cancelled（不得覆盖成 completed）
        task_id = uuid4()
        created_ids.append(task_id)
        await db.pool.execute(
            """
            INSERT INTO research_tasks (id, owner_id, query, status,
                                        cancel_requested_at, attempts)
            VALUES ($1,$2,$3,'verifying', NOW(), 0)
            """,
            task_id, owner, "真实库验证：完成操作不得覆盖已取消任务",
        )
        print("已插入测试任务:", task_id, "status=verifying, cancel_requested_at=NOW()")

        outcome = await repo.complete_task(task_id, {"markdown": "ok", "citation_ids": []})
        print("complete_task() 返回:", outcome)

        row = await db.pool.fetchrow(
            "SELECT status, lease_owner, lease_expires_at FROM research_tasks WHERE id=$1",
            task_id,
        )
        print("最终状态:", dict(row))

        assert outcome == "cancelled", f"期望 cancelled，实际 {outcome}"
        assert row["status"] == "cancelled", "status 应保持 cancelled（未被覆盖成 completed）"
        assert row["lease_owner"] is None, "lease_owner 应清空"
        assert row["lease_expires_at"] is None, "lease_expires_at 应清空"
        print("✓ 场景1验收通过：已取消任务未被完成操作覆盖，租约已清空")

        # 场景2：已是终态 cancelled -> 完成操作不得覆盖（应返回 state_changed）
        terminal_task_id = uuid4()
        created_ids.append(terminal_task_id)
        await db.pool.execute(
            """
            INSERT INTO research_tasks (id, owner_id, query, status, attempts)
            VALUES ($1, $2, $3, 'cancelled', 0)
            """,
            terminal_task_id, owner, "真实库验证：终态 cancelled 不得被完成",
        )
        terminal_outcome = await repo.complete_task(
            terminal_task_id, {"markdown": "should not be saved", "citation_ids": []},
        )
        terminal_row = await db.pool.fetchrow(
            """
            SELECT status, report, lease_owner, lease_expires_at
            FROM research_tasks WHERE id=$1
            """,
            terminal_task_id,
        )
        assert terminal_outcome == "state_changed", f"期望 state_changed，实际 {terminal_outcome}"
        assert terminal_row["status"] == "cancelled", "终态 cancelled 不应被改写"
        assert terminal_row["report"] is None, "report 不应被写入"
        assert terminal_row["lease_owner"] is None
        assert terminal_row["lease_expires_at"] is None
        print("✓ 场景2验收通过：终态 cancelled 未被完成操作覆盖")
    finally:
        # 清理测试数据（在 db.close() 之前执行，避免 pool is closed）
        for tid in created_ids:
            try:
                await db.pool.execute("DELETE FROM research_events WHERE task_id=$1", tid)
            except Exception:
                pass
            await db.pool.execute("DELETE FROM research_tasks WHERE id=$1", tid)
        print("已清理测试数据:", created_ids)
        await db.close()


asyncio.run(main())
