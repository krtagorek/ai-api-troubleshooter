"""Classifier, analyst, resolution, and final report output contracts."""

from typing import Literal

from pydantic import Field, model_validator

from models.evidence import EvidenceCollection, EvidenceGap
from models.incident import DomainModel
from models.validation import ValidationResult

Category = Literal["authentication", "authorization", "validation", "rate_limiting",
                   "timeout", "server_error", "schema_mismatch", "dependency_failure", "unknown"]


class Classification(DomainModel):
    category: Category = "unknown"
    confidence: float = Field(default=0, ge=0, le=1)
    reasoning: str = Field(min_length=1)
    evidence_references: list[str] = Field(default_factory=list, description="Copy supplied short handles such as E01 and echo evidence_contract/evidence_snapshot in the output envelope. Cryptographic IDs and incident-relative pointers are legacy compatibility formats only.")


class EvidenceAssessment(DomainModel):
    relation: Literal['supporting','contradicting','neutral','missing']
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)
    hypothesis_prediction: str | None = None

    @model_validator(mode='after')
    def causal_explanation(self):
        if self.relation=='missing' and self.evidence_ids:
            raise ValueError('Missing evidence is not an observed reference')
        if self.relation!='missing' and not self.evidence_ids:
            raise ValueError('Observed assessment requires evidence references')
        if self.relation=='contradicting' and (not self.hypothesis_prediction or len(self.explanation.strip())<12):
            raise ValueError('Contradiction requires a causal explanation and hypothesis prediction')
        return self


class Hypothesis(DomainModel):
    hypothesis_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    evidence_assessments: list[EvidenceAssessment] = Field(default_factory=list,
        description='Classify supporting, contradicting, neutral and missing evidence. Contradiction needs a causal explanation of which hypothesis prediction the observations exclude. Missing evidence has no observed references.')


class Diagnosis(DomainModel):
    status: Literal["unknown", "tentative", "supported", "inconclusive"] = "unknown"
    summary: str = Field(min_length=1)
    selected_hypothesis_id: str | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    uncertainty: list[str] = Field(default_factory=list)
    missing_evidence: list[EvidenceGap] = Field(default_factory=list)
    discriminating_evidence_ids: list[str] = Field(default_factory=list, description="Existing IDs distinguishing the selected cause from alternatives, not merely establishing a symptom. Empty if unavailable.")
    diagnostic_checks: list[str] = Field(default_factory=list, description="Proposed read-only checks to distinguish causes; never claim they were executed.")

    @model_validator(mode="after")
    def supported_requires_hypothesis(self):
        if self.status == "supported" and not self.selected_hypothesis_id:
            raise ValueError("Supported diagnosis requires a hypothesis reference")
        return self


class ProposedFix(DomainModel):
    plan_kind: Literal["diagnostic", "remediation"] = Field(default="remediation", description="diagnostic means read-only checks; remediation includes configuration changes and requires a supported diagnosis plus human approval.")
    summary: str = Field(min_length=1)
    steps: list[str] = Field(min_length=1)
    risks: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    verification_steps: list[str] = Field(min_length=1)
    requires_human_approval: bool = True
    evidence_ids: list[str] = Field(default_factory=list)


class FinalReport(DomainModel):
    incident_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    diagnosis: Diagnosis
    proposed_fix: ProposedFix | None = None
    evidence: EvidenceCollection
    validation_results: list[ValidationResult] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
