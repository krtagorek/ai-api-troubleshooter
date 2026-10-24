"""Generic development fixtures; real outputs are immutable known regressions."""
import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from agents.log_investigator import log_investigator
from graph.routing import evidence_sufficient
from graph.state import initialize_state,validate_state
from models.evidence import Evidence,EvidenceCollection
from tests.test_agents import FakeLLM
from tests.test_diagnostic_quality import state_for
from tools.diagnostic_sufficiency import readiness
from tools.evidence_catalog import handle_catalog


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    monkeypatch.setattr('agents.common.ChatOpenAI',Mock(side_effect=AssertionError('Offline only')))
    monkeypatch.setattr('evaluation.provider.ChatOpenAI',Mock(side_effect=AssertionError('Offline only')))


@pytest.mark.parametrize('fields',[
    {'received_scale':3,'allowed_scale':2},
    {'received_scale':3.0,'allowed_scale':2},
    {'actual_identity':'abc','expected_identity':'def'},
    {'measured_latency_ms':300,'configured_max_latency_ms':200},
    {'measured_size_bytes':3,'configured_min_size_bytes':4},
    {'actual_count':4,'expected_count':2},
])
def test_qualified_comparisons(fields):
    s=state_for(fields)
    r=readiness(s['sanitized_inputs'],s['evidence'])
    assert r.qualified_analysis and not r.definitive_confirmation
    assert len(r.comparison_references)==2


@pytest.mark.parametrize('fields',[
    {'received_scale':2,'allowed_scale':2},
    {'received_scale':1,'allowed_scale':2},
    {'received_scale':3},
    {'measured_latency_ms':100,'configured_max_latency_ms':200},
    {'measured_size_bytes':5,'configured_min_size_bytes':4},
    {'actual_identity':'abc','expected_identity':'abc'},
    {'measured_latency_ms':300,'configured_latency_ms':200},
    {'observed_latency_ms':300,'max_age_ms':200},
    {'measured_latency_ms':300,'configured_max_latency_seconds':.2},
    {'measured_latency_ticks':300,'configured_max_latency_ticks':200},
    {'received_scale':'3','allowed_scale':2},
    {'received_scale':3,'allowed_scale':None},
    {'received_scale':True,'allowed_scale':False},
    {'actual_latency_ms':'300','expected_latency_ms':'200'},
    {'actual_latency_furlongs':300,'expected_latency_furlongs':200},
    {'measured_latency_ms':300,'configured_upper_lower_latency_ms':200},
    {'partition_utilization':1.0},
])
def test_ambiguous_or_nonviolating_comparisons_stay_held(fields):
    assert not evidence_sufficient(state_for(fields,'complete'))


@pytest.mark.parametrize('logs',[
    [{'correlation_id':'a','fields':{'received_scale':3}}, {'correlation_id':'b','fields':{'allowed_scale':2}}],
    [{'correlation_id':'a','fields':{'received_scale':3}}, {'correlation_id':'a','fields':{'allowed_scale':2}}],
    [{'fields':{'one':{'received_scale':3},'two':{'allowed_scale':2}}}],
    [{'correlation_id':'a','fields':{'received_scale':3,'allowed_scale':2}}, {'correlation_id':'a','fields':{'received_scale':1,'allowed_scale':2}}],
])
def test_no_cross_scope_joins_or_conflict_suppression(logs):
    assert not evidence_sufficient(state_for({},logs=logs))


def investigate(state):
    catalog=handle_catalog(state['sanitized_inputs'])
    handle=next(e['handle'] for e in catalog['evidence_catalog'] if e['reference']=='/status_code')
    original={'evidence_contract':catalog['evidence_contract'],'evidence_snapshot':catalog['evidence_snapshot'],
              'selected_evidence_handles':[handle],'completeness':'complete','diagnostic_gaps':[]}
    raw=copy.deepcopy(original)
    update=log_investigator(state,llm=FakeLLM(raw))
    assert raw==original
    return validate_state({**state,**update})


@pytest.mark.parametrize('fields,ready',[
    ({'received_scale':3,'allowed_scale':2},True),
    ({'partition_utilization':1.0},False),
])
def test_sampling_warning_is_grounded_quality_not_causal_proof_or_veto(fields,ready):
    s=state_for({},logs=[{'correlation_id':'test','fields':fields,'message':'Diagnostic sampling incomplete'}])
    s=investigate(s)
    warnings=[e for e in s['evidence'].items if e.reference=='/logs/0/message']
    assert len(warnings)==1 and warnings[0].observation=='Diagnostic sampling incomplete' and warnings[0].source=='log'
    assert s['evidence'].completeness=='partial' and s['evidence'].missing
    assert evidence_sufficient(s) is ready
    assert '/logs/0/message' not in readiness(s['sanitized_inputs'],s['evidence']).comparison_references


def test_warning_alone_is_not_diagnostic_sufficiency():
    s=state_for({},logs=[{'message':'Diagnostic sampling incomplete'}])
    s=investigate(s)
    assert not evidence_sufficient(s) and s['evidence'].completeness=='partial'


def test_original_paid_outputs_replayed_without_modification():
    from evaluation.dataset import digest
    from evaluation.quality_pilot import sha
    p=Path('reports/rc-independent-v2-live-run')
    w=json.loads((p/'semantic-review-worksheet.json').read_text())
    assert sha(p/'outputs.json')==w['output_file_sha256']
    audit=[json.loads(x) for x in (p/'audit.jsonl').read_text().splitlines()]
    calls=next(e['calls'] for e in reversed(audit) if e['kind']=='actual_provider_accounting')
    for result in json.loads((p/'outputs.json').read_text()):
        ident=result['state']['incident_id']
        assert digest(result)==next(c['output_sha256'] for c in w['cases'] if c['incident_id']==ident)
        state=initialize_state(result['state']['sanitized_inputs'],'known-regression',max_retries=0)
        raw=next(c['provider_structured_output'] for c in calls if c['key']==ident+'/enhanced/investigator')
        state=validate_state({**state,**log_investigator(state,llm=FakeLLM(copy.deepcopy(raw)))})
        assert evidence_sufficient(state) is (ident=='V2-06-02')
        assert not readiness(state['sanitized_inputs'],state['evidence']).definitive_confirmation
        if ident=='V2-08-04':
            assert any(e.reference=='/logs/0/message' for e in state['evidence'].items)
            assert state['evidence'].completeness=='partial'


@pytest.mark.parametrize('fields,ready',[
    ({'cache_age_seconds':90,'max_age_seconds':30},False),
    ({'cache_age_seconds':90,'token_age_seconds':10,'max_age_seconds':30},False),
    ({'measured_cache_age_seconds':90,'configured_max_cache_age_seconds':30},True),
    ({'measured_cache_age_seconds':90,'configured_max_token_age_seconds':30},False),
    ({'received_scale':False,'allowed_scale':-1},False),
    ({'received_scale':3,'allowed_scale':'2'},False),
    ({'measured_latency_ms':300,'configured_max_latency_ms':200,'configured_min_latency_ms':400},False),
])
def test_namespaces_types_and_conflicting_bounds(fields,ready):
    assert evidence_sufficient(state_for(fields,'complete')) is ready
