"""Offline readiness only; no paid approval or model execution."""
import pytest
from evaluation.v2_minimal_verification import prepare,LEDGER,MARKER,PREVIOUS
from evaluation.v2_single import prepared_inputs,grading_adapter,run_approved_single


def test_latest_manifest_single_scope_and_five_fields(tmp_path):
    before={p:p.read_bytes() for p in (LEDGER,MARKER,PREVIOUS)}
    m=prepare()
    assert m['selected_ids']==['V2-02-01'] and len(prepared_inputs(m))==1
    assert m['prior_estimated_spend_usd']=='0.0225020' and m['aggregate_budget_usd']=='2'
    assert m['maximum_new_calls']==6 and m['new_reservation_ceiling_usd']=='0.0933888'
    assert m['conservative_aggregate_ceiling_usd']=='0.1158908'
    assert m['strict_structured_output'] and not m['legacy_live_fallback']
    assert len(m['live_investigator_fields'])==5 and not m['paid_approval_granted']
    assert len(m['reconciled_dispatches'])==2
    assert grading_adapter(m).summarize([])['planned']==1
    with pytest.raises(ValueError):run_approved_single(m,'unapproved',tmp_path/'never-dispatched')
    assert not (tmp_path/'never-dispatched').exists()
    assert all(p.read_bytes()==value for p,value in before.items())


def test_minimal_investigator_to_root_cause_is_consistent_offline():
    from graph.state import initialize_state
    from tools.evidence_catalog import handle_catalog
    from agents import log_investigator,root_cause
    from tests.test_agents import FakeLLM
    from data.benchmark_v2.build import DEFAULT,rows
    incident=next(r for r in rows(DEFAULT/'inference/test.jsonl') if r['incident_id']=='V2-02-01')
    s=initialize_state(incident,'offline-pilot-integration')
    envelope=handle_catalog(s['sanitized_inputs'])
    handle=next(e['handle'] for e in envelope['evidence_catalog'] if e['reference']=='/status_code')
    context={'evidence_contract':envelope['evidence_contract'],'evidence_snapshot':envelope['evidence_snapshot']}
    llm=FakeLLM({**context,'selected_evidence_handles':[handle],'completeness':'partial','diagnostic_gaps':[]})
    s.update(log_investigator.log_investigator(s,llm=llm))
    trusted=s['evidence'].items[0]
    assert trusted.observation=='401' and trusted.source=='response' and trusted.evidence_id.startswith('ev:')
    analyst=FakeLLM({**context,'hypotheses':[{'hypothesis_id':'h1','description':'Possible identity rejection',
        'confidence':.4,'supporting_evidence_ids':[handle]}], 'diagnosis':{'status':'tentative','summary':'Need issuer evidence',
        'selected_hypothesis_id':'h1','confidence':.4,'uncertainty':['No definitive cause']}})
    result=root_cause.root_cause(s,llm=analyst)
    assert result['hypotheses'][0].supporting_evidence_ids==[trusted.evidence_id]
    for messages in (llm.calls,analyst.calls):
        assert 'root_cause' not in str(messages) and 'expected_resolution' not in str(messages)
        assert 'ev:' not in str(messages)
