"""Pre-execution human LABEL review; distinct from post-output semantic review.

No model, graph invocation, approval or database operations are implemented here.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from pydantic import BaseModel,ConfigDict,Field,field_validator
from typing import Literal
from evaluation.dataset import digest
from evaluation.v2 import V2Adapter
from data.benchmark_v2.build import DEFAULT, rows
from tools.redaction import redact

PLAN=Path('reports/phase134-v2/plan.json')
DECISIONS=('ACCEPT','ACCEPT_WITH_ALTERNATIVES','NEEDS_REVISION','REJECT')

# Evaluator-only inspection notes, never imported into agent prompts.
NOTES={
 'V2-02-01':('Cache age exceeds its maximum; the received key matches discovery but is absent from cache.',
             ['Authenticity of discovery metadata and the configured issuer still needs confirmation.'],
             ['Verify trusted issuer metadata and cache refresh history before any approved refresh.']),
 'V2-04-04':('Only a delete operation and refusal are visible; a retention lock is NOT inferable.',
             ['Do not assume a legal hold or that ordinary delete permissions were granted.'],
             ['Inspect retention policy and the access-policy decision with the resource owner.']),
 'V2-10-02':('Prior decoding, retained gzip encoding and JSON magic bytes support a decompression-stage mismatch.',
             ['A second decompression attempt and middleware ownership are inferred, not directly traced.'],
             ['Inspect the ordered middleware trace and content-encoding transitions without changing configuration.']),
 'V2-14-05':('Expected body size plus timeout do not identify upload buffering or origin availability.',
             ['Do not assume upload speed, buffering, or whether the origin received the request.'],
             ['Collect upload timing, bytes received and proxy/origin traces before adjusting deadlines.']),
 'V2-16-03':('One-item object versus zero/many arrays supports cardinality collapse, not confirmed component ownership.',
             ['Do not assume the deployed serializer owns the defect before checking raw origin and consumer contracts.'],
             ['Compare raw zero/one/many responses against the approved contract and consumer decoding.']),
}


class LabelDecision(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    decision: Literal['ACCEPT','ACCEPT_WITH_ALTERNATIVES','NEEDS_REVISION','REJECT']
    reviewer_id: str=Field(min_length=1)
    rationale: str=Field(min_length=10)
    timestamp: str
    accepted_alternatives: list[str]=Field(default_factory=list)

    @field_validator('reviewer_id','rationale')
    @classmethod
    def nonblank(cls,value):
        if not value.strip() or value!=value.strip(): raise ValueError('Trimmed nonblank human input required')
        return value

    @field_validator('timestamp')
    @classmethod
    def aware_timestamp(cls,value):
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None or parsed>datetime.now(timezone.utc): raise ValueError('Timezone-aware nonfuture timestamp required')
        return value


def worksheet(plan_path=PLAN,directory=DEFAULT):
    adapter=V2Adapter(plan_path,directory)
    labels={r['incident_id']:r for r in rows(Path(directory)/'labels/test.jsonl')}
    cases=[]
    for incident in adapter.inputs():
        label=labels[incident.incident_id]; observed,assumptions,checks=NOTES[incident.incident_id]
        material={'incident_id':incident.incident_id,'sanitized_inference_evidence':redact(incident.model_dump(mode='json')),
                  'reference_cause_human_only':label['root_cause'], 'label_sha256':digest(label),
                  'known_injected_cause':label['root_cause'],
                  'evidence_sufficiency':label['evidence_quality'], 'cause_inferable_from_evidence':observed,
                  'cause_requiring_additional_evidence':label['root_cause'] if label['evidence_quality']=='ambiguous' else 'Confirm component attribution and assumptions before remediation.',
                  'acceptable_alternative_diagnoses':label['acceptable_alternatives'],
                  'expected_diagnostic_checks':checks+label['discriminating_checks'],
                  'acceptable_safe_resolutions':{'when_sufficient_and_confirmed':label['expected_resolution'],
                     'when_ambiguous':'Abstain from choosing a cause; collect discriminating evidence. Do not execute changes.',
                     'human_approval_required':True,'validation_without_tools':'NOT_TESTED'},
                  'potential_unsupported_assumptions':assumptions}
        cases.append({'material':material,'material_sha256':digest(material),'human_review':{
            'decision':None,'reviewer_id':None,'rationale':'','timestamp':None,'accepted_alternatives':[]}})
    return {'worksheet_version':'v2-preexecution-label-review-1','purpose':'HUMAN_ONLY_LABEL_REVIEW_BEFORE_INFERENCE_NOT_MODEL_OUTPUT_GRADING',
            'plan_sha256':adapter.plan['plan_sha256'],'dataset_sha256':adapter.plan['dataset_sha256'],
            'rubric_sha256':adapter.plan['semantic_rubric_sha256'],'allowed_decisions':list(DECISIONS),
            'notice':'Human entries are attestations, not authenticated identity. No decisions are auto-filled. No approval to execute is implied.',
            'cases':cases}


def validate_review(document,plan_path=PLAN,directory=DEFAULT):
    expected=worksheet(plan_path,directory)
    errors=[]; decisions={}
    if set(document)!=set(expected) or any(document.get(k)!=v for k,v in expected.items() if k!='cases'):
        raise ValueError('Worksheet binding/header mismatch')
    cases=document['cases']
    if len(cases)!=5: raise ValueError('Exactly five selected cases required')
    lookup={c['material']['incident_id']:c for c in expected['cases']}
    seen=set()
    for case in cases:
        ident=case.get('material',{}).get('incident_id')
        if ident not in lookup or ident in seen: raise ValueError('Unknown or duplicate case')
        seen.add(ident); frozen=lookup[ident]
        if set(case)!=set(frozen) or case['material']!=frozen['material'] or case['material_sha256']!=frozen['material_sha256']:
            raise ValueError('Frozen review material was modified; submit a revision proposal instead')
        try:
            decision=LabelDecision.model_validate(case['human_review'])
            if decision.decision=='ACCEPT_WITH_ALTERNATIVES' and not any(a.strip() for a in decision.accepted_alternatives):
                raise ValueError('Explicit accepted alternatives required')
            decisions[ident]=decision.decision
        except ValueError:
            errors.append({'incident_id':ident,'reason':'Human decision, reviewer identity, rationale, timezone-aware timestamp and applicable alternatives required'})
    return {'all_cases_reviewed':not errors,'reviewed_cases':len(decisions),'errors':errors,'decisions':decisions,
            'labels_accepted':not errors and all(v in ('ACCEPT','ACCEPT_WITH_ALTERNATIVES') for v in decisions.values()),
            'execution_approved':False,'paid_calls':0}


def revision_proposal(incident_id,proposed_label,rationale,plan_path=PLAN,directory=DEFAULT):
    adapter=V2Adapter(plan_path,directory)
    if incident_id not in adapter.plan['selected_ids']: raise ValueError('Out-of-plan label')
    original=next(r for r in rows(Path(directory)/'labels/test.jsonl') if r['incident_id']==incident_id)
    if set(original)!=set(proposed_label) or proposed_label.get('incident_id')!=incident_id or len(rationale.strip())<10:
        raise ValueError('Full same-schema label and rationale required')
    return {'proposal_version':'v2.1-label-proposal-1','status':'DRAFT_REQUIRES_HUMAN_ADJUDICATION',
            'base_dataset_sha256':adapter.plan['dataset_sha256'],'base_plan_sha256':adapter.plan['plan_sha256'],
            'original_label_sha256':digest(original),'incident_id':incident_id,'proposed_label':proposed_label,
            'rationale':rationale,'human_decision':None,'applied':False,
            'requirements':'Use existing two-reviewer and third-adjudicator sidecar workflow; generate new dataset revision and new plan. Never overwrite V2.'}


def prepare_runner(document,plan_path=PLAN,directory=DEFAULT):
    """Label-free runner handoff only; no graph or provider execution capability."""
    checked=validate_review(document,plan_path,directory)
    if not checked['labels_accepted']: raise ValueError('All five labels must be human-accepted; revise/reject blocks the original plan')
    adapter=V2Adapter(plan_path,directory)
    return {'integration_version':'v2-runner-handoff-1','scoring_version':'v2-scoring-1',
            'inputs':[i.model_dump(mode='json') for i in adapter.inputs()],
            'plan_sha256':adapter.plan['plan_sha256'],'review_sha256':digest(document),
            'model':adapter.plan['model'],'maximum_new_calls':26,'retries':0,'stop_on_first_failure':True,
            'budget':{'aggregate_limit_usd':'2','carried_prior_spend_usd':'0.0170944',
                      'new_reservation_ceiling_usd':adapter.plan['new_reservation_ceiling_usd']},
            'execution_enabled':False,'paid_approval_required':True,
            'integration':'Initialize shared state from inputs only; score returned state with V2Adapter.score after inference; summarize with V2Adapter.summarize.',
            'dispatch_blockers':['Fresh explicit paid approval','Recheck current ledger and atomically reserve','Reverify model pricing/access','Implement guarded single-use dispatch']}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['create','validate','prepare-runner'])
    parser.add_argument('--plan',type=Path,default=PLAN)
    parser.add_argument('--worksheet',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    try:
        if args.command=='create':
            value=worksheet(args.plan);args.worksheet.parent.mkdir(parents=True,exist_ok=True)
            with args.worksheet.open('x') as f: json.dump(value,f,indent=2)
            args.worksheet.chmod(0o600)
            print('Created pending HUMAN-ONLY pre-execution label worksheet. No calls or approval.');return 0
        document=json.loads(args.worksheet.read_text())
        if args.command=='validate':
            result=validate_review(document,args.plan); print(json.dumps(result));return 0 if result['all_cases_reviewed'] else 2
        if args.output is None: raise ValueError('Output file required')
        result=prepare_runner(document,args.plan)
        with args.output.open('x') as f:json.dump(result,f,indent=2)
        args.output.chmod(0o600)
        print('Prepared label-free handoff only. Execution remains disabled.');return 0
    except Exception:
        print('Review command blocked: check frozen bindings, human entries, decisions and output path. Sensitive details withheld.');return 2


if __name__=='__main__': raise SystemExit(main())
