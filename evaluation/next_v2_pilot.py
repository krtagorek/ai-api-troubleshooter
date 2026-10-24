"""Allowlisted reviewed V2 scope. Preparation never initializes a provider."""
import copy
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from data.benchmark_v2.build import DEFAULT, rows
from evaluation.dataset import digest, contains_labels
from evaluation.enhanced_pilot import prepare as enhanced_prepare
from evaluation.quality_pilot import sha
from evaluation.release_candidate import verify, PATH as RC_PATH
from evaluation.v2 import hidden_sufficiency_projection
from tools.evidence_catalog import build_catalog

VERSION = 'next-controlled-v2-preparation-1'
CASES = ('V2-12-01', 'V2-12-04')
OUTPUT = Path('reports/next-controlled-v2-plan')
REVIEW = OUTPUT/'human-review.json'
REVIEW_HASH = '2e94ebc4f4bc2d7a11aab27e14e5b47aec90c8eb32eae0fc5f9854877b47f440'
INPUT_HASH = '9bc03fa20c6e21a24e7906411a97afce9d7bc552afee2ee616c8622cd31dbdf9'
SNAPSHOTS = (
    'ec3f8452b1f1197513e817793dc601f02048f9457baae4160e4535bc3f25ce9e',
    '98d51dc38f783fd52e4f60e9325f3d9633abbeae3f09ba4b8215f7022e4ff3cb',
)


def inputs():
    path = OUTPUT/'inference-inputs.jsonl'
    if sha(path) != INPUT_HASH:
        raise ValueError('Reviewed input file changed')
    source = {r['incident_id']:r for r in rows(DEFAULT/'inference/test.jsonl')}
    records = [json.loads(line) for line in path.read_text().splitlines()]
    if [r.get('incident_id') for r in records] != list(CASES):
        raise ValueError('Unexpected incident scope')
    result = []
    for ident, expected, row in zip(CASES, SNAPSHOTS, records):
        frozen = hidden_sufficiency_projection(source[ident])
        if contains_labels(row) or digest(row) != expected or row != frozen.model_dump(mode='json'):
            raise ValueError('Inference input differs from reviewed sanitized projection')
        result.append(frozen)
    return result


def reference_review_status(document=None):
    review = document if document is not None else json.loads(REVIEW.read_text())
    incidents = {i.incident_id:i for i in inputs()}
    valid = [c['incident_id'] for c in review['cases']] == list(CASES)
    for case, sufficiency in zip(review['cases'], ('sufficient_for_qualified', 'ambiguous')):
        r = case['preexecution_human_review']
        try:
            valid = valid and (
                r['reviewer_id'] == 'rxk163' and r['decision'] == 'ACCEPT_WITH_ALTERNATIVES'
                and r['source_sufficiency'] == sufficiency
                and all(isinstance(r[k],str) and r[k].strip() for k in ('timestamp','rationale','prior_use_attestation'))
                and datetime.fromisoformat(r['timestamp']).tzinfo is not None
                and bool(r['accepted_alternatives']) and bool(r['required_references'])
                and set(r['required_references']) <= {e.reference for e in build_catalog(incidents[case['incident_id']]).values()})
        except (ValueError, TypeError, KeyError):
            valid = False
    return {'complete':bool(valid),'errors':[] if valid else ['Confirmed review scope or fields invalid']}


def prepare():
    verify()
    if sha(REVIEW) != REVIEW_HASH:
        raise ValueError('Confirmed human worksheet changed')
    review = json.loads(REVIEW.read_text())
    if not reference_review_status(review)['complete']:
        raise ValueError('Human reference review incomplete')
    rubric = Path(review['rubric_path'])
    if sha(rubric) != review['rubric_sha256']:
        raise ValueError('Review rubric changed')
    # Human-only frozen labels are checked here, never used by inputs().
    labels = {r['incident_id']:r for r in rows(DEFAULT/'labels/test.jsonl')}
    for case, incident in zip(review['cases'], inputs()):
        if (case['human_only_frozen_reference'] != labels[incident.incident_id]
                or case['sanitized_inference_evidence'] != incident.model_dump(mode='json')):
            raise ValueError('Human packet differs from frozen sources')
    manifest = copy.deepcopy(enhanced_prepare())
    manifest.pop('manifest_sha256')
    manifest.update(version=VERSION, status='AWAITING_FRESH_EXPLICIT_USER_APPROVAL',
        execution_enabled=False, paid_approval_granted=False,
        cases=[{'incident_id':i,'split':'test','sanitized_input_sha256':h} for i,h in zip(CASES,SNAPSHOTS)],
        schedule=[{'incident_id':i,'conditions':['enhanced']} for i in CASES],
        reference_review_path=str(REVIEW),human_review_template_path=str(REVIEW),
        reference_review_validation=reference_review_status(review))
    b = manifest['budget']
    if (b['prior_estimated_spend_usd'] != '0.0550552'
            or b['maximum_new_calls_including_preflight'] != 11
            or Decimal(b['maximum_new_reservations_usd']) != Decimal('0.1712128')):
        raise ValueError('Approved preparation budget ceiling changed')
    calculated=(Decimal(32768)*Decimal(b['input_usd_per_million']) + Decimal(1536)*Decimal(b['output_usd_per_million']))/Decimal(1000000)*11
    if calculated > Decimal('0.1712128'):
        raise ValueError('Recalculated reservation exceeds preparation limit')
    b.update(planning_estimate_usd=None, estimate_basis='Hard reservation ceiling only: one preflight plus at most five nodes per case; no forecast of actual usage. Include complete provider envelope guard. No retries, tools or loops.')
    manifest['execution_protocol']='After separate exact fresh approval: one preflight then V2-12-01 and V2-12-04 enhanced only; isolated snapshots/state, each node at most once, no retries, loops, external tools or fixes; stop entire run on first technical/grounding/budget/safety failure. Legitimate holds are valid.'
    manifest['evaluation_contract']={'benchmark':'V2','scoring_version':'v2-scoring-1','inference_contract':'rc-hidden-sufficiency-v1','adapter':VERSION,'independence':'Internally generated variants of one family; not independent mechanisms. These cases have now been used in explicitly recorded mocked transport verification.','grading':'Labels and human reviews are evaluator-only; score only after inference. No causal accuracy claim before human semantic review.'}
    manifest['offline_readiness']={'scope_binding':'ALLOWLISTED','reference_reviews':'CONFIRMED_HASH_BOUND','real_model_access':'NOT_TESTED','paid_execution':'NOT_APPROVED'}
    paths=[RC_PATH,REVIEW,OUTPUT/'inference-inputs.jsonl',OUTPUT/'plan.json',OUTPUT/'novelty-audit.json',rubric,
           DEFAULT/'inference/test.jsonl',DEFAULT/'labels/test.jsonl',Path('data/benchmark_v2/semantic_rubric.json')]
    price = OUTPUT/'pricing-verification.json'
    if price.exists():paths.append(price)
    manifest['bindings_sha256'].update({str(p):sha(p) for p in paths})
    manifest['manifest_sha256']=digest(manifest)
    return manifest


def score(state):
    from graph.state import validate_state
    from evaluation.v2 import score_completed
    state=validate_state(state)
    if state['incident_id'] not in CASES or state['status'] in ('pending','running') or not state['classification']:
        raise ValueError('Captured completed in-scope workflow required')
    return score_completed(state,inference_contract='rc-hidden-sufficiency-v1')
