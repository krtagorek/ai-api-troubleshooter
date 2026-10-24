"""Characterize current contract limits; synthetic responses are not paid outputs."""
import copy
import hashlib
import json
from pathlib import Path
from uuid import uuid4
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from agents import root_cause
from graph.state import initialize_state,validate_state,RootCauseAnalystOutput
from graph.routing import route_diagnosis,route_evidence
from graph.report_policy import legacy_tentative_report_decision
from graph.workflow import _final_report
from models.diagnosis import Classification,Diagnosis,Hypothesis,EvidenceAssessment,ProposedFix
from models.evidence import Evidence,EvidenceCollection
from models.validation import ValidationResult
from tools.counterevidence import validate_counterevidence
from tools.diagnostic_sufficiency import readiness
from tools.evidence_catalog import build_catalog

CASES=[
    ({'issuer_configured':'https://issuer.example.invalid/a','issuer_received':'https://issuer.example.invalid/b'},
     'Observed issuer discrepancy may explain rejection','Unknown issuer configuration origin'),
    ({'expected_identity':'resource-a','actual_identity':'resource-b'},
     'Observed identity discrepancy may explain rejection','Unknown upstream identity selection'),
    ({'observed_requests':110,'max_requests':100,'window_seconds':1},
     'Reported request count exceeds the explicit same-window upper bound','Unknown source of excess traffic'),
    ({'error_offset':7,'decoder':'json','content_type':'application/json'},
     'Reported decoding failure at a known offset','Unknown producer serialization behavior'),
]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr('socket.socket.connect',Mock(side_effect=AssertionError('Offline only')))
    monkeypatch.setattr('agents.common.ChatOpenAI',Mock(side_effect=AssertionError('No providers')))


def grounded(fields,logs=None):
    record={'incident_id':str(uuid4()),'service':'Generic development experiment','endpoint':'/probe',
        'http_method':'GET','status_code':401,'expected_behavior':'The configured operation succeeds.',
        'actual_behavior':'The operation was rejected.','logs':logs or [{'correlation_id':'development-scope','fields':fields}]}
    s=initialize_state(record,str(uuid4()))
    entries=list(build_catalog(s['sanitized_inputs']).values())
    s['evidence']=EvidenceCollection(items=[Evidence(evidence_id=e.evidence_id,reference=e.reference,
        source=e.source,observation=e.observation,reliability=.5) for e in entries],completeness='complete')
    s['classification']=Classification(reasoning='Synthetic classification')
    return s


def synthetic_response(s,descriptions,*,discriminate=False):
    ids=[e.evidence_id for e in s['evidence'].items]
    refs=readiness(s['sanitized_inputs'],s['evidence']).comparison_references
    by_ref={e.reference:e.evidence_id for e in s['evidence'].items}
    return RootCauseAnalystOutput(hypotheses=[Hypothesis(hypothesis_id='candidate-'+str(i),
        description=text,confidence=.6,supporting_evidence_ids=ids,
        evidence_assessments=[EvidenceAssessment(relation='supporting',evidence_ids=ids,
            explanation='Synthetic shared observations; references alone do not establish causal hierarchy.')])
        for i,text in enumerate(descriptions)],diagnosis=Diagnosis(status='tentative',summary='Qualified observation-based hypothesis',
        selected_hypothesis_id='candidate-0',confidence=.6,uncertainty=['Policy and upstream origin remain unverified.'],
        discriminating_evidence_ids=[by_ref[r] for r in refs] if discriminate else [],
        diagnostic_checks=['Review sanitized verifier decisions and rejection reasons.']))


def run_mocked(s,response,monkeypatch):
    before=copy.deepcopy(response)
    # Explicit mock boundary, not reconstructed provider wording or execution.
    monkeypatch.setattr(root_cause,'ask',lambda *a,**k: response.model_copy(deep=True))
    update=root_cause.root_cause(s)
    assert response==before
    return validate_state({**s,**update})


@pytest.mark.parametrize('fields,mechanism,origin',CASES)
def test_readiness_does_not_encode_hypothesis_relationships(fields,mechanism,origin,monkeypatch):
    s=grounded(fields);r=readiness(s['sanitized_inputs'],s['evidence'])
    assert r.qualified_analysis and not r.definitive_confirmation
    result=run_mocked(s,synthetic_response(s,[mechanism,origin]),monkeypatch)
    assert result['diagnosis'].status=='inconclusive'
    assert result['diagnosis'].selected_hypothesis_id is None and result['diagnosis'].confidence==0
    assert route_diagnosis(result)=='request_user_input'
    assert not legacy_tentative_report_decision(result).eligible


@pytest.mark.parametrize('reverse,confidence',[(False,.1),(False,.9),(True,.1),(True,.9)])
def test_confidence_order_and_labels_cannot_grant_causal_roles(reverse,confidence,monkeypatch):
    fields,mechanism,origin=CASES[0];s=grounded(fields)
    response=synthetic_response(s,[origin,mechanism] if reverse else [mechanism,origin])
    response.hypotheses[0]=response.hypotheses[0].model_copy(update={'confidence':confidence})
    assert run_mocked(s,response,monkeypatch)['diagnosis'].status=='inconclusive'


def test_genuinely_competing_authentication_explanations_remain_inconclusive(monkeypatch):
    s=grounded({'expiry_evaluated':False,'revocation_evaluated':False,'rejection':'invalid-credentials'})
    assert not readiness(s['sanitized_inputs'],s['evidence']).qualified_analysis
    result=run_mocked(s,synthetic_response(s,['Expiry may explain rejection','Revocation may explain rejection']),monkeypatch)
    assert route_evidence(result)=='request_user_input' and route_diagnosis(result)=='request_user_input'


def test_conflicting_correlated_evidence_is_not_resolved_by_hypothesis_ranking():
    s=grounded({},logs=[{'correlation_id':'same','fields':{'expected_identity':'a','actual_identity':'b'}},
                        {'correlation_id':'same','fields':{'expected_identity':'a','actual_identity':'c'}}])
    assert not readiness(s['sanitized_inputs'],s['evidence']).qualified_analysis
    assert route_evidence(s)=='request_user_input'


def test_unsupported_intermediary_is_not_detectable_from_reference_validation_alone():
    s=grounded(CASES[0][0]);response=synthetic_response(s,[CASES[0][1],'A proxy modified the upstream observation'],discriminate=True)
    validate_counterevidence(response.hypotheses,s['evidence'])
    assert 'proxy' not in json.dumps(s['sanitized_inputs'].model_dump(mode='json')).lower()
    assert response.hypotheses[0].supporting_evidence_ids==response.hypotheses[1].supporting_evidence_ids
    # Characterization of a limitation, not approval of the causal assertion.
    assert not set(Hypothesis.model_fields)&{'causal_level','parent_hypothesis_id','competes_with'}


@pytest.mark.parametrize('fields,mechanism,origin',CASES)
def test_existing_explicit_selection_and_discriminating_references_still_allow_tentative_reports(fields,mechanism,origin,monkeypatch):
    s=grounded(fields);result=run_mocked(s,synthetic_response(s,[mechanism],discriminate=True),monkeypatch)
    assert result['diagnosis'].status=='tentative' and route_diagnosis(result)=='resolution_generator'
    ids=result['diagnosis'].discriminating_evidence_ids
    result['proposed_fix']=ProposedFix(plan_kind='diagnostic',summary='Review sanitized observations',
        steps=['Review sanitized diagnostic metadata.'],verification_steps=['Obtain human-approved controlled verification.'],
        evidence_ids=ids,requires_human_approval=True)
    result['validation_results']=[ValidationResult(status='NOT_TESTED')]
    assert legacy_tentative_report_decision(result).eligible
    report=_final_report(result)['final_report']
    assert report.diagnosis.status=='tentative' and report.validation_results[-1].status=='NOT_TESTED'
    assert report.proposed_fix.requires_human_approval and not result.get('trusted_semantic')


def test_role_annotations_are_not_supported_by_the_strict_live_schema():
    s=grounded(CASES[0][0]);response=synthetic_response(s,[CASES[0][1]]).model_dump(mode='json')
    response['hypotheses'][0]['causal_level']='observed_mechanism'
    with pytest.raises(ValidationError):RootCauseAnalystOutput.model_validate(response)
    response['hypotheses'][0].pop('causal_level');response['diagnosis']['analysis_scope']='observed_mechanism'
    with pytest.raises(ValidationError):RootCauseAnalystOutput.model_validate(response)


def test_manual_run_remains_an_immutable_inconclusive_fixture():
    p=Path('tests/fixtures/manual_issuer_final_hold.json');before=p.read_bytes();fixture=json.loads(before)
    raw=fixture['state'];assert hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()==fixture['origin']['state_sha256']
    s=validate_state(raw)
    assert readiness(s['sanitized_inputs'],s['evidence']).qualified_analysis
    assert len(s['hypotheses'])==2 and not s['diagnosis'].discriminating_evidence_ids
    assert s['diagnosis'].status=='inconclusive' and s['diagnosis'].selected_hypothesis_id is None
    assert route_diagnosis(s)=='request_user_input' and not s['proposed_fix'] and not s['validation_results']
    assert not legacy_tentative_report_decision(s).eligible
    assert p.read_bytes()==before and raw==fixture['state']
