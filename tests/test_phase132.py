"""No-network scalar contracts and development-only reasoning refinements."""
import copy
import json
from pathlib import Path

import pytest

from agents import common, log_investigator, root_cause, resolution
from data.generate_incidents import generate
from evaluation.dataset import read_dataset
from evaluation.metrics import aggregate, score
from graph.state import initialize_state, serialize_state
from models.evidence import canonical_scalar, observation_matches, EvidenceCollection, Evidence
from models.diagnosis import Diagnosis, Hypothesis
from tests.test_agents import FakeLLM, ROOT, FIX


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError('No paid calls authorized')
    monkeypatch.setattr(common, 'ChatOpenAI', forbidden)


@pytest.fixture
def development_state():
    s = initialize_state(generate()['development'][0], 'phase132-offline')
    s['evidence'] = EvidenceCollection(completeness='complete', items=[Evidence(
        evidence_id='e1', source='response', observation=str(s['sanitized_inputs'].status_code),
        reference='/status_code', reliability=.5)])
    s['hypotheses'] = [Hypothesis(hypothesis_id='h1', description='Possible application error',
                                confidence=.4, supporting_evidence_ids=['e1'])]
    s['diagnosis'] = Diagnosis(status='tentative', summary='Uncertain origin',
                              confidence=.4, selected_hypothesis_id='h1')
    return s


@pytest.mark.parametrize('value,text,bad', [
    ('literal message', 'literal message', 'invented'), (403, '403', '40'),
    (-2, '-2', '2'), (1.0, '1.0', '1'), (1.25, '1.25', '1.2'),
    (True, 'true', 'True'), (False, 'false', 'false '), (None, 'null', 'None'),
])
def test_all_scalar_types_use_shared_exact_contract(development_state, value, text, bad):
    assert canonical_scalar(value) == text
    assert observation_matches(text, value)
    assert not observation_matches(bad, value)
    state = development_state
    state['sanitized_inputs'].response_body = {'value': value}
    output = {'evidence': {'items': [{'evidence_id':'e1', 'source':'user',
        'reference':'/response_body/value', 'observation':text, 'reliability':.5}]}}
    result = log_investigator.legacy_log_investigator(state, llm=FakeLLM(output))
    assert result['evidence'].items[0].source == 'response'
    output['evidence']['items'][0]['observation'] = bad
    with pytest.raises(common.AgentOutputError):
        log_investigator.legacy_log_investigator(state, llm=FakeLLM(output))


@pytest.mark.parametrize('value', [{}, [], {'code':403}, float('inf'), float('nan')])
def test_non_scalar_and_nonfinite_values_reject(value):
    with pytest.raises(ValueError): canonical_scalar(value)


def test_string_substrings_only_and_float_type_distinction():
    assert observation_matches('message', 'literal message')
    assert observation_matches('40', '403')
    assert not observation_matches('40', 403)
    assert not observation_matches('403.0', 403)
    assert not observation_matches('1', True)
    assert not observation_matches('', 'literal')


def test_inc0025_raw_replay_fails_and_corrected_copy_passes():
    path = Path(__file__).parent/'fixtures/inc0025_investigator.json'
    raw = path.read_bytes()
    output = json.loads(raw)
    incident = next(r for r in read_dataset()[0]['test'] if r['incident_id']=='INC-0025')
    state = initialize_state(incident, 'recorded-output-replay-only')
    before = serialize_state(state)
    with pytest.raises(common.AgentOutputError, match='not present'):
        log_investigator.legacy_log_investigator(state, llm=FakeLLM(output))
    corrected = copy.deepcopy(output)
    item = next(i for i in corrected['evidence']['items'] if i['reference']=='/logs/1/fields/status_code')
    assert item['observation']=='status_code 403'
    item['observation']='403'
    assert len(log_investigator.legacy_log_investigator(state,llm=FakeLLM(corrected))['evidence'].items)==6
    assert path.read_bytes()==raw and serialize_state(state)==before


def test_competing_causes_require_discriminating_evidence(development_state):
    output = copy.deepcopy(ROOT)
    output['hypotheses'].append({**output['hypotheses'][0], 'hypothesis_id':'h2',
                                'description':'Possible upstream intermediary failure'})
    result = root_cause.root_cause(development_state, llm=FakeLLM(output))
    assert result['diagnosis'].status=='inconclusive'
    assert result['diagnosis'].selected_hypothesis_id is None
    assert result['diagnosis'].confidence==0
    output['diagnosis']['discriminating_evidence_ids']=['fabricated-tool-result']
    with pytest.raises(common.AgentOutputError):
        root_cause.root_cause(development_state, llm=FakeLLM(output))


def test_unsupported_causal_references_reject(development_state):
    output = copy.deepcopy(ROOT)
    output['hypotheses'][0]['contradicting_evidence_ids']=['fabricated']
    with pytest.raises(common.AgentOutputError):
        root_cause.root_cause(development_state,llm=FakeLLM(output))


@pytest.mark.parametrize('kind,step', [('remediation','Update routing configuration'),
                                    ('diagnostic','Restart service')])
def test_premature_or_mislabeled_disruptive_fix_rejects(development_state,kind,step):
    output=copy.deepcopy(FIX)
    output['proposed_fix'].update(plan_kind=kind, steps=[step])
    with pytest.raises(common.AgentOutputError):
        resolution.resolution(development_state,llm=FakeLLM(output))


def test_readonly_diagnostics_require_human_approval(development_state):
    fix=resolution.resolution(development_state,llm=FakeLLM(FIX))['proposed_fix']
    assert fix.plan_kind=='diagnostic' and fix.requires_human_approval


def test_failed_cases_are_not_incorrect_diagnoses(development_state):
    label=generate()['development'][0].model_dump()
    metrics=score(development_state,label)
    cases=[{'status':'scored','metrics':metrics}, {'status':'failed','metrics':None}]
    result=aggregate(cases, planned=[{}, {}, {}])
    assert result['classification_accuracy']['denominator']==1
    assert result['scored_case_coverage']['value']==1/3
    assert result['workflow_failure_rate']['value']==.5
    assert result['unscored_case_rate']['value']==2/3


def test_metrics_reject_numeric_substrings_like_agent(development_state):
    development_state['evidence'].items[0].observation=str(development_state['sanitized_inputs'].status_code)[:1]
    assert score(development_state,generate()['development'][0].model_dump())['grounded_items']==0
