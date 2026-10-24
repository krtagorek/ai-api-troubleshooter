"""Shared contracts and runtime validation for the troubleshooting workflow.

Use initialize_state/validate_state at ingress and checkpoint boundaries.
TypedDict itself does not enforce runtime validation. Agent updates should be
validated through these helpers before persistence or use by another node.
"""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, TypeAdapter, model_validator
from typing_extensions import TypedDict, NotRequired

from models.diagnosis import Classification, Diagnosis, FinalReport, Hypothesis, ProposedFix
from models.evidence import EvidenceCollection, EvidenceGap
from models.incident import DomainModel, Incident, incident_from_record, sanitize
from models.validation import ValidationResult

AgentName = Literal["classifier", "log_investigator", "root_cause_analyst",
                    "resolution_generator", "validator"]
AGENT_NAMES = ("classifier", "log_investigator", "root_cause_analyst",
               "resolution_generator", "validator")


class ClassifierInput(DomainModel):
    incident: Incident
    evidence_catalog: list[dict] = Field(default_factory=list, description="Trusted scalar catalog. Select supplied short handles and echo the contract/snapshot; never invent IDs, paths or mappings.")
    evidence_contract: str | None = None
    evidence_snapshot: str | None = None


class EvidenceContractOutput(DomainModel):
    evidence_contract: str | None = Field(default=None, description="Copy evidence_contract from the supplied envelope when selecting short handles.")
    evidence_snapshot: str | None = Field(default=None, description="Copy evidence_snapshot exactly. Handles have no meaning outside this snapshot.")


class ClassifierOutput(EvidenceContractOutput):
    classification: Classification


class LogInvestigatorInput(ClassifierInput):
    classification: Classification


class LogInvestigatorOutput(EvidenceContractOutput):
    evidence: EvidenceCollection = Field(default_factory=EvidenceCollection)
    selected_evidence_ids: list[str] = Field(default_factory=list, description="Legacy cryptographic ID output only, retained for historical compatibility. New responses use selected_evidence_handles and contract/snapshot, never combine formats.")
    selected_evidence_handles: list[str] = Field(default_factory=list, description="Preferred: copy handles such as E01 from the supplied catalog; also copy its evidence_contract and evidence_snapshot. Never define mappings or combine with legacy selections.")


class LiveLogInvestigatorOutput(DomainModel):
    """Live-only handle selection. Legacy evidence objects are not part of this schema."""
    evidence_contract: Literal['evidence-handles-v1']
    evidence_snapshot: str
    selected_evidence_handles: list[str]
    completeness: Literal['unknown','complete','partial','missing','conflicting']
    diagnostic_gaps: list[EvidenceGap]


class RootCauseAnalystInput(LogInvestigatorInput):
    evidence: EvidenceCollection
    analysis_readiness: dict = Field(default_factory=dict,description='Trusted routing assessment, not proof of causal truth or verification.')


class RootCauseAnalystOutput(EvidenceContractOutput):
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    diagnosis: Diagnosis


class ResolutionGeneratorInput(RootCauseAnalystInput):
    hypotheses: list[Hypothesis]
    diagnosis: Diagnosis


class ResolutionGeneratorOutput(EvidenceContractOutput):
    proposed_fix: ProposedFix | None = None
    abstention_reason: str | None = None

    @model_validator(mode="after")
    def fix_or_abstention(self):
        if self.proposed_fix is None and not self.abstention_reason:
            raise ValueError("Resolution requires a proposed fix or an abstention reason")
        if self.proposed_fix is not None and self.abstention_reason:
            raise ValueError("Resolution cannot both propose a fix and abstain")
        return self


class ValidatorInput(ResolutionGeneratorInput):
    proposed_fix: ProposedFix | None


class ValidatorOutput(EvidenceContractOutput):
    validation: ValidationResult


class TrustedSemanticResult(DomainModel):
    version: Literal['trusted-semantic-1']
    status: Literal['qualified','held','denied']
    result_ref: str | None = Field(default=None, pattern=r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$')
    snapshot_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    result_digest: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


    @model_validator(mode='after')
    def reference_shape(self):
        if self.status in ('qualified','held') and (not self.result_ref or not self.result_digest):
            raise ValueError('Committed result reference and digest required')
        if self.status=='denied' and (self.result_ref is not None or self.result_digest is not None):
            raise ValueError('Denied evaluation has no committed result')
        return self


class TroubleshooterState(TypedDict):
    __pydantic_config__ = ConfigDict(extra="forbid", hide_input_in_errors=True)
    incident_id: Annotated[str, Field(min_length=1)]
    thread_id: Annotated[str, Field(min_length=1)]
    sanitized_inputs: Incident
    classification: Classification | None
    evidence: EvidenceCollection
    hypotheses: list[Hypothesis]
    diagnosis: Diagnosis | None
    proposed_fix: ProposedFix | None
    validation_results: list[ValidationResult]
    retry_counts: dict[AgentName, Annotated[int, Field(ge=0, strict=True)]]
    max_retries: Annotated[int, Field(ge=0, strict=True)]
    final_report: FinalReport | None
    status: Literal["pending", "running", "awaiting_evidence", "completed", "failed"]
    available_evidence_sources: list[str]
    consumed_evidence_sources: list[str]
    pending_user_questions: list[str]
    stop_reason: str | None
    trusted_semantic: NotRequired[TrustedSemanticResult | None]


def validate_state(value: dict) -> TroubleshooterState:
    state = TypeAdapter(TroubleshooterState).validate_python(sanitize(value))
    if state["incident_id"] != state["sanitized_inputs"].incident_id:
        raise ValueError("State incident_id does not match input")
    if set(state["retry_counts"]) != set(AGENT_NAMES):
        raise ValueError("Retry counts must include all five agents")
    if any(count > state["max_retries"] for count in state["retry_counts"].values()):
        raise ValueError("Retry count exceeds configured limit")
    semantic=state.get('trusted_semantic')
    if semantic:
        from diagnostic_contracts.ingestion import digest
        if semantic.snapshot_digest!=digest(state['sanitized_inputs'].model_dump(mode='json')):
            raise ValueError('Semantic result belongs to a different snapshot')
    evidence_ids = [item.evidence_id for item in state["evidence"].items]
    # Legacy report-local IDs remain readable. New catalog IDs are strictly
    # bound to this incident snapshot at state/checkpoint boundaries as well.
    from tools.evidence_catalog import resolve_evidence
    for item in state['evidence'].items:
        if item.evidence_id.startswith('ev:'):
            entry=resolve_evidence(state['sanitized_inputs'],item.evidence_id)
            if (item.reference,item.source,item.observation)!=(entry.reference,entry.source,entry.observation):
                raise ValueError('Catalog evidence differs from trusted incident observation')
    hypotheses = {item.hypothesis_id for item in state["hypotheses"]}
    if len(set(evidence_ids)) != len(evidence_ids) or len(hypotheses) != len(state["hypotheses"]):
        raise ValueError("Evidence and hypothesis IDs must be unique")
    for hypothesis in state["hypotheses"]:
        references = hypothesis.supporting_evidence_ids + hypothesis.contradicting_evidence_ids
        if not set(references) <= set(evidence_ids):
            raise ValueError("Hypothesis references unknown evidence")
        if any(not set(a.evidence_ids)<=set(evidence_ids) for a in hypothesis.evidence_assessments):
            raise ValueError("Evidence assessment references unknown evidence")
    diagnosis = state["diagnosis"]
    if diagnosis and not set(diagnosis.discriminating_evidence_ids) <= set(evidence_ids):
        raise ValueError("Diagnosis references unknown discriminating evidence")
    if diagnosis and diagnosis.selected_hypothesis_id and diagnosis.selected_hypothesis_id not in hypotheses:
        raise ValueError("Diagnosis references unknown hypothesis")
    report = state["final_report"]
    if report and report.incident_id != state["incident_id"]:
        raise ValueError("Report incident_id does not match state")
    # Apply ONLY after strict shape, identity and grounding checks. Provider
    # outputs remain intact in their separate audit; source evidence is immutable.
    from tools.output_safety import protect_generated, protect_report
    projected = dict(state)
    for key in ('classification','hypotheses','diagnosis','proposed_fix',
                'validation_results','pending_user_questions'):
        projected[key] = protect_generated(state[key], field=key)
    projected['evidence'] = state['evidence'].model_copy(update={
        'missing': [EvidenceGap.model_validate(protect_generated(g, gap=True))
                    for g in state['evidence'].missing]})
    if report:
        projected['final_report'] = protect_report(report)
    return TypeAdapter(TroubleshooterState).validate_python(projected)


def initialize_state(record, thread_id: str, *, max_retries: int = 2) -> TroubleshooterState:
    incident = incident_from_record(record)
    return validate_state({
        "incident_id": incident.incident_id, "thread_id": thread_id,
        "sanitized_inputs": incident, "classification": None,
        "evidence": EvidenceCollection(), "hypotheses": [], "diagnosis": None,
        "proposed_fix": None, "validation_results": [],
        "retry_counts": {name: 0 for name in AGENT_NAMES}, "max_retries": max_retries,
        "final_report": None, "status": "pending",
        "available_evidence_sources": [], "consumed_evidence_sources": [],
        "pending_user_questions": [], "stop_reason": None,
    })


def serialize_state(state: TroubleshooterState) -> str:
    """Revalidate mutable containers and redact again before checkpointing."""
    clean = validate_state(state)
    return TypeAdapter(TroubleshooterState).dump_json(clean).decode("utf-8")


def deserialize_state(payload: str) -> TroubleshooterState:
    import json

    return validate_state(json.loads(payload))
