"""
LLM 客户端 —— 对接 OpenAI 兼容 API

技术栈说明：
  - OpenAI SDK：统一接口，兼容 DeepSeek / OpenAI / 自部署
  - 配置注入：从 .env 读取，支持运行时动态切换
  - 流式支持：chat_stream() 逐 token 异步产出，供 WS 推前端打字机效果
  - 依赖倒置：不依赖 session / domain，只依赖环境变量

设计原则：
  LLM 客户端是"能力提供者"，只负责调用 API 并返回文本。
  不负责 prompt 拼装、不负责历史管理、不负责输出校验。
"""

import os
import json
import asyncio
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, AsyncGenerator, List
from openai import AsyncOpenAI

# 默认配置（从 .env 读取，与老 config.py 保持一致）
DEFAULT_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEFAULT_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEFAULT_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")


@dataclass
class LLMResponse:
    """LLM 调用响应，包含文本、token 用量、结束原因与工具调用"""
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str = "stop"
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def truncated(self) -> bool:
        """是否被 max_tokens 截断（finish_reason == 'length'）"""
        return self.finish_reason == "length"


class LLMClient:
    """
    LLM 客户端

    职责：
      1. 管理 API 配置（默认 + 运行时覆盖）
      2. 调用 OpenAI 兼容的 chat.completions.create
      3. 返回 LLMResponse（文本 + token 用量）

    为什么独立成类而不是直接 import openai：
      - 配置管理：支持多用户多密钥轮询
      - 错误处理：统一处理网络超时、配额不足、模型不可用
      - 监控埋点：记录调用耗时、token 用量、成功率
    """

    def __init__(
        self,
        api_key: str = DEFAULT_API_KEY,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
    ):
        """初始化 LLM 客户端。

        Args:
            api_key: DeepSeek/OpenAI 兼容 API Key，默认读 .env 中的 DEEPSEEK_API_KEY。
            base_url: API 基地址，默认 https://api.deepseek.com。
            model: 模型名，默认 deepseek-chat。

        说明：构造时仅保存默认配置与运行时覆盖字典（_custom_config），
        不创建实际网络客户端，按需由 _get_client 懒加载。
        """
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._custom_config: Dict[str, Any] = {}

    def set_config(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        """
        动态更新配置（运行时切换密钥 / 模型）

        场景：
          - 用户 A 用自己的 API Key
          - 用户 B 用系统默认 Key
          - 临时切换到 GPT-4 做复杂任务
        """
        if api_key is not None:
            self._custom_config["api_key"] = api_key
        if base_url is not None:
            self._custom_config["base_url"] = base_url
        if model is not None:
            self._custom_config["model"] = model

    def get_config(self) -> Dict[str, str]:
        """获取当前生效的配置（用于日志/调试）"""
        return {
            "api_key": self._custom_config.get("api_key", self._api_key),
            "base_url": self._custom_config.get("base_url", self._base_url),
            "model": self._custom_config.get("model", self._model),
        }

    def reset_config(self) -> None:
        """恢复默认配置（清除运行时覆盖）"""
        self._custom_config.clear()

    def _get_client(self) -> AsyncOpenAI:
        """根据当前配置创建异步 OpenAI 客户端实例"""
        api_key = self._custom_config.get("api_key", self._api_key)
        base_url = self._custom_config.get("base_url", self._base_url)
        return AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def chat(
        self,
        messages: list[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stream: bool = False,
        tools: Optional[list] = None,
        tool_choice: Optional[str] = None,
    ) -> LLMResponse:
        """
        异步调用 LLM

        参数：
          messages: OpenAI 格式的 messages 列表
          temperature: 温度（0~2），越高越随机
          max_tokens: 最大生成 token 数
          stream: 是否流式返回
          tools: OpenAI function calling 工具定义列表（agent 模式用）
          tool_choice: 工具选择策略（"auto" / "none" / {"type":"function","function":{"name":...}}）

        返回：LLMResponse(text, prompt_tokens, completion_tokens, total_tokens,
                         finish_reason, tool_calls)

        工具调用解析：
          - 当模型决定调用工具时，choices[0].message.tool_calls 非空，
            text 可能为空，tool_calls 含 {id, name, arguments(已 JSON 解析)}
          - finish_reason 为 "tool_calls"（而非 "stop"）
        """
        client = self._get_client()
        model = self._custom_config.get("model", self._model)

        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=stream,
                    tools=tools,
                    tool_choice=tool_choice,
                ),
                timeout=60,  # LLM 调用超时 60 秒
            )
            usage = response.usage
            choice = response.choices[0]
            msg = choice.message
            text = (msg.content or "").strip()
            finish_reason = choice.finish_reason or "stop"

            # 解析工具调用（如有）
            tool_calls: list[Dict[str, Any]] = []
            if getattr(msg, "tool_calls", None):
                for tc in msg.tool_calls:
                    raw_args = tc.function.arguments or "{}"
                    try:
                        arguments = json.loads(raw_args)
                    except Exception:
                        arguments = {}
                    tool_calls.append(
                        {
                            "id": getattr(tc, "id", ""),
                            "name": tc.function.name,
                            "arguments": arguments,
                        }
                    )

            return LLMResponse(
                text=text,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                finish_reason=finish_reason,
                tool_calls=tool_calls,
            )
        except asyncio.TimeoutError:
            raise RuntimeError("LLM 调用超时（60秒无响应），请检查网络或 API 配置")
        except Exception as e:
            raise RuntimeError(f"LLM 调用失败: {e}")

    async def chat_stream(
        self,
        messages: list[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> AsyncGenerator[str, None]:
        """
        流式调用 LLM，逐 token 产出文字片段。

        与 chat() 的区别：
          - chat() 等完整回复再返回 → 用户等待 6-8 秒后一次看到全部文字
          - chat_stream() 每个 token 立即产出 → 前端实时打字机效果，首字 ~1s 可见

        使用方式：
            async for token in client.chat_stream(messages):
                print(token, end="", flush=True)   # 实时输出

        注意：
          - 流式模式下 OpenAI SDK 不返回 usage 字段（token 计数在最后一块）。
          - 默认超时由 httpx 控制（约 60s），不额外套 asyncio.wait_for。
          - 流中断（网络抖动/超时）会 raise RuntimeError，调用方应 try/except。
        """
        client = self._get_client()
        model = self._custom_config.get("model", self._model)

        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content
        except Exception as e:
            raise RuntimeError(f"LLM 流式调用失败: {e}")