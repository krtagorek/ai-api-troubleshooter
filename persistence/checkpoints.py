"""Safe AsyncPostgresSaver: no pickle, arbitrary imports, or raw errors."""

import json
from uuid import UUID

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Interrupt
from pydantic import BaseModel

from models.diagnosis import Classification, Diagnosis, FinalReport, Hypothesis, ProposedFix
from models.evidence import Evidence, EvidenceCollection, EvidenceGap
from models.incident import Incident
from models.validation import ValidationCheck, ValidationResult
from graph.state import TrustedSemanticResult
from tools.redaction import LABELS, REDACTED, SENSITIVE, redact

MODELS = {cls.__name__: cls for cls in (
    Incident, Classification, Diagnosis, FinalReport, Hypothesis, ProposedFix,
    Evidence, EvidenceCollection, EvidenceGap, ValidationCheck, ValidationResult, TrustedSemanticResult)}


def clean(value):
    if isinstance(value, BaseModel):
        if type(value) not in MODELS.values():
            raise TypeError("Unsupported checkpoint model")
        return type(value).model_validate(redact(value.model_dump(mode="json")))
    if isinstance(value, Interrupt):
        return Interrupt(value=clean(value.value), id=redact(value.id))
    if isinstance(value, dict):
        return {key: REDACTED if SENSITIVE.search(str(key)) else clean(item)
                for key, item in value.items() if str(key) not in LABELS}
    if isinstance(value, tuple):
        return tuple(clean(item) for item in value)
    if isinstance(value, list):
        return [clean(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return redact(value)
    raise TypeError("Unsupported checkpoint value")


class SanitizedSerializer:
    """Versioned JSON encoding with explicit tags and a closed type registry.

    Every dictionary is wrapped, so external JSON cannot impersonate type tags.
    No imports, eval, pickle, or database-provided constructors are used.
    """
    def _encode(self, value):
        if isinstance(value, BaseModel):
            return ["model", type(value).__name__, self._encode(value.model_dump(mode="json"))]
        if isinstance(value, Interrupt):
            return ["interrupt", self._encode(value.value), value.id]
        if isinstance(value, dict):
            return ["dict", [[key, self._encode(item)] for key, item in value.items()]]
        if isinstance(value, (list, tuple)):
            return ["tuple" if isinstance(value, tuple) else "list", [self._encode(item) for item in value]]
        return ["scalar", value]

    def _decode(self, value):
        tag = value[0]
        if tag == "model" and value[1] in MODELS:
            return MODELS[value[1]].model_validate(self._decode(value[2]))
        if tag == "interrupt":
            return Interrupt(value=self._decode(value[1]), id=value[2])
        if tag == "dict":
            return {key: self._decode(item) for key, item in value[1]}
        if tag in ("list", "tuple"):
            result = [self._decode(item) for item in value[1]]
            return tuple(result) if tag == "tuple" else result
        if tag == "scalar" and (value[1] is None or isinstance(value[1], (str, bool, int, float))):
            return value[1]
        raise ValueError("Unsupported checkpoint encoding")

    def dumps_typed(self, value):
        return "safe-json-v1", json.dumps(self._encode(clean(value)), allow_nan=False).encode()

    def loads_typed(self, value):
        if value[0] != "safe-json-v1":
            raise ValueError("Unsupported checkpoint serialization version")
        return clean(self._decode(json.loads(value[1])))


def safe_config(config):
    values = config["configurable"]
    try:
        thread_id = str(UUID(values["thread_id"]))
    except Exception:
        raise ValueError("Persistent threads require a UUID") from None
    result = {"thread_id": thread_id, "checkpoint_ns": ""}
    if values.get("checkpoint_ns", "") != "":
        raise ValueError("This workflow does not support checkpoint subgraphs")
    if values.get("checkpoint_id"):
        result["checkpoint_id"] = str(UUID(values["checkpoint_id"]))
    return {"configurable": result}


class SanitizedAsyncPostgresSaver(AsyncPostgresSaver):
    def __init__(self, connection):
        super().__init__(connection, serde=SanitizedSerializer())

    async def aput(self, config, checkpoint, metadata, new_versions):
        # Primitive channel values/metadata bypass serde in the upstream saver.
        return await super().aput(safe_config(config), clean(checkpoint), redact(metadata), new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        safe_writes = [(channel, "Workflow node failed; details omitted" if channel == "__error__" else clean(value))
                       for channel, value in writes]
        return await super().aput_writes(safe_config(config), safe_writes, redact(task_id), redact(task_path))
