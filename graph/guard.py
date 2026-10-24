"""Validate before LangGraph's input checkpoint; resume without restarting nodes."""

from langchain_core.runnables import Runnable
from langgraph.types import Command

from graph.state import validate_state
from tools.redaction import redact


class GuardedWorkflow(Runnable):
    def __init__(self, compiled):
        self.compiled = compiled

    def _input(self, value, config):
        if value is None:
            return None  # LangGraph resume: keep saved channels and next tasks.
        if isinstance(value, Command):
            if value.update is not None or value.goto or value.graph is not None:
                raise ValueError("Resume commands cannot override state or routing")
            return Command(resume=redact(value.resume))
        clean = validate_state(value)
        thread = (config or {}).get("configurable", {}).get("thread_id")
        if thread is not None and thread != clean["thread_id"]:
            raise ValueError("Checkpoint thread does not match state")
        return clean

    def invoke(self, input, config=None, **kwargs):
        config = {"recursion_limit": 100, **(config or {})}
        return self.compiled.invoke(self._input(input, config), config, **kwargs)

    async def ainvoke(self, input, config=None, **kwargs):
        config = {"recursion_limit": 100, **(config or {})}
        return await self.compiled.ainvoke(self._input(input, config), config, **kwargs)

    def get_state(self, config):
        return self.compiled.get_state(config)

    async def aget_state(self, config):
        return await self.compiled.aget_state(config)

    def get_graph(self, config=None):
        return self.compiled.get_graph(config)
