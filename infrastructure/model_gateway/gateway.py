from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import BaseMessage

from app.rag.citations import message_content_to_text
from domain.model_gateway.contracts import (
    ChatResult,
    EmbeddingResult,
    ModelProfile,
    ModelRequestContext,
    RerankItem,
    RerankResult,
    TokenUsage,
)
from infrastructure.model_gateway.audit_repository import AuditRepository
from infrastructure.model_gateway.circuit_breaker import CircuitBreaker
from infrastructure.model_gateway.errors import (
    ModelProviderError,
    is_transient_model_error,
    safe_error_code,
)
from infrastructure.model_gateway.providers.deepseek import DeepSeekChatProvider
from infrastructure.model_gateway.providers.local_bge import LocalBgeProvider
from infrastructure.model_gateway.usage_repository import ModelUsageRepository


def _message_text(message: BaseMessage) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return str(content)


def estimate_tokens(texts: list[str]) -> int:
    # 只用于预留，宁可保守；最终账单以供应商 usage 为准。
    return max(1, (sum(len(text) for text in texts) + 2) // 3)


def response_usage(response: Any) -> TokenUsage:
    usage = getattr(response, "usage_metadata", None) or {}
    if not usage:
        metadata = getattr(response, "response_metadata", None) or {}
        usage = metadata.get("token_usage") or metadata.get("usage") or {}
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    return TokenUsage(
        input_tokens=int(input_tokens) if input_tokens is not None else None,
        output_tokens=int(output_tokens) if output_tokens is not None else None,
        estimated=input_tokens is None or output_tokens is None,
    )


class ModelGateway:
    def __init__(
        self,
        database,
        settings,
        *,
        chat_provider=None,
        local_provider=None,
        usage_repository=None,
        audit_repository=None,
        circuit_breaker=None,
    ) -> None:
        self._settings = settings
        self._chat = chat_provider or DeepSeekChatProvider(settings)
        self._local = local_provider or LocalBgeProvider()
        self._usage = usage_repository or ModelUsageRepository(
            database,
            settings.model_gateway_request_input_tokens,
            settings.model_gateway_request_output_tokens,
        )
        self._audit = audit_repository or AuditRepository(
            database,
            enabled=settings.model_gateway_audit_enabled,
        )
        self._circuit = circuit_breaker or CircuitBreaker(
            settings.model_gateway_circuit_failure_threshold,
            settings.model_gateway_circuit_recovery_seconds,
        )

    async def complete(
        self,
        messages: list[BaseMessage],
        profile: ModelProfile,
        context: ModelRequestContext,
    ) -> ChatResult:
        provider = self._chat.name
        model_name = self._chat.model_name
        circuit_key = f"{provider}:chat:{model_name}"
        estimated_input = estimate_tokens([_message_text(item) for item in messages])
        max_attempts = (
            self._settings.model_gateway_max_attempts
            if context.idempotent
            else 1
        )
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            await self._circuit.before_call(circuit_key)
            usage_id = await self._usage.reserve(
                context,
                profile,
                attempt,
                provider,
                model_name,
                estimated_input,
            )
            started = time.perf_counter()
            try:
                model = self._chat.build_model(profile)
                response = await asyncio.wait_for(
                    model.ainvoke(messages),
                    timeout=profile.timeout_seconds,
                )
                latency_ms = max(0, int((time.perf_counter() - started) * 1000))
                text = message_content_to_text(response.content)
                if not text:
                    raise ModelProviderError("模型返回空内容")
                usage = response_usage(response)
                await self._usage.finish_success(usage_id, usage, latency_ms)
                await self._circuit.record_success(circuit_key)
                await self._audit.write(
                    context,
                    "model.call",
                    "succeeded",
                    {
                        "provider": provider,
                        "model": model_name,
                        "operation": profile.operation,
                        "attempt": attempt,
                        "latency_ms": latency_ms,
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                    },
                )
                return ChatResult(
                    text=text,
                    raw=response,
                    provider=provider,
                    model=model_name,
                    usage=usage,
                    latency_ms=latency_ms,
                )
            except Exception as exc:
                last_error = exc
                latency_ms = max(0, int((time.perf_counter() - started) * 1000))
                code = safe_error_code(exc)
                await self._usage.finish_failure(usage_id, latency_ms, code)
                await self._circuit.record_failure(circuit_key)
                should_retry = (
                    context.idempotent
                    and attempt < max_attempts
                    and is_transient_model_error(exc)
                )
                await self._audit.write(
                    context,
                    "model.call",
                    code,
                    {
                        "provider": provider,
                        "model": model_name,
                        "operation": profile.operation,
                        "attempt": attempt,
                        "retrying": should_retry,
                    },
                    severity="warning" if should_retry else "error",
                )
                if not should_retry:
                    raise
                delay = self._settings.model_gateway_retry_base_seconds * (
                    2 ** (attempt - 1)
                )
                await asyncio.sleep(delay)

        raise ModelProviderError("模型调用失败") from last_error

    async def stream(
        self,
        messages: list[BaseMessage],
        profile: ModelProfile,
        context: ModelRequestContext,
        tools: list | None = None,
    ) -> AsyncIterator[Any]:
        """流式调用不做中途重试；开始输出后重复调用会产生重复文本。"""
        provider = self._chat.name
        model_name = self._chat.model_name
        circuit_key = f"{provider}:chat:{model_name}"
        await self._circuit.before_call(circuit_key)
        usage_id = await self._usage.reserve(
            context,
            profile,
            1,
            provider,
            model_name,
            estimate_tokens([_message_text(item) for item in messages]),
        )
        started = time.perf_counter()
        emitted = False
        try:
            model = self._chat.build_model(profile)
            if tools:
                model = model.bind_tools(tools)
            async for chunk in model.astream(messages):
                emitted = True
                yield chunk
            latency_ms = max(0, int((time.perf_counter() - started) * 1000))
            await self._usage.finish_success(
                usage_id,
                TokenUsage(estimated=True),
                latency_ms,
            )
            await self._circuit.record_success(circuit_key)
            await self._audit.write(
                context,
                "model.stream",
                "succeeded",
                {
                    "provider": provider,
                    "model": model_name,
                    "operation": profile.operation,
                    "emitted": emitted,
                    "latency_ms": latency_ms,
                },
            )
        except Exception as exc:
            latency_ms = max(0, int((time.perf_counter() - started) * 1000))
            code = safe_error_code(exc)
            await self._usage.finish_failure(usage_id, latency_ms, code)
            await self._circuit.record_failure(circuit_key)
            await self._audit.write(
                context,
                "model.stream",
                code,
                {"operation": profile.operation, "emitted": emitted},
                severity="error",
            )
            raise

    async def embed(
        self,
        texts: list[str],
        is_query: bool,
        context: ModelRequestContext,
    ) -> EmbeddingResult:
        profile = ModelProfile(
            operation=context.operation,
            max_tokens=0,
            timeout_seconds=self._settings.model_gateway_timeout_seconds,
        )
        estimated_input = estimate_tokens(texts)
        usage_id = await self._usage.reserve(
            context,
            profile,
            1,
            self._local.name,
            self._local.embedding_model,
            estimated_input,
        )
        started = time.perf_counter()
        try:
            vectors = await self._local.embed(texts, is_query=is_query)
        except Exception as exc:
            latency_ms = max(0, int((time.perf_counter() - started) * 1000))
            await self._usage.finish_failure(
                usage_id,
                latency_ms,
                safe_error_code(exc),
            )
            await self._audit.write(
                context,
                "model.embed",
                safe_error_code(exc),
                {"item_count": len(texts)},
                severity="error",
            )
            raise
        latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        await self._usage.finish_success(
            usage_id,
            TokenUsage(
                input_tokens=estimated_input,
                output_tokens=0,
                estimated=True,
            ),
            latency_ms,
        )
        await self._audit.write(
            context,
            "model.embed",
            "succeeded",
            {
                "provider": self._local.name,
                "model": self._local.embedding_model,
                "item_count": len(texts),
                "latency_ms": latency_ms,
            },
        )
        return EmbeddingResult(
            vectors=vectors,
            provider=self._local.name,
            model=self._local.embedding_model,
            latency_ms=latency_ms,
        )

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int,
        context: ModelRequestContext,
    ) -> RerankResult:
        profile = ModelProfile(
            operation=context.operation,
            max_tokens=0,
            timeout_seconds=self._settings.model_gateway_timeout_seconds,
        )
        estimated_input = estimate_tokens([query, *documents])
        usage_id = await self._usage.reserve(
            context,
            profile,
            1,
            self._local.name,
            self._local.rerank_model,
            estimated_input,
        )
        started = time.perf_counter()
        try:
            ranked = await self._local.rerank(query, documents, top_k)
        except Exception as exc:
            latency_ms = max(0, int((time.perf_counter() - started) * 1000))
            await self._usage.finish_failure(
                usage_id,
                latency_ms,
                safe_error_code(exc),
            )
            await self._audit.write(
                context,
                "model.rerank",
                safe_error_code(exc),
                {"item_count": len(documents)},
                severity="error",
            )
            raise
        latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        await self._usage.finish_success(
            usage_id,
            TokenUsage(
                input_tokens=estimated_input,
                output_tokens=0,
                estimated=True,
            ),
            latency_ms,
        )
        await self._audit.write(
            context,
            "model.rerank",
            "succeeded",
            {
                "provider": self._local.name,
                "model": self._local.rerank_model,
                "item_count": len(documents),
                "latency_ms": latency_ms,
            },
        )
        return RerankResult(
            items=[
                RerankItem(index=index, score=score, text=documents[index])
                for index, score in ranked
            ],
            provider=self._local.name,
            model=self._local.rerank_model,
            latency_ms=latency_ms,
        )