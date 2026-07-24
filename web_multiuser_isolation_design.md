# Web 多用户隔离 — 解决方案设计（问题 #2）

> 对应 `code_review_report.md` 中第二个严重项：
> **Web 端全局单例，无多用户隔离**（`interfaces/web/app.py:22`）。

---

## 1. 问题现状（精确定位）

- `interfaces/web/app.py:22`：`_session = ChatSession(source="web")` 是**模块级全局单例**。
- 所有 REST 路由（`/persona`、`/persona-history`、`/api-config`、`/history`）和 WebSocket（`/ws`）全部读写这一个 `_session`。
- 后果：所有浏览器用户**共享同一段对话、同一个角色、同一份记忆、同一份 API 配置**。
  - 用户 A 改了角色，用户 B 立刻看到；
  - 用户 A 的对话历史用户 B 也能通过 `GET /history` 拉到。
- 既是不正确（串台），也是隐私缺陷。

---

## 2. 设计目标

1. 每个用户拥有独立的 `ChatSession`（独立对话 / 角色 / 记忆 / 配置）。
2. 对前端侵入最小（靠 cookie 自动携带 sid，前端不改代码）。
3. 不引入新依赖（标准库 `uuid` + FastAPI 自带能力足够）。
4. 不破坏现有单用户 demo 行为（单用户时退化为"一个 session"，体验和现在一致）。
5. 可演进到多进程 / 外部存储（存储层接口预留）。

---

## 3. 方案概述

引入一个 `session_id`（sid）：

- 浏览器首次访问 `/` 时，服务端生成 `sid = uuid4()` 并通过 `Set-Cookie` 下发给浏览器。
- 浏览器后续所有 REST 请求和 WebSocket 握手**自动携带该 cookie**（浏览器原生行为，前端无需改动）。
- 服务端用 `SessionManager`（内存字典 `dict[sid -> ChatSession]`）按 sid 取 / 建会话。

```
Browser --cookie sid--> SessionManager.get_or_create(sid) --> ChatSession(sid)
```

---

## 4. 核心组件

### 4.1 SessionManager（新增模块 `interfaces/web/session_manager.py`）

```python
class SessionManager:
    def __init__(self, idle_ttl: int = 3600):
        self._sessions: dict[str, ChatSession] = {}
        self._last_active: dict[str, float] = {}
        self._idle_ttl = idle_ttl

    def get_or_create(self, sid: str) -> ChatSession:
        if sid not in self._sessions:
            # 用 sid 派生 source，保证持久化文件也隔离
            self._sessions[sid] = ChatSession(source=f"web_{sid}")
        self._last_active[sid] = time.monotonic()
        return self._sessions[sid]

    def touch(self, sid: str): ...
    def remove(self, sid: str): ...   # 清理内存 + 落盘文件
    def sweep(self): ...              # 后台定时驱逐 idle > ttl 的会话
```

### 4.2 sid 来源

- REST：`request.cookies.get("sid")`
- WebSocket：`ws.cookies.get("sid")`（Starlette WebSocket 自带 `.cookies`）
- 兜底：若 cookie 缺失，临时生成并用于本次（保证不崩，但不跨请求持久）。

### 4.3 路由改造（`app.py`）

- `app.py:22` 的全局 `_session` → 改为 `session_manager = SessionManager()`。
- `GET /`（L25-27）：若无 sid cookie，生成并 `response.set_cookie("sid", sid)`。
- 所有 REST 路由：把 `_session.xxx()` 换成
  `session_manager.get_or_create(get_sid(request)).xxx()`。
- `/ws`（L77-103）：取 `sid = ws.cookies.get("sid")`，拿到独立 `session`，用
  `session.chat(...)`；断线**不立即删除**（保留以支持刷新页面复用），由 TTL 后台清理。
- `/ws/asr`（L104-107）：ASR 是无状态音频转写，可不绑定 session；如需审计可按 sid 打日志。

---

## 5. 关键决策点（务必评审）

### 5.1 持久化隔离（重要）

当前 `JsonChatMemory` 用 `source` 拼文件名（`persona_web.txt` 等）。若所有用户都用
`source="web"`，则 `set_persona` 会**互相覆盖同一个文件**，重启后所有人读到同一份——隔离不彻底。

- **决策（推荐）**：每个 session 用 `source=f"web_{sid}"`，文件天然隔离。
  代价：`data/` 下会按用户产生文件，需配合 `SessionManager.remove / sweep` 清理落盘文件。
- **替代（MVP）**：会话只存内存、不落盘（persona / 历史不持久化），最简单，但重启即丢。
  建议先用 MVP 跑通，再上落盘隔离。

### 5.2 内存与清理

- 全局字典随用户增长膨胀 → 必须有**后台清理**：`startup` 起一个 `asyncio` 任务周期性
  `sweep()` 驱逐空闲会话；驱逐时一并删除该 sid 的 `persona_*` / `history_*` 文件（若采用 5.1）。

### 5.3 会话内并发

同一用户开两个标签页 → 两个 WS 指向同一 session，`chat()` 可能被并发调用，而
`ChatSession` 对 `history` 的修改不是协程安全的。

- **决策**：在 `ChatSession` 内（或 `SessionManager` 按 session）加一把 `asyncio.Lock`，
  `chat()` 入口 `async with lock:` 串行化。

### 5.4 多进程限制（已知边界）

内存字典是**单进程**的。若用 `--workers 2` 或多机部署，会话不共享。

- 现阶段的 web 服务是单进程（`run_web.py` 单 uvicorn），不受影响。
- 若将来要多 worker / 多机，把 `SessionManager` 的存储换成 Redis（接口已抽象，替换实现即可）。
  设计文档里标注为"后续演进项"。

---

## 6. 改动清单（精确到文件 / 行）

| 文件 | 位置 | 改动 |
|------|------|------|
| `interfaces/web/session_manager.py` | 新增 | `SessionManager` 类（get_or_create / touch / remove / sweep + 可选锁） |
| `interfaces/web/app.py` | L22 | 删除全局 `_session`，改为 `session_manager = SessionManager()` |
| `interfaces/web/app.py` | L25-27 `GET /` | 无 sid cookie 时生成并 `set_cookie` |
| `interfaces/web/app.py` | L35-75 各 REST 路由 | `_session` → `session_manager.get_or_create(get_sid(request))` |
| `interfaces/web/app.py` | L77-103 `/ws` | 取 sid，用独立 session；断线不删、交 TTL |
| `interfaces/web/app.py` | L15-17 startup | 启动 `sweep` 后台任务 |
| `app/session/session.py` | `chat()` | 加 `asyncio.Lock` 串行化（防同会话并发） |

---

## 7. 验证方法

1. 起服务，开两个浏览器（或隐私窗口）分别访问 → 各自 `GET /history` 应为空且互不干扰。
2. 窗口 A `POST /persona` 设"语文老师"，窗口 B `GET /persona` 应**看不到** A 的设置。
3. 两个窗口各发一条不同问题，回拉 `/history` 应只含自己的对话。
4. 跑一段空闲 + 观察 `sweep` 是否回收（日志或内存计数）。

---

## 8. 已知限制

- 单进程内存存储（多 worker 需换 Redis）。
- 重启丢失内存会话（除非采用 5.1 落盘隔离方案）。
- 会话数量无硬上限（依赖 TTL + 监控）。

---

## 9. 与问题 #1 的关系

`vector_repo.py` 的异步阻塞（问题 #1）与多用户隔离（问题 #2）**叠加会放大危害**：
单用户时同步 embed 只是变慢；一旦多用户隔离 + 记忆层接上，A 的同步 embed 会冻住 B 的
整个会话。因此**建议两个问题一起修**——先修 #1 排雷，再做 #2 隔离。
