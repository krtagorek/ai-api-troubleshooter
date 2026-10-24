"""Approved-scope preparation with mocked SDK only. Never real model access."""
import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from tests.rc_fixtures import isolated_release_candidate
from evaluation import next_v2_pilot as adapter,paired_live,provider
from evaluation.dataset import digest,contains_labels
from evaluation.paired_harness import _run,run_offline
from tests.test_paired_live import fake_sdk,pricing
from tools.evidence_catalog import handle_catalog


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr('socket.socket.connect',Mock(side_effect=AssertionError('No network')))
    monkeypatch.setattr(provider,'ChatOpenAI',Mock(side_effect=AssertionError('No real provider')))


def responses():
    result={}
    for incident in adapter.inputs():
        env=handle_catalog(incident)
        status=next(e['handle'] for e in env['evidence_catalog'] if e['reference']=='/status_code')
        common={'evidence_contract':env['evidence_contract'],'evidence_snapshot':env['evidence_snapshot']}
        prefix=incident.incident_id+'/enhanced/'
        result[prefix+'classifier']={**common,'classification':{'category':'dependency_failure','confidence':.5,'reasoning':'Synthetic CI symptom classification only','evidence_references':[status]}}
        result[prefix+'investigator']={**common,'selected_evidence_handles':[status],'completeness':'partial','diagnostic_gaps':[{'description':'CI fixture deliberately requests evidence','required_to_resolve':'Review sanitized correlated diagnostics'}]}
    return result


def test_scope_projection_review_hash_and_unapproved_budget():
    m=adapter.prepare()
    assert m['version']==adapter.VERSION
    assert m['schedule']==[{'incident_id':i,'conditions':['enhanced']} for i in adapter.CASES]
    assert m['bindings_sha256'][str(adapter.REVIEW)]==adapter.REVIEW_HASH
    assert m['reference_review_validation']['complete']
    assert not m['execution_enabled'] and not m['paid_approval_granted']
    assert m['budget']['prior_estimated_spend_usd']=='0.0550552'
    assert m['budget']['maximum_new_calls_including_preflight']==11
    assert m['budget']['maximum_new_reservations_usd']=='0.1712128'
    for i in adapter.inputs():
        row=i.model_dump(mode='json');assert not contains_labels(row)
        assert row['evidence_quality']=='unknown'
        assert 'evidence_quality' not in row['request_metadata']


def test_review_does_not_authorize_provider_or_claim(tmp_path,monkeypatch):
    m=adapter.prepare();client=Mock(side_effect=AssertionError('Must not initialize'))
    monkeypatch.setattr(paired_live,'MeteredOpenAI',client)
    with pytest.raises(ValueError,match='Exact fresh user approval required'):
        paired_live.run_approved(m,{},pricing(m),tmp_path/'absent')
    client.assert_not_called();assert not (tmp_path/'absent').exists()


@pytest.mark.parametrize('kind',['version','case','baseline','hash','calls'])
def test_altered_or_unsupported_manifest_rejected_before_provider(tmp_path,monkeypatch,kind):
    m=adapter.prepare()
    if kind=='version':m['version']='invented-proposal'
    if kind=='case':m['schedule'][0]['incident_id']='V2-01-01'
    if kind=='baseline':m['schedule'][0]['conditions']=['baseline']
    if kind=='hash':m['cases'][0]['sanitized_input_sha256']='0'*64
    if kind=='calls':m['budget']['maximum_new_calls_including_preflight']=12
    m['manifest_sha256']=digest({k:v for k,v in m.items() if k!='manifest_sha256'})
    approval={'manifest_sha256':m['manifest_sha256'],'maximum_new_spend_usd':'0.1712128','explicit_user_approval':True}
    client=Mock();monkeypatch.setattr(paired_live,'MeteredOpenAI',client)
    with pytest.raises(ValueError):paired_live.run_approved(m,approval,pricing(m),tmp_path/'absent')
    client.assert_not_called();assert not (tmp_path/'absent').exists()


def test_altered_input_and_review_fail_closed(tmp_path,monkeypatch):
    new=tmp_path/'proposal';new.mkdir()
    (new/'inference-inputs.jsonl').write_text('{}\n')
    monkeypatch.setattr(adapter,'OUTPUT',new)
    with pytest.raises(ValueError,match='input file changed'):adapter.inputs()
    review=tmp_path/'review.json';review.write_text('{}')
    monkeypatch.setattr(adapter,'REVIEW',review)
    with pytest.raises(ValueError,match='worksheet changed'):adapter.prepare()


def test_mock_sdk_holds_isolation_label_free_transport_and_numeric_usage(tmp_path,monkeypatch):
    m=adapter.prepare();context=paired_live.LiveContext(m)
    calls=fake_sdk(monkeypatch,context,responses())
    sdk=provider.ChatOpenAI
    captured=[]
    class InspectSDK:
        def __init__(self,**kw):self.client=sdk(**kw)
        def with_structured_output(self,schema,**kw):
            self.client.with_structured_output(schema,**kw);return self
        def invoke(self,messages):
            for message in messages:
                if isinstance(message.content,str) and message.content.startswith('UNTRUSTED_EVIDENCE_JSON\n'):
                    payload=json.loads(message.content.split('\n',1)[1]);row=payload['incident']
                    expected={i.incident_id:i.model_dump(mode='json') for i in adapter.inputs()}
                    assert row==expected[row['incident_id']]
                    assert not contains_labels(row)
                    assert 'human_review' not in payload and 'prior_use_attestation' not in str(payload)
                    captured.append(row['incident_id'])
            return self.client.invoke(messages)
    monkeypatch.setattr(provider,'ChatOpenAI',InspectSDK)
    summary=_run(m,{},tmp_path/'mocked',live=context)
    assert calls==['connectivity_preflight']+[i+'/enhanced/'+n for i in adapter.CASES for n in ('classifier','investigator')]
    assert len(calls)==len(set(calls))==5
    assert set(captured)==set(adapter.CASES)
    assert summary['actual_usage']['observed_input_tokens']==500
    assert summary['actual_usage']['observed_output_tokens']==100
    result=json.loads((tmp_path/'mocked/outputs.json').read_text())
    assert [r['state']['incident_id'] for r in result]==list(adapter.CASES)
    assert len({r['state']['thread_id'] for r in result})==2
    assert all(r['outcome']=='evidence_hold' and r['review'] is None for r in result)
    assert all(r['state']['max_retries']==0 and not r['state']['available_evidence_sources'] for r in result)


@pytest.mark.parametrize('failure',['transport','usage','model','grounding'])
def test_first_failure_stops_remaining_case(tmp_path,monkeypatch,failure):
    m=adapter.prepare();context=paired_live.LiveContext(m);r=responses()
    if failure=='grounding':r[adapter.CASES[0]+'/enhanced/investigator']['selected_evidence_handles']=['E999']
    calls=fake_sdk(monkeypatch,context,r,failure=None if failure=='grounding' else failure)
    with pytest.raises(ValueError,match='first failure'):_run(m,{},tmp_path/'failed',live=context)
    assert all(not c.startswith(adapter.CASES[1]) for c in calls)
    assert len(calls)==len(set(calls))
    assert not (tmp_path/'failed/outputs.json').exists()


def test_mocked_new_scope_uses_real_single_use_lifecycle(tmp_path,monkeypatch):
    from evaluation.dispatch_lifecycle import inspect_claim
    m=adapter.prepare();incidents=adapter.inputs();r=responses()
    m['bindings_sha256']={str(Path(p).resolve()):v for p,v in m['bindings_sha256'].items()}
    m['reference_review_path']=str(adapter.REVIEW.resolve())
    m['manifest_sha256']=digest({k:v for k,v in m.items() if k!='manifest_sha256'})
    monkeypatch.chdir(tmp_path);Path('reports').mkdir()
    checks=[]
    def prepared():
        checks.append(True)
        if list(Path('reports').glob('.*dispatch*.json')):
            raise ValueError('Unreconciled dispatch/history; stop')
        return m
    monkeypatch.setattr(adapter,'prepare',prepared)
    monkeypatch.setattr(adapter,'inputs',lambda:incidents)
    monkeypatch.setattr(paired_live,'configuration_check',lambda:{'ready':True})
    context=paired_live.LiveContext(m);calls=fake_sdk(monkeypatch,context,r)
    monkeypatch.setattr(paired_live,'LiveContext',lambda manifest:context)
    approval={'manifest_sha256':m['manifest_sha256'],'maximum_new_spend_usd':'0.1712128','explicit_user_approval':True}
    result=paired_live.run_approved(m,approval,pricing(m),tmp_path/'mocked')
    assert len(checks)==2 and result['actual_provider_calls']==5
    marker=Path('reports/.paired-live-dispatch-'+m['manifest_sha256']+'.json')
    assert inspect_claim(marker)['state']=='COMPLETED'
    original=marker.read_bytes()
    with pytest.raises(ValueError,match='Unreconciled'):
        paired_live.run_approved(m,approval,pricing(m),tmp_path/'replay')
    assert marker.read_bytes()==original and len(calls)==5


def test_budget_exhaustion_stops_before_remaining_nodes(tmp_path,monkeypatch):
    m=adapter.prepare();m['budget']['maximum_new_calls_including_preflight']=2
    context=paired_live.LiveContext(m);calls=fake_sdk(monkeypatch,context,responses())
    with pytest.raises(ValueError,match='first failure'):
        _run(m,{},tmp_path/'budget-failure',live=context)
    assert calls==['connectivity_preflight',adapter.CASES[0]+'/enhanced/classifier']
    assert not (tmp_path/'budget-failure/outputs.json').exists()
