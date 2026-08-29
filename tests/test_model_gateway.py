import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage, HumanMessage

from domain.model_gateway.contracts import ModelProfile, ModelRequestContext
from infrastructure.model_gateway.circuit_breaker import CircuitBreaker
from infrastructure.model_gateway.errors import ModelCircuitOpen
from infrastructure.model_gateway.gateway import ModelGateway


class _FakeUsage:
    def __init__(self):
        self.reserved = []
        self.success = []
        self.failure = []

    async def reserve(self, *args):
        from uuid import uuid4
        self.reserved.append(args)
        return uuid4()

    async def finish_success(self, *args):
        self.success.append(args)

    async def finish_failure(self, *args):
        self.failure.append(args)


class _FakeAudit:
    def __init__(self):
        self.events = []

    async def write(self, *args, **kwargs):
        self.events.append((args, kwargs))


class _FakeChatProvider:
    name = "fake"
    model_name = "fake-chat"

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)

    def build_model(self, profile):
        outcome = self._outcomes.pop(0)
        model = SimpleNamespace()
        if isinstance(outcome, Exception):
            model.ainvoke = AsyncMock(side_effect=outcome)
        else:
            model.ainvoke = AsyncMock(return_value=outcome)
        return model


class _FakeLocalProvider:
    name = "local"
    embedding_model = "fake-embed"
    rerank_model = "fake-rerank"

    async def embed(self, texts, is_query):
        return [[1.0, 0.0] for _ in texts]

    async def rerank(self, query, documents, top_k):
        return [(0, 1.0)][:top_k]


def _settings():
    return SimpleNamespace(
        model_gateway_request_input_tokens=100_000,
        model_gateway_request_output_tokens=20_000,
        model_gateway_audit_enabled=True,
        model_gateway_circuit_failure_threshold=2,
        model_gateway_circuit_recovery_seconds=0.01,
        model_gateway_max_attempts=2,
        model_gateway_retry_base_seconds=0.001,
        model_gateway_timeout_seconds=1,
    )


class ModelGatewayTests(unittest.IsolatedAsyncioTestCase):
    def _gateway(self, outcomes):
        return ModelGateway(
            SimpleNamespace(pool=object()),
            _settings(),
            chat_provider=_FakeChatProvider(outcomes),
            local_provider=_FakeLocalProvider(),
            usage_repository=_FakeUsage(),
            audit_repository=_FakeAudit(),
            circuit_breaker=CircuitBreaker(2, 0.01),
        )

    def _context(self, *, idempotent=True):
        return ModelRequestContext(
            owner_id="test-user",
            mode="normal",
            operation="test",
            idempotent=idempotent,
        )

    def _profile(self):
        return ModelProfile("test", max_tokens=20, timeout_seconds=1)

    async def test_success_records_usage(self):
        response = AIMessage(
            content="ok",
            usage_metadata={
                "input_tokens": 3,
                "output_tokens": 2,
                "total_tokens": 5,
            },
        )
        gateway = self._gateway([response])
        result = await gateway.complete([HumanMessage(content="hello")], self._profile(), self._context())
        self.assertEqual(result.text, "ok")

    async def test_transient_failure_is_retried_once(self):
        gateway = self._gateway([TimeoutError("slow"), AIMessage(content="ok")])
        result = await gateway.complete([HumanMessage(content="hello")], self._profile(), self._context())
        self.assertEqual(result.text, "ok")

    async def test_non_idempotent_call_is_not_retried(self):
        gateway = self._gateway([TimeoutError("slow"), AIMessage(content="never")])
        with self.assertRaises(TimeoutError):
            await gateway.complete([HumanMessage(content="hello")], self._profile(), self._context(idempotent=False))

    async def test_circuit_opens_after_threshold(self):
        breaker = CircuitBreaker(2, 60)
        await breaker.record_failure("provider")
        await breaker.record_failure("provider")
        with self.assertRaises(ModelCircuitOpen):
            await breaker.before_call("provider")

    async def test_circuit_closes_after_successful_probe(self):
        breaker = CircuitBreaker(1, 0.001)
        await breaker.record_failure("provider")
        await asyncio.sleep(0.01)
        await breaker.before_call("provider")
        await breaker.record_success("provider")
        self.assertFalse((await breaker.snapshot("provider"))["open"])

    async def test_local_embedding_is_accounted(self):
        gateway = self._gateway([])
        result = await gateway.embed(["embedding"], True, self._context())
        self.assertEqual(result.vectors, [[1.0, 0.0]])


if __name__ == "__main__":
    unittest.main()
