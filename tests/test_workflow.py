"""All routing tests use deterministic injected agents and local collectors."""

import asyncio
from collections import Counter

import pytest

from data.generate_incidents import generate
from graph.routing import route_validation
from graph.state import initialize_state, validate_state
from graph.workflow import build_workflow
from models.diagnosis import Classification, Diagnosis, Hypothesis, ProposedFix
from models.evidence import Evidence, EvidenceCollection, EvidenceGap
from models.validation import ValidationCheck, ValidationResult


@pytest.fixture
def state():
    return initialize_state(generate()["development"][0], "workflow-thread")


def fixtures(*, sufficient=True, outcomes=("passed",), diagnosis="supported", fix=True):
    calls = Counter()

    def classify(state):
        calls["classifier"] += 1
        return {"classification": Classification(reasoning="Synthetic test")}

    def investigate(state):
        calls["investigator"] += 1
        enough = sufficient(state) if callable(sufficient) else sufficient
        return {"evidence": EvidenceCollection(
            items=[Evidence(evidence_id="e1", source="response", observation="Observed failure",
                            reference="/status_code", reliability=0.8)] if enough else [],
            completeness="complete" if enough else "missing",
            missing=[] if enough else [EvidenceGap(description="Trace unavailable",
                required_to_resolve="Provide sanitized upstream trace")])}

    def analyze(state):
        calls["analyst"] += 1
        status = diagnosis(state) if callable(diagnosis) else diagnosis
        hypotheses = [Hypothesis(hypothesis_id="h1", description="Candidate cause", confidence=0.7,
                                  supporting_evidence_ids=["e1"])] if status == "supported" else []
        return {"hypotheses": hypotheses, "diagnosis": Diagnosis(
            status=status, summary="Test diagnosis", selected_hypothesis_id="h1" if hypotheses else None,
            uncertainty=[] if hypotheses else ["No distinguishing evidence"])}

    def resolve(state):
        calls["resolution"] += 1
        return {"proposed_fix": ProposedFix(summary="Test proposal", steps=["Review config"],
            verification_steps=["Run controlled check"], evidence_ids=["e1"]) if fix else None}

    def validator(state):
        index = calls["validator"]
        calls["validator"] += 1
        status = outcomes[min(index, len(outcomes) - 1)]
        result = ValidationResult(status=status, checks=[ValidationCheck(
            name="simulated contract", status="passed", detail="Injected fixture check passed")]
            if status == "passed" else [])
        return {"validation_results": [*state["validation_results"], result]}

    return {"classifier": classify, "log_investigator": investigate,
            "root_cause_analyst": analyze, "resolution_generator": resolve,
            "validator": validator}, calls


def test_success_final_report_and_async_parity(state):
    agents, calls = fixtures()
    graph = build_workflow(agents=agents)
    result = graph.invoke(state)
    assert result["status"] == "completed"
    assert result["stop_reason"] == "validated"
    assert result["final_report"].validation_results[-1].status == "passed"
    assert calls == Counter(classifier=1, investigator=1, analyst=1, resolution=1, validator=1)
    validate_state(result)
    other, _ = fixtures()
    async_result = asyncio.run(build_workflow(agents=other).ainvoke(state))
    assert result == async_result


def test_missing_evidence_stops_without_sources(state):
    agents, calls = fixtures(sufficient=False)
    result = build_workflow(agents=agents).invoke(state)
    assert result["status"] == "awaiting_evidence"
    assert result["pending_user_questions"]
    assert result["final_report"] is None
    assert calls == Counter(classifier=1, investigator=1)


def test_new_source_reinvestigates_only_after_new_observation(state):
    original_count = len(state["sanitized_inputs"].logs)
    agents, calls = fixtures(sufficient=lambda s: len(s["sanitized_inputs"].logs) > original_count)
    seen = []

    def collect(incident):
        seen.append(incident.incident_id)
        return [{"message": "Fresh upstream observation", "Authorization": "Bearer must-redact"}]

    result = build_workflow(agents=agents, evidence_tools={"upstream": collect}).invoke(state)
    assert result["status"] == "completed"
    assert seen == [state["incident_id"]]
    assert calls["investigator"] == 2
    assert result["retry_counts"]["log_investigator"] == 1
    assert result["consumed_evidence_sources"] == ["upstream"]
    assert result["sanitized_inputs"].logs[-1]["Authorization"] == "[REDACTED]"


@pytest.mark.parametrize("response", [[], [{"root_cause": "hidden"}], [{"message": ""}], None, "invalid"])
def test_empty_tools_do_not_trigger_reinvestigation(state, response):
    agents, calls = fixtures(sufficient=False)
    result = build_workflow(agents=agents, evidence_tools={"empty": lambda _: response}).invoke(state)
    assert calls["investigator"] == 1
    assert result["consumed_evidence_sources"] == ["empty"]
    assert result["status"] == "awaiting_evidence"


def test_duplicate_evidence_is_not_new(state):
    agents, calls = fixtures(sufficient=False)
    result = build_workflow(agents=agents, evidence_tools={
        "duplicate": lambda incident: incident.logs}).invoke(state)
    assert calls["investigator"] == 1
    assert result["status"] == "awaiting_evidence"


def test_tool_error_does_not_leak_and_next_source_can_help(state):
    agents, calls = fixtures(sufficient=lambda s: bool(s["consumed_evidence_sources"]))

    def broken(_):
        raise RuntimeError("Bearer secret-exception")

    result = build_workflow(agents=agents, evidence_tools={
        "broken": broken, "working": lambda _: [{"message": "new observation"}]}).invoke(state)
    assert result["status"] == "completed"
    assert result["consumed_evidence_sources"] == ["broken", "working"]
    assert calls["investigator"] == 2
    assert "secret-exception" not in str(result)


@pytest.mark.parametrize("budget", [0, 1, 2])
def test_investigation_budget_and_consumption_prevent_loops(state, budget):
    state["max_retries"] = budget
    agents, calls = fixtures(sufficient=False)
    tools = {f"source-{i}": lambda _, i=i: [{"message": f"observation-{i}"}] for i in range(5)}
    result = build_workflow(agents=agents, evidence_tools=tools).invoke(state)
    assert result["status"] == "awaiting_evidence"
    assert calls["investigator"] == budget + 1
    assert len(result["consumed_evidence_sources"]) == budget
    assert result["retry_counts"]["log_investigator"] == budget


def test_consumed_source_never_reused(state):
    state["consumed_evidence_sources"] = ["used"]
    agents, calls = fixtures(sufficient=False)
    def forbidden(_):
        pytest.fail("Consumed source must not execute again")
    result = build_workflow(agents=agents, evidence_tools={"used": forbidden}).invoke(state)
    assert calls["investigator"] == 1
    assert result["status"] == "awaiting_evidence"


@pytest.mark.parametrize("budget", [0, 1, 2])
def test_failed_validation_is_bounded(state, budget):
    state["max_retries"] = budget
    agents, calls = fixtures(outcomes=("failed",))
    result = build_workflow(agents=agents).invoke(state)
    assert result["status"] == "failed"
    assert result["stop_reason"] == "validation_retries_exhausted"
    assert calls["validator"] == calls["analyst"] == budget + 1
    assert calls["investigator"] == 1
    assert result["retry_counts"]["validator"] == budget
    assert len(result["validation_results"]) == budget + 1
    assert "failed" in result["final_report"].summary.lower()


def test_validation_failure_returns_to_analysis_then_succeeds(state):
    agents, calls = fixtures(outcomes=("failed", "passed"))
    result = build_workflow(agents=agents).invoke(state)
    assert result["status"] == "completed"
    assert calls["analyst"] == calls["resolution"] == 2
    assert result["retry_counts"]["validator"] == 1
    assert [r.status for r in result["validation_results"]] == ["failed", "passed"]


@pytest.mark.parametrize("outcome", ["NOT_TESTED", "not_run", "inconclusive"])
def test_untested_validation_never_reports_success(state, outcome):
    agents, calls = fixtures(outcomes=(outcome,))
    result = build_workflow(agents=agents).invoke(state)
    assert result["status"] == "awaiting_evidence"
    assert result["final_report"] is None
    assert any("validation" in q for q in result["pending_user_questions"])
    assert calls["validator"] == 1


def test_inconclusive_diagnosis_routes_to_new_source_or_user(state):
    agents, calls = fixtures(diagnosis="inconclusive")
    result = build_workflow(agents=agents, evidence_tools={
        "upstream": lambda _: [{"message": "fresh trace"}]}).invoke(state)
    assert calls["analyst"] == 2
    assert calls["resolution"] == 0
    assert result["status"] == "awaiting_evidence"
    assert result["consumed_evidence_sources"] == ["upstream"]


def test_resolution_abstention_requests_input(state):
    agents, calls = fixtures(fix=False)
    result = build_workflow(agents=agents).invoke(state)
    assert result["status"] == "awaiting_evidence"
    assert calls["validator"] == 0


def test_async_dependencies_support_both_interfaces(state):
    agents, _ = fixtures()
    original = agents["classifier"]
    async def classify(value):
        return original(value)
    agents["classifier"] = classify
    async def source(_):
        return [{"message": "async evidence"}]
    # Force collection before the successful path.
    count = len(state["sanitized_inputs"].logs)
    others, _ = fixtures(sufficient=lambda s: len(s["sanitized_inputs"].logs) > count)
    others["classifier"] = classify
    graph = build_workflow(agents=others, evidence_tools={"async": source})
    assert graph.invoke(state)["status"] == "completed"
    assert asyncio.run(graph.ainvoke(state))["status"] == "completed"


def test_agent_cannot_override_routing_budgets(state):
    agents, _ = fixtures()
    agents["classifier"] = lambda _: {"max_retries": 999}
    with pytest.raises(ValueError, match="outside its workflow contract"):
        build_workflow(agents=agents).invoke(state)


def test_validator_must_supply_fresh_result(state):
    agents, _ = fixtures()
    agents["validator"] = lambda _: {"validation_results": []}
    with pytest.raises(ValueError, match="append exactly one"):
        build_workflow(agents=agents).invoke(state)
    assert route_validation(state) == "request_user_input"


def test_configuration_rejects_unknown_agents():
    with pytest.raises(ValueError, match="Unknown agent"):
        build_workflow(agents={"unknown": lambda _: {}})
