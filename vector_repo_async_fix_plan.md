# vector_repo.py 异步阻塞修复方案

## 1. 问题确认（已读源码核实）

- 文件：`infrastructure/memory/vector_repo.py`
- 根因：`add()` / `search()` 是 `async` 方法，但内部调用**同步** `_embed()`；`_embed()` 用同步 `OpenAI` 客户端发起网络请求。
- 后果：在 asyncio 事件循环内执行同步阻塞 I/O，会**冻结整个事件循环**。由于 ASR / TTS / 对话共用一个进程的事件循环，一次 embedding 调用会让所有并发任务排队卡死。
- 对照范本：`infrastructure/llm/client.py` 已用 `AsyncOpenAI` + `await`，写法正确，应与之对齐。

## 2. 修复策略

### 方案 B（推荐）：原生 AsyncOpenAI

将 embedding 客户端从同步 `OpenAI` 换成异步 `AsyncOpenAI`，`_embed()` 改为 `async def`，`add()`/`search()` 内用 `await`。与 `llm/client.py` 风格统一，是团队应效仿的「异步一致性」范本。

### 方案 A（备选，最小改动）：run_in_executor

保留同步 `OpenAI`，在 `add()`/`search()` 中把 `self._embed(...)` 改为
`await asyncio.get_event_loop().run_in_executor(None, self._embed, text)`，
把同步 I/O 丢到线程池，不阻塞事件循环。改动仅 2 行调用处，但风格不如原生异步优雅。

## 3. 精确改动点（以方案 B 为准）

### 3.1 顶部导入（第 31-34 行）

```python
# 原
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

# 新
try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None
```

### 3.2 客户端工厂（第 90-98 行）

```python
# 原
def _get_embed_client(self):
    if self._embed_client is None:
        import os
        key = self._openai_api_key or os.getenv("OPENAI_API_KEY", "")
        if OpenAI is None:
            raise ImportError("需要安装 openai：pip install openai")
        self._embed_client = OpenAI(api_key=key)
    return self._embed_client

# 新
def _get_embed_client(self):
    if self._embed_client is None:
        import os
        key = self._openai_api_key or os.getenv("OPENAI_API_KEY", "")
        if AsyncOpenAI is None:
            raise ImportError("需要安装 openai：pip install openai")
        self._embed_client = AsyncOpenAI(api_key=key)
    return self._embed_client
```

> 说明：客户端**创建**本身是同步且极快（不联网），保持同步方法即可，只把「网络请求那一步」异步化。

### 3.3 `_embed` 改为 async（第 114-127 行）

```python
# 原
def _embed(self, text: str) -> List[float]:
    client = self._get_embed_client()
    resp = client.embeddings.create(model=self._embedding_model, input=text)
    return resp.data[0].embedding

# 新
async def _embed(self, text: str) -> List[float]:
    client = self._get_embed_client()
    resp = await client.embeddings.create(model=self._embedding_model, input=text)
    return resp.data[0].embedding
```

### 3.4 调用处加 await

- `add()` 第 163 行：`embedding = self._embed(summary)` → `embedding = await self._embed(summary)`
- `search()` 第 220 行：`query_embedding = self._embed(query)` → `query_embedding = await self._embed(query)`

## 4. 验证方式

编写 `verify_vector_async.py`：

- 用 monkeypatch / 本地 mock 替换 `AsyncOpenAI.embeddings.create` 为 `async def` 假实现（不联网），断言返回结构一致；
- 在 asyncio 中并发跑 `asyncio.gather(add(), search(), asyncio.sleep(0))`，用时间戳证明 embedding 等待期间事件循环仍在调度 `sleep`（即**未被冻结**）；
- 语法编译 + 导入冒烟：`python -m py_compile` 全绿。

## 5. 风险评估

- 依赖：`AsyncOpenAI` 同属 `openai` 包，已在 venv（之前确认 openai 可导入），无需新增依赖。
- API 行为：`AsyncOpenAI.embeddings.create` 返回结构与同步版完全一致，仅多一个 `await`，调用点零逻辑变化。
- 运行影响：当前 `VectorMemory` 未被运行路径 import（asyncpg 在 venv 也缺失，靠惰性 import 兜底），改动**不影响现有 demo 行为**；但将来接记忆层时不会再踩并发坑。
- 注意点：`_get_embed_client` 仍为同步方法，客户端对象可在多协程间共享（OpenAI 客户端线程/协程安全），无需每次重建。

## 6. 顺带建议（可选，非本次范围）

当前 `VectorMemory` 还存在 SRP 违反（embedding 生成 + 向量存储耦合）与缺 delete/get/TTL 等问题，建议后续将 embedding 抽成独立 `EmbeddingService`。本次仅修阻塞问题，保持改动最小、可审查。
