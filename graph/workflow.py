"""Bounded LangGraph workflow with injectable agents and evidence collectors.

Evidence tools are trusted application adapters, never selected by model text.
Each tool accepts a sanitized Incident and returns a list of structured log
records. It must supply actual observations, not benchmark answer labels.
No evidence tools or validation execution tools are enabled by default.
"""

import asyncio
import inspect
import json

from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph

from agents import error_classifier, log_investigator, resolution, root_cause, validator
from agents.common import inconclusive
from graph.routing import (next_evidence_source, route_collected, route_diagnosis,
                           route_evidence, route_resolution, route_validation)
from graph.state import AGENT_NAMES, TroubleshooterState, validate_state
from graph.guard import GuardedWorkflow
from models.diagnosis import FinalReport
from models.evidence import EvidenceCollection
from models.incident import Incident, sanitize

DEFAULT_AGENTS = {
    "classifier": error_classifier.error_classifier,
    "log_investigator": log_investigator.log_investigator,
    "root_cause_analyst": root_cause.root_cause,
    "resolution_generator": resolution.resolution,
    "validator": validator.validator,
}
OWNED_FIELDS = {
    "classifier": {"classification"}, "log_investigator": {"evidence"},
    "root_cause_analyst": {"hypotheses", "diagnosis"},
    "resolution_generator": {"proposed_fix", "diagnosis"},
    "validator": {"validation_results"},
}


def _sync_call(dependency, value):
    result = dependency.invoke(value) if hasattr(dependency, "invoke") else dependency(value)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


async def _async_call(dependency, value):
    if hasattr(dependency, "ainvoke"):
        return await dependency.ainvoke(value)
    if inspect.iscoroutinefunction(dependency):
        return await dependency(value)
    return await asyncio.to_thread(_sync_call, dependency, value)


def _update(state, update):
    clean = validate_state({**state, **update})
    return {key: clean[key] for key in update}


def _agent_node(name, dependency):
    def process(state, result):
        if not isinstance(result, dict) or not set(result) <= OWNED_FIELDS[name]:
            raise ValueError("Agent returned fields outside its workflow contract")
        if name == "validator":
            previous = state["validation_results"]
            results = result.get("validation_results", [])
            if len(results) != len(previous) + 1 or results[:-1] != previous:
                raise ValueError("Validator must append exactly one result")
        return _update(state, result)

    def sync(state):
        clean = validate_state(state)
        return process(clean, _sync_call(dependency, validate_state(clean)))

    async def async_(state):
        clean = validate_state(state)
        return process(clean, await _async_call(dependency, validate_state(clean)))

    return RunnableLambda(sync, afunc=async_)


def _collector_node(tools):
    def prepare(state):
        clean = validate_state(state)
        source = next_evidence_source(clean)
        return clean, source

    def finish(state, source, result):
        if source is None:
            return _update(state, {"stop_reason": "no_new_evidence_source"})
        counts = dict(state["retry_counts"])
        counts["log_investigator"] += 1
        # Deduplicate by content: a new tool name alone is not new evidence.
        original = state["sanitized_inputs"]
        known = {json.dumps(log, sort_keys=True) for log in original.logs}
        logs = []
        if isinstance(result, list):
            for log in sanitize(result):
                if isinstance(log, dict) and isinstance(log.get("message"), str) and log["message"].strip() not in ("", "[REDACTED]"):
                    fingerprint = json.dumps(log, sort_keys=True)
                    if fingerprint not in known:
                        logs.append(log)
                        known.add(fingerprint)
        update = {"consumed_evidence_sources": [*state["consumed_evidence_sources"], source],
                  "retry_counts": counts, "stop_reason": "no_new_evidence"}
        if logs:
            incident = Incident.model_validate({**original.model_dump(), "logs": [*original.logs, *logs]})
            if 'trusted_semantic' in state:update['trusted_semantic']=None
            update.update(sanitized_inputs=incident, hypotheses=[], diagnosis=None,
                          proposed_fix=None, final_report=None, stop_reason="new_evidence_collected")
        return _update(state, update)

    def sync(state):
        clean, source = prepare(state)
        try:
            result = _sync_call(tools[source], clean["sanitized_inputs"].model_copy(deep=True)) if source else []
        except Exception:
            result = []  # No raw provider exception or secret is persisted.
        return finish(clean, source, result)

    async def async_(state):
        clean, source = prepare(state)
        try:
            result = await _async_call(tools[source], clean["sanitized_inputs"].model_copy(deep=True)) if source else []
        except Exception:
            result = []
        return finish(clean, source, result)

    return RunnableLambda(sync, afunc=async_)


def _request_user(state):
    state = validate_state(state)
    diagnosis = state["diagnosis"] or inconclusive("Additional evidence or validation execution is required")
    questions = [gap.required_to_resolve for gap in state["evidence"].missing]
    questions += [gap.required_to_resolve for gap in diagnosis.missing_evidence]
    latest = state["validation_results"][-1] if state["validation_results"] else None
    if latest and latest.status=='NOT_TESTED' and state.get('trusted_semantic') is None:
        from graph.report_policy import legacy_tentative_report_decision
        decision=legacy_tentative_report_decision(state)
        if not decision.eligible:
            messages={
                'diagnostic_output_safety_review_required':'Review withheld diagnostic wording with the responsible owner; provide only sanitized nonsecret metadata.',
                'read_only_plan_required':'Obtain a human-reviewed diagnostic-only plan; no operational changes are authorized.',
                'unsupported_authority_or_certainty_claim':'Review unsupported authority or certainty claims; user-supplied observations do not establish verified provenance.',
            }
            return _update(state,{'status':'awaiting_evidence','diagnosis':diagnosis,
                'final_report':None,
                'pending_user_questions':[messages.get(decision.reason,
                    'Review the qualified hypothesis, grounded comparisons and evidence assessments before report publication; validation remains NOT_TESTED.')],
                'stop_reason':decision.reason})
    if latest and latest.status in ("NOT_TESTED", "not_run", "inconclusive"):
        questions.append("Provide real or simulated validation results for the proposed fix")
    if not questions:
        questions = ["Provide additional sanitized upstream logs, response details, or contract documentation"]
    return _update(state, {"status": "awaiting_evidence", "diagnosis": diagnosis,
                          "pending_user_questions": list(dict.fromkeys(questions)),
                          "stop_reason": "user_input_required"})


def _final_report(state):
    state = validate_state(state)
    from graph.report_policy import tentative_report_eligible,legacy_tentative_report_decision
    legacy=legacy_tentative_report_decision(state).eligible
    tentative=tentative_report_eligible(state) or legacy
    passed = bool(state["validation_results"]) and state["validation_results"][-1].status == "passed"
    successful = passed and state["proposed_fix"] is not None
    report = FinalReport(
        incident_id=state["incident_id"],
        summary="Tentative diagnostic analysis completed; validation NOT_TESTED and human approval required" if tentative else "Configured validation checks passed; proposed fix steps were not executed" if successful else "Validation failed; retry budget exhausted",
        diagnosis=state["diagnosis"] or inconclusive("Diagnosis unavailable"),
        proposed_fix=state["proposed_fix"], evidence=state["evidence"],
        validation_results=state["validation_results"],
        limitations=(["Qualified hypothesis only; upstream cause is not definitively confirmed", "Validation NOT_TESTED; no checks or fix steps were executed", "Diagnostic plan requires human approval"]+
            (["User-supplied observations; no signed ingestion provenance or trusted diagnostic-contract authority was verified"] if legacy else [])) if tentative else ["Workflow does not execute proposed fix steps"] if successful else ["No validated fix is available"],
    )
    return _update(state, {"final_report": report, "status": "completed" if successful or tentative else "failed",
                          "stop_reason": "tentative_diagnostic_report" if tentative else "validated" if successful else "validation_retries_exhausted"})


def build_workflow(*, agents=None, evidence_tools=None, validation_tool=None, checkpointer=None,
                   interrupt_before=None, interrupt_after=None, trusted_semantics_enabled=None, trusted_semantics=None):
    """Return a guarded LangGraph runnable supporting invoke/ainvoke.

    agents overrides any of the five AGENT_NAMES with a callable or Runnable.
    evidence_tools maps stable source IDs to sync/async observation collectors.
    Retry budgets are per invocation state, not per thread-global Python object.
    """
    from graph.semantic_gate import enabled, wrap, route as semantic_route
    semantic_enabled=enabled(trusted_semantics_enabled)
    nodes = {**DEFAULT_AGENTS, **(agents or {})}
    if validation_tool is not None:
        if agents and "validator" in agents:
            raise ValueError("Configure either a validator agent or a validation tool")
        nodes["validator"] = validator.create_agent(validation_tool=validation_tool)
    if set(nodes) != set(AGENT_NAMES):
        raise ValueError("Unknown agent name")
    tools = dict(evidence_tools or {})
    if any(not isinstance(key, str) or not key for key in tools):
        raise ValueError("Evidence tools require nonempty source IDs")
    graph = StateGraph(TroubleshooterState)

    def start(state):
        clean = validate_state(state)
        return _update(clean, {"status": "running", "final_report": None,
            "classification": None, "evidence": EvidenceCollection(),
            "hypotheses": [], "diagnosis": None, "proposed_fix": None,
            "pending_user_questions": [], "stop_reason": None,
            "available_evidence_sources": list(tools), **({"trusted_semantic": None} if semantic_enabled else {})})

    def retry(state):
        counts = dict(state["retry_counts"])
        counts["validator"] += 1
        return _update(state, {"retry_counts": counts, "hypotheses": [], "diagnosis": None,
                               "proposed_fix": None, "final_report": None})

    graph.add_node("initialize", start)
    for name, dependency in nodes.items():
        node=_agent_node(name, dependency)
        if semantic_enabled and name in ('log_investigator','root_cause_analyst','resolution_generator','validator'):
            node=wrap(name,node,trusted_semantics,validation_authorized=validation_tool is not None)
        graph.add_node(name,node)
    graph.add_node("collect_evidence", _collector_node(tools))
    graph.add_node("request_user_input", _request_user)
    graph.add_node("retry_analysis", retry)
    graph.add_node("validation_exhausted", lambda state: {})
    if semantic_enabled:
        graph.add_node("final_report", wrap('final_report',RunnableLambda(_final_report),trusted_semantics,validation_authorized=validation_tool is not None))
    else:
        graph.add_node("final_report", _final_report)
    graph.add_edge(START, "initialize")
    graph.add_edge("initialize", "classifier")
    graph.add_edge("classifier", "log_investigator")
    def validation_route(state):
        from graph.report_policy import tentative_report_eligible,legacy_tentative_report_decision
        eligible=(tentative_report_eligible(state) if semantic_enabled else
                  legacy_tentative_report_decision(state).eligible)
        return 'final_report' if eligible else route_validation(state)

    for source, router, destinations in [
        ("log_investigator", semantic_route if semantic_enabled else route_evidence, ["root_cause_analyst", "collect_evidence", "request_user_input"]),
        ("collect_evidence", route_collected, ["log_investigator", "collect_evidence", "request_user_input"]),
        ("root_cause_analyst", route_diagnosis, ["resolution_generator", "collect_evidence", "request_user_input"]),
        ("resolution_generator", route_resolution, ["validator", "request_user_input"]),
        ("validator", validation_route, ["final_report", "retry_analysis", "validation_exhausted", "request_user_input"]),
    ]:
        graph.add_conditional_edges(source, router, {name: name for name in destinations})
    graph.add_edge("retry_analysis", "root_cause_analyst")
    graph.add_edge("validation_exhausted", "final_report")
    graph.add_edge("request_user_input", END)
    graph.add_edge("final_report", END)
    compiled = graph.compile(checkpointer=checkpointer, interrupt_before=interrupt_before,
                             interrupt_after=interrupt_after)
    # Safety net beyond the explicit counters. Set high enough for typical budgets;
    # callers using large custom budgets should set config.recursion_limit too.
    # LangGraph checkpoints its input before initialize runs. Sanitize OUTSIDE
    # the compiled graph as well as at every node boundary.
    return GuardedWorkflow(compiled)
