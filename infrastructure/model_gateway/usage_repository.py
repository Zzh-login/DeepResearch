from decimal import Decimal
from uuid import UUID, uuid4

from domain.model_gateway.contracts import ModelProfile, ModelRequestContext, TokenUsage
from infrastructure.model_gateway.errors import ModelBudgetExceeded


class ModelUsageRepository:
    def __init__(
        self,
        database,
        request_input_limit: int,
        request_output_limit: int,
    ) -> None:
        self._database = database
        self._request_input_limit = request_input_limit
        self._request_output_limit = request_output_limit

    async def reserve(
        self,
        context: ModelRequestContext,
        profile: ModelProfile,
        attempt: int,
        provider: str,
        model: str,
        estimated_input_tokens: int,
    ) -> UUID:
        reserved_input = max(0, estimated_input_tokens)
        reserved_output = max(0, profile.max_tokens)
        if reserved_input > self._request_input_limit:
            raise ModelBudgetExceeded("单次请求输入 token 超过限制")
        if reserved_output > self._request_output_limit:
            raise ModelBudgetExceeded("单次请求输出 token 超过限制")
        if self._database.pool is None:
            raise ModelBudgetExceeded("无法连接预算账本，模型调用已拒绝")

        usage_id = uuid4()
        async with self._database.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE model_usage_events
                    SET status='failed', error_code='reservation_expired',
                        finished_at=NOW()
                    WHERE status='reserved'
                      AND created_at < NOW() - INTERVAL '15 minutes'
                    """
                )
                await conn.execute(
                    """
                    INSERT INTO model_usage_events (
                        id, request_id, attempt, owner_id, conversation_id,
                        research_task_id, mode, operation, provider, model,
                        status, reserved_input_tokens, reserved_output_tokens
                    ) VALUES (
                        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,
                        'reserved',$11,$12
                    )
                    """,
                    usage_id,
                    context.request_id,
                    attempt,
                    context.owner_id,
                    context.conversation_id,
                    context.research_task_id,
                    context.mode,
                    context.operation,
                    provider,
                    model,
                    reserved_input,
                    reserved_output,
                )
        return usage_id

    async def finish_success(
        self,
        usage_id: UUID,
        usage: TokenUsage,
        latency_ms: int,
        estimated_cost: Decimal | None = None,
    ) -> None:
        async with self._database.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE model_usage_events
                SET status='succeeded', input_tokens=$2, output_tokens=$3,
                    latency_ms=$4, estimated_cost=$5,
                    usage_estimated=$6, error_code=NULL, finished_at=NOW()
                WHERE id=$1 AND status='reserved'
                """,
                usage_id,
                usage.input_tokens,
                usage.output_tokens,
                latency_ms,
                estimated_cost,
                usage.estimated,
            )

    async def finish_failure(
        self,
        usage_id: UUID,
        latency_ms: int,
        error_code: str,
    ) -> None:
        async with self._database.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE model_usage_events
                SET status='failed', latency_ms=$2, error_code=$3,
                    finished_at=NOW()
                WHERE id=$1 AND status='reserved'
                """,
                usage_id,
                latency_ms,
                error_code,
            )