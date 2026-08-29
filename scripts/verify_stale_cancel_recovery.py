import asyncio
import os
import sys
from uuid import uuid4

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from infrastructure.config.settings import get_settings
from infrastructure.database.postgres import PostgresDatabase
from infrastructure.research.pg_repository import PgResearchRepository


async def main():
    settings = get_settings()
    db = PostgresDatabase(settings.pg_dsn)
    await db.connect()

    repo = PgResearchRepository(db)
    owner_id = "verify-stale-cancel-recovery"
    task_id = uuid4()

    try:
        # 模拟：
        # 1. Worker 正在 searching
        # 2. 用户已经请求取消
        # 3. Worker 随后崩溃
        # 4. lease 已经过期
        await db.pool.execute(
            """
            INSERT INTO research_tasks (
                id,
                owner_id,
                query,
                status,
                attempts,
                cancel_requested_at,
                lease_owner,
                lease_expires_at
            )
            VALUES (
                $1,
                $2,
                $3,
                'searching',
                1,
                NOW(),
                'crashed-worker',
                NOW() - INTERVAL '1 minute'
            )
            """,
            task_id,
            owner_id,
            "真实库验证：取消请求后的 Worker 崩溃恢复",
        )

        print("已插入测试任务:", task_id)
        print("初始状态: searching")
        print("cancel_requested_at: 已设置")
        print("lease_expires_at: 已过期")

        # 模拟新的 Worker 启动时执行恢复扫描
        recovered_count = await repo.finalize_stale_cancel_requests()

        print("恢复数量:", recovered_count)

        row = await db.pool.fetchrow(
            """
            SELECT
                status,
                cancel_requested_at,
                lease_owner,
                lease_expires_at,
                completed_at
            FROM research_tasks
            WHERE id = $1
            """,
            task_id,
        )

        print("恢复后任务:", dict(row))

        event_rows = await db.pool.fetch(
            """
            SELECT event_type, status, payload
            FROM research_events
            WHERE task_id = $1
            ORDER BY sequence
            """,
            task_id,
        )

        print("事件记录:")
        for event in event_rows:
            print(dict(event))

        # 验收断言
        assert recovered_count == 1, (
            f"期望恢复 1 个任务，实际恢复 {recovered_count} 个"
        )

        assert row["status"] == "cancelled", (
            f"期望 status=cancelled，实际是 {row['status']}"
        )

        assert row["cancel_requested_at"] is None, (
            "cancel_requested_at 应该被清空"
        )

        assert row["lease_owner"] is None, (
            "lease_owner 应该被清空"
        )

        assert row["lease_expires_at"] is None, (
            "lease_expires_at 应该被清空"
        )

        assert row["completed_at"] is not None, (
            "completed_at 应该被设置"
        )

        assert any(
            event["event_type"] == "task.cancelled"
            and event["status"] == "cancelled"
            for event in event_rows
        ), "缺少 task.cancelled 事件"

        print("场景验收通过：")
        print("- 取消请求后的过期任务已恢复为 cancelled")
        print("- cancel_requested_at 已清空")
        print("- lease_owner 已清空")
        print("- lease_expires_at 已清空")
        print("- task.cancelled 事件已记录")

    finally:
        # 只删除本次测试任务
        try:
            await db.pool.execute(
                "DELETE FROM research_events WHERE task_id = $1",
                task_id,
            )
        except Exception:
            pass

        await db.pool.execute(
            "DELETE FROM research_tasks WHERE id = $1",
            task_id,
        )

        print("已清理测试数据:", task_id)
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())