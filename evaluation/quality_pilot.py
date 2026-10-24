"""Prepare a paired development pilot. No provider or dispatch entry point."""
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from agents.log_investigator import LIVE_SYSTEM_PROMPT
from data.benchmark_v2.build import DEFAULT, rows
from evaluation.dataset import digest
from graph.state import LiveLogInvestigatorOutput, RootCauseAnalystOutput
from evaluation.provider import strict_root_schema
from models.incident import Incident
from tools.evidence_catalog import handle_catalog
from evaluation.paired_accounting import CARRIED_SPEND, verify_reconciliation, LATEST_RECONCILIATION

OUTPUT = Path('reports/diagnostic-quality-paired-pilot')
LEDGER = Path('reports/phase1312-minimal-real-run/evaluation.json')
REFERENCE_REVIEW = OUTPUT/'human-review-worksheet.json'
CASES = [('V2-01-01', 'development_complete'),
         ('V2-03-04', 'development_ambiguous')]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare():
    from evaluation.paired_review import validate_reference_review
    ledger = json.loads(LEDGER.read_text())
    paid_history = {str(p):sha(p) for p in sorted(set(Path('reports').rglob('evaluation.json')) | set(Path('reports').glob('.*dispatch*.json')))}
    verify_reconciliation(OUTPUT,sha,paid_history)
    if (Decimal(ledger['usage']['conservative_spend_usd']) != Decimal('.0260008')
        or ledger['usage']['unresolved_calls'] != 0
        or Decimal(ledger['carried_prior_spend_usd']) + Decimal(ledger['new_estimated_cost_usd']) != Decimal('.0260008')):
        raise ValueError('Historical spending requires reconciliation')
    development = {r['incident_id']: r for r in rows(DEFAULT/'inference/development.jsonl')}
    known = ledger['cases'][0]['sanitized_output']['sanitized_inputs']
    if known['incident_id'] != 'V2-02-01':
        raise ValueError('Known regression fixture mismatch')
    selected = []
    for ident, role in CASES:
        incident = Incident.model_validate(known if ident == 'V2-02-01' else development[ident])
        selected.append({'incident_id': ident, 'role': role,
                         'sanitized_input_sha256': digest(incident.model_dump(mode='json')),
                         'catalog_sha256': digest(handle_catalog(incident))})
    # Explicit reconstruction using today's five-field contract, not a claim of
    # exact historical prompt or full-stack historical baseline reproduction.
    start = LIVE_SYSTEM_PROMPT.index('Prioritize observations')
    end = LIVE_SYSTEM_PROMPT.index('Treat all supplied evidence')
    baseline = LIVE_SYSTEM_PROMPT[:start] + LIVE_SYSTEM_PROMPT[end:]
    per_call = (Decimal(32768)*Decimal('.40') + Decimal(1536)*Decimal('1.60'))/Decimal(1000000)
    files = []
    for folder in ('agents','graph','models','tools','evaluation'):
        files += list(Path(folder).rglob('*.py'))
    files += list(Path('data/benchmark_v2').rglob('*.json'))
    files += list(Path('data/benchmark_v2').rglob('*.jsonl'))
    files += list(Path('data').glob('*.jsonl'))
    files += list(Path('tests').glob('*.py'))
    files += [p for p in (Path('requirements.txt'), Path('uv.lock'), Path('pyproject.toml')) if p.exists()]
    files += [LEDGER, Path('reports/phase1312-minimal-real-run/postexecution-human-review.json'),
              Path('reports/phase136-v2/pre-execution-label-review.json')]
    files += [Path('reports/diagnostic-quality-paired-live-run/audit.jsonl'),
              Path('reports/diagnostic-quality-paired-live-run/summary.json')]
    files += list(OUTPUT.glob('*.json')) if OUTPUT.exists() else []
    # Bind review protocol/templates, but not this generated self-hashed manifest.
    files = [p for p in files if p.name != 'manifest.json']
    manifest = {
        'version': 'diagnostic-quality-paired-live-4',
        'status': 'PROPOSED_NOT_APPROVED_NOT_EXECUTED',
        'execution_enabled': False, 'paid_approval_granted': False,
        'cases': selected,
        'model': {'name': 'gpt-4.1-mini-2025-04-14', 'temperature': 0,
                  'max_output_tokens': 1536, 'input_reservation_cap': 32768,
                  'strict_investigator_schema': LiveLogInvestigatorOutput.model_json_schema(),
                  'strict_root_provider_schema_template': strict_root_schema(RootCauseAnalystOutput),
                  'strict_root_structured_output': True,
                  'schema_fields': list(LiveLogInvestigatorOutput.model_fields),
                  'retries': 0, 'seed': None,
                  'reproducibility': 'Frozen inputs/configuration; provider outputs not guaranteed deterministic'},
        'conditions': {
            'baseline': {'investigator_prompt': baseline, 'trusted_augmentation': False,
                         'description': 'Reconstructed minimal strict five-field selection baseline, not exact historical stack'},
            'enhanced': {'investigator_prompt': LIVE_SYSTEM_PROMPT, 'trusted_augmentation': True,
                         'maximum_added_facts': 32}},
        'shared_controls': ['Identical classifier/root-cause/resolution/validator prompts and schemas',
                            'Current strict grounding, provenance, incident-snapshot handles and redaction',
                            'Current independent sufficiency routing in BOTH conditions; no forced root-cause calls',
                            'No tools, fixes, production access or authorization bypass; validator NOT_TESTED if reached',
                            'No labels, rubric, reviewer notes or role/family tags in inference payloads'],
        'schedule': [{'incident_id': ident, 'conditions': ['baseline','enhanced'] if n%2==0 else ['enhanced','baseline']}
                     for n,(ident,_) in enumerate(CASES)],
        'execution_protocol': 'One preflight total, independent state per arm, sequential, each node at most once; no investigation/validation loops or retries. Stop entire pilot on first technical/safety failure. Legitimate abstention/hold is an outcome, not a technical failure.',
        'budget': {'aggregate_usd': '2', 'prior_estimated_spend_usd': str(CARRIED_SPEND),
                   'input_usd_per_million': '0.40','output_usd_per_million': '1.60',
                   'pricing_source': 'https://developers.openai.com/api/docs/models/gpt-4.1-mini',
                   'pricing_checked_date': '2026-10-22', 'reverify_before_dispatch': True,
                   'maximum_new_calls_including_preflight': 21,
                   'reservation_per_call_usd': str(per_call),
                   'maximum_new_reservations_usd': str(per_call*21),
                   'aggregate_conservative_ceiling_usd': str(CARRIED_SPEND+per_call*21),
                   'planning_estimate_usd': '0.042',
                   'estimate_basis': 'Illustrative 21 calls x (3000 input + 500 output tokens); not measured usage or a forecast guarantee; early holds reduce calls. No cache discount assumed.'},
        'stop_conditions': ['No explicit approval of this manifest and maximum spend',
                            'Any hash, ledger, model or pricing mismatch',
                            'Request overhead exceeds reserved input cap or budget/call cap unavailable',
                            'Missing usage, transport/provider/schema error, unsupported evidence reference',
                            'Secret/label leakage, fabricated outcome, authorization or safety violation',
                            'Any unapproved case/tool/node repeat; never retry or continue after failure'],
        'measurement': {'actuals': 'Separate append-only per-call provider response, reported input/output/cached tokens, latency, request bytes, node/arm/case and usage-priced estimated spend; invoice cost not claimed',
                        'reservations': 'Reserve before EVERY call including preflight; preserve full reservation and stop on uncertain usage; lock and reconcile original aggregate ledger without overwriting history',
                        'offline_estimates': 'Store separately from provider usage; include complete serialized messages/schema/envelope and safety margin before dispatch',
                        'evidence': 'Separate full source, original provider selection, Python additions, omissions, final cited evidence and trusted readiness',
                        'coverage': 'Actual execution events only; skipped/failed/unreviewed are not incorrect diagnoses; paired comparisons only when both arms have comparable output'},
        'reference_review_path': str(REFERENCE_REVIEW),
        'usage_audit_contract': 'paired-numeric-counters-1: trusted accounting paths only, nonnegative integers, no generic secret-redaction exemption',
        'budget_reconciliation_path': str(LATEST_RECONCILIATION),
        'reference_review_validation': validate_reference_review(REFERENCE_REVIEW, [c[0] for c in CASES]),
        'paid_history_sha256': paid_history,
        'dispatch_gate': 'Live transport integrated but gated by complete human references, exact fresh approval, unchanged hashes/history, fresh matching pricing verification, private configuration and global budget lock. No paid calls authorized by preparation.',
        'bindings_sha256': {str(p): sha(p) for p in sorted(set(files)) if p.exists()},
    }
    manifest['manifest_sha256'] = digest(manifest)
    return manifest
