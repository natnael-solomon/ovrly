import asyncio

from alembic import context
from sqlalchemy.engine import Connection

from services.database import Database, metadata
from services.settings import load_settings


def migrate(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


async def online() -> None:
    database = Database(load_settings())
    try:
        async with database.engine.connect() as connection:
            await connection.run_sync(migrate)
    finally:
        await database.close()


if context.is_offline_mode():
    context.configure(
        url=load_settings().database_url.get_secret_value(),
        target_metadata=metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(online())
