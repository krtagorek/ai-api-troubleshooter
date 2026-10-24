import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.redaction import redact, redact_message_content
from evaluation.paired_harness import _run
from evaluation.paired_live import LiveContext
from evaluation.quality_pilot import prepare
from tests.test_paired_harness import fixtures
from tests.test_paired_live import fake_sdk

MARKER='UNTRUSTED_EVIDENCE_JSON\n'
AUDIT=Path('reports/diagnostic-quality-paired-live-v2/audit.jsonl')


def test_serialized_authorization_confidence_is_valid_and_unchanged():
    source={'classification':{'category':'authorization','confidence':.8,'reasoning':'Observed 403'},
            'array':[False,None,403,1.5], 'headers':{'Authorization':'Bearer do-not-leak'}}
    encoded=json.dumps(source)
    decoded=json.loads(redact(encoded))
    assert decoded['classification']==source['classification']
    assert decoded['array']==source['array']
    assert decoded['headers']['Authorization']=='[REDACTED]'
    assert 'do-not-leak' not in redact(encoded)
    assert json.loads(redact(redact(encoded)))==decoded
    assert json.loads(redact_message_content(MARKER+encoded)[len(MARKER):])==decoded


def test_nested_json_strings_and_header_tuple_secrets_stay_protected():
    value={'category':'authorization','confidence':.8,'nested':json.dumps({
        'category':'authorization','confidence':.9,'password':'hidden'}),
        'log':"('authorization', 'Bearer hidden')", 'api_key':'sk-hidden-key'}
    result=json.loads(redact(json.dumps(value)))
    assert set(result)==set(value)
    assert result['confidence']==.8
    assert json.loads(result['nested'])['confidence']==.9
    assert 'hidden' not in json.dumps(result)


@pytest.mark.parametrize('body',['not json','{"category":','[]','null'])
def test_malformed_or_nonobject_evidence_envelope_fails_closed(body):
    with pytest.raises((ValueError, json.JSONDecodeError)):
        redact_message_content(MARKER+body)


def test_recorded_authorization_failure_passes_real_adapter_mocked_sdk(tmp_path,monkeypatch):
    from tests.publication_fixtures import read
    recorded=read('authorization-classifier.json')
    assert recorded['classification']['category']=='authorization'
    f=fixtures()
    f['V2-03-04/enhanced/classifier']=copy.deepcopy(recorded)
    m=prepare();context=LiveContext(m)
    with patch('socket.socket.connect',side_effect=AssertionError('No network')):
        calls=fake_sdk(monkeypatch,context,f)
        summary=_run(m,{},tmp_path/'run',live=context)
    assert len(calls)==10
    assert 'V2-03-04/enhanced/investigator' in calls
    assert summary['actual_usage']['observed_input_tokens']==1000  # Explicit mocked usage.
    rows=[json.loads(s) for s in (tmp_path/'run/audit.jsonl').read_text().splitlines()]
    account=next(r for r in reversed(rows) if r['kind']=='actual_provider_accounting')
    assert all(type(c['input_tokens']) is int for c in account['calls'])
