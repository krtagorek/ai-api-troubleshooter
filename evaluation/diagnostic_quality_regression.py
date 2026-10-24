"""Offline routing replay. Never dispatches, rewrites labels, or grades accuracy."""
from dataclasses import asdict
import json
from pathlib import Path

from agents import log_investigator
from data.benchmark_v2.build import DEFAULT, rows
from evaluation.paired_accounting import verify_reconciliation
from evaluation.dataset import digest
from evaluation.quality_pilot import OUTPUT, sha
from evaluation.selection_comparison import RecordedOutput
from graph.state import initialize_state, validate_state
from graph.routing import evidence_sufficient
from models.incident import Incident
from tools.counterevidence import validate_counterevidence
from tools.diagnostic_sufficiency import readiness
from tools.evidence_catalog import handle_catalog
from tools.evidence_selection import assess_selection


def old_gate(state):
    evidence = state['evidence']
    return bool(evidence.items) and evidence.completeness == 'complete' and not evidence.missing and assess_selection(
        state['sanitized_inputs'], [e.evidence_id for e in evidence.items], max_added=0).analysis_ready


def describe(state):
    return {'incident_id': state['incident_id'], 'before_ready': old_gate(state),
            'after_ready': evidence_sufficient(state),
            'trusted_readiness': asdict(readiness(state['sanitized_inputs'], state['evidence'])),
            'delivered_count': len(state['evidence'].items), 'completeness': state['evidence'].completeness}


def run():
    manifest = json.loads((OUTPUT / 'manifest.json').read_text())
    frozen = {p: expected for p, expected in manifest['bindings_sha256'].items()
              if p.startswith('data/benchmark_v2/') or p == str(OUTPUT / 'human-review-worksheet.json')}
    if not frozen or any(sha(p) != expected for p, expected in frozen.items()):
        raise ValueError('Frozen dataset or approved reference review changed')
    packet = json.loads(Path('reports/paired-redaction-review/reviewer-packet.json').read_text())
    if digest(packet) != 'a4dbd7642b12756864a7b987e4facb8c54937ff79d15aaea43cf33edaf8abd3f':
        raise ValueError('Blinded output packet changed')
    history = {str(p): sha(p) for p in sorted(set(Path('reports').rglob('evaluation.json')) |
                                            set(Path('reports').glob('.*dispatch*.json')))}
    verify_reconciliation(OUTPUT, sha, history)
    historical = []
    audit = Path('reports/diagnostic-quality-paired-live-v2/audit.jsonl')
    for line in audit.read_text().splitlines():
        event = json.loads(line)
        if event['kind'] != 'arm_completed':
            continue
        state = validate_state(event['sanitized_result']['state'])
        record = describe(state)
        record['recorded_arm'] = event['key']
        record['interpretation'] = 'Immutable known development regression, not independent holdout'
        try:
            validate_counterevidence(state['hypotheses'], state['evidence'])
            record['current_counterevidence_validation'] = 'PASS'
        except ValueError as exc:
            record['current_counterevidence_validation'] = str(exc)
        historical.append(record)
    development = []
    for row in rows(DEFAULT / 'inference/development.jsonl'):
        incident = Incident.model_validate(row)
        state = initialize_state(incident, 'offline-routing-comparison', max_retries=0)
        catalog = handle_catalog(incident)
        status = next(e['handle'] for e in catalog['evidence_catalog'] if e['reference'] == '/status_code')
        output = {'evidence_contract': catalog['evidence_contract'], 'evidence_snapshot': catalog['evidence_snapshot'],
                  'selected_evidence_handles': [status], 'completeness': 'complete', 'diagnostic_gaps': []}
        state.update(log_investigator.log_investigator(state, llm=RecordedOutput(output)))
        record = describe(state)
        record['generator_quality'] = incident.evidence_quality
        development.append(record)
    protected = set(DEFAULT.rglob('*.jsonl')) | {audit,
        Path('reports/diagnostic-quality-paired-live-run/audit.jsonl'),
        Path('reports/paired-redaction-review/human-review.json'),
        Path('reports/paired-redaction-review/reviewer-packet.json'),
        OUTPUT / 'human-review-worksheet.json'}
    return {'mode': 'OFFLINE_ONLY', 'paid_calls': 0, 'dispatch_prepared': False,
        'carried_estimated_spend_usd': '0.0550552', 'aggregate_budget_usd': '2',
        'budget_audit_reconciliation': 'PASS', 'frozen_bindings_validation': 'PASS',
        'known_regression': historical, 'development': development,
        'summary': {'cases': len(development),
            'before_ready': sum(c['before_ready'] for c in development),
            'after_ready': sum(c['after_ready'] for c in development),
            'newly_held': [c['incident_id'] for c in development if c['before_ready'] and not c['after_ready']],
            'newly_ready': [c['incident_id'] for c in development if not c['before_ready'] and c['after_ready']],
            'after_ready_generator_ambiguous': [c['incident_id'] for c in development
                if c['after_ready'] and c['generator_quality'] == 'ambiguous']},
        'protected_sha256': {str(p): sha(p) for p in sorted(protected)},
        'limitations': ['Development status-only stress responses are injected, not new real-model outputs.',
            'Identical augmented evidence is used on both sides to isolate the routing change.',
            'Readiness is permission to attempt qualified reasoning, never causal correctness or confirmation.',
            'Generator sufficiency flags are not human relevance labels; no new human ratings are inferred.',
            'No before/after diagnostic accuracy, semantic counterevidence score, or real token usage is measured.',
            'Legacy local evidence identifiers retain historical routing compatibility; new contracts use snapshot-bound catalog entries.',
            'Comparison vocabulary and credential-request detection are conservative finite rules, not general semantic proof.']}


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
