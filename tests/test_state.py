"""Phase 4 schema, leakage, checkpoint, and state-boundary tests."""

import json

import pytest
from pydantic import ValidationError

from data.generate_incidents import DEFAULT_OUTPUT, generate, inference_view
from graph.state import (
    AGENT_NAMES, ClassifierInput, ClassifierOutput, LogInvestigatorInput,
    LogInvestigatorOutput, ResolutionGeneratorInput, ResolutionGeneratorOutput,
    RootCauseAnalystInput, RootCauseAnalystOutput, ValidatorInput, ValidatorOutput,
    deserialize_state, initialize_state, serialize_state, validate_state,
)
from models.diagnosis import Classification, Diagnosis, FinalReport, Hypothesis, ProposedFix
from models.evidence import Evidence, EvidenceCollection, EvidenceGap
from models.incident import Incident, incident_from_record
from models.validation import ValidationCheck, ValidationResult


@pytest.fixture
def record():
    return generate()["development"][0].model_dump()


def test_initial_defaults_and_independent_mutable_values(record):
    first = initialize_state(record, "thread-1")
    second = initialize_state(record, "thread-2")
    assert first["incident_id"] == record["incident_id"]
    assert first["status"] == "pending"
    assert first["classification"] is first["diagnosis"] is first["proposed_fix"] is None
    assert first["final_report"] is None
    assert first["validation_results"] == first["hypotheses"] == []
    assert first["evidence"].completeness == "unknown"
    assert first["retry_counts"] == dict.fromkeys(AGENT_NAMES, 0)
    first["retry_counts"]["classifier"] += 1
    first["evidence"].missing.append(EvidenceGap(
        description="Upstream trace missing", required_to_resolve="Retrieve trace"))
    assert second["retry_counts"]["classifier"] == 0
    assert second["evidence"].missing == []


def test_all_saved_datasets_map_without_labels():
    for path in DEFAULT_OUTPUT.rglob("*.jsonl"):
        for line in path.read_text().splitlines():
            original = json.loads(line)
            state = initialize_state(original, "mapping-thread")
            encoded = serialize_state(state)
            assert '"root_cause":' not in encoded
            assert '"expected_resolution":' not in encoded
            for key in ("root_cause", "expected_resolution"):
                if key in original:
                    assert original[key] not in encoded
            incident = state["sanitized_inputs"]
            assert incident.status_code == original["status_code"]
            assert incident.evidence_quality == original["request_metadata"]["evidence_quality"]
            assert deserialize_state(encoded) == state


def test_mapping_accepts_pydantic_dataset_records():
    labeled = generate()["test"][0]
    assert incident_from_record(labeled) == incident_from_record(inference_view(labeled))


def test_secret_redaction_at_ingestion_and_checkpoint(record):
    record["request_metadata"]["headers"].update({
        "Authorization": "Bearer top-secret-auth", "X-API-Key": "raw-key",
        "Cookie": "session=raw-session"})
    record["response_body"] = {"nested": [{"password": "raw-password"}],
                               "root_cause": "secret-label"}
    record["logs"].append({"message": "Bearer raw-bearer password=raw-pass sk-demo123",
                           "url": "https://user:raw-url@example.invalid/api?token=raw-query"})
    state = initialize_state(record, "thread-safe")
    # Simulate accidental mutation by a future node; serialization sanitizes again.
    state["sanitized_inputs"].request_metadata["access_token"] = "raw-mutation"
    encoded = serialize_state(state)
    for secret in ("top-secret-auth", "raw-key", "raw-session", "raw-password",
                   "secret-label", "raw-bearer", "raw-pass", "sk-demo123", "raw-url",
                   "raw-query", "raw-mutation"):
        assert secret not in encoded
    assert "[REDACTED]" in encoded


def test_five_agent_contracts_and_model_serialization(record):
    incident = incident_from_record(record)
    classification = Classification(reasoning="Insufficient evidence")
    evidence = EvidenceCollection(items=[Evidence(
        evidence_id="e1", source="response", observation="Request rejected", reliability=0.7)],
        missing=[EvidenceGap(description="No upstream trace", required_to_resolve="Collect trace")],
        completeness="partial")
    hypothesis = Hypothesis(hypothesis_id="h1", description="Possible upstream issue",
                            confidence=0.4, supporting_evidence_ids=["e1"])
    diagnosis = Diagnosis(summary="Uncertain diagnosis", status="tentative",
                          selected_hypothesis_id="h1", uncertainty=["Missing upstream trace"])
    fix = ProposedFix(summary="Investigate upstream", steps=["Collect sanitized trace"],
                      verification_steps=["Compare upstream response"])
    validation = ValidationResult(status="inconclusive", issues=["Cannot reproduce"])
    base = {"incident": incident, "classification": classification}
    analysis = {**base, "evidence": evidence}
    resolution = {**analysis, "hypotheses": [hypothesis], "diagnosis": diagnosis}
    contracts = [
        ClassifierInput(incident=incident), ClassifierOutput(classification=classification),
        LogInvestigatorInput(**base), LogInvestigatorOutput(evidence=evidence),
        RootCauseAnalystInput(**analysis),
        RootCauseAnalystOutput(hypotheses=[hypothesis], diagnosis=diagnosis),
        ResolutionGeneratorInput(**resolution), ResolutionGeneratorOutput(proposed_fix=fix),
        ValidatorInput(**resolution, proposed_fix=fix), ValidatorOutput(validation=validation),
    ]
    for contract in contracts:
        assert type(contract).model_validate_json(contract.model_dump_json()) == contract
    state = initialize_state(record, "report-thread")
    state.update(evidence=evidence, hypotheses=[hypothesis], diagnosis=diagnosis,
                 proposed_fix=fix, validation_results=[validation], final_report=FinalReport(
                     incident_id=incident.incident_id, summary="Investigation pending",
                     diagnosis=diagnosis, proposed_fix=fix, evidence=evidence))
    assert deserialize_state(serialize_state(state)) == state


@pytest.mark.parametrize("updates", [
    {"incident_id": "wrong"}, {"thread_id": ""}, {"max_retries": -1},
    {"max_retries": True}, {"retry_counts": {}}, {"unexpected": "field"},
    {"status": "invalid"},
])
def test_invalid_states(record, updates):
    state = initialize_state(record, "valid-thread")
    with pytest.raises((ValidationError, ValueError)):
        validate_state({**state, **updates})


def test_retry_limits_and_evidence_references(record):
    state = initialize_state(record, "valid-thread")
    state["retry_counts"]["classifier"] = 3
    with pytest.raises(ValueError, match="Retry count"):
        validate_state(state)
    state["retry_counts"]["classifier"] = 0
    state["hypotheses"] = [Hypothesis(hypothesis_id="h1", description="Possible cause",
                                      confidence=0.2, supporting_evidence_ids=["missing"])]
    with pytest.raises(ValueError, match="unknown evidence"):
        validate_state(state)
    state["hypotheses"] = []
    state["diagnosis"] = Diagnosis(summary="Candidate", selected_hypothesis_id="missing")
    with pytest.raises(ValueError, match="unknown hypothesis"):
        validate_state(state)


@pytest.mark.parametrize("factory,kwargs", [
    (Classification, {"reasoning": "Invalid confidence", "confidence": 1.1}),
    (Hypothesis, {"hypothesis_id": "h1", "description": "Invalid", "confidence": -0.1}),
    (Diagnosis, {"summary": "Unsupported", "status": "supported"}),
    (ProposedFix, {"summary": "Empty", "steps": [], "verification_steps": []}),
    (ValidationResult, {"status": "passed"}),
    (ValidationResult, {"status": "passed", "checks": [ValidationCheck(
        name="contract", status="failed", detail="Mismatch")]}),
])
def test_invalid_domain_models(factory, kwargs):
    with pytest.raises(ValidationError):
        factory(**kwargs)


def test_domain_unknown_fields_and_assignment_validation(record):
    with pytest.raises(ValidationError):
        Incident.model_validate({**incident_from_record(record).model_dump(), "extra": True})
    classification = Classification(reasoning="Unknown")
    with pytest.raises(ValidationError):
        classification.confidence = -1


@pytest.mark.parametrize("text,secret", [
    ('{"password":"quoted-value"}', "quoted-value"),
    ("-----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----", "private-material"),
    ("eyJhbGciOiJIUzI1NiJ9.payload.signature", "eyJhbGciOiJIUzI1NiJ9.payload.signature"),
    ("Authorization: Bearer demo-credential", "demo-credential"),
])
def test_free_text_credential_redaction(record, text, secret):
    state = initialize_state(record, "safe-thread")
    state["diagnosis"] = Diagnosis(summary=text)
    assert secret not in serialize_state(state)


def test_explicit_resolution_abstention():
    output = ResolutionGeneratorOutput(abstention_reason="Need upstream trace before proposing a fix")
    assert output.proposed_fix is None
    with pytest.raises(ValidationError):
        ResolutionGeneratorOutput()
