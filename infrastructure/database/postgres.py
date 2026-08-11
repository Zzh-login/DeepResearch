import asyncpg
from pgvector.asyncpg import register_vector


class PostgresDatabase:
    def __init__(self, dsn: str):
        self._dsn = dsn
        self.pool = None

    async def _init_connection(self, conn):
        await register_vector(conn)

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