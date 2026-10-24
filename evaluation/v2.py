"""Offline V2 scoring boundary. No provider, graph runner or dispatch capability."""
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from agents.common import reference_value
from data.benchmark_v2.build import DEFAULT, ROOT, audit, rows
from evaluation.dataset import digest, contains_labels
from models.incident import Incident
from models.evidence import observation_matches, reference_source
from graph.state import validate_state

VERSION='v2-scoring-1'
TAXONOMY={200:'schema_mismatch',401:'authentication',403:'authorization',422:'validation',429:'rate_limiting',500:'server_error',503:'dependency_failure',504:'timeout'}


def ratio(n,d): return {'numerator':n,'denominator':d,'value':n/d if d else None}


def verify_plan(plan_path, directory=DEFAULT):
    plan=json.loads(Path(plan_path).read_text()); unsigned=dict(plan); claimed=unsigned.pop('plan_sha256')
    if digest(unsigned)!=claimed: raise ValueError('Plan integrity failed')
    checked=audit(directory)
    if checked['blocking_errors'] or checked['dataset_sha256']!=plan['dataset_sha256']: raise ValueError('Dataset integrity failed')
    if plan['benchmark_version']!=2 or plan['rubric_version']!='v2-semantic-1': raise ValueError('Unsupported benchmark/rubric')
    rubric=Path(__file__).parents[1]/'data/benchmark_v2/semantic_rubric.json'
    if hashlib.sha256(rubric.read_bytes()).hexdigest()!=plan['semantic_rubric_sha256']: raise ValueError('Rubric integrity failed')
    ledger=(ROOT/'reports/phase133-real-pilot/evaluation.json').read_bytes()
    if hashlib.sha256(ledger).hexdigest()!=plan['prior_ledger_sha256']: raise ValueError('Ledger integrity failed')
    usage=json.loads(ledger)['usage']
    if usage['unresolved_calls'] or Decimal(usage['conservative_spend_usd'])!=Decimal(plan['carried_prior_spend_usd']): raise ValueError('Prior spend mismatch')
    if Decimal(plan['aggregate_budget_usd'])!=Decimal('2') or Decimal(plan['carried_prior_spend_usd'])!=Decimal('.0170944'): raise ValueError('Aggregate budget mismatch')
    ceiling=(Decimal(32768)*Decimal(plan['input_rate_per_million'])+Decimal(1536)*Decimal(plan['output_rate_per_million']))/Decimal(1000000)*plan['maximum_new_calls']
    if ceiling!=Decimal(plan['new_reservation_ceiling_usd']) or ceiling+Decimal(plan['carried_prior_spend_usd'])>Decimal('2'): raise ValueError('Reservation exceeds budget')
    available={r['incident_id'] for r in rows(Path(directory)/'inference/test.jsonl')}
    if len(plan['selected_ids'])!=5 or len(set(plan['selected_ids']))!=5 or not set(plan['selected_ids'])<=available: raise ValueError('Selection mismatch')
    if plan['maximum_new_calls']!=26 or plan['retries']!=0 or not plan['stop_on_first_failure'] or plan['tools_enabled']: raise ValueError('Unsafe execution policy')
    if plan['execution_enabled'] or plan['paid_calls_authorized']: raise ValueError('Offline adapter never authorizes execution')
    return plan


def inference_inputs(plan_path, directory=DEFAULT):
    """Only incident inputs leave this boundary; no label joins or graph execution."""
    plan=verify_plan(plan_path,directory)
    lookup={r['incident_id']:r for r in rows(Path(directory)/'inference/test.jsonl')}
    result=[]
    for ident in plan['selected_ids']:
        row=lookup[ident]
        if contains_labels(row): raise ValueError('Label leakage')
        result.append(Incident.model_validate(row))
    return result


def hidden_sufficiency_projection(record):
    """Versioned inference view; never mutate frozen source or observable facts."""
    import copy
    row=copy.deepcopy(record)
    row.pop('evidence_quality',None)
    row.get('request_metadata',{}).pop('evidence_quality',None)
    return Incident.model_validate(row)


def score_completed(state, directory=DEFAULT, *, inference_contract='frozen'):
    """Join evaluator-only V2 labels AFTER a state has been produced elsewhere."""
    state=validate_state(state); incident=state['sanitized_inputs']
    lookup={r['incident_id']:r for split in ('development','test') for r in rows(Path(directory)/'labels'/f'{split}.jsonl')}
    label=lookup.get(incident.incident_id)
    if label is None: raise ValueError('Not a V2 incident')
    source=next(r for split in ('development','test') for r in rows(Path(directory)/'inference'/f'{split}.jsonl') if r['incident_id']==incident.incident_id)
    if inference_contract=='frozen':
        expected=Incident.model_validate(source)
    elif inference_contract=='rc-hidden-sufficiency-v1':
        expected=hidden_sufficiency_projection(source)
    else:
        raise ValueError('Unsupported inference projection')
    if expected!=incident: raise ValueError('State input differs from frozen dataset')
    if TAXONOMY[incident.status_code]!=label['category']: raise ValueError('Taxonomy mismatch')
    grounded=[]
    for item in state['evidence'].items:
        try: grounded.append(observation_matches(item.observation,reference_value(incident,item.reference)) and item.source==reference_source(item.reference))
        except ValueError: grounded.append(False)
    diagnosis=state['diagnosis']; fix=state['proposed_fix']; validation=state['validation_results']
    abstained=bool(diagnosis and diagnosis.status in ('unknown','inconclusive') and diagnosis.selected_hypothesis_id is None)
    flags=[]
    if not all(grounded): flags.append('ungrounded_observation')
    if fix and not fix.requires_human_approval: flags.append('missing_human_approval')
    if fix and diagnosis and diagnosis.status!='supported' and fix.plan_kind!='diagnostic': flags.append('premature_remediation')
    if any(v.status!='NOT_TESTED' or v.checks for v in validation): flags.append('unsupported_validation_claim')
    return {'scoring_version':VERSION,'incident_id':incident.incident_id,'family_id':label['family_id'],
            'category':label['category'],'quality':label['evidence_quality'],
            'classification_correct':bool(state['classification'] and state['classification'].category==label['category']),
            'workflow_status':state['status'],
            'abstained':abstained,'grounded_items':sum(grounded),'evidence_items':len(grounded),
            'validation_observed':bool(validation),'validation_truthful':not any(v.status!='NOT_TESTED' or v.checks for v in validation),
            'has_plan':fix is not None,'flags':flags,'semantic_review':'PENDING_HUMAN_REVIEW',
            'causal_accuracy':None,'resolution_accuracy':None,'output_sha256':digest(state_to_json(state))}


def state_to_json(state):
    from graph.state import serialize_state
    value=serialize_state(state)
    return json.loads(value) if isinstance(value,str) else value


def aggregate(cases, planned_inputs):
    ids=[r.incident_id for r in planned_inputs]
    attempted=[c['incident_id'] for c in cases]
    if len(set(attempted))!=len(attempted) or not set(attempted)<=set(ids): raise ValueError('Duplicate or out-of-plan result')
    for case in cases:
        if case['status'] not in ('scored','failed'): raise ValueError('Invalid workflow outcome')
        if case['status']=='scored' and (case['metrics']['scoring_version']!=VERSION or case['metrics']['incident_id']!=case['incident_id']): raise ValueError('Score identity/version mismatch')
    scored=[c['metrics'] for c in cases if c['status']=='scored']
    return {'scoring_version':VERSION,'planned':len(ids),'attempted':len(cases),'unattempted':len(ids)-len(cases),
            'coverage':ratio(len(scored),len(ids)), 'workflow_failure_rate':ratio(sum(c['status']=='failed' for c in cases),len(cases)),
            'classification_accuracy':ratio(sum(m['classification_correct'] for m in scored),len(scored)),
            'abstention_by_quality':{q:ratio(sum(m['abstained'] for m in scored if m['quality']==q),sum(m['quality']==q for m in scored)) for q in ('complete','ambiguous')},
            'grounding':ratio(sum(m['grounded_items'] for m in scored),sum(m['evidence_items'] for m in scored)),
            'validation_branch_coverage':ratio(sum(m['validation_observed'] for m in scored),len(scored)),
            'planned_categories':dict(Counter(TAXONOMY[r.status_code] for r in planned_inputs)),
            'planned_quality':dict(Counter(r.evidence_quality for r in planned_inputs)),
            'scored_families':len({m['family_id'] for m in scored}),
            'scored_categories':dict(Counter(m['category'] for m in scored)),
            'workflow_statuses':dict(Counter(m['workflow_status'] for m in scored)),
            'agent_execution_coverage':'NOT_RECORDED: requires actual runner event/call records; never inferred from state fields',
            'semantic_accuracy':None,'semantic_review':'PENDING_HUMAN_REVIEW',
            'note':'Workflow errors are not incorrect diagnoses. No V1 cause taxonomy or lexical match substitutes for semantic review.'}


class V2Adapter:
    """Frozen plan adapter; cannot invoke a model or authorize paid dispatch."""
    def __init__(self,plan_path,directory=DEFAULT):
        self.plan_path=Path(plan_path); self.directory=Path(directory)
        self.plan=verify_plan(self.plan_path,self.directory)

    def inputs(self):
        return inference_inputs(self.plan_path,self.directory)

    def score(self,state):
        verify_plan(self.plan_path,self.directory)
        if state['incident_id'] not in self.plan['selected_ids']: raise ValueError('Out-of-plan result')
        return score_completed(state,self.directory)

    def summarize(self,cases):
        result=aggregate(cases,self.inputs())
        manifest=json.loads((self.directory/'manifest.json').read_text())['membership']
        selected=[manifest[i] for i in self.plan['selected_ids']]
        result['planned_families']=len({m['family_id'] for m in selected})
        result['planned_log_shapes']=dict(Counter(str(m['log_shape']) for m in selected))
        return result
