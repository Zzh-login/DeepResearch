# 第七阶段：统一会话历史、结构化研究计划与独立 Worker（一周实施手册）

> 适用项目：`E:\robot_system`。

> 前置条件：第六阶段代码已落地；全量自动测试至少 114 项通过；第六阶段真实
> 无知识库研究、带知识库研究、刷新恢复、重启恢复、取消和用户隔离已经验收。

> 本手册是第七阶段施工指南，不表示第七阶段代码已经写入项目。必须按顺序完成，
> 每一步验证通过后再进入下一步。

## 1. 第七阶段解决什么问题

第六阶段已经能完成 Deep Research，但还有五个工业化缺口：

1. 普通聊天历史仍主要保存在用户目录 JSON，研究任务保存在 PostgreSQL，两套历史割裂。
2. 模型生成的 3~5 个搜索问题只存在 LangGraph 内存，刷新后看不到研究计划和每个子任务状态。
3. 相同 POST 重试会创建重复研究任务，缺少幂等键和服务端并发配额。
4. ResearchWorker 与 FastAPI 在同一个 Python 进程；FastAPI 崩溃时 Worker 也停止。
5. 前端只能恢复最近一个报告，没有完整会话列表、研究历史和计划明细。

第七阶段完成后的目标架构：

```text
浏览器
  ├─ WebSocket 普通/知识库/混合/自动聊天
  ├─ REST 创建和查询研究任务
  └─ REST 管理会话、历史、计划和子任务
           │
           ▼
FastAPI API 进程
  ├─ 认证、参数校验、用户隔离
  ├─ 写入 conversations / chat_messages
  ├─ 写入 research_tasks pending
  └─ 检查独立 Worker 心跳
           │
           ▼
PostgreSQL
  ├─ 会话与消息
  ├─ 研究任务、计划、子任务、来源和报告
  ├─ 幂等键与用户并发配额
  └─ FOR UPDATE SKIP LOCKED 持久任务队列
           ▲
           │
独立 run_research_worker.py 进程
  ├─ 周期心跳
  ├─ 原子领取 pending 任务
  ├─ 执行 LangGraph
  └─ 保存计划、进度、报告和消息
```

### 1.1 为什么本阶段不立刻加 Redis

当前任务量小，PostgreSQL 已有 `FOR UPDATE SKIP LOCKED` 原子领取能力，可以安全承担
单机持久队列。第七阶段先把 API 与 Worker 解耦，解决进程故障边界问题。等出现以下任一
条件，再迁移 Redis/RabbitMQ：

- 同时排队任务达到数百个。
- Worker 横向扩展到多台机器。
- 需要优先级队列、延迟任务或事件广播。
- PostgreSQL 队列轮询成为真实性能瓶颈。

### 1.2 本周范围

必须完成：

- PostgreSQL 统一会话、消息和消息引用。
- 研究任务可关联一个会话。
- 结构化研究计划和搜索子任务持久化。
- `Idempotency-Key` 防止重复创建研究任务。
- 每个用户最多一个 pending/running 研究任务。
- 独立 Worker 进程和数据库心跳。
- FastAPI 不再启动进程内 ResearchWorker。
- 研究历史、计划、子任务、报告刷新后全部可恢复。
- 会话和任务的用户隔离、删除规则和自动测试。

本周不做：

- 不迁移账户密码表；`users.db` 暂时保留。
- 不做 Redis、Kafka 或 Kubernetes。
- 不做多个模型角色互相讨论。
- 不做 DOCX/PDF 导出和定时研究。
- 不做自动判断“来源语义是否支持每一句话”的 Claim 级 NLI。
- 不删除旧 JSON 聊天历史；先双写和迁移，确认后再停旧存储。

---

## 2. 七天执行计划

### 第 1 天：备份、配置和数据库迁移

完成第 3~5 节。结束标准：迁移可重复执行，新表、外键、索引和约束存在。

### 第 2 天：会话仓储和历史 API

完成第 6~8 节。结束标准：用户只能查看自己的会话和消息。

### 第 3 天：结构化计划和子任务持久化

完成第 9~10 节。结束标准：每个研究查询都有数据库子任务记录和状态。

### 第 4 天：幂等创建和用户配额

完成第 11 节。结束标准：重复请求返回同一个 task ID，不产生两条任务。

### 第 5 天：独立 Worker 和心跳

完成第 12~14 节。结束标准：停止 FastAPI 后 Worker 继续研究；停止 Worker 后创建接口 503。

### 第 6 天：前端会话和研究历史

完成第 15 节。结束标准：刷新、重新登录后可以打开任意历史会话和研究报告。

### 第 7 天：自动测试、故障注入和真实验收

完成第 16~18 节。结束标准：全量回归、双进程恢复、幂等、隔离和删除全部通过。

---

## 3. 开工前检查和备份

```powershell
Set-Location E:\robot_system
.\venv\Scripts\Activate.ps1
python -m unittest discover -s tests -p "test_*.py" -v
python -m pip check
```

预期：至少 `Ran 114 tests`，最后 `OK`；`pip check` 输出
`No broken requirements found`。

备份：

```powershell
New-Item -ItemType Directory -Force data\stage7_backup | Out-Null
Copy-Item interfaces\web\app.py data\stage7_backup\web_app.py -Force
Copy-Item interfaces\web\frontend\index.html data\stage7_backup\index.html -Force
Copy-Item app\research\graph.py data\stage7_backup\research_graph.py -Force
Copy-Item app\research\worker.py data\stage7_backup\research_worker.py -Force
Copy-Item infrastructure\research\pg_repository.py data\stage7_backup\research_repo.py -Force
Copy-Item interfaces\web\research_routes.py data\stage7_backup\research_routes.py -Force
Copy-Item interfaces\web\chat_schemas.py data\stage7_backup\chat_schemas.py -Force
```

不要用备份覆盖当前项目。备份只用于逐文件对照。

---

## 4. 增加第七阶段配置

在 `infrastructure/config/settings.py` 的研究配置后增加：

```python
    research_worker_heartbeat_seconds: float = Field(default=5.0, gt=1, le=30)
    research_worker_stale_seconds: float = Field(default=20.0, gt=5, le=120)
    research_max_active_tasks_per_user: int = Field(default=1, ge=1, le=10)
    research_idempotency_ttl_hours: int = Field(default=24, ge=1, le=168)
    conversation_page_size: int = Field(default=30, ge=10, le=100)
    message_page_size: int = Field(default=100, ge=20, le=500)
```

在 `.env.example` 增加：

```dotenv
RESEARCH_WORKER_HEARTBEAT_SECONDS=5
RESEARCH_WORKER_STALE_SECONDS=20
RESEARCH_MAX_ACTIVE_TASKS_PER_USER=1
RESEARCH_IDEMPOTENCY_TTL_HOURS=24
CONVERSATION_PAGE_SIZE=30
MESSAGE_PAGE_SIZE=100
```

验证：

```powershell
python -c "from infrastructure.config.settings import get_settings; s=get_settings(); print(s.research_worker_stale_seconds, s.research_max_active_tasks_per_user)"
```

预期：`20.0 1`。

---

## 5. 数据库迁移

新建 `migrations/003_stage7_conversations_and_plans.sql`，完整内容：

```sql
CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY,
    owner_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '新对话',
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_owner_updated
ON conversations(owner_id, archived, updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    requested_mode TEXT NOT NULL CHECK (requested_mode IN (
        'normal', 'knowledge', 'hybrid', 'auto', 'deep_research'
    )),
    resolved_mode TEXT CHECK (resolved_mode IN (
        'normal', 'knowledge', 'hybrid', 'deep_research'
    )),
    knowledge_base_id UUID NULL
        REFERENCES knowledge_bases(id) ON DELETE SET NULL,
    research_task_id UUID NULL
        REFERENCES research_tasks(id) ON DELETE SET NULL,
    route_metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation_created
ON chat_messages(conversation_id, created_at, id);

CREATE INDEX IF NOT EXISTS idx_chat_messages_owner_created
ON chat_messages(owner_id, created_at DESC);

CREATE TABLE IF NOT EXISTS message_citations (
    id UUID PRIMARY KEY,
    message_id UUID NOT NULL
        REFERENCES chat_messages(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    source_kind TEXT NOT NULL CHECK (source_kind IN ('knowledge', 'web')),
    source_id TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    UNIQUE(message_id, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_message_citations_message
ON message_citations(message_id, ordinal);

ALTER TABLE research_tasks
ADD COLUMN IF NOT EXISTS conversation_id UUID NULL
    REFERENCES conversations(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_research_tasks_conversation
ON research_tasks(conversation_id, created_at DESC);

CREATE TABLE IF NOT EXISTS research_plans (
    task_id UUID PRIMARY KEY
        REFERENCES research_tasks(id) ON DELETE CASCADE,
    scope TEXT NOT NULL,
    raw_plan JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS research_subtasks (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL
        REFERENCES research_tasks(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 1 AND 10),
    query TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    result_count INTEGER NOT NULL DEFAULT 0 CHECK (result_count >= 0),
    error_message TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(task_id, ordinal),
    UNIQUE(task_id, query)
);

CREATE INDEX IF NOT EXISTS idx_research_subtasks_task
ON research_subtasks(task_id, ordinal);

CREATE TABLE IF NOT EXISTS research_request_keys (
    owner_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    task_id UUID NOT NULL
        REFERENCES research_tasks(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(owner_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_research_request_keys_expires
ON research_request_keys(expires_at);

CREATE TABLE IF NOT EXISTS service_heartbeats (
    service_name TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('starting', 'running', 'stopping')),
    metadata JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 5.1 执行迁移

```powershell
Get-Content migrations\003_stage7_conversations_and_plans.sql |
  docker exec -i robot-pg psql -U postgres -d robot
```

重复执行一次，第二次也必须成功。

### 5.2 验证迁移

```powershell
docker exec robot-pg psql -U postgres -d robot -c "\d conversations"
docker exec robot-pg psql -U postgres -d robot -c "\d chat_messages"
docker exec robot-pg psql -U postgres -d robot -c "\d research_plans"
docker exec robot-pg psql -U postgres -d robot -c "\d research_subtasks"
docker exec robot-pg psql -U postgres -d robot -c "\d service_heartbeats"
```

必须确认 `owner_id` 索引、消息级联删除、知识库 `SET NULL`、研究任务
`SET NULL`、子任务两个唯一约束和心跳主键存在。

---

## 6. 新增会话领域模型

新建 `domain/conversation/__init__.py` 空文件。

新建 `domain/conversation/models.py`，完整内容：

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class Conversation:
    id: UUID
    owner_id: str
    title: str
    archived: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class StoredMessage:
    id: UUID
    conversation_id: UUID
    owner_id: str
    role: str
    content: str
    requested_mode: str
    resolved_mode: str | None
    knowledge_base_id: UUID | None
    research_task_id: UUID | None
    route_metadata: dict[str, Any] = field(default_factory=dict)
    citations: list[dict[str, Any]] = field(default_factory=list)
```

验证：

```powershell
python -m py_compile domain\conversation\models.py
```

---

## 7. 新增 PostgreSQL 会话仓储

新建 `infrastructure/conversation/__init__.py` 空文件。

新建 `infrastructure/conversation/pg_repository.py`，完整内容：

```python
import json
from uuid import UUID, uuid4

from infrastructure.database.postgres import PostgresDatabase


class PgConversationRepository:
    def __init__(self, database: PostgresDatabase, owner_id: str) -> None:
        self._database = database
        self._owner_id = owner_id

    async def create(self, title: str = "新对话") -> UUID:
        conversation_id = uuid4()
        clean_title = title.strip()[:100] or "新对话"
        async with self._database.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO conversations (id, owner_id, title)
                VALUES ($1, $2, $3)
                """,
                conversation_id,
                self._owner_id,
                clean_title,
            )
        return conversation_id

    async def get_owned(self, conversation_id: UUID) -> dict | None:
        async with self._database.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM conversations
                WHERE id=$1 AND owner_id=$2
                """,
                conversation_id,
                self._owner_id,
            )
        return dict(row) if row else None

    async def list(self, limit: int = 30) -> list[dict]:
        async with self._database.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, title, archived, created_at, updated_at
                FROM conversations
                WHERE owner_id=$1 AND archived=FALSE
                ORDER BY updated_at DESC
                LIMIT $2
                """,
                self._owner_id,
                limit,
            )
        return [dict(row) for row in rows]

    async def rename(self, conversation_id: UUID, title: str) -> bool:
        clean_title = title.strip()[:100]
        if not clean_title:
            return False
        async with self._database.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE conversations
                SET title=$3, updated_at=NOW()
                WHERE id=$1 AND owner_id=$2
                """,
                conversation_id,
                self._owner_id,
                clean_title,
            )
        return result == "UPDATE 1"

    async def delete(self, conversation_id: UUID) -> bool:
        async with self._database.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM conversations WHERE id=$1 AND owner_id=$2",
                conversation_id,
                self._owner_id,
            )
        return result == "DELETE 1"

    async def add_message(
        self,
        conversation_id: UUID,
        role: str,
        content: str,
        requested_mode: str,
        resolved_mode: str | None,
        knowledge_base_id: UUID | None = None,
        research_task_id: UUID | None = None,
        route_metadata: dict | None = None,
        citations: list[dict] | None = None,
    ) -> UUID:
        message_id = uuid4()
        citation_rows = []
        for ordinal, citation in enumerate(citations or [], 1):
            source_id = str(citation.get("source_id", "")).strip()
            source_kind = "web" if source_id.startswith("W") else "knowledge"
            citation_rows.append(
                (
                    uuid4(), message_id, ordinal, source_kind,
                    source_id, json.dumps(citation, ensure_ascii=False),
                )
            )

        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                owned = await conn.fetchval(
                    "SELECT 1 FROM conversations WHERE id=$1 AND owner_id=$2",
                    conversation_id,
                    self._owner_id,
                )
                if not owned:
                    raise LookupError("会话不存在或无权访问")
                await conn.execute(
                    """
                    INSERT INTO chat_messages (
                        id, conversation_id, owner_id, role, content,
                        requested_mode, resolved_mode, knowledge_base_id,
                        research_task_id, route_metadata
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
                    """,
                    message_id,
                    conversation_id,
                    self._owner_id,
                    role,
                    content,
                    requested_mode,
                    resolved_mode,
                    knowledge_base_id,
                    research_task_id,
                    json.dumps(route_metadata or {}, ensure_ascii=False),
                )
                if citation_rows:
                    await conn.executemany(
                        """
                        INSERT INTO message_citations (
                            id, message_id, ordinal, source_kind,
                            source_id, payload
                        ) VALUES ($1,$2,$3,$4,$5,$6::jsonb)
                        """,
                        citation_rows,
                    )
                await conn.execute(
                    """
                    UPDATE conversations SET updated_at=NOW()
                    WHERE id=$1 AND owner_id=$2
                    """,
                    conversation_id,
                    self._owner_id,
                )
        return message_id

    async def list_messages(
        self,
        conversation_id: UUID,
        limit: int = 100,
    ) -> list[dict]:
        async with self._database.pool.acquire() as conn:
            owned = await conn.fetchval(
                "SELECT 1 FROM conversations WHERE id=$1 AND owner_id=$2",
                conversation_id,
                self._owner_id,
            )
            if not owned:
                raise LookupError("会话不存在或无权访问")
            rows = await conn.fetch(
                """
                SELECT m.*,
                       COALESCE(
                           jsonb_agg(c.payload ORDER BY c.ordinal)
                           FILTER (WHERE c.id IS NOT NULL),
                           '[]'::jsonb
                       ) AS citations
                FROM chat_messages m
                LEFT JOIN message_citations c ON c.message_id=m.id
                WHERE m.conversation_id=$1 AND m.owner_id=$2
                GROUP BY m.id
                ORDER BY m.created_at, m.id
                LIMIT $3
                """,
                conversation_id,
                self._owner_id,
                limit,
            )
        return [dict(row) for row in rows]
```

这里所有读取、更新和删除都同时过滤 `owner_id`。只按 UUID 查询属于越权漏洞，不能省略。

---

## 8. 新增会话 Schema 和 REST 路由

新建 `interfaces/web/conversation_schemas.py`：

```python
from pydantic import BaseModel, Field, field_validator


class ConversationCreate(BaseModel):
    title: str = Field(default="新对话", max_length=100)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return value.strip() or "新对话"


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=100)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("会话标题不能为空")
        return clean
```

新建 `interfaces/web/conversation_routes.py`：

```python
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.routing import APIRoute

from infrastructure.auth.dependency import get_current_user
from infrastructure.conversation.pg_repository import PgConversationRepository
from interfaces.web.conversation_schemas import (
    ConversationCreate,
    ConversationRename,
)


class ConversationDatabaseRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def guarded(request: Request):
            try:
                return await original(request)
            except (asyncpg.PostgresError, OSError, ConnectionError) as exc:
                raise HTTPException(503, "会话服务暂不可用") from exc

        return guarded


router = APIRouter(
    prefix="/api/conversations",
    tags=["conversations"],
    route_class=ConversationDatabaseRoute,
)


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(400, "无效的会话 ID") from None


def _repo(request: Request, user_id: str) -> PgConversationRepository:
    database = getattr(request.app.state, "database", None)
    if database is None or database.pool is None:
        raise HTTPException(503, "会话服务暂不可用")
    return PgConversationRepository(database, user_id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreate,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    conversation_id = await _repo(request, user_id).create(body.title)
    return {"id": str(conversation_id), "title": body.title}


@router.get("")
async def list_conversations(
    request: Request,
    user_id: str = Depends(get_current_user),
):
    settings = request.app.state.settings
    rows = await _repo(request, user_id).list(settings.conversation_page_size)
    return [
        {
            "id": str(row["id"]),
            "title": row["title"],
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }
        for row in rows
    ]


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    try:
        rows = await _repo(request, user_id).list_messages(
            _uuid(conversation_id),
            request.app.state.settings.message_page_size,
        )
    except LookupError:
        raise HTTPException(404, "会话不存在") from None
    return [
        {
            **row,
            "id": str(row["id"]),
            "conversation_id": str(row["conversation_id"]),
            "knowledge_base_id": (
                str(row["knowledge_base_id"])
                if row.get("knowledge_base_id") else None
            ),
            "research_task_id": (
                str(row["research_task_id"])
                if row.get("research_task_id") else None
            ),
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]


@router.patch("/{conversation_id}")
async def rename_conversation(
    conversation_id: str,
    body: ConversationRename,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    if not await _repo(request, user_id).rename(
        _uuid(conversation_id), body.title
    ):
        raise HTTPException(404, "会话不存在")
    return {"ok": True, "title": body.title}


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    if not await _repo(request, user_id).delete(_uuid(conversation_id)):
        raise HTTPException(404, "会话不存在")
```

在 `interfaces/web/app.py` import 区增加：

```python
from interfaces.web.conversation_routes import router as conversation_router
```

在现有 `app.include_router(research_router)` 后增加：

```python
app.include_router(conversation_router)
```

在 `lifespan()` 第一行设置配置，现有 `settings = get_settings()` 后增加：

```python
app.state.settings = settings
```

---

## 9. 让聊天请求携带 conversation_id

修改 `interfaces/web/chat_schemas.py`。

在 `ChatRequest` 的 `text` 后增加：

```python
    conversation_id: UUID
```

完整的关键结构应为：

```python
class ChatRequest(BaseModel):
    action: Literal["chat"] = "chat"
    text: str = Field(min_length=1, max_length=10000)
    conversation_id: UUID
    mode: ChatMode = ChatMode.NORMAL
    knowledge_base_id: UUID | None = None
    tts_mode: Literal["cloud", "local"] = "cloud"
    agent_mode: bool = True
```

### 9.1 WebSocket 写入用户消息和最终回答

在 `interfaces/web/app.py` 的 WebSocket 函数中，成功得到 `chat_request` 后、调用
`orchestrator.run()` 前增加：

```python
            conversation_repo = PgConversationRepository(
                ws.app.state.database,
                user_id,
            )
            if await conversation_repo.get_owned(
                chat_request.conversation_id
            ) is None:
                await ws.send_json(
                    {
                        "type": "chat.error",
                        "mode": chat_request.mode.value,
                        "code": "conversation_not_found",
                        "message": "会话不存在或无权访问",
                    }
                )
                continue

            await conversation_repo.add_message(
                conversation_id=chat_request.conversation_id,
                role="user",
                content=chat_request.text,
                requested_mode=chat_request.mode.value,
                resolved_mode=None,
                knowledge_base_id=chat_request.knowledge_base_id,
            )
```

在 `event["type"] == "chat.done"` 且没有错误时，发送 TTS 前增加：

```python
                        await conversation_repo.add_message(
                            conversation_id=chat_request.conversation_id,
                            role="assistant",
                            content=event.get("text", ""),
                            requested_mode=chat_request.mode.value,
                            resolved_mode=event.get("mode"),
                            knowledge_base_id=chat_request.knowledge_base_id,
                            route_metadata={
                                "route_source": event.get("route_source"),
                                "route_confidence": event.get("route_confidence"),
                                "route_reason": event.get("route_reason"),
                            },
                            citations=event.get("citations", []),
                        )
```

在 `interfaces/web/app.py` import 区增加：

```python
from infrastructure.conversation.pg_repository import PgConversationRepository
```

注意：只在最终 `chat.done` 保存 assistant 消息，不能把每个流式 token 写成一条消息。
模型生成中途失败时，保留用户消息但不写伪造的 assistant 成功消息。

### 9.2 旧 JSON 历史迁移策略

第七阶段上线时不要立即删除 `data/users/user_*/chat_history.json`。采用以下顺序：

1. 新消息从上线时刻开始写 PostgreSQL。
2. `/history` 暂时保留，作为旧历史只读入口。
3. 编写一次性迁移脚本，把旧 JSON 导入一个“历史导入”会话。
4. 对比消息数量和内容哈希。
5. 验收一周后再停止旧 JSON 写入。

本阶段不要双写两套存储超过一周，否则任一写入失败都会造成历史分叉。

---

## 10. 持久化研究计划和子任务

在 `infrastructure/research/pg_repository.py` 的类末尾增加以下完整方法：

```python
    async def replace_plan(
        self,
        task_id: UUID,
        scope: str,
        queries: list[str],
    ) -> None:
        raw_plan = {"scope": scope, "queries": queries}
        rows = [
            (uuid4(), task_id, ordinal, query)
            for ordinal, query in enumerate(queries, 1)
        ]
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO research_plans (
                        task_id, scope, raw_plan
                    ) VALUES ($1,$2,$3::jsonb)
                    ON CONFLICT (task_id) DO UPDATE SET
                        scope=EXCLUDED.scope,
                        raw_plan=EXCLUDED.raw_plan,
                        updated_at=NOW()
                    """,
                    task_id,
                    scope,
                    json.dumps(raw_plan, ensure_ascii=False),
                )
                await conn.execute(
                    "DELETE FROM research_subtasks WHERE task_id=$1",
                    task_id,
                )
                await conn.executemany(
                    """
                    INSERT INTO research_subtasks (
                        id, task_id, ordinal, query
                    ) VALUES ($1,$2,$3,$4)
                    """,
                    rows,
                )

    async def update_subtask(
        self,
        task_id: UUID,
        ordinal: int,
        status: str,
        result_count: int = 0,
        error_message: str | None = None,
    ) -> None:
        async with self._database.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE research_subtasks
                SET status=$3,
                    result_count=$4,
                    error_message=$5,
                    started_at=CASE
                        WHEN $3='running' THEN COALESCE(started_at, NOW())
                        ELSE started_at
                    END,
                    completed_at=CASE
                        WHEN $3 IN ('completed','failed') THEN NOW()
                        ELSE completed_at
                    END,
                    updated_at=NOW()
                WHERE task_id=$1 AND ordinal=$2
                """,
                task_id,
                ordinal,
                status,
                result_count,
                error_message[:500] if error_message else None,
            )

    async def get_plan(self, task_id: UUID) -> dict | None:
        async with self._database.pool.acquire() as conn:
            plan = await conn.fetchrow(
                "SELECT * FROM research_plans WHERE task_id=$1",
                task_id,
            )
            subtasks = await conn.fetch(
                """
                SELECT ordinal, query, status, result_count,
                       error_message, started_at, completed_at
                FROM research_subtasks
                WHERE task_id=$1
                ORDER BY ordinal
                """,
                task_id,
            )
        if plan is None:
            return None
        return {
            "scope": plan["scope"],
            "queries": plan["raw_plan"].get("queries", []),
            "subtasks": [dict(row) for row in subtasks],
        }
```

### 10.1 修改 LangGraph 的 plan 节点

在 `app/research/graph.py` 的 `_plan()` 中，完成 3~5 个不同搜索词校验后、return 前增加：

```python
        scope = str(data.get("scope", "")).strip()
        await state["repo"].replace_plan(
            state["task_id"],
            scope,
            queries,
        )
        return {"plan_queries": queries, "scope": scope}
```

删除原来的：

```python
return {"plan_queries": queries, "scope": str(data.get("scope", ""))}
```

### 10.2 修改网页搜索节点

把 `_search_web()` 中原来的：

```python
raw_results = []
for query in state["plan_queries"]:
    raw_results.extend(await self._web.search(query))
```

替换为：

```python
        raw_results = []
        for ordinal, query in enumerate(state["plan_queries"], 1):
            await state["repo"].update_subtask(
                state["task_id"], ordinal, "running"
            )
            try:
                results = await self._web.search(query)
                raw_results.extend(results)
                await state["repo"].update_subtask(
                    state["task_id"],
                    ordinal,
                    "completed",
                    result_count=len(results),
                )
            except Exception as exc:
                logger.warning(
                    "Research subtask failed task=%s ordinal=%s: %s",
                    state["task_id"],
                    ordinal,
                    exc,
                )
                await state["repo"].update_subtask(
                    state["task_id"],
                    ordinal,
                    "failed",
                    error_message="搜索源暂不可用",
                )
```

允许单个子任务失败，其他子任务继续。只有最终没有任何可用网页来源时，整个研究才失败。

### 10.3 详情接口返回计划

在 `interfaces/web/research_routes.py` 的 `get_research_task()` 中：

```python
    plan = await repo.get_plan(task_uuid)
    result = _serialize(row, sources)
    result["plan"] = plan
    return result
```

替换原来的：

```python
return _serialize(row, sources)
```

---

## 11. 幂等创建、会话关联和并发配额

### 11.1 修改创建 Schema

在 `ResearchTaskCreate` 增加：

```python
    conversation_id: UUID
```

### 11.2 增加仓储异常

在 `infrastructure/research/pg_repository.py` 顶部、类定义前增加：

```python
class ResearchQuotaExceeded(RuntimeError):
    pass


class IdempotencyConflict(RuntimeError):
    pass
```

把原来的 `create_task()` 完整替换为：

```python
    async def create_task(
        self,
        owner_id: str,
        query: str,
        knowledge_base_id: UUID | None,
        conversation_id: UUID,
        idempotency_key: str,
        request_hash: str,
        max_active: int,
        ttl_hours: int,
    ) -> tuple[UUID, bool]:
        task_id = uuid4()
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow(
                    """
                    SELECT request_hash, task_id
                    FROM research_request_keys
                    WHERE owner_id=$1 AND idempotency_key=$2
                      AND expires_at > NOW()
                    FOR UPDATE
                    """,
                    owner_id,
                    idempotency_key,
                )
                if existing:
                    if existing["request_hash"] != request_hash:
                        raise IdempotencyConflict(
                            "同一个幂等键不能用于不同请求"
                        )
                    return existing["task_id"], True

                owned = await conn.fetchval(
                    """
                    SELECT 1 FROM conversations
                    WHERE id=$1 AND owner_id=$2
                    """,
                    conversation_id,
                    owner_id,
                )
                if not owned:
                    raise LookupError("会话不存在或无权访问")

                active = await conn.fetchval(
                    """
                    SELECT count(*) FROM research_tasks
                    WHERE owner_id=$1 AND status IN ('pending','running')
                    """,
                    owner_id,
                )
                if active >= max_active:
                    raise ResearchQuotaExceeded("已有研究任务正在运行")

                await conn.execute(
                    """
                    INSERT INTO research_tasks (
                        id, owner_id, knowledge_base_id,
                        conversation_id, query
                    ) VALUES ($1,$2,$3,$4,$5)
                    """,
                    task_id,
                    owner_id,
                    knowledge_base_id,
                    conversation_id,
                    query,
                )
                await conn.execute(
                    """
                    INSERT INTO chat_messages (
                        id, conversation_id, owner_id, role, content,
                        requested_mode, resolved_mode, research_task_id
                    ) VALUES ($1,$2,$3,'user',$4,
                              'deep_research','deep_research',$5)
                    """,
                    uuid4(),
                    conversation_id,
                    owner_id,
                    query,
                    task_id,
                )
                await conn.execute(
                    """
                    INSERT INTO research_request_keys (
                        owner_id, idempotency_key, request_hash,
                        task_id, expires_at
                    ) VALUES (
                        $1,$2,$3,$4,NOW() + make_interval(hours => $5)
                    )
                    """,
                    owner_id,
                    idempotency_key,
                    request_hash,
                    task_id,
                    ttl_hours,
                )
        return task_id, False
```

### 11.3 修改创建路由

在 `interfaces/web/research_routes.py` 增加 import：

```python
import hashlib

from fastapi import Header
from infrastructure.research.pg_repository import (
    IdempotencyConflict,
    PgResearchRepository,
    ResearchQuotaExceeded,
)
```

把 `create_research_task()` 完整替换为：

```python
@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_research_task(
    body: ResearchTaskCreate,
    request: Request,
    idempotency_key: str = Header(
        min_length=16,
        max_length=100,
        alias="Idempotency-Key",
    ),
    user_id: str = Depends(get_current_user),
):
    database = _database(request)
    settings = request.app.state.settings

    repo = PgResearchRepository(database)
    if not await repo.has_healthy_worker(
        settings.research_worker_stale_seconds
    ):
        raise HTTPException(503, "研究任务处理器尚未就绪")

    conversation_repo = PgConversationRepository(database, user_id)
    if await conversation_repo.get_owned(body.conversation_id) is None:
        raise HTTPException(404, "会话不存在或无权访问")

    if body.knowledge_base_id is not None:
        kb_repo = PgKnowledgeRepository(database, user_id)
        if await kb_repo.get_knowledge_base(body.knowledge_base_id) is None:
            raise HTTPException(404, "知识库不存在或无权访问")

    normalized = json.dumps(
        {
            "query": body.query,
            "knowledge_base_id": (
                str(body.knowledge_base_id)
                if body.knowledge_base_id else None
            ),
            "conversation_id": str(body.conversation_id),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    request_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    try:
        task_id, reused = await repo.create_task(
            owner_id=user_id,
            query=body.query,
            knowledge_base_id=body.knowledge_base_id,
            conversation_id=body.conversation_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            max_active=settings.research_max_active_tasks_per_user,
            ttl_hours=settings.research_idempotency_ttl_hours,
        )
    except LookupError:
        raise HTTPException(404, "会话不存在或无权访问") from None
    except ResearchQuotaExceeded as exc:
        raise HTTPException(409, str(exc)) from exc
    except IdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc

    return {
        "id": str(task_id),
        "status": "pending",
        "reused": reused,
    }
```

这里的 `Idempotency-Key` 由浏览器对一次“点击发送”生成，并在网络重试时复用；用户
再次主动点击“重新研究”时必须生成新键。

### 11.4 完成研究时原子写入会话报告

研究用户消息已在 `create_task()` 的同一事务写入。最终报告也必须和任务完成状态在
同一事务写入，否则可能出现“任务 completed 但会话没有报告”。

把 `PgResearchRepository.complete_task()` 完整替换为：

```python
    async def complete_task(self, task_id: UUID, report: dict) -> None:
        citation_ids = list(dict.fromkeys(report.get("citation_ids", [])))
        message_id = uuid4()
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                task = await conn.fetchrow(
                    """
                    UPDATE research_tasks
                    SET status='completed', progress=100,
                        current_step='研究完成', report=$2::jsonb,
                        completed_at=NOW(), updated_at=NOW()
                    WHERE id=$1 AND status='running'
                    RETURNING owner_id, conversation_id
                    """,
                    task_id,
                    json.dumps(report, ensure_ascii=False),
                )
                if task is None or task["conversation_id"] is None:
                    return

                await conn.execute(
                    """
                    INSERT INTO chat_messages (
                        id, conversation_id, owner_id, role, content,
                        requested_mode, resolved_mode, research_task_id
                    ) VALUES ($1,$2,$3,'assistant',$4,
                              'deep_research','deep_research',$5)
                    """,
                    message_id,
                    task["conversation_id"],
                    task["owner_id"],
                    report.get("markdown", ""),
                    task_id,
                )

                if citation_ids:
                    sources = await conn.fetch(
                        """
                        SELECT source_id, source_type, title, url,
                               filename, excerpt, score, metadata
                        FROM research_sources
                        WHERE task_id=$1 AND source_id=ANY($2::text[])
                        """,
                        task_id,
                        citation_ids,
                    )
                    by_id = {row["source_id"]: dict(row) for row in sources}
                    rows = []
                    for ordinal, source_id in enumerate(citation_ids, 1):
                        source = by_id.get(source_id)
                        if source is None:
                            continue
                        payload = {
                            key: value
                            for key, value in source.items()
                            if key != "source_type"
                        }
                        rows.append(
                            (
                                uuid4(), message_id, ordinal,
                                source["source_type"], source_id,
                                json.dumps(payload, ensure_ascii=False,
                                           default=str),
                            )
                        )
                    if rows:
                        await conn.executemany(
                            """
                            INSERT INTO message_citations (
                                id, message_id, ordinal, source_kind,
                                source_id, payload
                            ) VALUES ($1,$2,$3,$4,$5,$6::jsonb)
                            """,
                            rows,
                        )

                await conn.execute(
                    """
                    UPDATE conversations SET updated_at=NOW()
                    WHERE id=$1 AND owner_id=$2
                    """,
                    task["conversation_id"],
                    task["owner_id"],
                )
```

`UPDATE ... WHERE status='running' RETURNING` 保证 cancelled 任务不能被完成逻辑覆盖；
`task is None` 时必须直接 return，不能继续插入报告消息。

---

## 12. 增加 Worker 心跳方法

在 `PgResearchRepository` 类末尾增加：

```python
    async def touch_worker(
        self,
        instance_id: str,
        status: str = "running",
    ) -> None:
        async with self._database.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO service_heartbeats (
                    service_name, instance_id, status, updated_at
                ) VALUES ('research-worker',$1,$2,NOW())
                ON CONFLICT (service_name) DO UPDATE SET
                    instance_id=EXCLUDED.instance_id,
                    status=EXCLUDED.status,
                    updated_at=NOW()
                """,
                instance_id,
                status,
            )

    async def has_healthy_worker(self, stale_seconds: float) -> bool:
        async with self._database.pool.acquire() as conn:
            return bool(
                await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM service_heartbeats
                        WHERE service_name='research-worker'
                          AND status='running'
                          AND updated_at > NOW()
                              - make_interval(secs => $1::double precision)
                    )
                    """,
                    stale_seconds,
                )
            )
```

---

## 13. 新增独立 Worker 启动入口

在项目根目录新建 `run_research_worker.py`，完整内容：

```python
import asyncio
import logging
import os
import socket
from contextlib import suppress
from uuid import uuid4

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
    repo = PgResearchRepository(database)
    graph = DeepResearchGraph(settings)
    worker = ResearchWorker(database, graph, settings)
    instance_id = f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"
    stop_event = asyncio.Event()

    await repo.touch_worker(instance_id, "starting")
    await worker.start()
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
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
        with suppress(Exception):
            await repo.touch_worker(instance_id, "stopping")
        await worker.stop()
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
```

### 13.1 为什么不用后台线程

这个文件必须作为独立 Python 进程启动。线程仍属于 FastAPI 进程，FastAPI 崩溃时线程
同样消失，无法解决第六阶段的故障边界。

---

## 14. 从 FastAPI 移除进程内 Worker

修改 `interfaces/web/app.py`：

1. 删除 `DeepResearchGraph` 和 `ResearchWorker` 两个 import。
2. 删除 `lifespan()` 中创建 `app.state.research_worker` 的整个代码块。
3. 删除 `finally` 中 `await research_worker.stop()` 的代码块。
4. 保留 `research_router`。

健康接口不能再读取进程内对象。先增加 import：

```python
from infrastructure.research.pg_repository import PgResearchRepository
```

在 `/health` 成功取得数据库连接后增加：

```python
        research_worker_ok = await PgResearchRepository(
            database
        ).has_healthy_worker(settings.research_worker_stale_seconds)
```

把返回值中的 `research_worker` 改为：

```python
"research_worker": "ok" if research_worker_ok else "stopped",
```

注意：`health()` 里需要取得 `settings = get_settings()`，不要引用不存在的局部变量。

### 14.1 正确启动顺序

终端 A：

```powershell
Set-Location E:\robot_system
.\venv\Scripts\Activate.ps1
python run_research_worker.py
```

终端 B：

```powershell
Set-Location E:\robot_system
.\venv\Scripts\Activate.ps1
python run_web.py
```

浏览器打开：

```text
http://127.0.0.1:5000/health
```

必须看到 `research_worker=ok`。只启动 FastAPI、不启动 Worker 时，读取历史仍可用，
但创建新研究任务必须返回 503。

---

## 15. 前端增加会话历史、研究计划和幂等键

修改文件：`interfaces/web/frontend/index.html`。

### 15.1 新增会话栏 HTML

在聊天主区域之前增加：

```html
<aside class="conversation-sidebar" aria-label="会话历史">
    <div class="conversation-toolbar">
        <strong>会话</strong>
        <button type="button" id="newConversationBtn" title="新建会话">+</button>
    </div>
    <div id="conversationList"></div>
</aside>
```

研究进度条后、报告前增加：

```html
<section id="researchPlan" class="research-plan" hidden>
    <h3>研究计划</h3>
    <p id="researchScope"></p>
    <ol id="researchSubtasks"></ol>
</section>
```

按钮应使用现有图标库时替换为 Lucide `plus`、`trash-2`、`pencil` 图标；本阶段
如果项目尚未加载 Lucide，可暂时保留 `+`，不要手绘 SVG。

### 15.2 新增 CSS

在现有 `<style>` 末尾增加：

```css
.conversation-sidebar {
    width: min(280px, 28vw);
    min-width: 220px;
    border-right: 1px solid #dfe2e8;
    overflow-y: auto;
}
.conversation-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 12px;
    border-bottom: 1px solid #dfe2e8;
}
.conversation-item {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: 8px;
    width: 100%;
    min-height: 40px;
    padding: 8px 12px;
    border: 0;
    border-bottom: 1px solid #eef0f3;
    background: #fff;
    text-align: left;
}
.conversation-item.active { background: #eef6f2; }
.conversation-title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.research-plan { border-top: 1px solid #dfe2e8; padding-top: 12px; }
.research-subtask-running { color: #8a5b00; }
.research-subtask-completed { color: #18794e; }
.research-subtask-failed { color: #b42318; }
@media (max-width: 760px) {
    .conversation-sidebar {
        width: 100%;
        min-width: 0;
        max-height: 180px;
        border-right: 0;
        border-bottom: 1px solid #dfe2e8;
    }
}
```

根据现有页面容器，把外层布局设置成 `display:grid`，桌面为
`280px minmax(0,1fr)`，移动端为单列。不要把会话栏做成嵌套卡片。

### 15.3 新增前端状态

在现有 DOM 常量区增加：

```javascript
const conversationList = document.getElementById("conversationList");
const newConversationBtn = document.getElementById("newConversationBtn");
const researchPlan = document.getElementById("researchPlan");
const researchScope = document.getElementById("researchScope");
const researchSubtasks = document.getElementById("researchSubtasks");

let activeConversationId = localStorage.getItem("activeConversationId") || "";
let pendingResearchKey = null;
```

### 15.4 新增会话函数

```javascript
async function createConversation(title = "新对话") {
    const response = await fetch("/api/conversations", {
        method: "POST",
        credentials: "include",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({title})
    });
    if (!response.ok) throw new Error("创建会话失败");
    const conversation = await response.json();
    activeConversationId = conversation.id;
    localStorage.setItem("activeConversationId", activeConversationId);
    await loadConversations();
    clearChatMessages();
    return conversation;
}

async function ensureConversation() {
    if (activeConversationId) return activeConversationId;
    const response = await fetch("/api/conversations", {
        credentials: "include"
    });
    if (!response.ok) throw new Error("读取会话失败");
    const conversations = await response.json();
    if (conversations.length) {
        activeConversationId = conversations[0].id;
        localStorage.setItem("activeConversationId", activeConversationId);
        return activeConversationId;
    }
    return (await createConversation()).id;
}

function clearChatMessages() {
    messages.textContent = "";
}

async function openConversation(conversationId) {
    activeConversationId = conversationId;
    localStorage.setItem("activeConversationId", conversationId);
    const response = await fetch(
        `/api/conversations/${conversationId}/messages`,
        {credentials: "include"}
    );
    if (!response.ok) throw new Error("读取会话消息失败");
    const items = await response.json();
    clearChatMessages();
    for (const item of items) {
        appendMessage(item.content, item.role, item.resolved_mode || item.requested_mode);
        if (item.role === "assistant" && item.citations?.length) {
            renderCitationCards(item.citations);
        }
    }
    await loadConversations();
}

async function loadConversations() {
    const response = await fetch("/api/conversations", {
        credentials: "include"
    });
    if (!response.ok) return;
    const items = await response.json();
    conversationList.textContent = "";
    for (const item of items) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "conversation-item";
        if (item.id === activeConversationId) button.classList.add("active");
        const title = document.createElement("span");
        title.className = "conversation-title";
        title.textContent = item.title;
        button.appendChild(title);
        button.addEventListener("click", () => openConversation(item.id));
        conversationList.appendChild(button);
    }
}

newConversationBtn.addEventListener("click", async () => {
    await createConversation();
});
```

若现有消息容器变量不叫 `messages`，替换为项目中真实 DOM 变量名。禁止直接粘贴后
保留未定义变量。

### 15.5 WebSocket 请求增加 conversation_id

找到发送 `action: "chat"` 的 JSON，在对象中增加：

```javascript
conversation_id: activeConversationId,
```

在发送前执行：

```javascript
await ensureConversation();
```

因此调用发送函数的事件处理器也必须是 `async`。

### 15.6 研究创建增加幂等键和会话 ID

把 `startResearch(query)` 中 POST 的关键部分改成：

```javascript
    await ensureConversation();
    pendingResearchKey = pendingResearchKey || crypto.randomUUID();
    const response = await fetch("/api/research-tasks", {
        method: "POST",
        credentials: "include",
        headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": pendingResearchKey
        },
        body: JSON.stringify({
            query,
            conversation_id: activeConversationId,
            knowledge_base_id: activeKnowledgeBaseId || null
        })
    });
```

POST 成功后设置：

```javascript
pendingResearchKey = null;
```

网络异常自动重试时不要清空，必须复用相同键；用户明确重新点击研究时生成新键。

### 15.7 显示计划和子任务

在 `showResearchTask(task)` 开头增加：

```javascript
    researchSubtasks.textContent = "";
    if (task.plan) {
        researchPlan.hidden = false;
        researchScope.textContent = task.plan.scope || "";
        for (const subtask of task.plan.subtasks || []) {
            const item = document.createElement("li");
            item.className = `research-subtask-${subtask.status}`;
            item.textContent = `${subtask.query} · ${subtask.status}`;
            researchSubtasks.appendChild(item);
        }
    } else {
        researchPlan.hidden = true;
        researchScope.textContent = "";
    }
```

所有内容使用 `textContent`，不能使用来源或模型文本构造 `innerHTML`。

### 15.8 初始化顺序

在 `init()` 认证成功后执行：

```javascript
await ensureConversation();
await loadConversations();
await openConversation(activeConversationId);
await loadResearchTasks();
```

先恢复会话，再恢复研究任务，避免报告显示在错误会话下。

---

## 16. 自动测试文件

新增以下文件：

```text
tests/test_conversation_repository.py
tests/test_conversation_routes.py
tests/test_research_idempotency.py
tests/test_research_plan_persistence.py
tests/test_research_worker_heartbeat.py
tests/test_stage7_frontend.py
```

### 16.1 `tests/test_research_plan_persistence.py` 完整内容

```python
import inspect
import unittest

from app.research.graph import DeepResearchGraph
from infrastructure.research.pg_repository import PgResearchRepository


class ResearchPlanPersistenceTests(unittest.TestCase):
    def test_plan_is_persisted_after_validation(self):
        source = inspect.getsource(DeepResearchGraph._plan)
        self.assertIn("replace_plan", source)
        self.assertIn("plan_queries", source)

    def test_each_search_updates_subtask(self):
        source = inspect.getsource(DeepResearchGraph._search_web)
        self.assertIn("update_subtask", source)
        self.assertIn('"running"', source)
        self.assertIn('"completed"', source)
        self.assertIn('"failed"', source)

    def test_repository_replaces_plan_transactionally(self):
        source = inspect.getsource(PgResearchRepository.replace_plan)
        self.assertIn("conn.transaction()", source)
        self.assertIn("DELETE FROM research_subtasks", source)
        self.assertIn("INSERT INTO research_subtasks", source)
```

### 16.2 `tests/test_research_worker_heartbeat.py` 完整内容

```python
import inspect
import unittest
from pathlib import Path

from infrastructure.research.pg_repository import PgResearchRepository


ROOT = Path(__file__).resolve().parents[1]


class ResearchWorkerHeartbeatTests(unittest.TestCase):
    def test_standalone_entry_exists(self):
        path = ROOT / "run_research_worker.py"
        self.assertTrue(path.exists())
        text = path.read_text(encoding="utf-8")
        self.assertIn("ResearchWorker", text)
        self.assertIn("heartbeat_loop", text)

    def test_health_check_uses_database_timestamp(self):
        source = inspect.getsource(PgResearchRepository.has_healthy_worker)
        self.assertIn("service_heartbeats", source)
        self.assertIn("updated_at", source)
        self.assertIn("stale_seconds", source)
```

### 16.3 `tests/test_stage7_frontend.py` 完整内容

```python
import unittest
from pathlib import Path


HTML = (
    Path(__file__).resolve().parents[1]
    / "interfaces" / "web" / "frontend" / "index.html"
).read_text(encoding="utf-8")


class Stage7FrontendTests(unittest.TestCase):
    def test_conversation_is_sent_with_chat(self):
        self.assertIn("conversation_id: activeConversationId", HTML)

    def test_research_uses_idempotency_key(self):
        self.assertIn('"Idempotency-Key": pendingResearchKey', HTML)
        self.assertIn("crypto.randomUUID()", HTML)

    def test_plan_is_rendered_with_text_content(self):
        self.assertIn("researchSubtasks.textContent", HTML)
        self.assertIn("item.textContent", HTML)

    def test_init_restores_conversation_before_research(self):
        block = HTML.split("async function init()", 1)[1]
        self.assertLess(
            block.index("await ensureConversation()"),
            block.index("await loadResearchTasks()"),
        )
```

### 16.4 必须自行补齐的行为测试

`test_conversation_repository.py` 必须用 fake pool/connection 验证：

- 所有读取、改名和删除 SQL 都有 `owner_id`。
- `add_message()` 在一个事务内写消息、引用和更新时间。
- 删除会话不会删除 research task，只把 `conversation_id` 设为 NULL。
- 用户 B 不能读取用户 A 的消息。

`test_conversation_routes.py` 必须验证：

- 非法 UUID 返回 400。
- 未登录返回 401。
- 不属于当前用户的会话返回 404。
- PostgreSQL 断线返回 503。
- 删除成功返回 204。

`test_research_idempotency.py` 必须验证：

- 相同用户、相同键、相同 body 返回同一 task ID。
- 相同键、不同 body 返回 409。
- 不同用户可以使用相同键。
- 用户已有 active task 时新键返回 409。
- 已完成任务不占 active 配额。

仅做 `inspect.getsource()` 不能代替这些行为测试；幂等和事务必须用 fake DB 或测试数据库
真实执行至少一次。

### 16.5 运行测试

```powershell
python -m compileall -q app domain infrastructure interfaces tests
python -m unittest tests.test_conversation_repository -v
python -m unittest tests.test_conversation_routes -v
python -m unittest tests.test_research_idempotency -v
python -m unittest tests.test_research_plan_persistence -v
python -m unittest tests.test_research_worker_heartbeat -v
python -m unittest tests.test_stage7_frontend -v
python -m unittest discover -s tests -p "test_*.py" -v
python -m pip check
```

阶段七完成时，预计全量测试不少于 130 项；数量不是唯一标准，所有行为测试必须通过。

---

## 17. 详细真实验收步骤

### 17.1 启动前检查

```powershell
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
docker exec robot-pg psql -U postgres -d robot -c "SELECT 1;"
```

PostgreSQL 必须为 Up，端口应显示宿主机 `15432` 映射容器 `5432`。

### 17.2 独立 Worker 心跳

只启动 `python run_research_worker.py`，不启动 FastAPI，然后执行：

```powershell
docker exec robot-pg psql -U postgres -d robot -c "SELECT service_name,instance_id,status,NOW()-updated_at AS age FROM service_heartbeats;"
```

预期：`research-worker | ... | running | 小于 10 秒`。

停止 Worker，等待 `RESEARCH_WORKER_STALE_SECONDS + 5` 秒，再启动 FastAPI。创建研究任务
必须返回 503，不能写入永远 pending 的任务。

### 17.3 FastAPI 崩溃不影响 Worker

1. 分别启动 Worker 和 FastAPI。
2. 创建一个研究任务，看到 running。
3. 只停止 `run_web.py`，不要停止 Worker。
4. 等待研究完成。
5. 直接查 PostgreSQL。

```powershell
docker exec robot-pg psql -U postgres -d robot -c "SELECT status,progress,current_step FROM research_tasks ORDER BY created_at DESC LIMIT 1;"
```

预期：即使 FastAPI 已停止，任务仍变为 `completed | 100 | 研究完成`。重新启动 FastAPI 后
前端可以恢复报告。

### 17.4 会话持久化

1. 新建“机器学习讨论”会话。
2. 分别发送普通聊天、严格知识库和混合问答。
3. 刷新页面并重新登录。
4. 打开该会话。

预期：用户和 assistant 消息顺序不变；每条消息保留 requested/resolved mode；知识库引用
仍可显示；不会重复回放旧 JSON 和 PostgreSQL 两份相同消息。

数据库核对：

```powershell
docker exec robot-pg psql -U postgres -d robot -c "SELECT role,requested_mode,resolved_mode,left(content,30) FROM chat_messages ORDER BY created_at DESC LIMIT 10;"
```

### 17.5 研究计划持久化

创建研究任务，进入 running 后刷新。

预期：页面显示 scope 和 3~5 个搜索子任务；状态从 pending 到 running，再到
completed/failed；单个搜索词失败不会让其他子任务消失。

```powershell
docker exec robot-pg psql -U postgres -d robot -c "SELECT ordinal,query,status,result_count FROM research_subtasks WHERE task_id=(SELECT id FROM research_tasks ORDER BY created_at DESC LIMIT 1) ORDER BY ordinal;"
```

必须有 3~5 行，ordinal 连续且 query 不重复。

### 17.6 幂等重试

浏览器开发者工具记录第一次 POST 的 `Idempotency-Key`，使用相同 header 和完全相同 body
再发一次。

预期：两次都是 202，第二次 `reused=true`，task ID 相同，数据库只有一条任务。

相同 key 改动 query 再请求，预期 409。使用新 key 且原任务仍 active，预期 409 配额错误。

### 17.7 用户隔离

用户 A 创建会话、聊天和研究；用户 B 尝试：

- 读取 A 的会话消息。
- 改名 A 的会话。
- 删除 A 的会话。
- 把 A 的 conversation_id 用于创建研究。

全部必须返回 404，不能泄露标题、消息、计划、来源或任务是否存在。

### 17.8 删除规则

删除一个包含已完成研究的会话。

预期：

- `chat_messages` 和 `message_citations` 级联删除。
- `research_tasks`、报告和 `research_sources` 保留。
- `research_tasks.conversation_id` 变为 NULL。
- 用户仍可从研究历史打开报告。

这条规则防止用户删聊天时误删耗时生成的研究成果。以后增加“同时删除报告”时必须做
二次确认。

### 17.9 故障恢复

分别测试：

1. Worker 停止，FastAPI 继续。
2. FastAPI 停止，Worker 继续。
3. PostgreSQL 短暂停止后恢复。
4. Worker 在任务 running 时被强制停止并重新启动。

正确结果：

- Worker 停止后新建研究 503，聊天历史读取仍可用。
- FastAPI 停止后研究继续。
- PostgreSQL 恢复后两个进程重新连接或给出明确错误，不泄露 DSN。
- Worker 重启后 running 恢复 pending，attempts 增加且不超过上限。

### 17.10 前六阶段回归

逐项执行登录、退出、普通聊天、ASR、TTS、知识库上传/删除、严格知识库、混合、自动、
Deep Research。最后运行全量测试。

---

## 18. 完成标准

- [ ] 第六阶段 114 项基线测试全部保留。
- [ ] `003` 迁移可重复执行。
- [ ] 会话、消息、引用、计划、子任务、幂等键和心跳表真实存在。
- [ ] 所有会话查询、改名、删除都过滤 owner_id。
- [ ] 普通/知识库/混合/自动消息写入同一会话时间线。
- [ ] 研究任务关联会话，研究报告完成后可从会话和研究历史访问。
- [ ] 研究计划和 3~5 个子任务刷新后仍存在。
- [ ] 相同幂等请求只创建一个 task。
- [ ] 每个用户 active 研究任务数量受限。
- [ ] Worker 是独立进程，不由 FastAPI lifespan 创建。
- [ ] Worker 心跳过期时创建研究返回 503。
- [ ] FastAPI 停止时已领取研究继续执行。
- [ ] Worker 重启时任务可恢复且不重复完成。
- [ ] 删除会话不误删研究报告。
- [ ] 前端不使用模型文本拼接不可信 HTML。
- [ ] 用户 B 看不到用户 A 的会话、消息、计划、来源和报告。
- [ ] 全量测试不少于 130 项且全部 OK。
- [ ] `pip check` 无依赖冲突。
- [ ] 第 17 节真实验收全部记录结果。

### 18.1 新增文件清单

```text
migrations/003_stage7_conversations_and_plans.sql
domain/conversation/__init__.py
domain/conversation/models.py
infrastructure/conversation/__init__.py
infrastructure/conversation/pg_repository.py
interfaces/web/conversation_schemas.py
interfaces/web/conversation_routes.py
run_research_worker.py
tests/test_conversation_repository.py
tests/test_conversation_routes.py
tests/test_research_idempotency.py
tests/test_research_plan_persistence.py
tests/test_research_worker_heartbeat.py
tests/test_stage7_frontend.py
```

### 18.2 修改文件清单

```text
.env.example
infrastructure/config/settings.py
infrastructure/research/pg_repository.py
interfaces/web/chat_schemas.py
interfaces/web/research_schemas.py
interfaces/web/research_routes.py
interfaces/web/app.py
app/research/graph.py
app/research/worker.py
interfaces/web/frontend/index.html
```

### 18.3 停线规则

- 相同幂等键能创建两条任务：停线。
- 用户 B 能读到用户 A 的消息或计划：停线。
- FastAPI 仍在 lifespan 启动 ResearchWorker：停线。
- Worker 心跳过期仍允许创建任务：停线。
- 删除会话误删研究报告：停线。
- 研究计划只在前端内存、刷新即消失：停线。
- 消息流式 token 被逐个写入数据库：停线。
- 新系统与旧 JSON 同一消息重复显示：停线。

---

## 19. 第七阶段之后

第七阶段完成后，系统具备“统一历史、可观察计划、幂等任务和独立 Worker”四个生产基础。
后续阶段再按实际需求增加：

1. Redis/RabbitMQ 多机队列与 Worker 横向扩展。
2. Claim 级语义引用验证，判断来源是否真正支持具体句子。
3. Planner、Searcher、Analyst、Writer、Verifier 多角色编排。
4. DOCX/PDF 导出、定时研究和通知。
5. 用 PostgreSQL 统一账户系统，淘汰 SQLite 用户表。
6. 配额计费、审计日志、指标监控、备份恢复和数据保留策略。

不要在第七阶段同时实现这些内容。先把任务边界、历史一致性和进程故障边界做正确，
再增加更多 Agent 角色，否则系统会更复杂但不可追踪。
