import asyncio

import httpx
from openai import APIConnectionError, APITimeoutError, RateLimitError


class ModelGatewayError(RuntimeError):
    code = "model_gateway_error"


class ModelBudgetExceeded(ModelGatewayError):
    code = "model_budget_exceeded"


class ModelCircuitOpen(ModelGatewayError):
    code = "model_circuit_open"


class ModelProviderError(ModelGatewayError):
    code = "model_provider_error"


def is_transient_model_error(exc: Exception) -> bool:
    if isinstance(
        exc,
        (
            asyncio.TimeoutError,
            TimeoutError,
            ConnectionError,
            OSError,
            httpx.TimeoutException,
            httpx.NetworkError,
            APIConnectionError,
            APITimeoutError,
            RateLimitError,
        ),
    ):
        return True
    status_code = getattr(exc, "status_code", None)
    return status_code == 429 or (
        isinstance(status_code, int) and status_code >= 500
    )


def safe_error_code(exc: Exception) -> str:
    if isinstance(exc, ModelGatewayError):
        return exc.code
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, APITimeoutError)):
        return "provider_timeout"
    if isinstance(exc, RateLimitError) or getattr(exc, "status_code", None) == 429:
        return "provider_rate_limited"
    if is_transient_model_error(exc):
        return "provider_temporarily_unavailable"
    return "provider_request_failed"