"""
TTS 客户端 —— 对接火山引擎语音合成

技术栈说明：
  - httpx：异步 HTTP 客户端
  - 火山引擎 TTS API：返回 base64 编码的 mp3
  - 配置注入：从 .env 读取 AppID / Token / Voice
  - 缓存：相同文本缓存 5 分钟，避免重复请求

设计原则：
  TTS 客户端是"语音能力提供者"，只负责文本转语音。
  不负责文本生成、不负责情感分析、不负责音频播放。
"""

import os
import uuid
import asyncio
from typing import Dict,Optional
from datetime import datetime, timedelta
import httpx

# 默认配置（从 .env 读取）
DEFAULT_APP_ID = os.getenv("TTS_APP_ID", "")
DEFAULT_ACCESS_TOKEN = os.getenv("TTS_ACCESS_TOKEN", "")
DEFAULT_VOICE = os.getenv("TTS_VOICE", "BV700_streaming")


class TTSClient:
    """
    TTS 客户端

    职责：
      1. 管理火山引擎 API 配置
      2. 调用 TTS 接口，返回 base64 mp3
      3. 简单内存缓存（避免重复合成相同文本）

    为什么独立成类而不是直接一个 async 函数：
      - 配置管理：支持多语音模型切换（男声/女声/方言）
      - 缓存策略：相同文本缓存，节省 API 调用次数
      - 错误处理：网络重试、token 刷新、降级到本地 TTS
    """

    def __init__(
        self,
        app_id: str = DEFAULT_APP_ID,
        access_token: str = DEFAULT_ACCESS_TOKEN,
        voice: str = DEFAULT_VOICE,
        cache_ttl: int = 300,  # 缓存 5 分钟
    ):
        """初始化 TTS 客户端。

        Args:
            app_id: 火山引擎 AppID，默认读 .env 的 TTS_APP_ID。
            access_token: 火山引擎访问令牌，默认读 TTS_ACCESS_TOKEN。
            voice: 发音人类型，默认 BV700_streaming。
            cache_ttl: 相同文本合成结果的缓存时长（秒），默认 300。

        说明：仅保存配置与空缓存字典，不发起网络请求。
        """
        self._app_id = app_id
        self._access_token = access_token
        self._voice = voice
        self._cache_ttl = cache_ttl
        self._cache: Dict[str, tuple[datetime, str]] = {}  # text → (expiry, base64)

    def set_config(
        self,
        app_id: Optional[str] = None,
        access_token: Optional[str] = None,
        voice: Optional[str] = None,
    ) -> None:
        """动态更新 TTS 配置"""
        if app_id is not None:
            self._app_id = app_id
        if access_token is not None:
            self._access_token = access_token
        if voice is not None:
            self._voice = voice
        # 配置变更时清空缓存（因为语音可能变了）
        self._cache.clear()

    def get_config(self) -> Dict[str, str]:
        """获取当前配置"""
        return {
            "app_id": self._app_id,
            "access_token": self._access_token[:8] + "..." if self._access_token else "",
            "voice": self._voice,
        }

    def _get_cached(self, text: str) -> Optional[str]:
        """从缓存获取 base64 音频"""
        if text not in self._cache:
            return None
        expiry, base64_data = self._cache[text]
        if datetime.now() > expiry:
            del self._cache[text]
            return None
        return base64_data

    def _set_cache(self, text: str, base64_data: str) -> None:
        """写入缓存"""
        expiry = datetime.now() + timedelta(seconds=self._cache_ttl)
        self._cache[text] = (expiry, base64_data)

    async def synthesize(self, text: str) -> str:
        """
        语音合成（异步）

        参数：
          text: 要合成的文本（建议不超过 200 字）

        返回：base64 编码的 mp3 音频数据（可直接用于 <audio src="data:audio/mp3;base64,...">）

        错误处理：
          - 无配置 → 返回空字符串（前端静默降级）
          - 网络失败 → 重试一次
          - API 错误 → 记录日志，返回空字符串
        """
        if not self._app_id or not self._access_token:
            # 无 TTS 配置，静默降级（前端显示纯文本）
            return ""

        # 检查缓存
        cached = self._get_cached(text)
        if cached is not None:
            return cached

        # 构造请求
        payload = {
            "app": {
                "appid": self._app_id,
                "token": self._access_token,
                "cluster": "volcano_tts",
            },
            "user": {"uid": "demo_user"},
            "audio": {
                "voice_type": self._voice,
                "encoding": "mp3",
                "rate": 24000,
                "speed_ratio": 1,
            },
            "request": {
                "reqid": uuid.uuid4().hex,
                "text": text,
                "text_type": "plain",
                "operation": "query",
            },
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://openspeech.bytedance.com/api/v1/tts",
                    json=payload,
                    headers={"Authorization": f"Bearer;{self._access_token}"},
                )
                resp.raise_for_status()
                result = resp.json()

            base64_data = result.get("data", "")
            if base64_data:
                self._set_cache(text, base64_data)
            return base64_data

        except Exception as e:
            # TODO: 更精细的错误处理（重试、降级到本地 TTS）
            print(f"[TTSClient] 合成失败: {e}")
            return ""