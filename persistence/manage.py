"""Run explicit checkpoint schema setup after application migrations."""

import asyncio

from dotenv import load_dotenv

from persistence.config import DatabaseSettings
from persistence.runtime import open_persistence


async def setup():
    async with open_persistence(DatabaseSettings.from_env(), setup_checkpoints=True):
        pass
    print("LangGraph checkpoint schema is ready")


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(setup())
