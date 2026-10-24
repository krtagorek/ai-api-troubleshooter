"""No-network handle contracts, snapshot boundaries and actual failure replay."""
import copy
import json
from pathlib import Path
import pytest
from tools.evidence_catalog import handle_catalog,resolve_handles,HANDLE_CONTRACT,resolve_evidence
from data.benchmark_v2.build import DEFAULT,rows
from graph.state import initialize_state,serialize_state
from agents import common,error_classifier,log_investigator,root_cause
from tests.test_agents import FakeLLM


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('No paid calls')
    monkeypatch.setattr(common,'ChatOpenAI',forbidden)


def state():
    return initialize_state(next(r for r in rows(DEFAULT/'inference/test.jsonl') if r['incident_id']=='V2-02-01'),'offline-handles')


def response(s,handles):
    c=handle_catalog(s['sanitized_inputs'])
    return {'evidence_contract':c['evidence_contract'],'evidence_snapshot':c['evidence_snapshot'],
            'classification':{'category':'authentication','reasoning':'Observed response','evidence_references':handles}}


def test_deterministic_one_to_one_handles_and_model_catalog_is_safe():
    s=state();c=handle_catalog(s['sanitized_inputs'])
    assert c==handle_catalog(s['sanitized_inputs'].model_dump(mode='json'))
    assert c['evidence_catalog'][0]['handle']=='E01'
    assert len({e['handle'] for e in c['evidence_catalog']})==len(c['evidence_catalog'])
    text=json.dumps(c)
    assert 'ev:' not in text and 'root_cause' not in text and 'expected_resolution' not in text
    s['sanitized_inputs'].response_body={'password':'raw-secret','root_cause':'hidden-label','safe':True}
    assert 'raw-secret' not in json.dumps(handle_catalog(s['sanitized_inputs']))
    assert 'hidden-label' not in json.dumps(handle_catalog(s['sanitized_inputs']))


@pytest.mark.parametrize('handles',[['E00'],['E9999'],['e01'],['E1'],[' E01'],['E01','E01'],['/status_code']])
def test_unknown_malformed_duplicate_handles_reject(handles):
    s=state()
    with pytest.raises(common.AgentOutputError):error_classifier.error_classifier(s,llm=FakeLLM(response(s,handles)))


@pytest.mark.parametrize('field,value',[('evidence_snapshot',None),('evidence_contract',None),('evidence_contract','v0'),('evidence_snapshot','wrong')])
def test_missing_or_wrong_context_rejects(field,value):
    s=state();r=response(s,['E01']);r[field]=value
    with pytest.raises(common.AgentOutputError):error_classifier.error_classifier(s,llm=FakeLLM(r))


def test_cross_incident_and_changed_snapshot_replay_reject():
    s=state();r=response(s,['E01'])
    for change in ({'incident_id':'other'}, {'status_code':500}):
        other=s['sanitized_inputs'].model_copy(update=change)
        with pytest.raises(ValueError):resolve_handles(other,['E01'],r['evidence_contract'],r['evidence_snapshot'])


def test_recorded_malformed_id_still_fails_equivalent_handle_selection_passes():
    s=state();path=Path(__file__).parent/'fixtures/v2_02_01_malformed_id_classifier.json';original=path.read_bytes()
    raw=json.loads(original)
    with pytest.raises(common.AgentOutputError):error_classifier.error_classifier(s,llm=FakeLLM(raw))
    # Build a separate corrected output from the actual three valid references
    # plus the actual_behavior entry identified by the duplicated ID's leaf hash;
    # never repair or replace the recorded response itself.
    pointers=['/status_code','/response_body/status','/response_body/result','/actual_behavior']
    c=handle_catalog(s['sanitized_inputs']);by_path={e['reference']:e['handle'] for e in c['evidence_catalog']}
    selected=[by_path[p] for p in pointers]
    r=response(s,selected);llm=FakeLLM(r)
    update=error_classifier.error_classifier(s,llm=llm)
    assert update['classification'].evidence_references==pointers
    assert path.read_bytes()==original and r['classification']['evidence_references']==selected
    assert 'ev:' not in str(llm.calls)


def test_investigator_to_root_cause_preserves_internal_reference_consistency():
    s=state();c=handle_catalog(s['sanitized_inputs']);handle=next(e['handle'] for e in c['evidence_catalog'] if e['reference']=='/status_code')
    out={'evidence_contract':HANDLE_CONTRACT,'evidence_snapshot':c['evidence_snapshot'],
         'selected_evidence_handles':[handle],'evidence':{'completeness':'partial'}}
    update=log_investigator.legacy_log_investigator(s,llm=FakeLLM(out));s.update(update)
    internal=s['evidence'].items[0].evidence_id
    assert internal.startswith('ev:') and s['evidence'].items[0].observation=='401'
    analyst={'evidence_contract':HANDLE_CONTRACT,'evidence_snapshot':c['evidence_snapshot'],
             'hypotheses':[{'hypothesis_id':'h1','description':'Possible identity rejection','confidence':.4,'supporting_evidence_ids':[handle]}],
             'diagnosis':{'status':'tentative','summary':'Need issuer evidence','selected_hypothesis_id':'h1','confidence':.4}}
    llm=FakeLLM(analyst);result=root_cause.root_cause(s,llm=llm)
    assert result['hypotheses'][0].supporting_evidence_ids==[internal]
    assert 'ev:' not in str(llm.calls)
    analyst['evidence_snapshot']='wrong'
    with pytest.raises(common.AgentOutputError):root_cause.root_cause(s,llm=FakeLLM(analyst))


def test_model_cannot_redefine_mapping_or_mix_contracts():
    s=state();r=response(s,['E01']);r['handle_mapping']={'E01':'/root_cause'}
    with pytest.raises(common.AgentOutputError):error_classifier.error_classifier(s,llm=FakeLLM(r))
    r=response(s,[])
    with pytest.raises(common.AgentOutputError):error_classifier.error_classifier(s,llm=FakeLLM(r))
    c=handle_catalog(s['sanitized_inputs'])
    with pytest.raises(common.AgentOutputError):log_investigator.legacy_log_investigator(s,llm=FakeLLM({
        'selected_evidence_handles':['E01'],'selected_evidence_ids':['ev:invented'],
        'evidence_contract':HANDLE_CONTRACT,'evidence_snapshot':c['evidence_snapshot']}))
