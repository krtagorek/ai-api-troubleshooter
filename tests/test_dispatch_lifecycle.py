"""Disposable claims and mocked transport only; no paid or real history writes."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import Mock

import pytest

from evaluation import paired_live, provider, enhanced_pilot
from evaluation.dataset import digest
from evaluation.dispatch_lifecycle import DispatchClaim, inspect_claim, reject_orphans
from tests.test_enhanced_pilot import fixtures
from tests.test_paired_live import fake_sdk, pricing


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr('socket.socket.connect', Mock(side_effect=AssertionError('No network')))
    monkeypatch.setattr(provider, 'ChatOpenAI', Mock(side_effect=AssertionError('No real SDK')))


def setup_dispatch(tmp_path, monkeypatch):
    m=copy.deepcopy(enhanced_pilot.prepare())
    responses=fixtures()
    m['bindings_sha256']={str(Path(p).resolve()): v for p,v in m['bindings_sha256'].items()}
    m['reference_review_path']=str(Path(m['reference_review_path']).resolve())
    m['manifest_sha256']=digest({k:v for k,v in m.items() if k!='manifest_sha256'})
    monkeypatch.chdir(tmp_path)
    Path('reports').mkdir()
    preparation_calls=[]
    def prepared():
        preparation_calls.append(True)
        if list(Path('reports').glob('.*dispatch*.json')):
            raise ValueError('Unreconciled dispatch/history; stop')
        return m
    monkeypatch.setattr(enhanced_pilot,'prepare',prepared)
    monkeypatch.setattr(paired_live,'configuration_check',lambda:{'ready':True})
    approval={'manifest_sha256':m['manifest_sha256'],
              'maximum_new_spend_usd':m['budget']['maximum_new_reservations_usd'],
              'explicit_user_approval':True}
    marker=Path('reports')/('.paired-live-dispatch-'+m['manifest_sha256']+'.json')
    return m,responses,approval,marker,preparation_calls


def record():
    return {'manifest_sha256':'CI-NOT-A-REAL-APPROVAL', 'maximum_new_reservations_usd':'0.1712128'}


def test_exact_historical_failure_and_fixed_mocked_dispatch(tmp_path,monkeypatch):
    m,f,approval,marker,checks=setup_dispatch(tmp_path,monkeypatch)
    # Reproduce the former engine's erroneous post-claim prepare call, entirely
    # within a disposable claim; it must fail before any provider initializes.
    reproduction=tmp_path/'reproduction';reproduction.mkdir()
    old_marker=Path('reports/.reproduction-dispatch.json')
    with old_marker.open('x') as s: json.dump(record(),s)
    transport=Mock(side_effect=AssertionError('Legacy failure must precede transport'))
    def legacy_engine_handoff():
        # This was the first action of _run even when a live context existed.
        enhanced_pilot.prepare()
        transport()
    with pytest.raises(ValueError,match='Unreconciled dispatch/history'):
        legacy_engine_handoff()
    transport.assert_not_called()
    provider.ChatOpenAI.assert_not_called()
    # Move this synthetic fixture OUT of the history inventory (not real history).
    # No real audit/marker is removed or changed.
    old_marker.rename(reproduction/'synthetic-marker.json')
    checks.clear()
    context=paired_live.LiveContext(m)
    calls=fake_sdk(monkeypatch,context,f)
    monkeypatch.setattr(paired_live,'LiveContext',lambda manifest:context)
    result=paired_live.run_approved(m,approval,pricing(m),tmp_path/'run')
    assert len(checks)==2  # Both before claim; never preparation after claim.
    assert result['actual_provider_calls']==8 and len(calls)==8
    assert inspect_claim(marker)['state']=='COMPLETED'
    events=[json.loads(s) for s in Path(str(marker)+'.lifecycle.jsonl').read_text().splitlines()]
    assert [e['state'] for e in events]==['PREPARED','CLAIMED','RUNNING','COMPLETED']
    assert 'budget_reservation' in json.loads(marker.read_text())
    assert all('/baseline/' not in k for k in calls)
    contents=marker.read_bytes()
    with pytest.raises(ValueError,match='Unreconciled'):
        paired_live.run_approved(m,approval,pricing(m),tmp_path/'replay')
    assert marker.read_bytes()==contents and len(calls)==8


@pytest.mark.parametrize('failure',['transport','usage','grounding','model'])
def test_claimed_failure_stays_consumed(tmp_path,monkeypatch,failure):
    m,f,approval,marker,_=setup_dispatch(tmp_path,monkeypatch)
    if failure=='grounding':f['V2-01-01/enhanced/investigator']['selected_evidence_handles']=['E99999']
    context=paired_live.LiveContext(m)
    calls=fake_sdk(monkeypatch,context,f,None if failure=='grounding' else failure)
    monkeypatch.setattr(paired_live,'LiveContext',lambda manifest:context)
    with pytest.raises(ValueError,match='first failure'):
        paired_live.run_approved(m,approval,pricing(m),tmp_path/'failed')
    assert inspect_claim(marker)['state']=='FAILED'
    assert len(calls)==(3 if failure=='grounding' else 1)
    assert not inspect_claim(marker)['automatic_resume_allowed']
    with pytest.raises(ValueError,match='Unreconciled'):
        paired_live.run_approved(m,approval,pricing(m),tmp_path/'retry')


@pytest.mark.parametrize('bad',['approval','price','history','hash','review'])
def test_prerequisites_fail_before_claim_and_provider(tmp_path,monkeypatch,bad):
    m,f,approval,marker,_=setup_dispatch(tmp_path,monkeypatch)
    price=pricing(m)
    if bad=='approval':approval['explicit_user_approval']=False
    if bad=='price':price['output_usd_per_million']='2.00'
    if bad=='history':Path('reports/.unrelated-dispatch.json').write_text('{}')
    if bad=='hash':m['model']['retries']=1  # Digest cannot validate anymore.
    if bad=='review':
        m['reference_review_validation']={'complete':False}
        m['manifest_sha256']=digest({k:v for k,v in m.items() if k!='manifest_sha256'})
    with pytest.raises(ValueError):
        paired_live.run_approved(m,approval,price,tmp_path/'blocked')
    assert not marker.exists()
    assert not Path(str(marker)+'.lifecycle.jsonl').exists()
    provider.ChatOpenAI.assert_not_called()


def test_collision_or_orphan_never_overwrites(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path);Path('reports').mkdir()
    marker=Path('reports/.paired-live-dispatch-test.json')
    marker.write_text('{')
    with pytest.raises(FileExistsError):DispatchClaim(marker,record())
    assert marker.read_text()=='{'
    assert inspect_claim(marker)['state']=='CORRUPT_OR_PARTIAL'
    journal=Path('reports/.paired-live-dispatch-orphan.json.lifecycle.jsonl')
    journal.write_text('partial')
    with pytest.raises(ValueError,match='Orphan'):reject_orphans(Path('reports'))
    with pytest.raises(FileExistsError):DispatchClaim(Path(str(journal).removesuffix('.lifecycle.jsonl')),record())


def test_midrun_unrelated_history_blocks_before_provider(tmp_path,monkeypatch):
    m,f,approval,marker,_=setup_dispatch(tmp_path,monkeypatch)
    context=paired_live.LiveContext(m)
    calls=fake_sdk(monkeypatch,context,f)
    monkeypatch.setattr(paired_live,'LiveContext',lambda manifest:context)
    real_start=context.start
    def contaminated_start(*args):
        Path('reports/.other-dispatch.json').write_text('{}')
        real_start(*args)
    context.start=contaminated_start
    with pytest.raises(ValueError,match='first failure'):
        paired_live.run_approved(m,approval,pricing(m),tmp_path/'contaminated')
    assert not calls
    assert inspect_claim(marker)['state']=='FAILED'


def test_concurrent_claims_execute_once(tmp_path,monkeypatch):
    m,f,approval,marker,_=setup_dispatch(tmp_path,monkeypatch)
    entered=threading.Event();release=threading.Event();executions=[];errors=[]
    def engine(*args,**kwargs):
        executions.append(True);entered.set()
        assert release.wait(5)
        return {'mocked':True}
    monkeypatch.setattr(paired_live,'_run',engine)
    def first():
        try:paired_live.run_approved(m,approval,pricing(m),tmp_path/'one')
        except BaseException as exc:errors.append(exc)
    thread=threading.Thread(target=first);thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises((ValueError,BlockingIOError)):
            paired_live.run_approved(m,approval,pricing(m),tmp_path/'two')
    finally:
        release.set();thread.join(5)
    assert not thread.is_alive() and not errors
    assert len(executions)==1 and inspect_claim(marker)['state']=='COMPLETED'


def test_killed_process_claim_remains_reserved_no_automatic_recovery(tmp_path):
    root=Path(__file__).resolve().parents[1]
    program='''
import sys,os,fcntl
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from evaluation.dispatch_lifecycle import DispatchClaim
Path('reports').mkdir()
with Path('reports/.v2-aggregate-budget.lock').open('a+') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 c=DispatchClaim(Path('reports/.paired-live-dispatch-crash.json'),{'maximum_new_reservations_usd':'0.1712128','manifest_sha256':'CI'})
 c.transition('RUNNING')
 os._exit(42)
'''
    result=subprocess.run([sys.executable,'-c',program,str(root)],cwd=tmp_path,capture_output=True)
    assert result.returncode==42
    marker=tmp_path/'reports/.paired-live-dispatch-crash.json'
    status=inspect_claim(marker)
    assert status['state']=='RUNNING' and status['manual_reconciliation_required']
    assert status['maximum_reserved_usd']=='0.1712128'
    assert not status['automatic_resume_allowed']
    with pytest.raises(FileExistsError):DispatchClaim(marker,record())


def test_partial_journal_never_authorizes_resume(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path);Path('reports').mkdir()
    marker=Path('reports/.paired-live-dispatch-partial.json')
    claim=DispatchClaim(marker,record());claim.transition('RUNNING');claim.close()
    with Path(str(marker)+'.lifecycle.jsonl').open('a') as s:s.write('{"torn":')
    assert inspect_claim(marker)['state']=='CORRUPT_OR_PARTIAL'
    with pytest.raises(FileExistsError):DispatchClaim(marker,record())


def test_live_context_without_claim_cannot_initialize_provider(tmp_path):
    from evaluation.budget import Budget
    from evaluation.paired_harness import Audit
    from decimal import Decimal
    context=paired_live.LiveContext(enhanced_pilot.prepare())
    audit=Audit(tmp_path/'journal')
    try:
        with pytest.raises(ValueError,match='RUNNING dispatch claim'):
            context.start(Budget(Decimal('.1712128'),Decimal('.4'),Decimal('1.6'),11),audit)
        assert not hasattr(context,'provider')
        provider.ChatOpenAI.assert_not_called()
    finally:
        audit.close()
