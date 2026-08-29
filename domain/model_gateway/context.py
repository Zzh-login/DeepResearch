from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator

from domain.model_gateway.contracts import ModelRequestContext


_CURRENT_CONTEXT: ContextVar[ModelRequestContext | None] = ContextVar(
    "model_request_context",
    default=None,
)


def current_model_context() -> ModelRequestContext:
    context = _CURRENT_CONTEXT.get()
    if context is None:
        raise RuntimeError("模型调用缺少 ModelRequestContext")
    return context


@contextmanager
def model_request_scope(
    context: ModelRequestContext,
) -> Iterator[ModelRequestContext]:
    token = _CURRENT_CONTEXT.set(context)
    try:
        yield context
    finally:
        _CURRENT_CONTEXT.reset(token)