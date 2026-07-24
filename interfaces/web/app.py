"""浏览器版入口：FastAPI + WebSocket —— 完整账户体系（JWT 认证 + user_id 隔离）"""

import json
import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Depends
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from infrastructure.asr.client import handle_asr_ws, load_model
from infrastructure.auth.user_repo import UserRepository
from infrastructure.auth.jwt_handler import create_token, verify_token
from infrastructure.auth.dependency import get_current_user
from interfaces.web.session_manager import SessionManager

app = FastAPI()

manager = SessionManager()
user_repo = UserRepository()


@app.on_event("startup")
async def startup():
    """应用启动钩子：加载 ASR 模型、启动会话管理器与 BGE-M3 后台预热任务。"""
    load_model()
    manager.start()
    # 预热 BGE-M3 模型（后台加载，不阻塞启动；加载完后首次 search 秒回）
    asyncio.create_task(_warmup_bge_m3())


async def _warmup_bge_m3():
    """后台预加载 BGE-M3 模型，避免首次搜索时阻塞事件循环"""
    try:
        from infrastructure.memory.vector_repo import _load_bge_m3
        await asyncio.to_thread(_load_bge_m3)
        print("[Warmup] BGE-M3 model loaded successfully")
    except Exception as e:
        print(f"[Warmup] BGE-M3 preload failed (will lazy-load on first search): {e}")


@app.on_event("shutdown")
async def shutdown():
    """应用关闭钩子：停止会话管理后台任务并释放所有在线会话资源。"""
    await manager.stop()


frontend_dir = Path(__file__).parent / "frontend"
app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")


# ═══════════════════════════════════════════════════════════════
# 中间件：每次认证请求后刷新 token cookie（活跃用户永不过期）
# ═══════════════════════════════════════════════════════════════

@app.middleware("http")
async def refresh_token_cookie(request: Request, call_next):
    """HTTP 中间件：对成功（2xx）响应刷新 JWT cookie，使活跃用户会话永不过期。"""
    response = await call_next(request)

    # 只对成功（2xx）响应刷新 token
    if 200 <= response.status_code < 300:
        token = request.cookies.get("token")
        if token:
            try:
                payload = verify_token(token)
                new_token = create_token(payload["sub"], payload["uname"])
                response.set_cookie(
                    "token", new_token,
                    max_age=7 * 86400,
                    httponly=True,
                    samesite="lax",
                    path="/",
                )
            except Exception:
                pass

    return response


# ═══════════════════════════════════════════════════════════════
# 公开路由（无需认证）
# ═══════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    """健康检查接口，返回 {"status": "ok"} 供探活/负载均衡使用。"""
    return {"status": "ok"}


@app.get("/")
async def root():
    """返回前端首页 index.html。"""
    return FileResponse(str(frontend_dir / "index.html"))


@app.get("/login")
async def login_page():
    """返回前端登录页 login.html。"""
    return FileResponse(str(frontend_dir / "login.html"))


# ═══════════════════════════════════════════════════════════════
# 认证路由
# ═══════════════════════════════════════════════════════════════

@app.post("/auth/register")
async def auth_register(request: Request):
    """用户注册：读取请求体中的 username/password 创建账户，成功则签发 JWT 并写入 cookie。

    参数: request - 含 username、password 的 JSON 请求体。
    返回: 成功返回 {"ok": True, "username": ...} 并设置 token cookie；用户名冲突等返回 400 错误。
    """
    data = await request.json()
    username = data.get("username", "")
    password = data.get("password", "")

    try:
        user = user_repo.register(username, password)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    token = create_token(user.user_id, user.username)
    resp = JSONResponse({"ok": True, "username": user.username})
    resp.set_cookie("token", token, max_age=7 * 86400, httponly=True, samesite="lax", path="/")
    return resp


@app.post("/auth/login")
async def auth_login(request: Request):
    """用户登录：校验 username/password，成功则签发 JWT 并写入 cookie。

    参数: request - 含 username、password 的 JSON 请求体。
    返回: 成功返回 {"ok": True, "username": ...} 并设置 token cookie；凭据错误返回 401。
    """
    data = await request.json()
    username = data.get("username", "")
    password = data.get("password", "")

    try:
        user = user_repo.login(username, password)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=401)

    token = create_token(user.user_id, user.username)
    resp = JSONResponse({"ok": True, "username": user.username})
    resp.set_cookie("token", token, max_age=7 * 86400, httponly=True, samesite="lax", path="/")
    return resp


@app.post("/auth/logout")
async def auth_logout():
    """退出登录：清除客户端的 token cookie。"""
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("token", path="/")
    return resp


@app.get("/auth/me")
async def auth_me(user_id: str = Depends(get_current_user)):
    """获取当前已认证用户的基本信息（user_id、username）。

    参数: user_id - 由 JWT 依赖注入解析出的用户标识。
    返回: 用户存在返回 {"user_id", "username"}；不存在返回 404。
    """
    user = user_repo.get_by_id(user_id)
    if user is None:
        return JSONResponse({"error": "用户不存在"}, status_code=404)
    return {"user_id": user.user_id, "username": user.username}


# ═══════════════════════════════════════════════════════════════
# 受保护路由（需要 JWT 认证 → 自动注入 user_id）
# ═══════════════════════════════════════════════════════════════

@app.get("/persona")
async def get_persona(user_id: str = Depends(get_current_user)):
    """获取当前用户的角色设定（persona）文本。"""
    return manager.get_or_create(user_id).get_persona()


@app.post("/persona")
async def set_persona(request: Request, user_id: str = Depends(get_current_user)):
    """更新当前用户的角色设定，返回更新后的 persona。

    参数: request - 含 persona 字段的 JSON 请求体。
    """
    data = await request.json()
    session = manager.get_or_create(user_id)
    session.set_persona(data.get("persona", ""))
    return session.get_persona()


@app.get("/persona-history")
async def get_persona_history(user_id: str = Depends(get_current_user)):
    """获取当前用户保存的历史角色设定列表。"""
    return {"items": manager.get_or_create(user_id).get_persona_history()}


@app.delete("/persona-history/{index}")
async def remove_persona_history(index: int, user_id: str = Depends(get_current_user)):
    """删除指定索引的历史角色设定，返回更新后的历史列表。

    参数: index - 要删除的历史角色索引（路径参数）。
    """
    session = manager.get_or_create(user_id)
    session.remove_persona_history(index)
    return {"items": session.get_persona_history()}


@app.get("/api-config")
async def get_api_config(user_id: str = Depends(get_current_user)):
    """获取当前用户的 API 配置（api_key、base_url、model）。"""
    return manager.get_or_create(user_id).get_api_config()


@app.post("/api-config")
async def set_api_config(request: Request, user_id: str = Depends(get_current_user)):
    """更新当前用户的 API 配置（api_key、base_url、model）。

    参数: request - 含 api_key、base_url、model 字段的 JSON 请求体。
    """
    data = await request.json()
    session = manager.get_or_create(user_id)
    session.set_api_config(
        api_key=data.get("api_key", ""),
        base_url=data.get("base_url", ""),
        model=data.get("model", ""),
    )
    return {"ok": True}


@app.get("/history")
async def get_history(user_id: str = Depends(get_current_user)):
    """获取当前用户的对话历史消息列表。"""
    return {"messages": manager.get_or_create(user_id).get_history()}


# ═══════════════════════════════════════════════════════════════
# WebSocket（cookie 自动携带 JWT，从中解码 user_id）
# ═══════════════════════════════════════════════════════════════

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """主聊天 WebSocket：从 cookie 中的 JWT 解析 user_id 取得会话，连接后先回放历史消息，
    再循环接收文本消息（调用 session.chat 并发送回复/语音）或 action="tts" 的重读语音请求。
    内部异常会被捕获并转换为错误文本回传，避免断开连接。
    """
    await ws.accept()

    user_id = "anon"
    token = ws.cookies.get("token")
    if token:
        try:
            payload = verify_token(token)
            user_id = payload["sub"]
        except Exception:
            pass

    session = manager.get_or_create(user_id)

    for m in session.get_history():
        await ws.send_text(json.dumps({"text": m["content"], "role": m["role"], "history": True}))

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)

            # 重读：前端请求对指定文本重新合成语音（仅云语音模式走后端）
            if msg.get("action") == "tts":
                tts_text = msg.get("text", "").strip()
                if tts_text:
                    try:
                        audio = await session.synthesize_tts(tts_text)
                        if audio:
                            await ws.send_json({"text": "", "audio": audio})
                    except Exception as e:
                        print(f"[WS] tts replay error for user={user_id}: {e}")
                continue

            user_text = msg.get("text", "").strip()
            if not user_text:
                continue
            # 前端切换：local = 浏览器本机语音（跳过云 TTS，省成本+省延迟）
            tts_mode = msg.get("tts_mode", "cloud")
            # 前端切换：agent_mode = 关闭则退化为纯对话（一键排查 Agent 问题）
            try:
                session.set_agent_mode(msg.get("agent_mode", True))
            except Exception:
                pass

            try:
                # 流式生成：逐 token 推前端打字机效果
                async for event in session.chat_stream(user_text, user_id=user_id):
                    if event["type"] == "token":
                        await ws.send_json({"text": event["text"], "stream": True})
                    elif event["type"] == "done":
                        final_reply = event["text"]
                        await ws.send_json({"text": final_reply, "stream": False, "audio": ""})
                        # 云 TTS 合成在流式全部结束后（避免碎片化合成）
                        if tts_mode != "local" and not event.get("error"):
                            audio = await session.synthesize_tts(final_reply)
                            if audio:
                                await ws.send_json({"text": "", "audio": audio})
            except Exception as e:
                # chat 内部异常不断连，返回错误信息给前端
                import traceback
                print(f"[WS] chat() error for user={user_id}: {e}\n{traceback.format_exc()}")
                error_reply = f"抱歉，处理消息时出错了：{e}"
                await ws.send_json({"text": error_reply, "audio": ""})

    except WebSocketDisconnect:
        pass


@app.websocket("/ws/asr")
async def asr_websocket_endpoint(ws: WebSocket):
    """语音识别 WebSocket：接受连接后直接交给 ASR 客户端处理音频流。"""
    await ws.accept()
    await handle_asr_ws(ws)
