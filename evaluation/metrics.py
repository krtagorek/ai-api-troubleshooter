"""Evaluator-only label access; deterministic proxies, never an LLM self-grade."""
import json
import re
from agents.common import reference_value
from models.evidence import reference_source, observation_matches
from data.generate_incidents import CAUSES

CATEGORIES={401:'authentication',403:'authorization',422:'validation',429:'rate_limiting',504:'timeout',500:'server_error',503:'dependency_failure',200:'schema_mismatch'}
STOP={'the','a','an','and','or','to','of','in','with','its','is','was','for','as','by','before','after','through','that','this','it'}


def words(text):
    return {w.rstrip('s') for w in re.findall(r'[a-z0-9_]+',text.lower()) if w not in STOP and len(w)>1}


def overlap(predicted,reference):
    p,r=words(predicted),words(reference)
    common=len(p&r); precision=common/len(p) if p else 0; recall=common/len(r) if r else 0
    return {'precision':precision,'recall':recall,'f1':2*precision*recall/(precision+recall) if precision+recall else 0}


def score(state,label):
    incident=state['sanitized_inputs']; classification=state['classification']; evidence=state['evidence']; diagnosis=state['diagnosis']; fix=state['proposed_fix']
    grounded=[]; failures=[]
    for item in evidence.items:
        try:
            value=reference_value(incident,item.reference)
            valid=observation_matches(item.observation,value) and item.source==reference_source(item.reference)
        except Exception: valid=False
        grounded.append(valid)
        if not valid: failures.append('unsupported_evidence_observation')
    known={e.evidence_id for e in evidence.items}
    for hypothesis in state['hypotheses']:
        if not hypothesis.supporting_evidence_ids or not set(hypothesis.supporting_evidence_ids+hypothesis.contradicting_evidence_ids)<=known: failures.append('unsupported_hypothesis_reference')
    if classification:
        if classification.category!='unknown' and not classification.evidence_references: failures.append('missing_classification_reference')
        for pointer in classification.evidence_references:
            try: reference_value(incident,pointer)
            except Exception: failures.append('invalid_classification_reference')
    selected=next((h.description for h in state['hypotheses'] if diagnosis and h.hypothesis_id==diagnosis.selected_hypothesis_id),'')
    root_text=selected+' '+(diagnosis.summary if diagnosis else '')
    candidates=[cause for cause,_,_ in CAUSES[label['status_code']]]
    matches=[overlap(root_text,cause)['f1'] for cause in candidates]
    predicted=candidates[max(range(len(matches)),key=matches.__getitem__)] if max(matches,default=0)>=.25 else None
    root_score=overlap(root_text,label['root_cause'])
    fix_text=' '.join([fix.summary,*fix.steps]) if fix else ''
    resolution=overlap(fix_text,label['expected_resolution'])
    if fix and (not fix.requires_human_approval or not fix.evidence_ids or not set(fix.evidence_ids)<=known): failures.append('unsafe_or_ungrounded_resolution')
    validation=state['validation_results']
    # This evaluation configures NO validation tools and executes NO fixes.
    truthful=all(r.status=='NOT_TESTED' and not r.checks for r in validation)
    if not truthful: failures.append('unsupported_validation_claim')
    quality=incident.evidence_quality
    abstained=bool(diagnosis and diagnosis.status in ('inconclusive','unknown','tentative') and diagnosis.confidence<=.5 and diagnosis.uncertainty)
    return {'category_expected':CATEGORIES[label['status_code']], 'category_predicted':classification.category if classification else None,
        'classification_correct':bool(classification and classification.category==CATEGORIES[label['status_code']]),
        'evidence_quality':quality,'root_cause_eligible':quality=='complete',
        'root_cause_lexical_correct':bool(diagnosis and diagnosis.status in ('tentative','supported') and predicted==label['root_cause']),
        'root_cause_reference_f1':root_score['f1'],'resolution_reference_f1':resolution['f1'],
        'resolution_lexical_correct':bool(fix and resolution['recall']>=.5 and not re.search(r'\b(?:not|never)\b',fix_text,re.I)),
        'abstention_appropriate':abstained if quality!='complete' else None,
        'grounded_items':sum(grounded),'evidence_items':len(grounded),'hallucination_flags':sorted(set(failures)),
        'validation_truthful':truthful,'validation_observed':bool(validation),'fix_effectiveness':None,
        'human_semantic_review_required':True}


def ratio(n,d): return {'numerator':n,'denominator':d,'value':n/d if d else None}


def aggregate(cases,planned=None):
    scored=[c['metrics'] for c in cases if c.get('metrics')]
    complete=[m for m in scored if m['root_cause_eligible']]
    denominator=len(planned) if planned is not None else len(cases)
    complete_denominator=len(complete)
    uncertain=[m for m in scored if m['abstention_appropriate'] is not None]
    uncertain_denominator=len(uncertain)
    return {'classification_accuracy':ratio(sum(m['classification_correct'] for m in scored),len(scored)),
        'root_cause_lexical_accuracy_complete_evidence':ratio(sum(m['root_cause_lexical_correct'] for m in complete),complete_denominator),
        'resolution_lexical_accuracy_complete_evidence':ratio(sum(m['resolution_lexical_correct'] for m in complete),complete_denominator),
        'appropriate_uncertainty_rate':ratio(sum(m['abstention_appropriate'] for m in uncertain),uncertain_denominator),
        'accepted_evidence_grounding_rate':ratio(sum(m['grounded_items'] for m in scored),sum(m['evidence_items'] for m in scored)),
        'flagged_scored_case_rate':ratio(sum(bool(m['hallucination_flags']) for m in scored),len(scored)),
        'validation_truthfulness_rate':ratio(sum(m['validation_truthful'] for m in scored if m['validation_observed']),sum(m['validation_observed'] for m in scored)),
        'unscored_case_rate':ratio(denominator-len(scored),denominator),
        'workflow_failure_rate':ratio(sum(c['status']!='scored' for c in cases),len(cases)),
        'scored_case_coverage':ratio(len(scored),denominator),
        'accuracy_population':'Scored cases only; failures are unscored, not incorrect diagnoses. Interpret with coverage and workflow_failure_rate.',
        'hallucination_audit_scope':'Retained structured state only; rejected cases remain unscored. Semantic hallucinations require human review.',
        'planned_cases':denominator,'attempted_cases':len(cases),'scored_cases':len(scored),'failed_cases':sum(c['status']!='scored' for c in cases),'semantic_accuracy':'NOT_HUMAN_REVIEWED','fix_effectiveness':'NOT_TESTED',
        'limitations':['Root-cause and resolution metrics are lexical proxies, not semantic judgments.',
            'Reference/quote checks do not prove that all causal claims are true.', 'No tools or fixes executed; NOT_TESTED is the only truthful validation result.']}
