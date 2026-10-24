"""python -m worker: managed as a separate process from Uvicorn."""
import asyncio
import signal
from dotenv import load_dotenv
from sqlalchemy import text
from persistence.config import DatabaseSettings
from persistence.runtime import open_persistence
from worker.runner import Worker


async def main():
    async with open_persistence(DatabaseSettings.from_env()) as runtime:
        async with runtime.database.transaction() as session:
            await session.execute(text("SELECT job_id FROM app.jobs LIMIT 0"))
            await session.execute(text("SELECT sequence FROM app.progress_events LIMIT 0"))
            await session.execute(text("SELECT thread_id FROM langgraph.checkpoints LIMIT 0"))
        task = asyncio.create_task(Worker(runtime).run())
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, task.cancel)
        try:
            await task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    load_dotenv()
    try:
        asyncio.run(main())
    except Exception:
        raise SystemExit("Worker startup failed; check PostgreSQL, migrations, and checkpoint setup (details withheld)") from None
