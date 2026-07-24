"""浏览器版启动入口"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ── 必须在任何 transformers / huggingface_hub 相关库导入之前设好离线环境变量 ──
# 这些库在首次 import 时就定死联网/离线状态，之后再用 os.environ 设置已无效，
# 会导致 BGE-M3 加载时仍直连 huggingface.co 超时（WinError 10060）。
os.environ["HF_HOME"] = "E:/robot_system/models/hf_cache"
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import uvicorn
from interfaces.web.app import app

if __name__ == "__main__":
    # 启动逻辑：从环境变量读取监听地址与端口（默认值 127.0.0.1:5001），
    # 调用 uvicorn.run 以单进程阻塞方式启动已导入的 FastAPI 应用 app，
    # 进程持续运行直至被手动终止或异常退出。
    host = os.getenv("SERVER_HOST", "127.0.0.1")
    port = int(os.getenv("SERVER_PORT", "5001"))
    uvicorn.run(app, host=host, port=port)