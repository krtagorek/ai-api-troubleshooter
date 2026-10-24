"""Fence checkpoint writes against the same job row used by claim/recovery.

The application row lock stays held until the separate checkpoint transaction
has committed. A replacement claim cannot overtake an old checkpoint writer.
"""
from persistence.checkpoints import SanitizedAsyncPostgresSaver


class LeasedPostgresSaver(SanitizedAsyncPostgresSaver):
    def __init__(self, pool, queue, claim):
        super().__init__(pool)
        self.queue, self.claim = queue, claim

    async def aput(self, config, checkpoint, metadata, new_versions):
        async with self.queue.database.transaction() as session:
            await self.queue._fenced(session, self.claim)
            return await super().aput(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        async with self.queue.database.transaction() as session:
            await self.queue._fenced(session, self.claim)
            return await super().aput_writes(config, writes, task_id, task_path)
