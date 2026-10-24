"""Actual SDK serialization with local MockTransport; no provider connections."""
import json
from decimal import Decimal
from pathlib import Path
import httpx
import pytest

from evaluation import provider
from evaluation.budget import Budget
from evaluation.v2_handles_verification import prepare
from evaluation.v2 import V2Adapter
from graph.state import initialize_state
from agents import error_classifier,log_investigator
from tools.evidence_catalog import handle_catalog


def test_actual_sdk_request_handle_schema_and_complete_overhead(monkeypatch,tmp_path):
    from data.benchmark_v2.build import DEFAULT, rows
    from models.incident import Incident
    incident=Incident.model_validate(rows(DEFAULT/'inference/development.jsonl')[0])
    envelope=handle_catalog(incident);handle=next(e['handle'] for e in envelope['evidence_catalog'] if e['reference']=='/status_code')
    captured=[];real_client=httpx.Client
    def transport(request):
        payload=json.loads(request.content);captured.append((payload,len(request.content)))
        props=payload['response_format']['json_schema']['schema']['properties']
        assert props['evidence_snapshot']['enum']==[envelope['evidence_snapshot']]
        assert props['evidence_contract']['enum']==['evidence-handles-v1']
        defs=payload['response_format']['json_schema']['schema']['$defs']
        name=payload['response_format']['json_schema']['name']
        if name=='ClassifierOutput':
            assert handle in defs['Classification']['properties']['evidence_references']['items']['enum']
            answer={'classification':{'category':'authentication','confidence':.5,'reasoning':'Offline transport fixture','evidence_references':[handle]}}
        else:
            assert 'EvidenceCollection' not in defs
            assert set(props)=={'evidence_contract','evidence_snapshot','selected_evidence_handles','completeness','diagnostic_gaps'}
            assert payload['response_format']['json_schema']['strict'] is True
            answer={'selected_evidence_handles':[handle],'completeness':'partial','diagnostic_gaps':[]}
        answer.update(evidence_contract=envelope['evidence_contract'],evidence_snapshot=envelope['evidence_snapshot'])
        assert 'ev:' not in json.dumps(payload['messages'])
        assert 'root_cause' not in json.dumps(payload['messages'])
        return httpx.Response(200,json={'id':'offline-fixture','object':'chat.completion','model':'explicit-offline-sdk-fixture',
          'choices':[{'index':0,'message':{'role':'assistant','content':json.dumps(answer)},'finish_reason':'stop'}],
          'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150}})
    class OfflineClient(real_client):
        def __init__(self,**kwargs):super().__init__(transport=httpx.MockTransport(transport),**kwargs)
    monkeypatch.setattr(httpx,'Client',OfflineClient)
    monkeypatch.setenv('OPENAI_API_KEY','CI-only-not-real')
    budget=Budget(Decimal('2'),Decimal('.4'),Decimal('1.6'),6,reserved_or_spent=Decimal('.0188840'),observed_cost=Decimal('.0188840'))
    llm=provider.MeteredOpenAI('gpt-4.1-mini-2025-04-14',budget,lambda:None)
    state=initialize_state(incident,'offline-wire-serialization')
    result=error_classifier.error_classifier(state,llm=llm)
    assert result['classification'].evidence_references==['/status_code']
    evidence=log_investigator.log_investigator(state,llm=llm)['evidence'].items[0]
    assert evidence.observation=='401' and evidence.source=='response' and evidence.evidence_id.startswith('ev:')
    assert len(captured)==2
    for row,(_,length) in zip(budget.calls,captured):
        assert row['provider_request_bytes']==length
        assert row['input_envelope_bytes_with_margin']==length+4096<=32768
    assert budget.calls[0]['structured_output']['classification']['evidence_references']==[handle]
    (tmp_path/'provider-request-audit.json').write_text(json.dumps({'mode':'OFFLINE_MOCK_TRANSPORT_NO_PROVIDER_CONNECTION',
        'requests':[payload for payload,_ in captured],'metering':budget.calls},indent=2))


def test_new_manifest_latest_budget_and_single_scope():
    m=prepare()
    assert m['selected_ids']==['V2-02-01'] and m['maximum_new_calls']==6
    assert m['prior_estimated_spend_usd']=='0.0188840'
    assert m['new_reservation_ceiling_usd']=='0.0933888'
    assert m['conservative_aggregate_ceiling_usd']=='0.1122728'
    assert not m['paid_approval_granted'] and not m['execution_enabled']


def test_replay_examples_are_different_not_silent_repairs():
    directory=Path('reports/phase1310-handles')
    first=json.loads((directory/'offline-replay.initial-example.json').read_text())
    final=json.loads((directory/'offline-replay.json').read_text())
    assert first['trusted_classification']['evidence_references'][-1]=='/logs/0/message'
    assert final['trusted_classification']['evidence_references'][-1]=='/actual_behavior'
    assert first['original_run_sha256']==final['original_run_sha256']
    assert not first['historical_outcome_changed'] and not final['historical_outcome_changed']
