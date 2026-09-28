import asyncio
import math

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import create_async_engine

from services.settings import Settings

metadata = MetaData()


class Database:
    def __init__(self, settings: Settings):
        self.timeout = settings.database_timeout_seconds
        self.engine = create_async_engine(
            settings.database_url.get_secret_value(),
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=0,
            pool_timeout=self.timeout,
            connect_args={"connect_timeout": max(2, math.ceil(self.timeout))},
            hide_parameters=True,
        )

    async def ping(self) -> None:
        async with asyncio.timeout(self.timeout):
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))

    async def close(self) -> None:
        await self.engine.dispose()
