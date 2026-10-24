"""Live-only investigator shape and explicit legacy replay, all offline."""
import json
from pathlib import Path
import pytest
from agents import common,log_investigator
from graph.state import initialize_state,LiveLogInvestigatorOutput
from data.benchmark_v2.build import DEFAULT,rows
from tools.evidence_catalog import handle_catalog
from tests.test_agents import FakeLLM


def state():return initialize_state(next(r for r in rows(DEFAULT/'inference/test.jsonl') if r['incident_id']=='V2-02-01'),'offline-1312')


def output(s):
    c=handle_catalog(s['sanitized_inputs'])
    return {'evidence_contract':c['evidence_contract'],'evidence_snapshot':c['evidence_snapshot'],
            'selected_evidence_handles':['E22'],'completeness':'partial','diagnostic_gaps':[]}


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    def fail(*a,**kw):raise AssertionError('No paid calls')
    monkeypatch.setattr(common,'ChatOpenAI',fail)


def test_minimal_schema_and_trusted_resolution():
    s=state();result=log_investigator.log_investigator(s,llm=FakeLLM(output(s)))
    item=result['evidence'].items[0]
    assert item.observation=='401' and item.source=='response' and item.reference=='/status_code'
    assert item.evidence_id.startswith('ev:')
    schema=LiveLogInvestigatorOutput.model_json_schema()
    assert set(schema['properties'])==set(schema['required'])=={'evidence_contract','evidence_snapshot','selected_evidence_handles','completeness','diagnostic_gaps'}
    assert 'Evidence' not in schema.get('$defs',{}) and schema['additionalProperties'] is False


@pytest.mark.parametrize('change',[{'evidence':{'items':[]}}, {'selected_evidence_ids':[]},
 {'selected_evidence_handles':['E22','E22']},{'selected_evidence_handles':['E999']},
 {'evidence_snapshot':'stale'},{'completeness':'certain'},{'diagnostic_gaps':[{'observation':'fabricated'}]}])
def test_invalid_or_mixed_live_outputs_reject(change):
    s=state();value=output(s);value.update(change)
    with pytest.raises(common.AgentOutputError):log_investigator.log_investigator(s,llm=FakeLLM(value))


def test_empty_selection_never_claims_complete():
    s=state();value=output(s);value.update(selected_evidence_handles=[],completeness='complete')
    evidence=log_investigator.log_investigator(s,llm=FakeLLM(value))['evidence']
    assert not evidence.items and evidence.completeness=='missing' and evidence.missing


def test_recorded_1311_failure_rejected_not_silently_converted():
    r=json.loads(Path('tests/fixtures/v2_02_01_investigator_1311.json').read_text())
    s=state()
    with pytest.raises(common.AgentOutputError):log_investigator.log_investigator(s,llm=FakeLLM(r))
    with pytest.raises(common.AgentOutputError,match='Versioned handle output'):
        log_investigator.legacy_log_investigator(s,llm=FakeLLM(r))
    # A separate corrected fixture demonstrates selection; not a new model output.
    corrected=output(s);corrected['selected_evidence_handles']=[f'E{i:02d}' for i in range(1,23)]
    assert len(log_investigator.log_investigator(s,llm=FakeLLM(corrected))['evidence'].items)==22
