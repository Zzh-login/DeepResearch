"""Web 多用户会话管理 —— 按 user_id（来自 JWT）隔离 ChatSession

与旧版区别：
  旧版按随机 sid（cookie UUID）隔离 → 删 cookie 数据找不回。
  新版按 user_id（来自账户/JWT）隔离 → 重新登录即恢复同一份数据。

职责：
  - 每个 user_id 一个 ChatSession（source="user_{user_id}"）
  - 落盘文件天然隔离：data/users/user_{uid}/chat_history.json 等
  - 后台 sweep 驱逐空闲会话，释放内存
"""

import asyncio
import time
from typing import Dict, Optional

from app.session.session import ChatSession


class SessionManager:
    """Web 端多用户会话管理器：按 user_id（来自 JWT）隔离并复用 ChatSession。

    每个 user_id 对应一个 ChatSession（source="user_{user_id}"），
    落盘文件天然按用户文件夹隔离。后台 sweep 任务定期驱逐
    超过 TTL 未活跃的会话以释放内存。
    """
    def __init__(self, ttl_seconds: int = 3600, sweep_interval: int = 300):
        """初始化按 user_id 隔离的会话管理器。

        参数:
            ttl_seconds - 会话空闲存活时长（秒），超过则被后台 sweep 驱逐。
            sweep_interval - 后台清理任务的执行间隔（秒）。
        """
        self._sessions: Dict[str, ChatSession] = {}
        self._last_active: Dict[str, float] = {}
        self._ttl = ttl_seconds
        self._sweep_interval = sweep_interval
        self._task: Optional[asyncio.Task] = None

    def get_or_create(self, user_id: str) -> ChatSession:
        """按 user_id 取已有会话；没有则创建独立会话"""
        now = time.time()
        session = self._sessions.get(user_id)
        if session is None:
            # 用 user_{id} 作为 source，磁盘文件按用户隔离
            session = ChatSession(source=f"user_{user_id}")
            self._sessions[user_id] = session
        self._last_active[user_id] = now
        return session

    async def sweep(self) -> None:
        """后台任务：驱逐超过 TTL 未活跃的会话并释放资源"""
        while True:
            await asyncio.sleep(self._sweep_interval)
            now = time.time()
            expired = [
                uid for uid, ts in self._last_active.items()
                if now - ts > self._ttl
            ]
            for uid in expired:
                session = self._sessions.pop(uid, None)
                self._last_active.pop(uid, None)
                if session is not None:
                    try:
                        session.close()
                    except Exception:
                        pass

    def start(self) -> None:
        """启动后台会话清理（sweep）任务；若任务尚未创建或已结束则重建。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.sweep())

    async def stop(self) -> None:
        """停止后台清理任务，并关闭、清空所有在线会话以释放资源。"""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        for session in self._sessions.values():
            try:
                session.close()
            except Exception:
                pass
        self._sessions.clear()
        self._last_active.clear()
