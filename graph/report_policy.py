"""Eligibility for an unvalidated diagnostic report, never validation success."""
from graph.state import validate_state
from tools.output_safety import unsafe_request,NOTICE
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class LegacyReportDecision:
    eligible: bool
    reason: str


def legacy_tentative_report_decision(state):
    """Ordinary grounded observations confer no contract authority or provenance.

    This is separate from the existing contract policy below. A descriptive
    contract result, even denied/stale, can never fall back to this path.
    """
    def hold(reason): return LegacyReportDecision(False, reason)
    try:
        s=validate_state(state)
        if s.get('trusted_semantic') is not None:
            return hold('contract_authorization_required')
        d,p,e=s['diagnosis'],s['proposed_fix'],s['evidence']
        if not d or d.status!='tentative' or d.confidence<=0 or not d.selected_hypothesis_id or not any(x.strip() for x in d.uncertainty):
            return hold('tentative_diagnosis_required')
        if not p or p.plan_kind!='diagnostic' or not p.requires_human_approval:
            return hold('diagnostic_plan_required')
        if not s['validation_results'] or s['validation_results'][-1].status!='NOT_TESTED':
            return hold('not_tested_validation_required')
        # validate_state applies the presentation boundary first. Its explicit
        # replacement markers must not be mistaken for approved safe wording.
        import json
        narrative=json.dumps({k:s[k] for k in
            ('classification','diagnosis','hypotheses','proposed_fix','validation_results')},
            default=lambda value:value.model_dump(mode='json'))
        narrative+=' '+e.model_dump_json()
        if '[OUTPUT SAFETY:' in narrative or NOTICE in narrative:
            return hold('diagnostic_output_safety_review_required')
        from tools.diagnostic_sufficiency import readiness
        ready=readiness(s['sanitized_inputs'],e)
        if not ready.qualified_analysis:
            return hold('qualified_evidence_required')
        from tools.evidence_catalog import resolve_evidence
        for item in e.items:
            entry=resolve_evidence(s['sanitized_inputs'],item.evidence_id)
            if (entry.reference,entry.source,entry.observation)!=(item.reference,item.source,item.observation):
                return hold('grounding_review_required')
        selected=next((h for h in s['hypotheses'] if h.hypothesis_id==d.selected_hypothesis_id),None)
        if not selected or selected.confidence<=0 or not selected.supporting_evidence_ids:
            return hold('supported_hypothesis_required')
        from tools.counterevidence import validate_counterevidence
        validate_counterevidence(s['hypotheses'],e)
        support=set(selected.supporting_evidence_ids)
        discriminating=set(d.discriminating_evidence_ids)
        if support & set(selected.contradicting_evidence_ids) or not discriminating or not discriminating<=support:
            return hold('counterevidence_review_required')
        refs={item.reference:item.evidence_id for item in e.items}
        if not ready.comparison_references or not {refs[r] for r in ready.comparison_references}<=discriminating:
            return hold('discriminating_evidence_required')
        assessed={i for a in selected.evidence_assessments if a.relation=='supporting' and a.explanation.strip() for i in a.evidence_ids}
        if not support<=assessed:
            return hold('counterevidence_review_required')
        ids={item.evidence_id for item in e.items}
        if not p.evidence_ids or not set(p.evidence_ids)<=ids or not discriminating<=set(p.evidence_ids):
            return hold('grounded_plan_required')
        # Agents already enforce read-only plans. Repeat the bound at publication
        # to cover injected agents and recovered checkpoints as well.
        actions=[*p.steps,*p.verification_steps,*p.prerequisites,*d.diagnostic_checks]
        if any(re.search(r'\b(deploy|restart|disable|delete|modify|update|reconfigure|rotate|change)\b',x,re.I) for x in actions):
            return hold('read_only_plan_required')
        if re.search(r'\b(trusted semantic authority|verified provenance|cryptographically verified|definitively confirmed|proven root cause)\b',narrative,re.I):
            return hold('unsupported_authority_or_certainty_claim')
        from models.diagnosis import FinalReport
        from tools.output_safety import protect_report
        protect_report(FinalReport(incident_id=s['incident_id'],summary='Tentative diagnostic review',
            diagnosis=d,proposed_fix=p,evidence=e,validation_results=s['validation_results']))
        return LegacyReportDecision(True,'legacy_tentative_diagnostic_report')
    except (ValueError,KeyError,TypeError):
        return hold('grounding_or_counterevidence_review_required')


def tentative_report_eligible(state):
    try:
        s=validate_state(state);d=s['diagnosis'];p=s['proposed_fix'];e=s['evidence']
        result=s.get('trusted_semantic')
        if not result or result.status!='qualified':return False # descriptive, fresh gate still mandatory
        if not d or d.status!='tentative' or not d.selected_hypothesis_id or not d.uncertainty or d.confidence<=0:return False
        if not p or p.plan_kind!='diagnostic' or not p.requires_human_approval:return False
        if not s['validation_results'] or s['validation_results'][-1].status!='NOT_TESTED':return False
        if e.completeness not in ('partial','complete') or not e.items or any(x.ambiguous for x in e.items):return False
        from tools.evidence_catalog import resolve_evidence
        for item in e.items:
            entry=resolve_evidence(s['sanitized_inputs'],item.evidence_id)
            if (entry.reference,entry.source,entry.observation)!=(item.reference,item.source,item.observation):return False
        ids={x.evidence_id for x in e.items}
        selected=next((h for h in s['hypotheses'] if h.hypothesis_id==d.selected_hypothesis_id),None)
        if not selected or selected.confidence<=0 or not selected.supporting_evidence_ids or not set(selected.supporting_evidence_ids)<=ids:return False
        if set(selected.supporting_evidence_ids)&set(selected.contradicting_evidence_ids):return False
        from tools.counterevidence import validate_counterevidence
        validate_counterevidence(s['hypotheses'],e)
        if not d.discriminating_evidence_ids or not set(d.discriminating_evidence_ids)<=set(selected.supporting_evidence_ids):return False
        if not p.evidence_ids or not set(p.evidence_ids)<=ids:return False
        # Do not promote an output already withheld by the safety boundary.
        text=' '.join([d.summary,selected.description,p.summary,*p.steps,*p.verification_steps,*d.diagnostic_checks])
        if '[OUTPUT SAFETY:' in text or NOTICE in text:return False
        if any(unsafe_request(x,request_context=True) for x in [*p.steps,*p.verification_steps,*d.diagnostic_checks]):return False
        return True
    except (ValueError,KeyError,TypeError):return False


def publication_hold(state):
    """Remove protected output, preserving source evidence and honest validation."""
    from agents.common import inconclusive
    return validate_state({**state,'final_report':None,'diagnosis':inconclusive('Report publication authorization unavailable; no protected findings published'),
        'hypotheses':[],'proposed_fix':None,'status':'awaiting_evidence','trusted_semantic':None,
        'pending_user_questions':['Obtain fresh authorized sanitized diagnostic evidence.'],'stop_reason':'report_publication_denied'})
