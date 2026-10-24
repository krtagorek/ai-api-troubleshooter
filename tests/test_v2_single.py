"""Single-case preparation never dispatches or resets the aggregate ledger."""
import copy
import json
from decimal import Decimal

import pytest

from evaluation import provider,v2_single
from graph.state import initialize_state
from tools.evidence_catalog import resolve_evidence
from agents import error_classifier,log_investigator
from tests.test_agents import FakeLLM


@pytest.fixture(autouse=True)
def no_paid(monkeypatch):
    def fail(*a,**kw):raise AssertionError('No paid calls authorized')
    monkeypatch.setattr(provider,'ChatOpenAI',fail)


def test_single_scope_budget_hashes_and_zero_execution():
    original=v2_single.LEDGER.read_bytes(); marker=v2_single.MARKER.read_bytes()
    m=v2_single.prepare();inputs=v2_single.prepared_inputs(m)
    assert m['selected_ids']==['V2-02-01'] and len(inputs)==1
    assert m['maximum_new_calls']==6 and m['new_reservation_ceiling_usd']=='0.0933888'
    assert Decimal(m['conservative_aggregate_ceiling_usd'])==Decimal('.1109740')
    assert m['prior_estimated_spend_usd']=='0.0175852' and not m['paid_approval_granted']
    assert not m['execution_enabled']
    assert v2_single.LEDGER.read_bytes()==original and v2_single.MARKER.read_bytes()==marker
    result=v2_single.grading_adapter(m).summarize([])
    assert result['planned']==1 and result['attempted']==0 and result['classification_accuracy']['value'] is None


def test_catalog_integration_through_actual_agents_offline():
    m=v2_single.prepare();state=initialize_state(v2_single.prepared_inputs(m)[0],'offline-139')
    entry=resolve_evidence(state['sanitized_inputs'],'/status_code')
    llm=FakeLLM({'classification':{'category':'authentication','reasoning':'Observed status',
                'evidence_references':[entry.evidence_id]}})
    update=error_classifier.error_classifier(state,llm=llm)
    assert update['classification'].evidence_references==['/status_code']
    assert '"handle": "E' in str(llm.calls[0][1].content)
    assert entry.evidence_id not in str(llm.calls[0])
    assert 'accepted_alternatives' not in str(llm.calls[0])
    result=log_investigator.legacy_log_investigator(state,llm=FakeLLM({'selected_evidence_ids':[entry.evidence_id]}))
    item=result['evidence'].items[0]
    assert item.source=='response' and item.observation=='401'


def test_modified_scope_or_budget_rejects():
    m=v2_single.prepare();changed=copy.deepcopy(m);changed['selected_ids'].append('V2-04-04')
    with pytest.raises(ValueError):v2_single.prepared_inputs(changed)
    changed=copy.deepcopy(m);changed['prior_estimated_spend_usd']='0'
    with pytest.raises(ValueError):v2_single.prepared_inputs(changed)


def test_latest_ledger_unknown_usage_rejects(tmp_path,monkeypatch):
    record=json.loads(v2_single.LEDGER.read_text());record['usage']['unresolved_calls']=1
    path=tmp_path/'evaluation.json';path.write_text(json.dumps(record));monkeypatch.setattr(v2_single,'LEDGER',path)
    with pytest.raises(ValueError):v2_single.prepare()


def test_dispatch_requires_new_approval_before_any_write(tmp_path):
    m=v2_single.prepare()
    with pytest.raises(ValueError):v2_single.run_approved_single(m,'unapproved',tmp_path/'output')
    assert not (tmp_path/'output').exists()


def test_dispatch_budget_and_single_scope_with_offline_runner(tmp_path,monkeypatch):
    import evaluation.runner as runner
    reports=tmp_path/'reports';reports.mkdir()
    monkeypatch.setattr(v2_single,'ROOT',tmp_path)
    monkeypatch.setattr(provider,'configuration_check',lambda:{'ready':True})
    m=v2_single.prepare();old_marker=v2_single.MARKER.read_bytes();old_ledger=v2_single.LEDGER.read_bytes()
    def offline(selected,labels,model,budget,directory,report,**kwargs):
        assert len(selected)==1 and selected[0]['incident_id']=='V2-02-01' and labels is None
        assert budget.max_calls==6 and budget.reserved_or_spent==Decimal('.0175852')
        assert budget.limit==Decimal('2') and kwargs['stop_on_failure'] is True
        assert kwargs['scoring_adapter'].summarize([])['planned']==1
        report['status']='offline-test-only-no-provider'
    monkeypatch.setattr(runner,'run_cases',offline)
    result=v2_single.run_approved_single(m,m['approval_sha256'],tmp_path/'output')
    assert result['usage']['calls']==0 and result['new_estimated_cost_usd']=='0E-7'
    with pytest.raises(ValueError):v2_single.run_approved_single(m,m['approval_sha256'],tmp_path/'another')
    assert v2_single.MARKER.read_bytes()==old_marker and v2_single.LEDGER.read_bytes()==old_ledger
