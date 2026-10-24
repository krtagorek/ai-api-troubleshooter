"""Extract verifiable observations from untrusted incident material."""

import json

from agents.common import (AgentOutputError, ask, checked_update,
                           classification_or_unknown, reference_value)
from graph.state import LogInvestigatorInput, LogInvestigatorOutput, LiveLogInvestigatorOutput, validate_state
from models.evidence import EvidenceGap, REFERENCE_SOURCES, reference_source, observation_matches
from tools.log_parser import parse_logs
from tools.evidence_catalog import handle_catalog, resolve_evidence, resolve_handles
from models.evidence import Evidence, EvidenceCollection

SYSTEM_PROMPT = """You are the log investigator. Extract observations, not causes.
Prefer selected_evidence_handles: select supplied short handles (E01, E02, etc.).
Copy evidence_contract and evidence_snapshot exactly. Never generate long IDs or
redefine mappings. Trusted code copies literal observations and provenance.
Never invent IDs; do not combine selected IDs with legacy evidence.items. Provide
evidence.completeness and gaps separately. The following rules apply only to
legacy evidence.items responses:
Each item needs a unique evidence_id and a JSON pointer reference into the incident
such as /logs/0/message or /response_body/error/message. observation must be an
exact quote or substring of that referenced value. source must match the reference.
Reference scalar leaves only, never objects or arrays. Strings require literal
nonempty substrings. Non-strings require the ENTIRE canonical JSON scalar text:
integer 403 -> "403", float 1.0 -> "1.0", true -> "true", false -> "false",
null -> "null". Never add labels ("status_code 403"), whitespace, units or partial
digits. Never infer missing log entries. Explicitly
record gaps and use partial, missing, or conflicting completeness as appropriate.
Source is derived by trusted code from the reference, not from your interpretation.
/expected_behavior and /actual_behavior are user statements, never documentation.
Do not claim a supplied expected behavior is an independently verified contract.
Canonical reference root to source mapping: """ + json.dumps(dict(REFERENCE_SOURCES))


def legacy_log_investigator(state, *, llm=None, model=None):
    """Explicit offline compatibility adapter; not used by live agent factories."""
    if llm is None:
        raise AgentOutputError('Legacy replay requires an explicitly injected output source')
    state = validate_state(state)
    parsed = parse_logs(state["sanitized_inputs"].logs)
    if parsed.status == "error":
        raise AgentOutputError("Log input exceeds safe parsing limits")
    output = ask(llm, LogInvestigatorOutput, SYSTEM_PROMPT, LogInvestigatorInput(
        incident=state["sanitized_inputs"], classification=classification_or_unknown(state),
        **handle_catalog(state['sanitized_inputs'])), model)
    if output.evidence_contract is not None or output.evidence_snapshot is not None:
        try:
            resolve_handles(state['sanitized_inputs'],[],output.evidence_contract,output.evidence_snapshot)
        except ValueError as exc:
            raise AgentOutputError(str(exc)) from None
        if output.evidence.items or output.selected_evidence_ids:
            raise AgentOutputError('Versioned handle output cannot use legacy selections')
    if output.selected_evidence_handles:
        if output.evidence.items or output.selected_evidence_ids:
            raise AgentOutputError('Mixed evidence contracts are not permitted')
        try:
            entries=resolve_handles(state['sanitized_inputs'],output.selected_evidence_handles,
                                    output.evidence_contract,output.evidence_snapshot)
        except ValueError as exc:
            raise AgentOutputError(str(exc)) from None
        output.evidence.items=[Evidence(evidence_id=e.evidence_id,source=e.source,observation=e.observation,
                                       reference=e.reference,reliability=.5) for e in entries]
    if output.selected_evidence_ids:
        if output.evidence.items or len(set(output.selected_evidence_ids))!=len(output.selected_evidence_ids):
            raise AgentOutputError('Mixed or duplicate evidence selections')
        try:
            entries=[resolve_evidence(state['sanitized_inputs'],ident) for ident in output.selected_evidence_ids]
            if any(not ident.startswith('ev:') for ident in output.selected_evidence_ids):
                raise ValueError('Catalog selections require evidence IDs')
        except ValueError as exc:
            raise AgentOutputError(str(exc)) from None
        output.evidence.items=[Evidence(evidence_id=e.evidence_id,source=e.source,observation=e.observation,
                                       reference=e.reference,reliability=.5) for e in entries]
    grounded = []
    for item in output.evidence.items:
        try:
            entry=resolve_evidence(state['sanitized_inputs'],item.reference)
        except ValueError as exc:
            raise AgentOutputError(str(exc)) from None
        observed = reference_value(state["sanitized_inputs"], item.reference)
        try:
            matches = observation_matches(item.observation, observed)
        except ValueError:
            raise AgentOutputError("Evidence reference must resolve to a finite JSON scalar") from None
        if not matches:
            raise AgentOutputError("Extracted observation is not present in referenced evidence")
        # Only after reference resolution and exact observation grounding succeed.
        # The model cannot promote user text to documentation or tool evidence.
        grounded.append(type(item).model_validate({
            **item.model_dump(mode="json"), "source": entry.source, "reference":entry.reference}))
    output.evidence.items = grounded
    if not output.evidence.items:
        output.evidence = output.evidence.model_copy(update={"completeness": "missing"})
        output.evidence.missing.append(EvidenceGap(
            description="No grounded observations extracted",
            required_to_resolve="Collect sanitized response details and correlated upstream logs"))
    return checked_update(state, {"evidence": output.evidence})


