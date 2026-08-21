import asyncio
import logging
import os
import socket
from contextlib import suppress
from uuid import uuid4

# ── 必须在任何 transformers / huggingface_hub 相关库导入之前设好离线环境变量 ──
# 与 run_web.py 相同的原因：这些库在首次 import 时就定死缓存目录(HF_HUB_CACHE)
# 和联网端点，之后 bge_m3._load_bge_m3 再用 os.environ 设置已无效。
# Worker 的 import 链(app.research.graph → langchain/langgraph)会提前拉起
# huggingface_hub，导致 BGE-M3 去默认缓存(C:\Users\...\.cache)找不到模型、
# 又直连 huggingface.co 超时报错。
os.environ["HF_HOME"] = "E:/robot_system/models/hf_cache"
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from app.research.graph import DeepResearchGraph
from app.research.worker import ResearchWorker
from infrastructure.config.settings import get_settings
from infrastructure.database.postgres import PostgresDatabase
from infrastructure.research.pg_repository import PgResearchRepository


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("research-worker")
WORKER_LOCK_KEY = 735_240_017


async def heartbeat_loop(repo, instance_id, seconds, stop_event):
    while not stop_event.is_set():
        try:
            await repo.touch_worker(instance_id, "running")
        except Exception:
            logger.exception("Research worker heartbeat failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass


async def main():
    settings = get_settings()
    if not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")

    database = PostgresDatabase(settings.pg_dsn)
    await database.connect()
    lock_connection = await database.pool.acquire()
    try:
        locked = await lock_connection.fetchval(
            "SELECT pg_try_advisory_lock($1)", WORKER_LOCK_KEY
        )
        if not locked:
            raise RuntimeError("已有 Research Worker 正在运行")

        repo = PgResearchRepository(database)
        graph = DeepResearchGraph(settings)
        worker = ResearchWorker(database, graph, settings)
        instance_id = f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"
        stop_event = asyncio.Event()
        heartbeat = None

        await repo.touch_worker(instance_id, "starting")
        await worker.start()
        if not worker.running:
            raise RuntimeError("Research Worker 启动失败")
        heartbeat = asyncio.create_task(
            heartbeat_loop(
                repo,
                instance_id,
                settings.research_worker_heartbeat_seconds,
                stop_event,
            )
        )
        logger.info("Research worker started: %s", instance_id)

        try:
            while worker.running:
                await asyncio.sleep(1)
        finally:
            stop_event.set()
            if heartbeat is not None:
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat
            with suppress(Exception):
                await repo.touch_worker(instance_id, "stopping")
            await worker.stop()
    finally:
        with suppress(Exception):
            await lock_connection.execute(
                "SELECT pg_advisory_unlock($1)", WORKER_LOCK_KEY
            )
        await database.pool.release(lock_connection)
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
