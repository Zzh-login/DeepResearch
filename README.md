# DeepResearch

DeepResearch 是一个面向知识检索和深度研究场景的 AI 助手，将普通对话、RAG 知识库、混合问答、自动路由、长期记忆、语音交互和可恢复的深度研究任务整合到一个 Web 应用中。

## 核心能力

- 普通聊天、严格知识库问答、混合问答、自动路由和深度研究五种模式
- TXT、MD、PDF、DOCX 文档上传、解析、分块、向量化和检索
- PostgreSQL + pgvector + BGE-M3 知识库检索
- 知识库证据与模型补充分离、引用校验和提示词污染防护
- LangGraph 深度研究工作流：计划、检索、阅读、写作、校验和自动修复
- 研究任务暂停、恢复、取消、重试、租约、心跳、检查点和异常恢复
- DeepSeek 模型网关：超时、重试、熔断、Token 限额、用量记录和调用审计
- 用户长期记忆、向量记忆、会话历史和模式记忆隔离
- WebSocket 流式响应、SenseVoiceSmall 本地 ASR、TTS 语音播报
- Markdown、DOCX 和 PDF 研究报告导出

## 技术栈

Python、FastAPI、Uvicorn、WebSocket、LangGraph、DeepSeek、PostgreSQL、pgvector、BGE-M3、SenseVoiceSmall、Docker、Microsoft Word。

## 快速启动

### 1. 安装依赖

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### 2. 配置环境变量

```powershell
Copy-Item .env.example .env
notepad .env
```

至少填写 `DEEPSEEK_API_KEY`、`PG_DSN` 和 `POSTGRES_PASSWORD`，其中 `PG_DSN` 中的密码要与 `POSTGRES_PASSWORD` 一致。不要把 `.env` 提交到 GitHub。

### 3. 启动数据库

```powershell
docker compose up -d postgres
```

首次部署时，按顺序执行 `migrations/` 下的 SQL 文件，具体命令见 [本地部署步骤](docs/LOCAL_DEPLOYMENT_STEPS.md)。

### 4. 启动服务

```powershell
python run_web.py
```

另开一个终端：

```powershell
python run_research_worker.py
```

浏览器打开 <http://127.0.0.1:18923/login>。Windows 用户也可以执行 `start_robot_system.bat` 一键启动。

## 测试

```powershell
python -m unittest discover -s tests -v
```

测试覆盖认证、会话、RAG、混合问答、引用边界、提示词污染、长期记忆、用户隔离、研究状态机、Worker 恢复、模型网关和报告导出。

## 目录结构

```text
app/             应用编排和 LangGraph 流程
domain/          领域模型、状态和接口
infrastructure/  PostgreSQL、模型、记忆、语音和外部服务适配器
interfaces/      FastAPI 路由、WebSocket 和前端
migrations/      数据库迁移脚本
scripts/         评测和验收脚本
tests/           自动化测试
evals/           RAG 和模式路由评测样例
docs/            部署、发布和架构说明
examples/        脱敏示例数据
```

## 公网演示

```powershell
cloudflared tunnel --url http://127.0.0.1:18923
```

Cloudflare Quick Tunnel 适合临时面试演示，地址每次启动都可能变化，不要写入代码或提交记录。

## 已知限制

- BGE-M3 和 SenseVoiceSmall 模型权重不随仓库发布。
- PDF 导出依赖本机 Microsoft Word。
- Cloudflare Quick Tunnel 不适合作为生产入口。

## License

本项目使用 MIT License，详见 [LICENSE](LICENSE)。
