import copy
import json
from unittest.mock import patch

import jsonschema
import pytest

from evaluation.audit_metrics import accounting_event, artifact, provider_usage
from evaluation.paired_failure_replay import AUDIT, replay
from evaluation.paired_harness import Audit
from evaluation.provider import provider_schema
from graph.state import RootCauseAnalystOutput
from tools.redaction import redact


def test_original_replay_immutable_corrected_example_strict_grounded():
    before=AUDIT.read_bytes()
    with patch('socket.socket.connect',side_effect=AssertionError('No network')), patch(
        'agents.common.ChatOpenAI',side_effect=AssertionError('No provider')):
        r=replay()
    assert AUDIT.read_bytes()==before
    assert r['original_rejected'] and r['corrected_grounding_valid']
    assert not r['corrected_agent_and_grounding_valid']  # Legacy unsupported counterclaims are now rejected.
    assert r['original_errors']==[{'location':['hypotheses',2,'missing_evidence'],'type':'extra_forbidden'}]
    assert all('missing_evidence' not in h for h in r['corrected_schema_example']['hypotheses'])
    assert r['corrected_schema_example']['diagnosis']['missing_evidence']
    assert r['semantic_accuracy'] is None


def test_provider_root_schema_all_objects_closed_and_required_without_defaults():
    schema=provider_schema(RootCauseAnalystOutput,[])
    def walk(node):
        if isinstance(node,dict):
            assert 'default' not in node
            if 'properties' in node:
                assert node['additionalProperties'] is False
                assert set(node['required'])==set(node['properties'])
            for v in node.values():walk(v)
        if isinstance(node,list):
            for v in node:walk(v)
    walk(schema)
    assert 'missing_evidence' not in schema['$defs']['Hypothesis']['properties']
    assert 'missing_evidence' in schema['$defs']['Diagnosis']['properties']


def test_mock_sdk_enforces_root_schema_before_returning_extra_fields():
    schema=provider_schema(RootCauseAnalystOutput,[])
    corrected=replay()['corrected_schema_example']
    jsonschema.validate(corrected,schema)
    for extra in ('missing_evidence','tool_results','authorization'):
        invalid=copy.deepcopy(corrected);invalid['hypotheses'][0][extra]=[]
        with pytest.raises(jsonschema.ValidationError):jsonschema.validate(invalid,schema)
    del corrected['diagnosis']['uncertainty']
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(corrected,schema)


def test_numeric_metrics_survive_journal_roundtrip_without_secret_exceptions(tmp_path):
    audit=Audit(tmp_path/'audit.jsonl')
    try:
        audit.append('actual_provider_accounting',calls=[{'input_tokens':123,'output_tokens':45,
            'access_token':987654,'password':'do-not-leak','provider_response':'Bearer do-not-leak',
            'provider_usage_metadata':provider_usage({'input_tokens':123,'output_tokens':45,'total_tokens':168,
                'input_token_details':{'cache_read':80,'access_token':999},'secret':111})}],
            usage={'observed_input_tokens':123,'observed_output_tokens':45,'api_key':'do-not-leak'})
    finally:audit.close()
    text=(tmp_path/'audit.jsonl').read_text();event=json.loads(text)
    assert event['calls'][0]['input_tokens']==123 and event['calls'][0]['output_tokens']==45
    assert event['calls'][0]['provider_usage_metadata']['input_token_details']['cache_read']==80
    assert event['usage']['observed_input_tokens']==123
    assert event['calls'][0]['access_token']=='[REDACTED]'
    assert 'do-not-leak' not in text and '987654' not in text
    assert 'access_token' not in event['calls'][0]['provider_usage_metadata']['input_token_details']
    assert redact({'input_tokens':123})['input_tokens']=='[REDACTED]'


@pytest.mark.parametrize('value',['123','sk-secret-key',True,-1,1.5,None,{'secret':123}])
def test_non_integer_or_unsafe_counter_not_restored(value):
    assert 'input_tokens' not in provider_usage({'input_tokens':value})
    result=accounting_event('actual_provider_accounting',{'calls':[{'input_tokens':value}],'usage':{}})
    assert result['calls'][0]['input_tokens']=='[REDACTED]'


def test_numeric_whitelist_does_not_apply_to_untrusted_evidence_or_summaries():
    assert accounting_event('fixture_response',{'input_tokens':123})['input_tokens']=='[REDACTED]'
    result=artifact('summary.json',{'actual_usage':{'observed_input_tokens':123,'observed_output_tokens':4,'access_token':12345},'access_token':98765})
    assert result['actual_usage']['observed_input_tokens']==123
    assert result['actual_usage']['access_token']=='[REDACTED]'
    assert result['access_token']=='[REDACTED]'


def test_mock_sdk_rejects_original_extra_hypothesis_field(tmp_path,monkeypatch):
    from evaluation.paired_harness import _run
    from evaluation.paired_live import LiveContext
    from evaluation.quality_pilot import prepare
    from tests.test_paired_live import fake_sdk
    from tests.test_paired_harness import full_branch_fixtures
    events=[json.loads(s) for s in AUDIT.read_text().splitlines()]
    original=next(e for e in reversed(events) if e['kind']=='actual_provider_accounting')['calls'][-1]['provider_structured_output']
    f=full_branch_fixtures()
    f['V2-01-01/baseline/root_cause']=original
    m=prepare();context=LiveContext(m)
    with patch('socket.socket.connect',side_effect=AssertionError('No network')):
        calls=fake_sdk(monkeypatch,context,f)
        with pytest.raises(ValueError,match='first failure'):
            _run(m,{},tmp_path/'run',live=context)
    assert len(calls)==4
    assert 'V2-01-01/enhanced/classifier' not in calls
