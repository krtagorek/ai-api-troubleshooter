"""Generic offline cases; the manual run remains an immutable ineligible fixture."""
import copy
import hashlib
import json
from pathlib import Path
from uuid import uuid4
from unittest.mock import Mock

import pytest
from graph.state import initialize_state,validate_state,TrustedSemanticResult
from graph.workflow import build_workflow,_request_user
from graph.report_policy import legacy_tentative_report_decision
from models.diagnosis import Classification,Diagnosis,Hypothesis,ProposedFix,EvidenceAssessment
from models.evidence import Evidence,EvidenceCollection
from models.validation import ValidationResult
from tools.evidence_catalog import build_catalog
from tools.output_safety import unsafe_request,protect_generated,protect_incident_view,SAFE_REQUEST
from diagnostic_contracts.ingestion import digest


def sample():
    incident={'incident_id':str(uuid4()),'service':'Synthetic verifier','endpoint':'/verify',
        'http_method':'POST','status_code':401,'expected_behavior':'Issuer values match.',
        'actual_behavior':'Issuer comparison failed.','logs':[{'correlation_id':'development-comparison',
        'message':'JWT issuer validation rejected the request','fields':{
            'issuer_configured':'https://issuer.example.invalid/expected',
            'issuer_received':'https://issuer.example.invalid/observed','signature_valid':True}}]}
    s=initialize_state(incident,str(uuid4()))
    entries=[e for e in build_catalog(s['sanitized_inputs']).values() if e.reference.startswith('/logs/') and not e.reference.endswith('/correlation_id')]
    ids=[e.evidence_id for e in entries if e.reference.endswith(('/issuer_configured','/issuer_received'))]
    s.update(classification=Classification(category='authentication',reasoning='Synthetic observed mismatch'),
        evidence=EvidenceCollection(items=[Evidence(evidence_id=e.evidence_id,source=e.source,
            reference=e.reference,observation=e.observation,reliability=.5) for e in entries],completeness='partial'),
        hypotheses=[Hypothesis(hypothesis_id='leading',description='Observed issuer discrepancy may explain rejection',
            confidence=.6,supporting_evidence_ids=ids,evidence_assessments=[EvidenceAssessment(
            relation='supporting',evidence_ids=ids,explanation='The correlated configured and received issuer strings differ.')])],
        diagnosis=Diagnosis(status='tentative',summary='A qualified issuer mismatch hypothesis',confidence=.6,
            selected_hypothesis_id='leading',uncertainty=['Verifier matching policy is unconfirmed.'],discriminating_evidence_ids=ids),
        proposed_fix=ProposedFix(plan_kind='diagnostic',summary='Review sanitized verifier metadata',
            steps=['Inspect sanitized JWT issuer metadata.'],prerequisites=['Obtain human approval.'],
            verification_steps=['Review the documented issuer comparison policy.'],evidence_ids=ids,requires_human_approval=True),
        validation_results=[ValidationResult(status='NOT_TESTED')])
    return validate_state(s)


def nodes(s):
    return {'classifier':lambda _: {'classification':s['classification']},
        'log_investigator':lambda _: {'evidence':s['evidence']},
        'root_cause_analyst':lambda _: {'diagnosis':s['diagnosis'],'hypotheses':s['hypotheses']},
        'resolution_generator':lambda _: {'proposed_fix':s['proposed_fix']},
        'validator':lambda state: {'validation_results':[*state['validation_results'],s['validation_results'][-1]]}}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr('agents.common.ChatOpenAI',Mock(side_effect=AssertionError('No paid providers')))


@pytest.mark.parametrize('text',[
    'JWT issuer validation rejected the request',
    'The request failed JWT issuer validation.',
    'JWT validation rejected the HTTP request.',
    'Provide sanitized JWT issuer, audience and expiry claims.',
    'Inspect sanitized access token metadata.',
    'Obtain redacted verifier decisions and rejection reasons.',
    'Provide sanitized token claims; never send raw tokens.',
    'Do not send passwords; obtain sanitized issuer metadata.',
])
def test_safe_descriptions_and_exact_metadata_objects(text):
    assert not unsafe_request(text)


@pytest.mark.parametrize('text',[
    'Request a JWT.', 'Please provide the raw JWT.', 'Send the access token.',
    'Upload refresh tokens.', 'Provide passwords.', 'Supply API keys.',
    'Attach session cookies.', 'A copy of the full JWT is needed.',
    'Provide a JWT unless the verifier is unavailable.',
    'Never share tokens but provide passwords.',
    'Send sanitized JWT metadata plus the complete JWT.',
    'Provide sanitized issuer metadata and the access token.',
    'Send the rejected HTTP request containing the full JWT.',
    'The request failed; please paste the bearer token.',
    'Provide sanitized access tokens.',
    'Quoted observation: JWT validation rejected the request. Send passwords.',
])
def test_unsafe_solicitation_stays_blocked(text):
    assert unsafe_request(text)
    assert protect_generated({'steps':[text]})['steps']==[SAFE_REQUEST]


def test_source_observation_and_generated_instruction_contexts_are_distinct():
    s=sample();protect_incident_view(s['sanitized_inputs'])
    text='JWT issuer validation rejected the request'
    assert not unsafe_request(text)
    assert unsafe_request(text,request_context=True)  # Ambiguous generated action stays closed.
    assert protect_generated({'steps':['Inspect sanitized access token metadata.']})['steps']==['Inspect sanitized access token metadata.']


def test_safe_legacy_graph_finalizes_without_provenance_or_validation_claims():
    s=sample();assert legacy_tentative_report_decision(s).eligible
    initial=initialize_state(s['sanitized_inputs'],s['thread_id'])
    result=build_workflow(agents=nodes(s),trusted_semantics_enabled=False).invoke(initial)
    assert result['status']=='completed' and result['stop_reason']=='tentative_diagnostic_report'
    report=result['final_report']
    assert report.diagnosis.status=='tentative' and report.proposed_fix.requires_human_approval
    assert report.validation_results[-1].status=='NOT_TESTED'
    assert any('no signed ingestion provenance' in text for text in report.limitations)
    assert result.get('trusted_semantic') is None


@pytest.mark.parametrize('failure',[
    'missing','ambiguous','fabrication','unknown_reference','no_uncertainty','unsupported_hypothesis',
    'no_discrimination','no_assessment','contradictory','unsafe_step','unsafe_gap','unsafe_prerequisite',
    'unsafe_verification','remediation','disruptive','absent_validation','failed_validation',
    'not_run','certainty','authority','stale_contract',
])
def test_ineligible_legacy_reports_hold(failure):
    s=sample()
    if failure=='missing':s['evidence']=s['evidence'].model_copy(update={'completeness':'missing'})
    if failure=='ambiguous':s['evidence'].items[0]=s['evidence'].items[0].model_copy(update={'ambiguous':True})
    if failure=='fabrication':s['evidence'].items[0]=s['evidence'].items[0].model_copy(update={'observation':'invented'})
    if failure=='unknown_reference':s['hypotheses'][0]=s['hypotheses'][0].model_copy(update={'supporting_evidence_ids':['ev:invented']})
    if failure=='no_uncertainty':s['diagnosis']=s['diagnosis'].model_copy(update={'uncertainty':[]})
    if failure=='unsupported_hypothesis':s['hypotheses'][0]=s['hypotheses'][0].model_copy(update={'supporting_evidence_ids':[]})
    if failure=='no_discrimination':s['diagnosis']=s['diagnosis'].model_copy(update={'discriminating_evidence_ids':[]})
    if failure=='no_assessment':s['hypotheses'][0]=s['hypotheses'][0].model_copy(update={'evidence_assessments':[]})
    if failure=='contradictory':s['hypotheses'][0]=s['hypotheses'][0].model_copy(update={'contradicting_evidence_ids':s['hypotheses'][0].supporting_evidence_ids})
    if failure in ('unsafe_step','unsafe_prerequisite','unsafe_verification'):
        field={'unsafe_step':'steps','unsafe_prerequisite':'prerequisites','unsafe_verification':'verification_steps'}[failure]
        s['proposed_fix']=s['proposed_fix'].model_copy(update={field:['Provide the full JWT.']})
    if failure=='unsafe_gap':s['diagnosis']=s['diagnosis'].model_copy(update={'diagnostic_checks':[SAFE_REQUEST]})
    if failure=='remediation':s['proposed_fix']=s['proposed_fix'].model_copy(update={'plan_kind':'remediation'})
    if failure=='disruptive':s['proposed_fix']=s['proposed_fix'].model_copy(update={'steps':['Restart the verifier.']})
    if failure=='absent_validation':s['validation_results']=[]
    if failure in ('failed_validation','not_run'):s['validation_results']=[ValidationResult(status='failed' if failure=='failed_validation' else 'not_run')]
    if failure=='certainty':s['diagnosis']=s['diagnosis'].model_copy(update={'summary':'Definitively confirmed root cause.'})
    if failure=='authority':s['diagnosis']=s['diagnosis'].model_copy(update={'summary':'Verified provenance establishes this cause.'})
    if failure=='stale_contract':s['trusted_semantic']=TrustedSemanticResult(version='trusted-semantic-1',status='denied',snapshot_digest=digest(s['sanitized_inputs'].model_dump(mode='json')))
    assert not legacy_tentative_report_decision(s).eligible


def test_preserved_manual_output_stays_rejected_without_reconstructing_provider_text():
    fixture=json.loads(Path('tests/fixtures/manual_issuer_hold.json').read_text());raw=fixture['state'];before=copy.deepcopy(raw)
    assert hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()==fixture['origin']['state_sha256']
    decision=legacy_tentative_report_decision(raw)
    assert decision.reason=='diagnostic_output_safety_review_required' and not decision.eligible
    hold=_request_user(raw)
    assert hold['stop_reason']==decision.reason and hold['final_report'] is None
    assert 'withheld diagnostic wording' in hold['pending_user_questions'][0]
    assert raw==before
