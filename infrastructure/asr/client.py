"""ASR 客户端 —— 基于 SenseVoiceSmall 流式识别"""

import json
from funasr import AutoModel

# 全局单例，服务启动时加载一次
_model = None


def get_model():
    """获取（懒加载）SenseVoiceSmall ASR 模型全局单例。

    首次调用时通过 funasr.AutoModel 加载模型（禁用自动更新、
    信任远程代码），后续复用同一实例，避免重复加载。进程级单例。

    Returns:
        AutoModel: 已加载的语音识别模型实例。
    """
    global _model
    if _model is None:
        _model = AutoModel(
            model="iic/SenseVoiceSmall",
            disable_update=True,
            trust_remote_code=True,
        )
    return _model


def load_model():
    """启动时预加载 ASR 模型，阻塞直到加载完成（首次需下载约 500MB）。"""
    print("[ASR] 正在加载 SenseVoiceSmall 模型（首次下载约 500MB）...")
    get_model()
    print("[ASR] 模型就绪")


def transcribe(audio_bytes: bytes) -> str:
    """将音频字节流识别为中文文本。

    调用全局模型做流式识别（中文、开启 ITN 逆文本正则化、
    batch_size_s=0 单句、合并 VAD），并过滤 SenseVoice 输出的
    元标签（如 <|zh|> |<情感>| |<事件>| 等），仅保留纯文本。

    Args:
        audio_bytes: 原始音频字节（如 PCM/WAV）。

    Returns:
        str: 去标签后的识别文本；识别为空时返回空字符串。
    """
    model = get_model()
    result = model.generate(
        input=audio_bytes,
        language="zh",
        use_itn=True,
        batch_size_s=0,
        merge_vad=True,
    )
    if result and len(result) > 0:
        text = result[0].get("text", "")
        # 过滤 SenseVoice 元标签：<语言> |<情感>| |<事件>| |<格式化>|
        import re
        text = re.sub(r"<\|[^|]*\|>", "", text)  # 去 |<XXX>|
        text = re.sub(r"<\w+>", "", text)          # 去 <zh> 等语言标签
        text = text.strip()
        return text
    return ""


async def handle_asr_ws(websocket):
    """处理 ASR WebSocket 连接的异步循环。

    接收客户端发来的音频字节帧，逐帧调用 transcribe 识别，
    将本帧文本与累计文本(accumulated)以 JSON 回传；
    收到 action=="reset" 的文本消息时清空累计文本；
    收到断开消息或异常时退出循环。异常仅打印日志不向外抛出。

    Args:
        websocket: FastAPI/Starlette 的 WebSocket 对象。
    """
    accumulated = ""
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if "bytes" in message:
                text = transcribe(message["bytes"])
                if text:
                    accumulated += text
                    await websocket.send_text(json.dumps({
                        "text": text,
                        "accumulated": accumulated,
                    }))
            elif "text" in message:
                data = json.loads(message["text"])
                if data.get("action") == "reset":
                    accumulated = ""
    except Exception as e:
        print(f"[ASR] 异常: {e}")