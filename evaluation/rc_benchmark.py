"""Untouched V2 test proposal; labels/reviews never enter inference or prompts."""
import copy
import json
from datetime import datetime
from pathlib import Path

from data.benchmark_v2.build import DEFAULT,rows
from evaluation.dataset import digest,contains_labels
from evaluation.enhanced_pilot import prepare as enhanced_prepare
from evaluation.quality_pilot import sha
from evaluation.release_candidate import PATH as RC_PATH,verify
from models.incident import Incident
from tools.evidence_catalog import build_catalog
from evaluation.v2 import hidden_sufficiency_projection

VERSION='rc-v2-test-two-case-1'
OUTPUT=Path('reports/rc-independent-v2-plan')
CASES=('V2-06-02','V2-08-04')
REVIEW=OUTPUT/'human-review.json'


def inputs():
    source={r['incident_id']:r for r in rows(DEFAULT/'inference/test.jsonl')}
    result=[]
    for ident in CASES:
        row=copy.deepcopy(source[ident])
        if contains_labels(row):raise ValueError('Inference label leakage')
        # Generated sufficiency tags are evaluator information, not evidence.
        result.append(hidden_sufficiency_projection(row))
    return result


def score(state):
    """Evaluator-only join after actual workflow execution, never prompt material."""
    from graph.state import validate_state
    from evaluation.v2 import score_completed
    clean=validate_state(state)
    if clean['incident_id'] not in CASES or clean['status'] in ('pending','running') or not clean['classification']:
        raise ValueError('Captured completed in-plan workflow required for scoring')
    result=score_completed(clean,inference_contract='rc-hidden-sufficiency-v1')
    return {**result,'inference_contract':'rc-hidden-sufficiency-v1'}


def reference_review_status(document=None):
    review=document if document is not None else json.loads(REVIEW.read_text())
    sources={i.incident_id:i for i in inputs()}
    complete=True
    for case in review['cases']:
        r=case['preexecution_human_review']
        try:
            valid=(all(isinstance(r[k],str) and r[k].strip() for k in ('reviewer_id','timestamp','rationale','prior_use_attestation'))
                and datetime.fromisoformat(r['timestamp']).tzinfo is not None
                and r['decision'] in ('ACCEPT','ACCEPT_WITH_ALTERNATIVES')
                and r['source_sufficiency'] in ('sufficient_for_qualified','ambiguous','insufficient','conflicting')
                and isinstance(r['required_references'],list) and bool(r['required_references'])
                and set(r['required_references'])<={e.reference for e in build_catalog(sources[case['incident_id']]).values()})
        except (ValueError,TypeError,KeyError):
            valid=False
        if not valid:
            complete=False
    if [c['incident_id'] for c in review['cases']]!=list(CASES):raise ValueError('Review scope mismatch')
    return {'complete':complete,'errors':[] if complete else ['New case reference reviews and prior-use attestation pending']}


def prepare():
    verify()
    manifest=copy.deepcopy(enhanced_prepare());manifest.pop('manifest_sha256')
    manifest['version']=VERSION
    manifest['status']='BLOCKED_PENDING_NEW_HUMAN_REFERENCE_REVIEWS'
    manifest['cases']=[{'incident_id':i.incident_id,'split':'test','sanitized_input_sha256':digest(i.model_dump(mode='json'))} for i in inputs()]
    manifest['schedule']=[{'incident_id':ident,'conditions':['enhanced']} for ident in CASES]
    manifest['reference_review_path']=str(REVIEW)
    manifest['reference_review_validation']=reference_review_status()
    if manifest['reference_review_validation']['complete']:
        manifest['status']='AWAITING_FRESH_EXPLICIT_USER_APPROVAL'
    manifest['human_review_template_path']=str(REVIEW)
    manifest['offline_readiness']={'application_rc':'VERIFIED_LOCAL','real_model_quality':'NOT_TESTED','new_cases_used_for_tuning':False}
    manifest['execution_protocol']='After separate fresh approval: one preflight, then only the two scheduled test incidents, enhanced workflow. Five nodes maximum each, no loops, no retries, no tools/fixes. Stop on first technical/grounding/budget/safety failure. Holds are valid outcomes.'
    manifest['evaluation_contract']={'benchmark':'V2','scoring_version':'v2-scoring-1',
        'inference_contract':'rc-hidden-sufficiency-v1',
        'independence':'Internally generated family-split holdout; no prior recorded use found. Not independently authored or real-world benchmarking.',
        'limitations':'Unrecorded/external/transformed-ID use cannot be conclusively excluded; human attestation required.',
        'source_sufficiency_tags':'Removed from inference snapshot; frozen original datasets unchanged.',
        'grading':'Only after inference: evaluation.rc_benchmark.score uses exact versioned input projection and evaluator-only V2 label join. Human semantic review remains separate. Unscored/skipped/failed outputs are not incorrect diagnoses.',
        'human_protocol':'Blank pre-execution reference review required. Post-output semantic ratings remain blank until human confirmation; independent second review recommended before independent accuracy claims.'}
    manifest['bindings_sha256'].update({str(p):sha(p) for p in (
        RC_PATH,REVIEW,OUTPUT/'plan.json',OUTPUT/'inference-inputs.jsonl',
        Path('reports/release-candidate-safety/novelty-audit.json'),
        Path('reports/release-candidate-safety/live-http-evidence.json'),
        Path('reports/release-candidate-safety/browser-evidence.json'),
        Path('reports/release-candidate-safety/schema-evidence.json'),
        Path('data/benchmark_v2/semantic_rubric.json'))})
    manifest['manifest_sha256']=digest(manifest)
    return manifest


def write_artifacts():
    verify()  # Freeze code before reading evaluator-only labels for human material.
    OUTPUT.mkdir(exist_ok=True)
    inference=inputs()
    originals={r['incident_id']:r for r in rows(DEFAULT/'inference/test.jsonl')}
    labels={r['incident_id']:r for r in rows(DEFAULT/'labels/test.jsonl')}
    rubric_path=Path('reports/diagnostic-quality-paired-pilot/human-review-rubric.json')
    rubric=json.loads(rubric_path.read_text());dimensions={**rubric['frozen_v2_dimensions'],**rubric['supplemental_dimensions']}
    plan={'version':'rc-independent-v2-proposal-1','selected_ids':list(CASES),'selection_basis':'One source-sufficient and one ambiguous case from different held-out families; chosen before label inspection.',
          'source_sufficiency_operator_only':{ident:originals[ident]['evidence_quality'] for ident in CASES},
          'human_review_required':True,'paid_calls_authorized':False,'dispatch_enabled':False}
    review={'status':'PENDING_HUMANS','human_only':True,'rubric_path':str(rubric_path),'rubric_sha256':sha(rubric_path),
            'cases':[{'incident_id':ident,'sanitized_inference_evidence':i.model_dump(mode='json'),
                'human_only_frozen_reference':labels[ident],
                'preexecution_human_review':{'reviewer_id':None,'timestamp':None,'decision':None,'rationale':None,'source_sufficiency':None,'required_references':[],'accepted_alternatives':[],'prior_use_attestation':None},
                'postexecution_human_review':{'reviewer_id':None,'timestamp':None,'output_sha256':None,'dimension_ratings':{d:None for d in dimensions},'rationales':{d:None for d in dimensions},'hold_adjudication':None}}
                for ident,i in zip(CASES,inference)]}
    for name,value in [('plan.json',plan),('human-review.json',review)]:
        with (OUTPUT/name).open('x') as stream:
            import os
            os.chmod(OUTPUT/name,0o600);json.dump(value,stream,indent=2)
    with (OUTPUT/'inference-inputs.jsonl').open('x') as stream:
        import os
        os.chmod(OUTPUT/'inference-inputs.jsonl',0o600)
        for incident in inference:stream.write(incident.model_dump_json()+'\n')
    manifest=prepare()
    with (OUTPUT/'approval-manifest.json').open('x') as stream:json.dump(manifest,stream,indent=2)
    return manifest


if __name__=='__main__':
    m=write_artifacts();print(json.dumps({'manifest_sha256':m['manifest_sha256'],'reference_review':m['reference_review_validation']}))
