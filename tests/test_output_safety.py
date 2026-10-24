"""Actual recorded wording and generic paraphrases; no providers or label tuning."""
import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from evaluation.dataset import digest
from graph.state import validate_state,serialize_state,LiveLogInvestigatorOutput
from graph.workflow import _request_user
from models.diagnosis import FinalReport
from tools.output_safety import (safe_text,unsafe_request,protect_generated,protect_report,
                                 OutputSafetyError,SAFE_REQUEST)

ROOT=Path('reports/diagnostic-quality-enhanced-live-run')


def recorded():
    return json.loads((Path(__file__).parent/'fixtures/publication/output-safety-state.json').read_text())


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr('socket.socket.connect',Mock(side_effect=AssertionError('Offline only')))
    monkeypatch.setattr('agents.common.ChatOpenAI',Mock(side_effect=AssertionError('No providers')))


@pytest.mark.parametrize('text',[
 "Evidence of the client's authentication credentials and their validation results.",
 'Authentication credentials are needed for this investigation.',
 'A sample of the complete JWT would help.',
 'Please paste the bearer string into the issue.',
 'Send raw access tokens without passwords.',
 'Attach the session cookie value.',
 'Include the API_key in diagnostic evidence.',
 'Upload refresh-token details.',
 'We should see the password to confirm.',
 'Obtain the full JSON web token.',
 'Validation material: client secret.',
 'Please forward your reusable credentials.',
 'Provide sanitized access tokens.',
 'Do not share tokens; send passwords instead.',
 'Never request a token but attach the API key.',
 'Never request tokens\nProvide refresh tokens',
 'Please supply the API%20key.',
 'Send the pa\u200bssword.',
 'Provide ｐａｓｓｗｏｒｄｓ.',
 'A copy of your authentication header would be useful.',
 'We await your login secret.',
 'No raw tokens are available please provide passwords.',
 'Never request tokens unless a password is available.',
 json.dumps({'nested':{'observation':'Provide passwords'}}),
 '{"text":"Provide \\u0063redentials"}',
])
def test_unsafe_direct_indirect_encoded_and_nested_variants(text):
    assert unsafe_request(text)
    assert safe_text(text)==SAFE_REQUEST
    assert not unsafe_request(SAFE_REQUEST)


@pytest.mark.parametrize('text',[
 'Provide sanitized issuer, audience and expiry claims.',
 'Obtain redacted verifier decisions and rejection reasons.',
 'Provide sanitized token metadata; never send access tokens.',
 'Never collect passwords or reusable credentials.',
 'No raw tokens are needed.',
 'Access to sanitized authentication token claims (issuer, audience, expiry) and verifier decisions without sensitive credentials.',
 'The access token expired.',
])
def test_allowed_metadata_prohibitions_and_non_request_facts(text):
    assert not unsafe_request(text)
    assert safe_text(text)==text


@pytest.mark.parametrize('field',[
 'description','required_to_resolve','diagnostic_checks','steps','prerequisites',
 'verification_steps','summary','risks','limitations','pending_user_questions',
 'reasoning','assumptions','explanation','hypothesis_prediction','issues','detail','instructions',
])
def test_all_generated_narrative_fields_share_boundary(field):
    raw={field:[{'nested':"Evidence of the client's authentication credentials and their validation results."}],
         'confidence':.5,'attempt':3,'approved':True}
    clean=protect_generated(raw)
    assert clean[field][0]['nested']==SAFE_REQUEST
    assert (clean['confidence'],clean['attempt'],clean['approved'])==(.5,3,True)
    assert json.loads(json.dumps(clean))==clean
    assert raw[field][0]['nested']!=SAFE_REQUEST


def test_recorded_replay_followups_checkpoints_and_reports_are_safe():
    original=recorded();snapshot=copy.deepcopy(original)
    clean=validate_state(original)
    assert original==snapshot
    assert all(g.required_to_resolve==SAFE_REQUEST or not unsafe_request(g.required_to_resolve,request_context=True)
               for g in clean['evidence'].missing)
    follow=_request_user(clean)
    assert all(not unsafe_request(q,request_context=True) for q in follow['pending_user_questions'])
    assert json.loads(serialize_state(clean))['evidence']['items']==original['evidence']['items']
    report=FinalReport(incident_id=clean['incident_id'],summary='Diagnostic plan awaiting independent verification',
        diagnosis=clean['diagnosis'],proposed_fix=clean['proposed_fix'],evidence=clean['evidence'],
        validation_results=clean['validation_results'],limitations=['Provide passwords'])
    view=protect_report(report)
    assert view.limitations==[SAFE_REQUEST]
    assert view.proposed_fix.requires_human_approval
    assert view.validation_results[0].status=='NOT_TESTED'
    assert not unsafe_request(view.model_dump_json())


def test_invalid_schema_and_snapshot_never_repaired_by_safety():
    s=recorded();s['evidence']['items'][0]['observation']='fabricated'
    with pytest.raises(ValueError,match='Catalog evidence differs'):validate_state(s)
    s=recorded();s['evidence']['items'][0]['evidence_id']='ev:other-snapshot:invented'
    with pytest.raises(ValueError):validate_state(s)
    raw={'evidence_contract':'evidence-handles-v1','evidence_snapshot':'bad',
         'selected_evidence_handles':[], 'completeness':'missing','diagnostic_gaps':[],
         'legacy_evidence':{'description':'Provide passwords'}}
    with pytest.raises(ValidationError):LiveLogInvestigatorOutput.model_validate(raw)


def test_unsafe_literal_evidence_is_never_silently_rewritten_for_publication():
    clean=validate_state(recorded())
    raw={'incident_id':clean['incident_id'],'summary':'Read source with caution',
         'diagnosis':clean['diagnosis'].model_dump(mode='json'),
         'evidence':{'items':[{'evidence_id':'legacy','source':'log',
                    'observation':'Provide passwords','reliability':.5}], 'completeness':'partial'}}
    before=copy.deepcopy(raw)
    with pytest.raises(OutputSafetyError):protect_report(raw)
    assert raw==before


def test_provider_audit_originals_and_hash_chain_remain_intact():
    events=[json.loads(s) for s in (ROOT/'audit.jsonl').read_text().splitlines()]
    previous=None
    for n,e in enumerate(events):
        assert e['sequence']==n and e['previous']==previous
        assert e['sha256']==digest({k:v for k,v in e.items() if k!='sha256'})
        previous=e['sha256']
    a=next(e for e in reversed(events) if e['kind']=='actual_provider_accounting')
    assert a['usage']['observed_input_tokens']==15334
    assert a['usage']['observed_output_tokens']==1649
    raw=next(c for c in a['calls'] if c['key']=='V2-01-01/enhanced/investigator')['provider_structured_output']
    assert any('credentials' in g['required_to_resolve'] for g in raw['diagnostic_gaps'])
    assert all(type(c['input_tokens']) is int for c in a['calls'])


def test_exact_confirmed_human_reviews_and_negative_rating_validation(tmp_path):
    from evaluation.enhanced_review import validate_review
    result=validate_review()
    assert result['complete'] and result['reviewers']==['rxk163']
    original=json.loads((ROOT/'semantic-review-worksheet.json').read_text())
    for mutation in ('rating','identity','timestamp','hash'):
        w=copy.deepcopy(original);r=w['reviews'][0]
        if mutation=='rating':r['dimension_ratings']['validation_truthfulness']=2
        if mutation=='identity':r['reviewer_id']=''
        if mutation=='timestamp':r['timestamp']='2026-01-01T00:00:00'
        if mutation=='hash':r['output_sha256']='wrong'
        p=tmp_path/(mutation+'.json');p.write_text(json.dumps(w))
        with pytest.raises(ValueError):validate_review(p)


def test_report_publication_forces_human_approval_and_strict_shapes():
    s=validate_state(recorded())
    raw={'incident_id':s['incident_id'],'summary':'Read-only plan',
         'diagnosis':s['diagnosis'].model_dump(mode='json'),'evidence':s['evidence'].model_dump(mode='json'),
         'proposed_fix':s['proposed_fix'].model_dump(mode='json')}
    raw['proposed_fix']['requires_human_approval']=False
    assert protect_report(raw).proposed_fix.requires_human_approval
    raw['extra']={'instructions':'Send passwords'}
    with pytest.raises(ValidationError):protect_report(raw)


def test_incident_publication_rejects_requests_but_preserves_failure_facts():
    from tools.output_safety import protect_incident_view
    s=recorded()['sanitized_inputs']
    s['response_body']={'error':'Authentication token missing'}
    assert protect_incident_view(s).model_dump(mode='json')==s
    s['actual_behavior']='Evidence of the client authentication credentials is required'
    with pytest.raises(OutputSafetyError):protect_incident_view(s)


def test_progress_replay_is_controlled_metadata_not_arbitrary_payload():
    from tools.output_safety import progress_view
    raw={'status':'running','attempt':1,'node':'classifier','message':'Provide passwords','provider_output':'private'}
    assert progress_view(raw,status='succeeded',event='node_completed',attempt=1)=={
        'status':'running','attempt':1,'node':'classifier'}
    assert raw['message']=='Provide passwords'
    with pytest.raises(OutputSafetyError):progress_view({'node':'Provide passwords'},status='running',event='node_completed',attempt=1)
