import asyncio
import time
from dataclasses import dataclass

from infrastructure.model_gateway.errors import ModelCircuitOpen


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: float | None = None
    probe_in_flight: bool = False


class CircuitBreaker:
    def __init__(self, failure_threshold: int, recovery_seconds: float) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_seconds = recovery_seconds
        self._states: dict[str, _CircuitState] = {}
        self._lock = asyncio.Lock()

    async def before_call(self, key: str) -> None:
        async with self._lock:
            state = self._states.setdefault(key, _CircuitState())
            if state.opened_at is None:
                return
            elapsed = time.monotonic() - state.opened_at
            if elapsed < self._recovery_seconds:
                raise ModelCircuitOpen(f"模型服务熔断中: {key}")
            if state.probe_in_flight:
                raise ModelCircuitOpen(f"模型服务正在半开探测: {key}")
            state.probe_in_flight = True

    async def record_success(self, key: str) -> None:
        async with self._lock:
            self._states[key] = _CircuitState()

    async def record_failure(self, key: str) -> None:
        async with self._lock:
            state = self._states.setdefault(key, _CircuitState())
            state.probe_in_flight = False
            state.failures += 1
            if state.failures >= self._failure_threshold:
                state.opened_at = time.monotonic()

    async def snapshot(self, key: str) -> dict:
        async with self._lock:
            state = self._states.setdefault(key, _CircuitState())
            return {
                "failures": state.failures,
                "open": state.opened_at is not None,
                "probe_in_flight": state.probe_in_flight,
            }