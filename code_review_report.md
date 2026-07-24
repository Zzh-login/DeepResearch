# robot_system 全量代码质量审查报告

> 审查人：高级开发工程师（代码质量把控）
> 审查范围：`app/` `domain/` `infrastructure/` `interfaces/` 全部源码（34 个 .py，约 3200 行，排除 venv）+ `data/` 配置 + `schema.sql` + 根入口
> 审查方式：逐文件通读 + 全量 `py_compile` 语法编译 + 依赖导入冒烟验证
> 审查日期：2026-07-18

---

## 0. 总体结论

**架构分层清晰、Prompt 工程与 Validator 的设计颇为规范，是当前项目最大的亮点。** 但"外部服务集成层（infrastructure）"与"记忆/RAG 链路"目前处于**半成品状态**：要么没接线，要么有真实的并发性能缺陷。整体处于"能跑通 CLI/Web 单用户 demo，但未达生产级"的水平。

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构分层 / 依赖倒置 | 🟢 良好 | interfaces→app→domain→infrastructure 边界清晰，`ChatMemory` 抽象到位 |
| 配置驱动 / 可维护性 | 🟢 良好 | 人设/规则/格式全外挂 YAML；Validator checker 字典注册（OCP） |
| 代码正确性（已跑通路径） | 🟢 良好 | 全量编译通过；session 编排逻辑自洽 |
| 并发 / 异步质量 | 🔴 严重 | `vector_repo` 同步阻塞事件循环；Web 全局单例无多用户隔离 |
| 记忆/RAG 完整性 | 🟠 中等 | PG 记忆层是"画了接口没接线"；Vector 缺删改 |
| 工程化（依赖/测试/可观测） | 🔴 严重 | requirements 缺核心包；零测试套件；无调用追踪 |

**本次会话已修复的（✅ 已验证）**：
- `set_persona` 断点（运行时角色切换不生效）→ 加 `persona_override` 打通，验证通过
- `builder.py` 冗余 `if` 死代码 → 删除
- `_check_format` 潜伏 bug（校验错误对象）→ 删除并验证兜底不受影响

---

## 🔴 严重（必须修，影响生产可用性）

### S1. vector_repo.py：async 方法内调用同步 OpenAI 客户端，阻塞事件循环
**位置**：`infrastructure/memory/vector_repo.py:32`（`from openai import OpenAI`，同步客户端）+ `add()` / `search()` 内 `self._embed(...)` 同步网络 I/O。

**问题**：在 `async def add/search` 中直接调用**同步** embedding 请求，会**阻塞整个 asyncio 事件循环**。本项目 ASR（WebSocket）、LLM、TTS 共享同一事件循环；一旦并发，一次 embedding 调用会让整轮对话、语音流全卡住。对比 `llm/client.py` 用的是 `AsyncOpenAI`（正确做法），`vector_repo` 用了同步 `OpenAI`（错误做法）——同一个项目两种标准，正是团队需要统一的反面教材。

**修复**：① 改用 `AsyncOpenAI`；或 ② 用 `loop.run_in_executor` 把同步调用丢线程池。建议对齐 `LLMClient` 用 `AsyncOpenAI`。

### S2. Web 端会话是全局单例，无多用户隔离
**位置**：`interfaces/web/app.py` —— `_session = ChatSession(...)` 定义在模块顶层，作为全局变量被所有 HTTP/WS 请求共享。

**问题**：所有通过 Web 接入的用户**共享同一段对话历史、同一个角色设定、同一份长期记忆**。多用户场景下：A 用户能看到 B 用户的对话；A 切换角色会影响 B；记忆互相污染。这是**正确性与隐私双层缺陷**。

**修复**：按 `user_id`（或 session token）维护会话字典 `dict[str, ChatSession]`，请求时按身份取用，并加生命周期/超时清理。

### S3. requirements.txt 缺核心依赖，无法复现环境
**事实**：`entity.py` 用 `import yaml`，但 `requirements.txt` 未列 `PyYAML`；同时缺失 `openai`、`httpx`、`asyncpg`、`funasr`、`keyboard`、`pygame`、`fastapi`、`uvicorn`、`python-dotenv`。本地能跑只是因为 venv 里手动装过——**换台机器 `pip install -r requirements.txt` 后直接 import 崩溃**。

**修复**：从实际导入清单生成完整 requirements（建议用 `pip freeze` 后裁剪，或 `pipreqs`）。`yaml` 是必列项，优先级最高。

### S4. 零测试套件
**事实**：全项目除本次新增的 `verify_persona_fix.py`（冒烟脚本）外，**没有任何单元测试/集成测试**。架构文档中"LLM 调用追踪 ❌ 无"也印证了可观测性缺失。

**影响**：任何重构（如本次修断点）只能靠人工肉眼 + 临时脚本验证，回归风险高，团队无法安全演进。

**修复**：至少补三类测试：① `PromptBuilder.build()` 单元测试（已示范）；② `Validator` 后缀兜底测试；③ `set_persona` 链路集成测试。

---

## 🟠 中等（应修，影响正确性或扩展性）

### M1. PG 记忆层是"留了接口没接线"
**事实**：`kv_repo.py` / `vector_repo.py` 从未在运行路径被导入——`session.py:24` 只 `from infrastructure.storage.json_repo import JsonChatMemory`。且 `asyncpg` 在 venv 中 MISSING（惰性导入兜底，所以不崩）。即 KV 记忆、向量记忆、RAG **当前全部未生效**，与架构文档"预留接口"一致。

**修复**：明确路线图——要么接上（注入 `KVMemory`/`VectorMemory` 并补测试），要么在文档标注"暂未实现，不可用"，避免误以为 RAG 已上线。

### M2. VectorMemory 破坏单一职责（SRP）
**位置**：`vector_repo.py` 的 `_embed()` 在仓储类内直接调 OpenAI 生成向量。

**问题**：Embedding（外部 API）与向量存储（pgvector）耦合在同一类。想换本地 BGE 模型、加重试、加降级，都得改这个文件。

**修复**：拆 `EmbeddingService`（可换供应商、可重试、可异步）+ `VectorRepository`（只管增删改查）。

### M3. Vector 缺 delete / update / get / 过期机制
**事实**：`vector_repo` 只有 `add` / `search`，且注释标"预留接口未接前端"。

**问题**：记忆**只增不删**——无 TTL、无按 `user_id` 批量清理、无单条删除。生产环境里不仅占空间、拖慢检索，还无法满足"用户要求遗忘"的合规诉求。

**修复**：补齐 `delete(id)` / `delete_by_user(user_id)` / `get(id)` / `clear(user_id)`，并加可选 TTL。

### M4. CLI listener 用类变量做跨线程信号，脆弱
**位置**：`interfaces/cli/listener.py` —— 用类变量 `_esc_pressed` 在键盘监听线程与主线程间传递"退出"信号。

**问题**：全局可变状态 + 非线程安全 + 难以单元测试。`keyboard` 库本身也仅桌面环境可用，容器/服务器部署会失效。

**修复**：用 `threading.Event` 或队列传递信号；把输入源抽象成接口以便测试。

### M5. ASR（funasr/torch）在 Web 启动时被顶层导入
**事实**：`interfaces/web/app.py` 顶层 `from infrastructure.asr.client import handle_asr_ws`，funasr 依赖 torch，属重型导入。

**问题**：拉高 Web 冷启动时间；即便用户只走文字对话，也强制加载语音模型。

**修复**：按需惰性导入（首次访问 `/ws/asr` 时再 import），或拆独立语音服务进程。

---

## 🟡 轻微（建议修，品味 / 健壮性）

| 编号 | 位置 | 问题 | 建议 |
|------|------|------|------|
| L1 | `vector_repo.py` | 建了 `ivfflat(lists=100)` 索引但没 `SET ivfflat.probes`，ANN 召回率差 | 查询前设 `probes`（如 10） |
| L2 | `vector_repo.py:32` | Embedding 模型 `text-embedding-ada-002` 硬编码，供应商锁定 | 抽象成配置项，支持 BGE-small-zh 等 |
| L3 | `domain/memory/chat_memory.py` | `create_chat_memory` 工厂定义了但从未调用（死代码） | 要么接线使用，要么删除以免误导 |
| L4 | 全局 | 无调用耗时/失败率监控（LLM/ASR/TTS/embedding） | 加轻量计时 + 异常日志，便于定位瓶颈 |
| L5 | `format.yaml` 消费侧 | 三段式仅靠 system prompt 软约束，删除 `_check_format` 后无硬校验 | 业务强依赖结构时再补"正经"校验 |

---

## ✅ 亮点（值得团队保持 / 作为范本）

1. **分层与依赖倒置做得对**：`domain/memory/chat_memory.py` 用 ABC 定义抽象，存储实现可替换而上层零改动——这是教科书级的解耦。
2. **配置驱动 Prompt**：人设/规则/格式外挂 YAML，非程序员可改，注释意图清晰。
3. **Validator 的软硬约束分离 + checker 字典注册**：符合开闭原则（OCP），扩展新规则只加一行，是本项目的设计标杆。
4. **kv_repo.py 本身写得好**：异步连接池、UPSERT、惰性 import asyncpg —— 可作为 `vector_repo` 对齐的参考范本。
5. **惰性导入 asyncpg 的防御写法**：值得在团队内推广（模块不因可选依赖缺失而崩）。

---

## 📋 团队 DoD（完成定义）建议

针对"外部服务集成层"与"记忆链路"，达标线建议：
1. 异步一致性：所有外部 I/O 一律异步（对齐 `LLMClient`），禁止 async 内同步阻塞。
2. 依赖可复现：`requirements.txt` 完整，新环境 `pip install -r` 即可跑。
3. 记忆完整：增删改查 + TTL + 用户隔离三件套齐备后再称"接好 RAG"。
4. 多用户安全：Web 会话按用户隔离，无共享全局状态。
5. 可观测 + 可测：外部调用有耗时/失败日志；核心模块有单测（从 `verify_persona_fix.py` 起步）。

---

## 🗂️ 修复优先级排序（给排期参考）

| 优先级 | 项 | 工作量 | 风险 |
|--------|----|--------|------|
| P0 | S3 requirements 补全 | 0.5h | 阻断部署 |
| P0 | S1 vector_repo 异步化 | 2h | 并发卡死 |
| P0 | S2 Web 多用户隔离 | 4h | 隐私/正确 |
| P1 | S4 起步测试套件 | 1d | 回归安全 |
| P1 | M1/M3 记忆层接线或标注 | 2d | 功能缺失 |
| P2 | M2/M5 解耦与惰性导入 | 1d | 扩展性 |
| P3 | L1-L5 打磨项 | 0.5d | 健壮性 |

---

*本报告基于逐文件通读与全量语法编译（EXIT=0）得出。所有"未生效/未接线"结论均以"运行路径实际导入关系"为据，非凭文档推断。*
