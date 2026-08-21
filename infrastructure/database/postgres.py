import asyncpg
from pgvector.asyncpg import register_vector
from infrastructure.database.codecs import register_json_codecs


class PostgresDatabase:
    def __init__(self, dsn: str):
        self._dsn = dsn
        self.pool = None

    async def _init_connection(self, conn):
        await register_vector(conn)
        # 让 json/jsonb 列读回自动成为 dict/list、写入时自动序列化，
        # 避免 asyncpg 默认把 jsonb 读成 str 导致的手动 json.loads 分散在各处。
        await register_json_codecs(conn)

    async def connect(self):
        self.pool = await asyncpg.create_pool(
            self._dsn,
            min_size=1,
            max_size=10,
            init=self._init_connection,
        )

    async def close(self):
        if self.pool is not None:
            await self.pool.close()
