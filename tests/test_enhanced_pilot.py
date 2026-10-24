"""No network or real SDK: enhanced-only manifest and transport rehearsal."""
import copy
import json
from decimal import Decimal
from unittest.mock import Mock

import pytest

from evaluation import paired_live, provider
from evaluation.enhanced_pilot import prepare
from evaluation.dataset import digest
from evaluation.paired_harness import run_offline, _run
from tests.test_paired_harness import full_branch_fixtures
from tests.test_paired_live import fake_sdk, pricing


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    monkeypatch.setattr('socket.socket.connect', Mock(side_effect=AssertionError('Offline only')))
    monkeypatch.setattr(provider, 'ChatOpenAI', Mock(side_effect=AssertionError('No real provider')))


def fixtures():
    result = {k: v for k, v in full_branch_fixtures().items() if '/enhanced/' in k}
    # Immutable previously reviewed investigator output, not a newly inferred answer.
    from tests.publication_fixtures import read
    result['V2-01-01/enhanced/investigator']=read('investigator-selection.json')
    hypothesis=result['V2-01-01/enhanced/root_cause']['hypotheses'][0]
    hypothesis['evidence_assessments']=[
        {'relation':'supporting','evidence_ids':hypothesis['supporting_evidence_ids'],
         'explanation':'The observed identifier is a member of the qualified comparison.',
         'hypothesis_prediction':None},
        {'relation':'missing','evidence_ids':[],
         'explanation':'Independent expiry and revocation decisions remain unobserved; mechanisms may coexist.',
         'hypothesis_prediction':None}]
    return result


def test_manifest_scope_budget_and_blank_human_review():
    m = prepare()
    assert m == prepare()
    assert m['manifest_sha256'] == digest({k:v for k,v in m.items() if k!='manifest_sha256'})
    assert list(m['conditions']) == ['enhanced']
    assert m['budget']['maximum_new_calls_including_preflight'] == 11
    assert Decimal(m['budget']['maximum_new_reservations_usd']) == Decimal('.1712128')
    assert Decimal(m['budget']['aggregate_conservative_ceiling_usd']) == Decimal('.2262680')
    assert m['budget']['prior_estimated_spend_usd'] == '0.0550552'
    assert not m['execution_enabled'] and not m['paid_approval_granted']
    assert m['reference_review_validation']['complete']
    w=json.load(open(m['human_review_template_path']))
    assert all(r['reviewer_id'] is None and r['timestamp'] is None and r['output_sha256'] is None
               and all(v is None for v in r['dimension_ratings'].values()) for r in w['reviews'])


def test_enhanced_only_qualified_and_ambiguous_routes(tmp_path):
    summary = run_offline(prepare(), fixtures(), tmp_path/'fixtures')
    assert summary['actual_provider_calls'] == 0
    results=json.loads((tmp_path/'fixtures/outputs.json').read_text())
    assert results[0]['executed_nodes'] == ['classifier','investigator','root_cause','resolution','validator']
    assert results[0]['state']['diagnosis']['status'] == 'tentative'
    assert results[0]['state']['evidence']['completeness'] == 'partial'
    assert len(results[0]['state']['hypotheses'][0]['evidence_assessments']) == 2
    assert results[0]['state']['proposed_fix']['requires_human_approval']
    assert results[0]['state']['validation_results'][-1]['status']=='NOT_TESTED'
    assert results[1]['executed_nodes'] == ['classifier','investigator']
    assert results[1]['outcome']=='evidence_hold'
    assert summary['simulated_calls_reserved']==8


def test_mock_sdk_transport_usage_schemas_and_event_audit(tmp_path,monkeypatch):
    m=prepare(); context=paired_live.LiveContext(m)
    calls=fake_sdk(monkeypatch,context,fixtures())
    result=_run(m,{},tmp_path/'sdk',live=context)
    # Transport rehearsal reports SDK usage, but all responses/usage are synthetic.
    assert len(calls)==len(set(calls))==8
    assert all('/baseline/' not in k for k in calls)
    assert result['actual_usage']['observed_input_tokens']==800
    assert result['actual_usage']['observed_output_tokens']==160
    events=[json.loads(s) for s in (tmp_path/'sdk/audit.jsonl').read_text().splitlines()]
    previous=None
    for n,event in enumerate(events):
        assert event['sequence']==n and event['previous']==previous
        previous=event['sha256']
        assert previous==digest({k:v for k,v in event.items() if k!='sha256'})
    journal=(tmp_path/'sdk/audit.jsonl').read_text()
    assert 'CI-ONLY-NOT-A-REAL-KEY' not in journal
    assert 'provider_response' in journal and 'node_completed' in journal


@pytest.mark.parametrize('failure',['transport','usage','grounding','model'])
def test_first_failure_stops_entire_enhanced_pilot(tmp_path,monkeypatch,failure):
    m=prepare(); context=paired_live.LiveContext(m); f=fixtures()
    if failure=='grounding': f['V2-01-01/enhanced/investigator']['selected_evidence_handles']=['E99999']
    calls=fake_sdk(monkeypatch,context,f,None if failure=='grounding' else failure)
    with pytest.raises(ValueError,match='first failure'):
        _run(m,{},tmp_path/'failed',live=context)
    assert len(calls)==(3 if failure=='grounding' else 1)
    assert not any('V2-03-04' in k for k in calls)


def test_no_approval_blocks_before_provider_or_marker(tmp_path):
    m=prepare()
    with pytest.raises(ValueError,match='approval'):
        paired_live.run_approved(m,{},pricing(m),tmp_path/'blocked')
    provider.ChatOpenAI.assert_not_called()
    assert not (tmp_path/'blocked').exists()


def test_stale_and_unapproved_arm_rejected(tmp_path):
    m=copy.deepcopy(prepare());m['model']['retries']=1
    with pytest.raises(ValueError,match='manifest'):
        run_offline(m,fixtures(),tmp_path/'stale')
    f=fixtures();f['V2-01-01/baseline/classifier']={}
    with pytest.raises(ValueError,match='Unapproved'):
        run_offline(prepare(),f,tmp_path/'extra')
