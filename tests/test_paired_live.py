"""No network: exercise real transport adapter with mocked SDK I/O only."""
import copy
import json
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage

from evaluation import paired_live, provider
from evaluation.paired_harness import _run
from evaluation.paired_review import validate_reference_review
from evaluation.quality_pilot import prepare, REFERENCE_REVIEW
from evaluation.dataset import digest
from tests.test_paired_harness import fixtures


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    monkeypatch.setattr('socket.socket.connect',Mock(side_effect=AssertionError('Network forbidden')))
    monkeypatch.setattr(provider,'ChatOpenAI',Mock(side_effect=AssertionError('Real SDK forbidden')))


def pricing(m):
    b=m['budget']
    return {'model':m['model']['name'],'source':b['pricing_source'],
            'input_usd_per_million':b['input_usd_per_million'],'output_usd_per_million':b['output_usd_per_million'],
            'verified_at':datetime.now(timezone.utc).isoformat(),'verified_by':'CI-only-synthetic-verifier'}


def test_incomplete_reference_review_fixture_blocks_dispatch(tmp_path,monkeypatch):
    # Keep this negative test independent of the real human review lifecycle.
    m=copy.deepcopy(prepare())
    m['reference_review_validation']={'complete':False,'reviewed_cases':0,'errors':['Synthetic incomplete review']}
    m['manifest_sha256']=digest({k:v for k,v in m.items() if k!='manifest_sha256'})
    monkeypatch.setattr(paired_live,'prepare',lambda:m)
    approval={'manifest_sha256':m['manifest_sha256'],'maximum_new_spend_usd':m['budget']['maximum_new_reservations_usd'],'explicit_user_approval':True}
    with pytest.raises(ValueError,match='reference review incomplete'):
        paired_live.run_approved(m,approval,pricing(m),tmp_path/'run')
    provider.ChatOpenAI.assert_not_called()
    assert not (tmp_path/'run').exists()


def test_reference_validator_positive_and_negative_in_temporary_file(tmp_path):
    w=json.loads(REFERENCE_REVIEW.read_text())
    for n,c in enumerate(w['cases']):
        c['preoutput_reference_review']={'reviewer_id':'CI-SYNTHETIC-NOT-A-HUMAN','timestamp':'2026-01-01T00:00:00Z',
            'rationale':'Test fixture only; never a real review','source_sufficiency':'ambiguous',
            'injected_vs_inferable_cause':'Synthetic test of validator only','accepted_alternatives':[],
            'required_references':['/status_code'],'optional_references':[],'irrelevant_references':[],
            'selection_set_exhaustive':False}
    p=tmp_path/'review.json';p.write_text(json.dumps(w))
    assert validate_reference_review(p,['V2-01-01','V2-03-04'])['complete']
    w['cases'][0]['preoutput_reference_review']['required_references']=['/root_cause']
    p.write_text(json.dumps(w))
    assert not validate_reference_review(p,['V2-01-01','V2-03-04'])['complete']


@pytest.mark.parametrize('kind',['approval','hash','price'])
def test_dispatch_gates_before_client_creation(tmp_path,monkeypatch,kind):
    m=prepare()
    if kind=='hash':
        m['model']['retries']=1
    else:
        m=copy.deepcopy(m);m['reference_review_validation']={'complete':True}
        m['manifest_sha256']=digest({k:v for k,v in m.items() if k!='manifest_sha256'})
        monkeypatch.setattr(paired_live,'prepare',lambda:m)
    approval={'manifest_sha256':m['manifest_sha256'],'maximum_new_spend_usd':m['budget']['maximum_new_reservations_usd'],'explicit_user_approval':True}
    p=pricing(m)
    if kind=='approval': approval['explicit_user_approval']=False
    if kind=='price': p['input_usd_per_million']='0.41'
    with pytest.raises(ValueError): paired_live.run_approved(m,approval,p,tmp_path/'run')
    provider.ChatOpenAI.assert_not_called()


def fake_sdk(monkeypatch,context,responses, failure=None):
    # Unit transport rehearsal substitutes the upstream claim guard explicitly.
    # Real dispatcher tests replace this with the actual lock-held claim guard.
    context.dispatch_guard = lambda: None
    calls=[]
    class Fake:
        def __init__(self,**kwargs):
            assert kwargs['max_retries']==0 and kwargs['base_url']=='https://api.openai.com/v1'
            assert kwargs['store'] is False and kwargs['http_client'].follow_redirects is False
            self.options=kwargs
        def with_structured_output(self,schema,**options):
            self.schema=schema
            if isinstance(schema,dict) and schema.get('title')=='LiveLogInvestigatorOutput':
                assert options['strict'] is True
                assert len(schema['properties'])==5
            if isinstance(schema,dict) and schema.get('title')=='RootCauseAnalystOutput':
                assert options['strict'] is True
                assert schema['$defs']['Hypothesis']['additionalProperties'] is False
            return self
        def invoke(self,messages):
            calls.append(context.current)
            import httpx
            schema=self.schema.model_json_schema() if isinstance(self.schema,type) else self.schema
            request=httpx.Request('POST','https://api.openai.com/v1/chat/completions',
                json={'messages':[m.content for m in messages],'response_format':schema})
            self.options['http_client'].event_hooks['request'][0](request)
            if failure=='transport': raise RuntimeError('password=must-never-appear')
            data={'connectivity':True} if context.current=='connectivity_preflight' else responses[context.current]
            import jsonschema
            jsonschema.validate(data,schema)
            usage=None if failure=='usage' else {'input_tokens':100,'output_tokens':20,'total_tokens':120}
            return {'raw':AIMessage(content=json.dumps(data),usage_metadata=usage,
                response_metadata={'model_name':'unexpected-model' if failure=='model' else 'gpt-4.1-mini-2025-04-14'}),'parsed':data,'parsing_error':None}
    monkeypatch.setattr(provider,'ChatOpenAI',Fake)
    monkeypatch.setenv('OPENAI_API_KEY','CI-ONLY-NOT-A-REAL-KEY')
    return calls


def test_real_adapter_mocked_io_shared_paths_and_actual_usage_accounting(tmp_path,monkeypatch):
    m=prepare();context=paired_live.LiveContext(m)
    calls=fake_sdk(monkeypatch,context,fixtures())
    summary=_run(m,{},tmp_path/'run',live=context)
    assert len(calls)==len(set(calls))==10
    assert summary['actual_provider_calls']==10
    assert summary['actual_usage']['observed_input_tokens']==1000
    assert Decimal(summary['new_usage_priced_estimated_spend_usd'])==Decimal('0.00072')
    assert Decimal(summary['aggregate_conservative_spend_usd'])==Decimal('0.0557752')
    assert len(json.loads((tmp_path/'run/reviewer-outputs.json').read_text()))==4
    assert 'CI-ONLY-NOT-A-REAL-KEY' not in (tmp_path/'run/audit.jsonl').read_text()
    events=[json.loads(s) for s in (tmp_path/'run/audit.jsonl').read_text().splitlines()]
    account=next(e for e in reversed(events) if e['kind']=='actual_provider_accounting')
    assert account['calls'][0]['input_tokens']==100
    assert account['calls'][0]['output_tokens']==20
    assert account['usage']['observed_input_tokens']==1000
    persisted=json.loads((tmp_path/'run/summary.json').read_text())
    assert persisted['actual_usage']['observed_output_tokens']==200


@pytest.mark.parametrize('failure',['transport','usage','grounding','model'])
def test_transport_usage_and_grounding_failure_stop_every_arm(tmp_path,monkeypatch,failure):
    m=prepare();context=paired_live.LiveContext(m);f=fixtures()
    if failure=='grounding': f['V2-01-01/baseline/investigator']['selected_evidence_handles']=['E99999']
    calls=fake_sdk(monkeypatch,context,f,None if failure=='grounding' else failure)
    with pytest.raises(ValueError,match='first failure'):
        _run(m,{},tmp_path/'run',live=context)
    assert len(calls)==(3 if failure=='grounding' else 1)
    journal=(tmp_path/'run/audit.jsonl').read_text()
    assert 'live_stopped' in journal and 'must-never-appear' not in journal
    assert 'V2-01-01/enhanced' not in journal
    if failure=='model':
        assert context.budget.reserved_or_spent==Decimal('.0155648')
        assert context.budget.stopped=='unexpected_model'


def test_single_use_marker_and_shared_budget_lock(tmp_path,monkeypatch):
    m=copy.deepcopy(prepare());m['reference_review_validation']={'complete':True}
    m['manifest_sha256']=digest({k:v for k,v in m.items() if k!='manifest_sha256'})
    monkeypatch.setattr(paired_live,'prepare',lambda:m)
    monkeypatch.setattr(paired_live,'configuration_check',lambda:{'ready':True})
    execute=Mock(return_value={'mocked_transport':True})
    monkeypatch.setattr(paired_live,'_run',execute)
    monkeypatch.chdir(tmp_path);Path('reports').mkdir()
    approval={'manifest_sha256':m['manifest_sha256'],'maximum_new_spend_usd':m['budget']['maximum_new_reservations_usd'],'explicit_user_approval':True}
    import fcntl
    with Path('reports/.v2-aggregate-budget.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError): paired_live.run_approved(m,approval,pricing(m),tmp_path/'run')
        execute.assert_not_called()
    paired_live.run_approved(m,approval,pricing(m),tmp_path/'run')
    execute.assert_called_once()
    with pytest.raises(FileExistsError): paired_live.run_approved(m,approval,pricing(m),tmp_path/'run2')
    execute.assert_called_once()


def test_live_validator_failures_and_no_tools(tmp_path,monkeypatch):
    from tests.test_paired_harness import full_branch_fixtures
    m=prepare();context=paired_live.LiveContext(m);f=full_branch_fixtures()
    f['V2-01-01/baseline/validator']['validation']['status']='passed'
    calls=fake_sdk(monkeypatch,context,f)
    with pytest.raises(ValueError,match='first failure'):
        _run(m,{},tmp_path/'run',live=context)
    assert len(calls)==6 and 'V2-01-01/enhanced/classifier' not in calls


def test_oversize_actual_request_is_stopped_before_mock_transport(tmp_path,monkeypatch):
    from evaluation.budget import Budget
    from evaluation.paired_harness import Audit
    m=prepare();context=paired_live.LiveContext(m)
    calls=fake_sdk(monkeypatch,context,fixtures())
    audit=Audit(tmp_path/'journal')
    # Large preflight request exercises the same complete SDK request guard.
    budget=Budget(Decimal('.3268608'),Decimal('.4'),Decimal('1.6'),21,input_cap=4100)
    try:
        with pytest.raises(Exception): context.start(budget,audit)
        assert len(calls)<=1 and budget.stopped
    finally: audit.close()
