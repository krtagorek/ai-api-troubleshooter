"""Propose steps for human review; never execute a fix."""

import re

from agents.common import (AgentOutputError, ask, checked_update, classification_or_unknown,
                           diagnosis_or_unknown, require_evidence)
from graph.state import ResolutionGeneratorInput, ResolutionGeneratorOutput, validate_state
from tools.evidence_requests import POLICY,safe_text

SYSTEM_PROMPT = """You are the resolution generator. Propose minimal, reversible
remediation and future verification steps for the supported hypothesis. Include
existing evidence_ids, prerequisites, and risks. Never claim execution or success.
Require human approval. If diagnosis is inconclusive, abstain with a reason rather
than guess a fix. Credentials must be placeholders, never actual values.
For tentative diagnoses return only plan_kind=diagnostic with read-only checks
that discriminate alternatives. Review correlated traces, origin/intermediary
response headers and routing evidence before recommending any configuration change.
Do not deploy, restart, disable authentication, or change configuration based only
on a symptom. Remediation requires a supported diagnosis and human approval.
Use plan_kind=remediation for any potentially disruptive change. Human approval
is required even for diagnostic plans; never claim checks or changes were run."""
SYSTEM_PROMPT += '\n'+POLICY


def resolution(state, *, llm=None, model=None):
    state = validate_state(state)
    diagnosis = diagnosis_or_unknown(state)
    if diagnosis.status in ("unknown", "inconclusive"):
        return checked_update(state, {"proposed_fix": None, "diagnosis": diagnosis})
    output = ask(llm, ResolutionGeneratorOutput, SYSTEM_PROMPT, ResolutionGeneratorInput(
        incident=state["sanitized_inputs"], classification=classification_or_unknown(state),
        evidence=state["evidence"], hypotheses=state["hypotheses"], diagnosis=diagnosis), model)
    if output.proposed_fix:
        require_evidence(state, output.proposed_fix.evidence_ids)
        if diagnosis.status != "supported" and output.proposed_fix.plan_kind != "diagnostic":
            raise AgentOutputError("Unresolved diagnosis requires read-only diagnostic checks before remediation")
        if output.proposed_fix.plan_kind == "diagnostic" and any(re.search(
                r"\b(deploy|restart|disable|delete|modify|update|reconfigure|rotate|change)\b", step, re.I)
                for step in output.proposed_fix.steps):
            raise AgentOutputError("Diagnostic plan contains potentially disruptive instructions")
        output.proposed_fix.requires_human_approval = True
        output.proposed_fix.steps=[safe_text(x) for x in output.proposed_fix.steps]
        output.proposed_fix.summary=safe_text(output.proposed_fix.summary)
        output.proposed_fix.prerequisites=[safe_text(x) for x in output.proposed_fix.prerequisites]
        output.proposed_fix.verification_steps=[safe_text(x) for x in output.proposed_fix.verification_steps]
    else:
        diagnosis.uncertainty.append(output.abstention_reason)
        return checked_update(state, {"proposed_fix": None, "diagnosis": diagnosis})
    return checked_update(state, {"proposed_fix": output.proposed_fix})


def create_agent(*, llm=None, model=None):
    def node(state):
        return resolution(state, llm=llm, model=model)
    return node
