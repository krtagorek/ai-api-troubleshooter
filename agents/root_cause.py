"""Form evidence-linked hypotheses; explicitly abstain without support."""

from agents.common import (ask, checked_update, classification_or_unknown,
                           inconclusive, require_evidence)
from graph.state import RootCauseAnalystInput, RootCauseAnalystOutput, validate_state
from tools.counterevidence import validate_counterevidence
from tools.evidence_requests import POLICY, safe_gaps, safe_text
from tools.diagnostic_sufficiency import readiness
from dataclasses import asdict

SYSTEM_PROMPT = """You are the root cause analyst. Compare competing hypotheses.
Follow the supplied strict schema exactly. Hypotheses contain only hypothesis_id,
description, confidence, supporting_evidence_ids, contradicting_evidence_ids,
assumptions and evidence_assessments. Never add other hypothesis fields. Put missing_evidence ONLY in diagnosis,
as objects with description and required_to_resolve; never in a hypothesis.
Use only existing evidence IDs in supporting/contradicting references. Every
hypothesis needs supporting evidence. The selected diagnosis must reference a
hypothesis ID. Do not equate HTTP status with proof of a cause. Explain uncertainty,
assumptions, contradictory observations, and missing evidence. Use inconclusive
when observations do not distinguish possible causes. Never invent tool results.
Separate observed symptoms from causal hypotheses, assumptions, and missing evidence.
A symptom does not identify its origin. When response content could originate at
either an intermediary (gateway/proxy/authentication interception) or the application
(routing/configuration), compare BOTH explanations and what each predicts. This
comparison is conditional on evidence, not a default preferred cause. Do not claim
an intermediary exists without evidence. If no discriminating observation exists,
return inconclusive with no selected hypothesis and no definitive causal assertion.
Use discriminating_evidence_ids only for observations that distinguish alternatives.
List read-only diagnostic_checks (correlated origin/intermediary traces, route and
response-header inspection) before suggesting changes. Mark checks as proposed,
not executed. No model assumption or supplied expected behavior verifies a cause."""

SYSTEM_PROMPT += """
Distinguish supporting, contradicting, neutral and missing evidence explicitly in
evidence_assessments. Missing evidence has no observed evidence IDs. Each claimed
contradiction needs a causal explanation and a hypothesis_prediction showing what
the observation excludes. Co-occurring causes can coexist: an identifier mismatch
does not disprove expiry or revocation. Mere alternative support is not refutation.
Partial evidence can support a qualified tentative mechanism while confirmation
remains missing. Do not demand definitive proof before proposing safe checks.
Review all grounded observations, not just the classifier's status citations.
Compare timing against thresholds, actual against expected identifiers/values and
correlated upstream/downstream records. Cite both members of a comparison. Treat
false, null and conflicting records as potential counterevidence. Prefer a causal
chain tied to these observations over generic application-versus-proxy speculation.
Only compare intermediary origin when the data supports that ambiguity. Evidence
completeness is coverage, not proof: state assumptions and propose read-only checks
when trust, provenance or a discriminating observation remains unverified.
"""
SYSTEM_PROMPT += '\n'+POLICY


def root_cause(state, *, llm=None, model=None):
    state = validate_state(state)
    if not state["evidence"].items:
        return checked_update(state, {"hypotheses": [], "diagnosis": inconclusive(
            "No grounded evidence is available for root cause analysis")})
    from diagnostic_contracts.integration import current_analysis
    trusted_readiness = current_analysis(state['sanitized_inputs'])
    output = ask(llm, RootCauseAnalystOutput, SYSTEM_PROMPT, RootCauseAnalystInput(
        incident=state["sanitized_inputs"], classification=classification_or_unknown(state),
        evidence=state["evidence"],
        analysis_readiness=trusted_readiness or (asdict(readiness(state['sanitized_inputs'],state['evidence'])) if all(e.evidence_id.startswith('ev:') for e in state['evidence'].items) else {})), model)
    for hypothesis in output.hypotheses:
        require_evidence(state, hypothesis.supporting_evidence_ids)
        if hypothesis.contradicting_evidence_ids:
            require_evidence(state, hypothesis.contradicting_evidence_ids)
    try:validate_counterevidence(output.hypotheses,state['evidence'])
    except ValueError as exc:
        from agents.common import AgentOutputError
        raise AgentOutputError(str(exc)) from None
    diagnosis = output.diagnosis.model_copy(update={
        'summary':safe_text(output.diagnosis.summary),
        'missing_evidence':safe_gaps(output.diagnosis.missing_evidence),
        'diagnostic_checks':[safe_text(x) for x in output.diagnosis.diagnostic_checks]})
    if all(e.evidence_id.startswith('ev:') for e in state['evidence'].items) and diagnosis.status=='supported':
        diagnosis=diagnosis.model_copy(update={'status':'tentative','uncertainty':[
            *diagnosis.uncertainty,'Grounded observations permit a qualified mechanism; no independent confirmation was executed.']})
    if diagnosis.discriminating_evidence_ids:
        require_evidence(state, diagnosis.discriminating_evidence_ids)
    if len(output.hypotheses) > 1 and not diagnosis.discriminating_evidence_ids:
        diagnosis = diagnosis.model_copy(update={
            "status": "inconclusive", "selected_hypothesis_id": None, "confidence": 0,
            "summary": "Competing hypotheses remain unresolved; no discriminating evidence was provided.",
            "uncertainty": [*diagnosis.uncertainty, "Evidence does not distinguish competing causes"],
            "diagnostic_checks": diagnosis.diagnostic_checks or [
                "Review correlated origin and intermediary traces to locate where the response was generated; no checks have been executed."],
        })
    if diagnosis.status in ("unknown", "inconclusive"):
        diagnosis = diagnosis.model_copy(update={
            "confidence": 0,
            "uncertainty": diagnosis.uncertainty or ["Available evidence does not identify a cause"],
        })
    if diagnosis.status in ("tentative", "supported") and not diagnosis.selected_hypothesis_id:
        from agents.common import AgentOutputError
        raise AgentOutputError("Diagnostic claim requires a selected hypothesis")
    if state["evidence"].completeness != "complete":
        diagnosis = diagnosis.model_copy(update={
            "status": "tentative" if diagnosis.status == "supported" else diagnosis.status,
            "confidence": min(diagnosis.confidence, 0.5),
            "uncertainty": [*diagnosis.uncertainty, "Evidence is incomplete or conflicting"],
        })
        output.hypotheses = [hypothesis.model_copy(update={
            "confidence": min(hypothesis.confidence, 0.5)}) for hypothesis in output.hypotheses]
    return checked_update(state, {"hypotheses": output.hypotheses, "diagnosis": diagnosis})


def create_agent(*, llm=None, model=None):
    def node(state):
        return root_cause(state, llm=llm, model=model)
    return node
