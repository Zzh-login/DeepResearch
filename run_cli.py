"""终端版启动入口"""
import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ── 必须在任何 transformers / huggingface_hub 相关库导入之前设好离线环境变量 ──
# 这些库在首次 import 时就定死联网/离线状态，之后再用 os.environ 设置已无效。
os.environ["HF_HOME"] = "E:/robot_system/models/hf_cache"
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from interfaces.cli.main import main

if __name__ == "__main__":
    # 启动逻辑：调用 interfaces.cli.main.main()（终端交互入口），
    # 通过 asyncio.run 创建事件循环并运行该异步主函数；
    # main 内部负责初始化依赖、进入命令行聊天循环，直到用户退出。
    asyncio.run(main())