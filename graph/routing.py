"""Pure bounded routing policies; no tools or model calls."""

from graph.state import TroubleshooterState


def evidence_sufficient(state: TroubleshooterState) -> bool:
    evidence = state["evidence"]
    if not evidence.items:return False
    if any(e.evidence_id.startswith('ev:') for e in evidence.items) and not all(e.evidence_id.startswith('ev:') for e in evidence.items):
        return False
    if all(e.evidence_id.startswith('ev:') for e in evidence.items):
        from tools.diagnostic_sufficiency import readiness
        return readiness(state['sanitized_inputs'],evidence).qualified_analysis
    return evidence.completeness=='complete' and not evidence.missing # Historical compatibility.


def next_evidence_source(state: TroubleshooterState) -> str | None:
    if state["retry_counts"]["log_investigator"] >= state["max_retries"]:
        return None
    return next((source for source in state["available_evidence_sources"]
                 if source not in state["consumed_evidence_sources"]), None)


def route_evidence(state: TroubleshooterState) -> str:
    if evidence_sufficient(state):
        return "root_cause_analyst"
    return "collect_evidence" if next_evidence_source(state) else "request_user_input"


def route_collected(state: TroubleshooterState) -> str:
    if state["stop_reason"] == "new_evidence_collected":
        return "log_investigator"
    return "collect_evidence" if next_evidence_source(state) else "request_user_input"


def route_diagnosis(state: TroubleshooterState) -> str:
    diagnosis = state["diagnosis"]
    if diagnosis and diagnosis.status in ("tentative", "supported") and diagnosis.selected_hypothesis_id:
        return "resolution_generator"
    return "collect_evidence" if next_evidence_source(state) else "request_user_input"


def route_resolution(state: TroubleshooterState) -> str:
    return "validator" if state["proposed_fix"] else "request_user_input"


def route_validation(state: TroubleshooterState) -> str:
    result = state["validation_results"][-1] if state["validation_results"] else None
    if result and result.status == "passed":
        return "final_report"
    if result and result.status == "failed":
        if state["retry_counts"]["validator"] < state["max_retries"]:
            return "retry_analysis"
        return "validation_exhausted"
    return "request_user_input"
