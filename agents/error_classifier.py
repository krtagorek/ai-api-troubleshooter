"""Classify integration failures without asserting a root cause."""

from agents.common import ask, checked_update, reference_value
from graph.state import ClassifierInput, ClassifierOutput, validate_state
from tools.evidence_catalog import handle_catalog, resolve_evidence, resolve_handles

SYSTEM_PROMPT = """You are the error classifier. Categorize the observed failure,
not its unproven cause. Copy short handles (E01, E02, etc.) from evidence_catalog
into evidence_references. Copy evidence_contract and evidence_snapshot exactly.
Do not generate JSON pointers, long IDs, or redefine catalog mappings.
A status alone does not prove a root cause.
Return unknown with confidence 0 when insufficient information exists."""


def error_classifier(state, *, llm=None, model=None):
    state = validate_state(state)
    output = ask(llm, ClassifierOutput, SYSTEM_PROMPT,
                 ClassifierInput(incident=state["sanitized_inputs"], **handle_catalog(state['sanitized_inputs'])), model)
    classification = output.classification
    if classification.category == "unknown":
        classification = classification.model_copy(update={"confidence": 0})
    if classification.category != "unknown" and not classification.evidence_references:
        from agents.common import AgentOutputError
        raise AgentOutputError("Classification requires evidence references")
    try:
        if output.evidence_contract is not None or output.evidence_snapshot is not None or any(ref.startswith('E') for ref in classification.evidence_references):
            entries=resolve_handles(state['sanitized_inputs'],classification.evidence_references,
                                    output.evidence_contract,output.evidence_snapshot)
            classification=classification.model_copy(update={'evidence_references':[e.reference for e in entries]})
        references=[resolve_evidence(state['sanitized_inputs'],ref).reference for ref in classification.evidence_references]
    except ValueError as exc:
        from agents.common import AgentOutputError
        raise AgentOutputError(str(exc)) from None
    classification=classification.model_copy(update={'evidence_references':references})
    return checked_update(state, {"classification": classification})


def create_agent(*, llm=None, model=None):
    def node(state):
        return error_classifier(state, llm=llm, model=model)
    return node
