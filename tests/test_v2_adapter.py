"""No models or graph execution: adapter and human-entered workflow contracts."""
import copy
import json
from pathlib import Path

import pytest

from evaluation.v2 import V2Adapter, VERSION, verify_plan
from evaluation.v2_review import HumanReview,compare_reviews,adjudicate_reviews,label_adjudication,worksheet,DIMENSIONS,bind_review,semantic_summary
from evaluation.dataset import digest
from evaluation import metrics,provider
from data.benchmark_v2.build import DEFAULT,write,rows
from graph.state import initialize_state
from models.diagnosis import Classification
from tests.test_evaluation import state_and_label

PLAN=Path('reports/phase134-v2/plan.json')


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    def forbidden(*a,**kw): raise AssertionError('No paid calls authorized')
    monkeypatch.setattr(provider,'ChatOpenAI',forbidden)


def test_inputs_are_label_free_and_plan_remains_unexecuted():
    adapter=V2Adapter(PLAN)
    inputs=adapter.inputs()
    assert len(inputs)==5
    text=json.dumps([r.model_dump() for r in inputs])
    assert 'root_cause' not in text and 'expected_resolution' not in text and 'family_id' not in text
    result=adapter.summarize([])
    assert result['unattempted']==5 and result['classification_accuracy']['value'] is None
    assert result['semantic_accuracy'] is None
    assert not (PLAN.parent/'.dispatch.lock').exists()


def test_v2_scoring_does_not_use_v1_taxonomy(monkeypatch):
    monkeypatch.setattr(metrics,'CAUSES',{})
    adapter=V2Adapter(PLAN); state=initialize_state(adapter.inputs()[0],'offline-score')
    state['classification']=Classification(category='authentication',reasoning='Observed status')
    result=adapter.score(state)
    assert result['scoring_version']==VERSION and result['classification_correct']
    assert result['causal_accuracy'] is None and result['resolution_accuracy'] is None
    summary=adapter.summarize([{'incident_id':state['incident_id'],'status':'scored','metrics':result},
                               {'incident_id':adapter.inputs()[1].incident_id,'status':'failed'}])
    assert summary['classification_accuracy']['denominator']==1
    assert summary['workflow_failure_rate']['value']==.5
    assert summary['coverage']['value']==.2 and summary['unattempted']==3


def test_v1_metrics_stay_backward_compatible():
    state,label=state_and_label()
    before=metrics.score(state,label)
    V2Adapter(PLAN).summarize([])
    assert metrics.score(state,label)==before
    assert before['root_cause_lexical_correct'] and 'scoring_version' not in before


def test_state_must_match_frozen_incident_and_plan():
    adapter=V2Adapter(PLAN);state=initialize_state(adapter.inputs()[0],'offline')
    state['sanitized_inputs'].status_code=500
    with pytest.raises(ValueError):adapter.score(state)
    other=rows(DEFAULT/'inference/test.jsonl')[1]
    with pytest.raises(ValueError):adapter.score(initialize_state(other,'offline'))


@pytest.mark.parametrize('field,value',[('dataset_sha256','wrong'),('carried_prior_spend_usd','0'),
                                      ('aggregate_budget_usd','20'),('maximum_new_calls',100),('execution_enabled',True)])
def test_plan_and_budget_tampering_rejected(tmp_path,field,value):
    p=json.loads(PLAN.read_text());p[field]=value
    unsigned={k:v for k,v in p.items() if k!='plan_sha256'};p['plan_sha256']=digest(unsigned)
    path=tmp_path/'plan.json';path.write_text(json.dumps(p))
    with pytest.raises(ValueError): verify_plan(path)


def test_dataset_modification_rejected(tmp_path):
    target=tmp_path/'v2';write(target)
    p=target/'inference/test.jsonl';p.write_text(p.read_text().replace('request unsuccessful','changed'))
    with pytest.raises(ValueError):V2Adapter(PLAN,target)


def review(reviewer='alice',rating=2):
    return {'incident_id':'V2-02-01','reviewer_id':reviewer,'output_sha256':'a'*64,
            'rubric_version':'v2-semantic-1','dimension_ratings':{k:(3 if k=='validation_truthfulness' else rating) for k in DIMENSIONS},
            'applicability':{k:'Applicable to this recorded output' for k in DIMENSIONS},
            'rationale':'Evidence supports this assessment.','evidence_references':['/logs/0/fields/cache_age_seconds'],
            'hard_failures':[]}


def test_human_review_agreement_disagreement_and_adjudication():
    a=review();b=review('bob')
    assert compare_reviews(a,b)['status']=='AGREED'
    b['dimension_ratings']['evidence_grounding']=1
    assert compare_reviews(a,b)['status']=='NEEDS_ADJUDICATION'
    result=adjudicate_reviews(a,b,'carol',review('carol'),'Reviewed conflicting interpretation against the original evidence.')
    assert result['status']=='ADJUDICATED' and len(result['review_hashes'])==2
    with pytest.raises(ValueError):adjudicate_reviews(a,b,'alice',a,'Cannot self-adjudicate the review.')
    b['output_sha256']='b'*64
    with pytest.raises(ValueError):compare_reviews(a,b)


def test_review_requires_identity_valid_scores_and_real_output_hash():
    with pytest.raises(ValueError):HumanReview.model_validate(worksheet('V2-02-01'))
    bad=review();bad['dimension_ratings']['evidence_grounding']=4
    with pytest.raises(ValueError):HumanReview.model_validate(bad)
    with pytest.raises(ValueError):compare_reviews(review(),review())


def test_label_adjudication_is_sidecar_only():
    original=rows(DEFAULT/'labels/test.jsonl')[0];before=copy.deepcopy(original)
    proposed={**original,'expected_resolution':'Review key discovery before any approved cache operation'}
    result=label_adjudication(original,proposed,reviewer_ids=['alice','bob'],adjudicator_id='carol',
                              decision='propose_revision',rationale='Clarify the safe preconditions for refresh.')
    assert original==before and result['dataset_mutated'] is False
    with pytest.raises(ValueError):label_adjudication(original,proposed,reviewer_ids=['alice','alice'],
                    adjudicator_id='carol',decision='retain',rationale='Independent reviewers required.')


def test_semantic_summary_and_output_binding():
    assert semantic_summary([])['dimensions']['evidence_grounding']['mean'] is None
    a=review()
    assert semantic_summary([a])['dimensions']['evidence_grounding']['mean']==2
    with pytest.raises(ValueError): bind_review(a,{'incident_id':a['incident_id'],'output_sha256':'b'*64})
    assert bind_review(a,{'incident_id':a['incident_id'],'output_sha256':'a'*64}).reviewer_id=='alice'
    with pytest.raises(ValueError):semantic_summary([a,a])
