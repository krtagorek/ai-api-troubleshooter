import copy
import json
from decimal import Decimal
from unittest.mock import patch

import pytest

from data.benchmark_v2.build import DEFAULT, rows
from evaluation.budget import Budget
from evaluation.dataset import digest
from evaluation.paired_harness import Audit, FixtureBoundary, run_offline
from evaluation.quality_pilot import prepare
from graph.state import ClassifierOutput, RootCauseAnalystOutput
from models.incident import Incident
from tools.evidence_catalog import handle_catalog


def fixtures():
    data = {r['incident_id']:r for r in rows(DEFAULT/'inference/development.jsonl')}
    result = {}
    for case in ('V2-01-01','V2-03-04'):
        catalog = handle_catalog(Incident.model_validate(data[case]))
        handle = next(e['handle'] for e in catalog['evidence_catalog'] if e['reference']=='/status_code')
        for arm in ('baseline','enhanced'):
            prefix = f'{case}/{arm}/'
            version = {k:catalog[k] for k in ('evidence_contract','evidence_snapshot')}
            result[prefix+'classifier'] = {**version,'classification':{'category':'unknown','confidence':0,'reasoning':'Offline fixture only'}}
            result[prefix+'investigator'] = {k:catalog[k] for k in ('evidence_contract','evidence_snapshot')}
            result[prefix+'investigator'].update(selected_evidence_handles=[handle],completeness='complete',diagnostic_gaps=[])
            result[prefix+'root_cause'] = RootCauseAnalystOutput.model_validate({**version,'hypotheses':[], 'diagnosis':{'status':'inconclusive','summary':'Offline fixture only','uncertainty':['No real model inference']}}).model_dump(mode='json')
    return result


@pytest.fixture(autouse=True)
def prohibit_providers():
    with patch('socket.socket.connect',side_effect=AssertionError('No network')), patch(
        'agents.common.ChatOpenAI',side_effect=AssertionError('No provider')
    ), patch('evaluation.provider.ChatOpenAI',side_effect=AssertionError('No provider')):
        yield


def test_isolated_arms_coverage_blinding_and_append_only_audit(tmp_path):
    f = fixtures(); original = copy.deepcopy(f)
    summary = run_offline(prepare(), f, tmp_path/'run')
    assert f == original
    assert summary['actual_provider_calls'] == 0 and summary['actual_new_spend_usd'] == '0'
    assert summary['simulated_calls_reserved'] == 10
    results = json.loads((tmp_path/'run/outputs.json').read_text())
    assert len(results) == 4
    assert len({r['state']['thread_id'] for r in results}) == 4
    assert results[0]['executed_nodes'] == ['classifier','investigator']
    assert results[1]['executed_nodes'] == ['classifier','investigator','root_cause']
    assert len(results[0]['state']['evidence']['items']) == 1
    assert len(results[1]['state']['evidence']['items']) > 1
    assert all(max(r['state']['retry_counts'].values()) == 0 for r in results)
    assert all(len(r['executed_nodes']) == len(set(r['executed_nodes'])) for r in results)
    public = (tmp_path/'run/reviewer-outputs.json').read_text()
    assert 'baseline' not in public and 'enhanced' not in public
    assert all(r['reviewer_id'] is None and r['ratings'] is None for r in json.loads((tmp_path/'run/human-review.json').read_text())['reviews'])
    previous = None
    for n, event in enumerate(map(json.loads,(tmp_path/'run/audit.jsonl').read_text().splitlines())):
        assert event['sequence'] == n and event['previous'] == previous
        previous = event.pop('sha256')
        assert digest(event) == previous
    with pytest.raises(FileExistsError):
        run_offline(prepare(), f, tmp_path/'run')


@pytest.mark.parametrize('mutation',['unknown','stale','mixed','empty'])
def test_handle_failures_stop_or_empty_holds(tmp_path, mutation):
    f=fixtures(); out=f['V2-01-01/baseline/investigator']
    if mutation=='unknown': out['selected_evidence_handles']=['E99999']
    if mutation=='stale': out['evidence_snapshot']='stale'
    if mutation=='mixed': out['evidence']={'items':[]}
    if mutation=='empty': out['selected_evidence_handles']=[]
    if mutation=='empty':
        run_offline(prepare(),f,tmp_path/'run')
        first=json.loads((tmp_path/'run/outputs.json').read_text())[0]
        assert not first['analysis_ready'] and first['state']['evidence']['completeness']=='missing'
    else:
        with pytest.raises(ValueError,match='first failure'):
            run_offline(prepare(),f,tmp_path/'run')
        log=(tmp_path/'run/audit.jsonl').read_text()
        assert 'offline_stopped' in log
        assert 'V2-01-01/enhanced' not in log  # No retry/next-arm continuation.


def test_missing_fixture_and_unapproved_tools_fail_closed(tmp_path):
    f=fixtures();del f['V2-01-01/baseline/classifier']
    with pytest.raises(ValueError,match='first failure'):
        run_offline(prepare(),f,tmp_path/'missing')
    f=fixtures();f['V2-01-01/baseline/http_tool']={}
    with pytest.raises(ValueError,match='Unapproved'):
        run_offline(prepare(),f,tmp_path/'tool')


def test_manifest_tampering_rejected(tmp_path):
    m=prepare();m['model']['retries']=1
    with pytest.raises(ValueError,match='manifest'):
        run_offline(m, fixtures(),tmp_path/'run')


def test_budget_reserves_before_fixture_and_stops_at_limit(tmp_path):
    from langchain_core.messages import HumanMessage
    budget=Budget(Decimal('.0155648'),Decimal('.4'),Decimal('1.6'),1)
    audit=Audit(tmp_path/'journal')
    try:
        for n in range(2):
            b=FixtureBoundary({'classification':{'category':'unknown','confidence':0,'reasoning':'Fixture'}},budget,audit,str(n))
            b.with_structured_output(ClassifierOutput)
            if n==0: b.invoke([HumanMessage(content='offline')])
            else:
                with pytest.raises(Exception,match='limit'): b.invoke([HumanMessage(content='offline')])
        assert budget.reserved_or_spent == Decimal('.0155648')
        assert budget.observed_cost == 0  # Simulated reservations, no invented usage.
    finally: audit.close()


def test_provider_request_overhead_and_audit_redaction(tmp_path):
    from langchain_core.messages import HumanMessage
    budget=Budget(Decimal('1'),Decimal('.4'),Decimal('1.6'),2)
    audit=Audit(tmp_path/'journal')
    try:
        b=FixtureBoundary({},budget,audit,'oversized')
        b.with_structured_output(ClassifierOutput)
        with pytest.raises(ValueError,match='envelope'):
            b.invoke([HumanMessage(content='x'*33000)])
        assert not budget.calls
        audit.append('test',password='do-not-leak',Authorization='Bearer do-not-leak')
        assert 'do-not-leak' not in (tmp_path/'journal').read_text()
    finally: audit.close()


def full_branch_fixtures():
    f=fixtures()
    source=next(r for r in rows(DEFAULT/'inference/development.jsonl') if r['incident_id']=='V2-01-01')
    env=handle_catalog(Incident.model_validate(source))
    version={k:env[k] for k in ('evidence_contract','evidence_snapshot')}
    handle=next(e['handle'] for e in env['evidence_catalog'] if e['reference']=='/logs/0/fields/issuer_configured')
    for arm in ('baseline','enhanced'):
        key=f'V2-01-01/{arm}/'
        f[key+'investigator']['selected_evidence_handles']=[e['handle'] for e in env['evidence_catalog']]
        f[key+'root_cause']={**version,'hypotheses':[{'hypothesis_id':'h1','description':'Compare issuer identifiers',
            'confidence':.4,'supporting_evidence_ids':[handle]}],
            'diagnosis':{'status':'tentative','summary':'Inspect the observed identifier discrepancy',
                         'confidence':.4,'selected_hypothesis_id':'h1'}}
        f[key+'resolution']={**version,'proposed_fix':{'plan_kind':'diagnostic','summary':'Review issuer values',
            'steps':['Compare configured and observed issuer values with the owner'],
            'verification_steps':['Request independent sanitized issuer telemetry'],
            'evidence_ids':[handle],'requires_human_approval':False}}
        f[key+'validator']={**version,'validation':{'status':'NOT_TESTED','checks':[]}}
        f[key+'root_cause']=RootCauseAnalystOutput.model_validate(f[key+'root_cause']).model_dump(mode='json')
    return f


def test_full_branch_no_tools_and_truthful_validation(tmp_path):
    summary=run_offline(prepare(),full_branch_fixtures(),tmp_path/'run')
    results=json.loads((tmp_path/'run/outputs.json').read_text())
    assert summary['simulated_calls_reserved']==15
    for result in results[:2]:
        assert result['executed_nodes']==['classifier','investigator','root_cause','resolution','validator']
        assert result['state']['validation_results'][-1]['status']=='NOT_TESTED'
        assert result['state']['validation_results'][-1]['checks']==[]


def test_fabricated_validation_stops_even_if_agent_normally_swallows_errors(tmp_path):
    f=full_branch_fixtures()
    f['V2-01-01/baseline/validator']['validation']['status']='passed'
    with pytest.raises(ValueError,match='first failure'):
        run_offline(prepare(),f,tmp_path/'run')
    log=(tmp_path/'run/audit.jsonl').read_text()
    assert 'offline_stopped' in log and 'V2-01-01/enhanced' not in log


def test_cross_incident_and_duplicate_handles_rejected(tmp_path):
    for n,mutation in enumerate(('cross','duplicate')):
        f=fixtures();target=f['V2-01-01/baseline/investigator']
        if mutation=='cross':
            target['evidence_snapshot']=f['V2-03-04/enhanced/investigator']['evidence_snapshot']
        else: target['selected_evidence_handles'] *= 2
        with pytest.raises(ValueError,match='first failure'):
            run_offline(prepare(),f,tmp_path/f'run{n}')


def test_reservation_failure_at_validator_cannot_be_swallowed(tmp_path, monkeypatch):
    import evaluation.paired_harness as harness
    from evaluation.budget import BudgetStopped
    class LimitedBudget(Budget):
        def reserve(self):
            # Preflight + classifier + investigator + analyst + resolution.
            if len(self.calls) == 5:
                self.stopped='budget_limit'
                raise BudgetStopped('Call limit reached')
            return super().reserve()
    monkeypatch.setattr(harness,'Budget',LimitedBudget)
    with pytest.raises(ValueError,match='first failure'):
        run_offline(prepare(),full_branch_fixtures(),tmp_path/'run')
    log=(tmp_path/'run/audit.jsonl').read_text()
    assert 'offline_stopped' in log and 'V2-01-01/enhanced' not in log


def test_inference_label_isolation_and_frozen_integrity(tmp_path, monkeypatch):
    from pathlib import Path
    from evaluation.dataset import contains_labels
    from evaluation.quality_pilot import sha, LEDGER
    protected=list(Path('data/benchmark_v2').rglob('*.jsonl'))+[LEDGER]
    before={str(p):sha(p) for p in protected}
    original=FixtureBoundary.invoke
    def inspect(self,messages):
        for message in messages:
            if message.content.startswith('UNTRUSTED_EVIDENCE_JSON\n'):
                payload=json.loads(message.content.split('\n',1)[1])
                assert not contains_labels(payload)
                assert 'reviewer_id' not in message.content
                assert 'ACCEPT_WITH_ALTERNATIVES' not in message.content
        return original(self,messages)
    monkeypatch.setattr(FixtureBoundary,'invoke',inspect)
    run_offline(prepare(),fixtures(),tmp_path/'run')
    assert before=={str(p):sha(p) for p in protected}
