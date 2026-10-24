"""Preparation only: new holdouts are never sent to a real or mocked model."""
import copy
import json
from decimal import Decimal
from unittest.mock import Mock

import pytest
from tests.rc_fixtures import isolated_release_candidate
from evaluation import paired_live
from evaluation.rc_benchmark import prepare,inputs,reference_review_status,REVIEW,CASES
from evaluation.dataset import digest,contains_labels
from evaluation.release_candidate import verify


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    monkeypatch.setattr('socket.socket.connect',Mock(side_effect=AssertionError('No network')))
    monkeypatch.setattr('evaluation.provider.ChatOpenAI',Mock(side_effect=AssertionError('No model')))
    monkeypatch.setattr('agents.common.ChatOpenAI',Mock(side_effect=AssertionError('No model')))


@pytest.fixture
def incomplete_reviews():
    """In-memory pending state, independent of actual human completion."""
    document=copy.deepcopy(json.loads(REVIEW.read_text()))
    for case in document['cases']:
        case['preexecution_human_review']={
            'reviewer_id':None,'timestamp':None,'decision':None,'rationale':None,
            'source_sufficiency':None,'required_references':[],
            'accepted_alternatives':[],'prior_use_attestation':None}
    return document


def test_rc_snapshot_and_manifest_bindings_are_stable():
    verify();m=prepare()
    assert prepare()==m
    assert m['manifest_sha256']==digest({k:v for k,v in m.items() if k!='manifest_sha256'})
    assert [c['incident_id'] for c in m['cases']]==list(CASES)
    assert m['reference_review_validation']==reference_review_status()
    assert not m['paid_approval_granted'] and not m['execution_enabled']
    assert m['budget']['prior_estimated_spend_usd']=='0.0550552'
    assert m['budget']['maximum_new_calls_including_preflight']==11
    assert Decimal(m['budget']['maximum_new_reservations_usd'])==Decimal('.1712128')
    assert Decimal(m['budget']['aggregate_conservative_ceiling_usd'])==Decimal('.2262680')


def test_new_inference_projection_has_no_labels_or_sufficiency_answers():
    for incident in inputs():
        payload=incident.model_dump(mode='json')
        assert not contains_labels(payload)
        assert payload['evidence_quality']=='unknown'
        assert 'evidence_quality' not in payload['request_metadata']
        assert 'human_review' not in payload and 'reference_cause' not in payload


def test_pending_review_blocks_even_a_simulated_approval_before_claim(tmp_path,monkeypatch,incomplete_reviews):
    status=reference_review_status(incomplete_reviews)
    monkeypatch.setattr('evaluation.rc_benchmark.reference_review_status',lambda:status)
    provider=Mock(side_effect=AssertionError('Provider must not initialize'))
    monkeypatch.setattr(paired_live,'MeteredOpenAI',provider)
    m=prepare();a={'manifest_sha256':m['manifest_sha256'],'maximum_new_spend_usd':m['budget']['maximum_new_reservations_usd'],'explicit_user_approval':True}
    with pytest.raises(ValueError,match='review incomplete'):
        paired_live.run_approved(m,a,{},tmp_path/'must-not-exist')
    assert not (tmp_path/'must-not-exist').exists()
    provider.assert_not_called()


def test_review_validation_never_accepts_missing_or_unsupported_references(incomplete_reviews):
    blank=incomplete_reviews;assert not reference_review_status(blank)['complete']
    synthetic=copy.deepcopy(blank)
    for case in synthetic['cases']:
        case['preexecution_human_review'].update(reviewer_id='CI-SYNTHETIC-NOT-A-HUMAN',timestamp='2026-10-23T00:00:00+00:00',rationale='Synthetic validator example only',decision='ACCEPT',source_sufficiency='ambiguous',required_references=['/status_code'],prior_use_attestation='Synthetic test, never a real human approval')
    assert reference_review_status(synthetic)['complete']
    synthetic['cases'][0]['preexecution_human_review']['required_references']=['/root_cause']
    assert not reference_review_status(synthetic)['complete']
    assert all(c['preexecution_human_review']['decision'] is None for c in blank['cases'])


def test_confirmed_reviews_do_not_grant_paid_approval(tmp_path,monkeypatch):
    assert reference_review_status()['complete']
    m=prepare()
    assert m['status']=='AWAITING_FRESH_EXPLICIT_USER_APPROVAL'
    assert not m['paid_approval_granted'] and not m['execution_enabled']
    provider=Mock(side_effect=AssertionError('Provider must not initialize'))
    monkeypatch.setattr(paired_live,'MeteredOpenAI',provider)
    with pytest.raises(ValueError,match='Exact fresh user approval required'):
        paired_live.run_approved(m,{}, {},tmp_path/'must-not-exist')
    provider.assert_not_called()
    assert not (tmp_path/'must-not-exist').exists()


@pytest.mark.parametrize('field,value',[
    ('reviewer_id',''),('timestamp','2026-10-23T00:00:00'),
    ('decision','REJECT'),('rationale',''),('prior_use_attestation',''),
    ('required_references',[]),('required_references',['/root_cause'])])
def test_incomplete_or_invalid_confirmed_review_is_rejected(field,value):
    document=copy.deepcopy(json.loads(REVIEW.read_text()))
    document['cases'][0]['preexecution_human_review'][field]=value
    assert not reference_review_status(document)['complete']


def test_versioned_grading_projection_on_previously_used_development_fixture():
    from data.benchmark_v2.build import rows,DEFAULT
    from evaluation.v2 import hidden_sufficiency_projection,score_completed
    from graph.state import initialize_state
    row=next(r for r in rows(DEFAULT/'inference/development.jsonl') if r['incident_id']=='V2-01-01')
    state=initialize_state(hidden_sufficiency_projection(row),'offline-projection-contract')
    assert score_completed(state,inference_contract='rc-hidden-sufficiency-v1')['scoring_version']=='v2-scoring-1'
    with pytest.raises(ValueError,match='differs from frozen'):
        score_completed(state)
    changed=copy.deepcopy(row);changed['actual_behavior']='Invented replacement observation'
    state=initialize_state(hidden_sufficiency_projection(changed),'offline-projection-contract')
    with pytest.raises(ValueError,match='differs from frozen'):
        score_completed(state,inference_contract='rc-hidden-sufficiency-v1')


def test_changed_code_identity_blocks_preparation_before_provider(isolated_release_candidate,monkeypatch):
    from evaluation import rc_benchmark
    identity=json.loads(isolated_release_candidate.read_text())
    identity['files_sha256']['graph/workflow.py']='0'*64
    isolated_release_candidate.write_text(json.dumps(identity))
    provider=Mock(side_effect=AssertionError('No provider initialization'))
    monkeypatch.setattr(paired_live,'MeteredOpenAI',provider)
    with pytest.raises(ValueError,match='code or installed dependencies changed'):
        rc_benchmark.prepare()
    provider.assert_not_called()
