"""Public tests are self-contained; authentic private-history checks opt in."""
import copy
import json
from pathlib import Path

import pytest

from tests.publication_fixtures import FIXTURES, manifest

PRIVATE_MODULES = {'test_rc_benchmark.py', 'test_next_v2_pilot.py', 'test_v2_execution.py',
                   'test_selection_comparison.py', 'test_v2_single.py', 'test_paired_human_review.py'}
PRIVATE_FUNCTIONS = {
    'test_saved_development_replay_routing_and_protected_artifacts',
    'test_latest_manifest_single_scope_and_five_fields',
    'test_provider_audit_originals_and_hash_chain_remain_intact',
    'test_exact_confirmed_human_reviews_and_negative_rating_validation',
    'test_original_paid_outputs_replayed_without_modification',
    'test_new_manifest_latest_budget_and_single_scope',
    'test_replay_examples_are_different_not_silent_repairs',
    'test_latest_real_history_resolves_without_modification',
    'test_integrated_successor_is_pinned_before_acceptance',
}
TRANSPORT_MODULES = {'test_paired_harness.py', 'test_paired_live.py',
                     'test_enhanced_pilot.py', 'test_dispatch_lifecycle.py',
                     'test_json_redaction.py', 'test_quality_pilot.py', 'test_paired_failure_hardening.py'}


def pytest_addoption(parser):
    parser.addoption('--private-audit', action='store_true',
                     help='Also verify authentic ignored evaluation archives; never calls providers')


def pytest_collection_modifyitems(config, items):
    for item in items:
        if Path(item.path).name in PRIVATE_MODULES or item.originalname in PRIVATE_FUNCTIONS:
            item.add_marker(pytest.mark.private_audit)
            if not config.getoption('--private-audit'):
                item.add_marker(pytest.mark.skip(reason='Authentic private evaluation archive: use --private-audit'))


@pytest.fixture(autouse=True)
def portable_preparation(request, tmp_path, monkeypatch):
    """Replace private history only in mock-transport unit tests, never live code."""
    name = Path(request.node.path).name
    if request.config.getoption('--private-audit'):
        return
    if name in TRANSPORT_MODULES:
        from evaluation import quality_pilot, paired_harness, paired_live, enhanced_pilot
        paired = manifest('paired', tmp_path / 'paired-context')
        enhanced = manifest('enhanced', tmp_path / 'enhanced-context')
        # Test modules import prepare by value; patch those aliases as well.
        for module in (quality_pilot, paired_harness, paired_live):
            monkeypatch.setattr(module, 'prepare', lambda: copy.deepcopy(paired))
        monkeypatch.setattr(enhanced_pilot, 'prepare', lambda: copy.deepcopy(enhanced))
        selected = enhanced if name == 'test_enhanced_pilot.py' else paired
        if hasattr(request.module, 'prepare'):
            monkeypatch.setattr(request.module, 'prepare', lambda: copy.deepcopy(selected))
        ledger=tmp_path/'synthetic-ledger.json'
        ledger.write_text(json.dumps({'fixture_only':True,'usage':{'unresolved_calls':0,'conservative_spend_usd':'0.0550552'}}))
        monkeypatch.setattr(quality_pilot,'LEDGER',ledger)
        if hasattr(request.module, 'REFERENCE_REVIEW'):
            monkeypatch.setattr(request.module, 'REFERENCE_REVIEW', Path(paired['reference_review_path']))
    if name == 'test_paired_failure_hardening.py':
        from evaluation import paired_failure_replay
        packet=json.loads((FIXTURES/'root-schema-failure-calls.json').read_text())
        audit=tmp_path/'synthetic-output-envelope.jsonl'
        audit.write_text(json.dumps({'kind':'actual_provider_accounting','fixture_only':True,'calls':packet['calls']})+'\n')
        monkeypatch.setattr(paired_failure_replay,'AUDIT',audit)
        monkeypatch.setattr(request.module,'AUDIT',audit)
    if name in ('test_v2_adapter.py','test_v2_preexecution.py','test_benchmark_v2.py'):
        # Same real plan validator and frozen dataset; isolated synthetic ledger.
        from evaluation import v2
        from evaluation.dataset import digest
        root = tmp_path / 'synthetic-v2-accounting'
        ledger = root / 'reports/phase133-real-pilot/evaluation.json'
        ledger.parent.mkdir(parents=True)
        ledger.write_text(json.dumps({'fixture_only': True, 'usage': {
            'unresolved_calls': 0, 'conservative_spend_usd': '0.0170944'}}))
        plan = json.loads((FIXTURES / 'v2-plan.json').read_text())
        import hashlib
        plan['prior_ledger_sha256'] = hashlib.sha256(ledger.read_bytes()).hexdigest()
        plan['plan_sha256'] = digest({k:v for k,v in plan.items() if k != 'plan_sha256'})
        target = root / 'plan.json'
        target.write_text(json.dumps(plan))
        monkeypatch.setattr(v2, 'ROOT', root)
        if hasattr(request.module,'PLAN'):
            monkeypatch.setattr(request.module,'PLAN',target)
        if name=='test_v2_preexecution.py':
            from evaluation import v2_preexecution
            monkeypatch.setattr(v2_preexecution,'PLAN',target)
            for value in vars(v2_preexecution).values():
                defaults=getattr(value,'__defaults__',None)
                if defaults and any(str(d)=='reports/phase134-v2/plan.json' for d in defaults):
                    monkeypatch.setattr(value,'__defaults__',tuple(target if str(d)=='reports/phase134-v2/plan.json' else d for d in defaults))
        if name=='test_benchmark_v2.py' and request.node.originalname=='test_five_case_plan_does_not_dispatch_and_carries_ledger':
            from data.benchmark_v2 import build
            import shutil
            shutil.copytree(build.ROOT/'data/incidents',root/'data/incidents')
            monkeypatch.setattr(build,'ROOT',root)
