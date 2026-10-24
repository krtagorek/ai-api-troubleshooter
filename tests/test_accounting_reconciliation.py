"""Synthetic append-only successor proofs; no SDK or real history mutations."""
import copy
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest
from evaluation import paired_accounting as accounting
from evaluation.dataset import digest
from evaluation.quality_pilot import sha,OUTPUT
from evaluation.dispatch_lifecycle import DispatchClaim,history_snapshot
from evaluation.paired_harness import Audit


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr('socket.socket.connect',Mock(side_effect=AssertionError('Offline only')))
    monkeypatch.setattr('evaluation.provider.ChatOpenAI',Mock(side_effect=AssertionError('No provider')))


@pytest.fixture
def successor(tmp_path):
    predecessor=tmp_path/'previous.json';predecessor.write_text('{}')
    run=tmp_path/'run';run.mkdir()
    marker=tmp_path/'.paired-live-dispatch-ci.json'
    # DispatchClaim's history inventory is relative to reports; this synthetic
    # isolated marker is not in actual project history and is never dispatched.
    claim=DispatchClaim(marker,{'manifest_sha256':'CI-MANIFEST','output':str(run),
        'prior_spend_usd':'0.05','maximum_new_reservations_usd':'0.1712128',
        'pricing_verification':{'model':'CI-MODEL','input_usd_per_million':'0.40','output_usd_per_million':'1.60'}})
    claim.transition('RUNNING')
    calls=[{'key':'connectivity_preflight','status':'observed','provider_model':'CI-MODEL','input_tokens':100,'output_tokens':20,'observed_usd':'0.000072'}]
    usage={'unresolved_calls':0,'observed_input_tokens':100,'observed_output_tokens':20,'observed_cost_usd':'0.000072'}
    audit=Audit(run/'audit.jsonl');audit.append('run_start',manifest_sha256='CI-MANIFEST')
    audit.append('actual_provider_accounting',calls=calls,usage=usage)
    audit.append('run_completed',actual_provider_calls=1);audit.close()
    claim.transition('COMPLETED');claim.close()
    summary={'actual_usage':usage,'actual_provider_calls':1,'new_usage_priced_estimated_spend_usd':'0.000072','carried_prior_spend_usd':'0.05','aggregate_conservative_spend_usd':'0.050072'}
    (run/'summary.json').write_text(json.dumps(summary))
    sources=[marker,Path(str(marker)+'.lifecycle.jsonl'),run/'audit.jsonl',run/'summary.json']
    history={str(marker):sha(marker)}
    record={'previous_reconciliation_path':str(predecessor),'previous_reconciliation_sha256':sha(predecessor),'aggregate_budget_usd':'2','unresolved_calls':0,'source_sha256':{str(p):sha(p) for p in sources},'paid_history_sha256':history,'audit_path':str(run/'audit.jsonl'),'prior_estimated_spend_usd':'0.05','new_estimated_spend_usd':'0.000072','aggregate_estimated_spend_usd':'0.050072'}
    path=tmp_path/'successor.json';path.write_text(json.dumps(record))
    return path,predecessor,record,marker


def check(fixture):
    path,predecessor,record,marker=fixture
    return accounting._verify_completed_extension(path,predecessor,{'paid_history_sha256':{}},Decimal('.05'),sha,record['paid_history_sha256'],set())


def test_latest_real_history_resolves_without_modification():
    before=sha(accounting.LATEST_RECONCILIATION)
    record=accounting.verify_reconciliation(OUTPUT,sha,history_snapshot())
    assert record['aggregate_estimated_spend_usd']=='0.0550552'
    assert accounting.CARRIED_SPEND==Decimal('.0550552') and sha(accounting.LATEST_RECONCILIATION)==before


def test_valid_completed_synthetic_successor(successor):
    record,spend=check(successor)
    assert spend==Decimal('.050072') and record['unresolved_calls']==0


@pytest.mark.parametrize('kind',['predecessor','missing_source','conflicting_history','duplicate_history','cost','prior','stale','unresolved','budget','output'])
def test_invalid_successor_rejected(successor,kind):
    path,predecessor,r,marker=successor
    if kind=='predecessor':r['previous_reconciliation_sha256']='0'*64
    if kind=='missing_source':r['source_sha256'].pop(r['audit_path'])
    if kind=='conflicting_history':r['paid_history_sha256'][str(marker)]='0'*64
    if kind=='duplicate_history':r['paid_history_sha256']['another-dispatch.json']='0'*64
    if kind=='cost':r['new_estimated_spend_usd']='0'
    if kind=='prior':r['prior_estimated_spend_usd']='0'
    if kind=='stale':r['paid_history_sha256']={}
    if kind=='unresolved':r['unresolved_calls']=1
    if kind=='budget':r['aggregate_budget_usd']='20'
    if kind=='output':
        d=json.loads(marker.read_text());d['output']=str(path.parent/'other');marker.write_text(json.dumps(d));r['source_sha256'][str(marker)]=sha(marker);r['paid_history_sha256'][str(marker)]=sha(marker)
    path.write_text(json.dumps(r))
    with pytest.raises((ValueError,KeyError)):check(successor)


@pytest.mark.parametrize('kind',['usage','duplicate_calls','forged_chain','incomplete','model','bool_tokens'])
def test_invalid_audit_rejected_even_with_refreshed_source_hash(successor,kind):
    path,predecessor,r,marker=successor;audit=Path(r['audit_path']);events=[json.loads(s) for s in audit.read_text().splitlines()]
    account=events[1]
    if kind=='usage':account['calls'][0]['observed_usd']='0'
    if kind=='duplicate_calls':account['calls']*=2
    if kind=='model':account['calls'][0]['provider_model']='OTHER'
    if kind=='bool_tokens':account['calls'][0]['input_tokens']=True
    if kind=='incomplete':events[-1]['kind']='live_stopped'
    previous=None
    for e in events:
        e['previous']=previous;e['sha256']=digest({k:v for k,v in e.items() if k!='sha256'});previous=e['sha256']
    if kind=='forged_chain':events[1]['sha256']='0'*64
    audit.write_text(''.join(json.dumps(e)+'\n' for e in events));r['source_sha256'][str(audit)]=sha(audit);path.write_text(json.dumps(r))
    with pytest.raises(ValueError):check(successor)


def test_duplicate_json_keys_fail_closed(tmp_path):
    p=tmp_path/'duplicate.json';p.write_text('{"aggregate_budget_usd":"2","aggregate_budget_usd":"20"}')
    with pytest.raises(ValueError,match='Duplicate'):accounting._read(p)


@pytest.mark.parametrize('kind',['missing','forged'])
def test_integrated_successor_is_pinned_before_acceptance(tmp_path,monkeypatch,kind):
    p=tmp_path/'latest.json'
    if kind=='forged':p.write_text('{}')
    monkeypatch.setattr(accounting,'LATEST_RECONCILIATION',p)
    with pytest.raises((ValueError,FileNotFoundError)):
        accounting.verify_reconciliation(OUTPUT,sha,history_snapshot())


def test_duplicate_dispatch_proof_rejected(successor):
    path,predecessor,r,marker=successor
    with pytest.raises(ValueError,match='Duplicate dispatch'):
        accounting._verify_completed_extension(path,predecessor,{'paid_history_sha256':{}},Decimal('.05'),sha,r['paid_history_sha256'],{Path(r['audit_path']).resolve()})


@pytest.mark.parametrize('state',['RUNNING','FAILED','CORRUPT'])
def test_noncompleted_or_partial_lifecycle_never_releases_reservation(successor,state):
    path,predecessor,r,marker=successor
    lifecycle=Path(str(marker)+'.lifecycle.jsonl')
    events=[json.loads(s) for s in lifecycle.read_text().splitlines()]
    if state=='RUNNING':events=events[:-1]
    elif state=='FAILED':
        events[-1]['state']='FAILED';events[-1]['sha256']=digest({k:v for k,v in events[-1].items() if k!='sha256'})
    if state=='CORRUPT':lifecycle.write_text('{partial')
    else:lifecycle.write_text(''.join(json.dumps(e)+'\n' for e in events))
    r['source_sha256'][str(lifecycle)]=sha(lifecycle);path.write_text(json.dumps(r))
    with pytest.raises(ValueError,match='not completed'):check(successor)
