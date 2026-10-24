"""Offline comparison integrity, with provider and network access prohibited."""
import hashlib
from pathlib import Path

import pytest

from evaluation.selection_comparison import run_comparison
from tools.evidence_catalog import build_catalog
from models.incident import Incident


@pytest.fixture(scope='module')
def comparison():
    from unittest.mock import patch
    protected = [Path('reports/phase1312-minimal-real-run/evaluation.json')]
    protected += list(Path('data/benchmark_v2/dataset').rglob('*.jsonl'))
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    with patch('agents.common.ChatOpenAI', side_effect=AssertionError('Provider prohibited')), patch(
        'socket.socket.connect', side_effect=AssertionError('Network prohibited')
    ):
        result = run_comparison()
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    return result


def test_population_and_metric_boundaries(comparison):
    r = comparison
    assert len(r['development_cases']) == 40
    assert all(c['source_kind'].startswith('DEVELOPMENT_') for c in r['development_cases'])
    assert r['known_regression']['incident_id'] == 'V2-02-01'
    assert 'NOT_INDEPENDENT' in r['known_regression']['source_kind']
    assert r['paid_calls'] == 0 and not r['dispatch_prepared']
    assert r['carried_spend_usd'] == '0.0260008'
    for c in r['development_cases'] + [r['known_regression']]:
        assert c['human_selection_precision'] is None
        assert c['human_selection_recall'] is None
        assert c['causal_accuracy'] is None
        assert c['unsupported_claim_comparison'].startswith('NOT_MEASURED')


def test_added_facts_are_existing_source_not_model_selections(comparison):
    for c in comparison['development_cases'] + [comparison['known_regression']]:
        catalog = build_catalog(Incident.model_validate(c['complete_source_evidence']))
        assert set(c['trusted_additions']) <= set(catalog)
        assert not set(c['trusted_additions']) & set(c['selected_ids'])
        assert set(c['before']['delivered_ids']) == set(c['selected_ids'])
        assert set(c['after']['delivered_ids']) == set(c['selected_ids']) | set(c['trusted_additions'])
        assert c['before']['investigator_selected_count'] == c['after']['investigator_selected_count']


def test_known_fixture_and_conservative_hold_cost(comparison):
    c = comparison['known_regression']
    assert c['before']['investigator_selected_count'] == 4
    assert c['after']['python_added_count'] == 5
    assert c['before']['candidate_coverage']['n'] == 0
    assert c['after']['candidate_coverage']['n'] == 5
    assert c['before']['analysis_ready']
    # Stricter metric identity deliberately no longer assumes an unqualified
    # max_age bound applies to cache_age merely because both use seconds.
    # This is a recorded new conservative hold, not evidence-selection loss.
    assert not c['after']['analysis_ready']
    assert c['after']['root_input_estimate']['tokens'] > c['before']['root_input_estimate']['tokens']
    summary = comparison['development_summary']['after']
    assert summary['held_generator_ambiguous'] == 16
    assert summary['held_generator_complete'] > 0  # Expose over-conservative holds.
