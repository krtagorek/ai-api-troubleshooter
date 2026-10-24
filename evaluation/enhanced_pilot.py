"""Prepare the enhanced-only development pilot; never dispatch or approve it."""
import copy
import json
from decimal import Decimal
from pathlib import Path

from evaluation.dataset import digest
from evaluation.quality_pilot import prepare as paired_prepare, sha
from evaluation.diagnostic_quality_regression import run as offline_regression

VERSION = 'diagnostic-quality-enhanced-live-1'
OUTPUT = Path('reports/diagnostic-quality-enhanced-pilot')


def prepare():
    regression = offline_regression()  # Verify frozen artifacts and carry-forward proof.
    if not all(c['after_ready'] for c in regression['known_regression']):
        raise ValueError('Known qualified-analysis regression failed')
    ambiguous = next(c for c in regression['development'] if c['incident_id'] == 'V2-03-04')
    if ambiguous['after_ready']:
        raise ValueError('Ambiguous development case must remain held')
    manifest = copy.deepcopy(paired_prepare())
    manifest.pop('manifest_sha256')
    manifest['version'] = VERSION
    manifest['conditions'] = {'enhanced': manifest['conditions']['enhanced']}
    manifest['schedule'] = [{'incident_id': c['incident_id'], 'conditions': ['enhanced']} for c in manifest['cases']]
    manifest['shared_controls'] = [
        'Enhanced workflow only; each approved incident has fresh isolated state.',
        'Strict five-field investigator and strict root-cause provider schemas; application validation remains mandatory.',
        'Snapshot-bound handles, trusted provenance/grounding and structured JSON-safe redaction.',
        'Readiness permits tentative analysis only; missing confirmation is not automatically a hold.',
        'Conflicting/ambiguous observations remain held; no counts or coverage alone establish sufficiency.',
        'No external diagnostic tools, collection, fixes, production access or loops; human approval always required.',
        'Validation remains NOT_TESTED without an executed validation tool.',
        'Inference receives sanitized incident evidence only, never labels, rubric, review notes or case role metadata.']
    b = manifest['budget']
    per_call = Decimal(b['reservation_per_call_usd'])
    b.update(maximum_new_calls_including_preflight=11,
             maximum_new_reservations_usd=str(per_call * 11),
             aggregate_conservative_ceiling_usd=str(Decimal(b['prior_estimated_spend_usd']) + per_call * 11),
             pricing_checked_date=None, pricing_status='OFFLINE_PLANNING_RATES_UNVERIFIED_FOR_NEW_DISPATCH',
             model_access_status='NOT_CHECKED_OFFLINE; approved real preflight must verify returned model identity',
             planning_estimate_usd='0.016',
             estimate_basis='Illustrative eight calls x (3000 input + 500 output tokens) at planning rates; not measured usage or a prediction. Eight assumes five agents for V2-01-01, two for V2-03-04, plus preflight. Hard ceiling remains eleven calls. No cache discount.')
    manifest['execution_protocol'] = ('One real connectivity preflight only after fresh explicit approval, then V2-01-01 enhanced, then V2-03-04 enhanced. Each of five nodes at most once per incident; at most 11 calls including preflight. No retries/loops. Stop entire pilot at first technical, grounding, budget or safety failure. Legitimate evidence holds and justified abstentions are valid outcomes.')
    manifest['dispatch_lifecycle'] = {
        'version': 'append-only-single-use-1',
        'states': ['PREPARED', 'CLAIMED', 'RUNNING', 'COMPLETED', 'FAILED'],
        'validation': 'Fresh manifest, code/dataset/review hashes, historical accounting, explicit approval and pricing verified twice before exclusive claim; second verification under aggregate budget lock.',
        'current_claim': 'No preparation after claim. Before each provider call, require exact approved history plus the one immutable current marker and a valid RUNNING lifecycle chain.',
        'atomicity': 'Aggregate OS file lock held throughout dispatch; exclusive fsynced claim reserves full pilot ceiling. Hash-chained fsynced lifecycle and per-call reservation audits are append-only.',
        'recovery': 'Never resume or retry automatically. Crashed, partial, failed and completed claims remain single-use; manual usage reconciliation and a fresh manifest/approval are required for any future dispatch.',
        'filesystem_scope': 'Local filesystem with working flock, exclusive create and fsync; not a distributed queue or network-filesystem lease.'}
    manifest['measurement']['coverage'] = 'Persist actual node_started/node_completed events; skipped/failed nodes are not diagnoses. Preserve uncertainty and evidence_assessments. No accuracy claim before human semantic review.'
    manifest['human_review_template_path'] = str(OUTPUT / 'human-review-template.json')
    manifest['offline_readiness'] = {
        'historical_enhanced_v20101': 'qualified analysis, not confirmed diagnosis',
        'v20304': 'held in deterministic status-only stress replay',
        'budget_audit': regression['budget_audit_reconciliation'],
        'frozen_bindings': regression['frozen_bindings_validation'],
        'real_model_quality': 'NOT_VERIFIED'}
    extra = [Path('reports/paired-redaction-review/human-review.json'),
             Path('reports/paired-redaction-review/reviewer-packet.json'),
             Path('reports/diagnostic-quality-paired-live-v2/audit.jsonl'),
             Path('reports/diagnostic-quality-paired-live-v2/summary.json'),
             Path('reports/diagnostic-quality-paired-pilot/manifest.json'),
             OUTPUT / 'manifest.json',
             OUTPUT / 'dispatch-blocked-before-preflight.json',
             OUTPUT / 'human-review-template.json']
    if not extra[-1].exists():
        raise ValueError('Prepare blank human-review template before manifest')
    manifest['bindings_sha256'].update({str(p): sha(p) for p in extra})
    manifest['manifest_sha256'] = digest(manifest)
    return manifest


def blank_review():
    rubric_path = Path('reports/diagnostic-quality-paired-pilot/human-review-rubric.json')
    rubric = json.loads(rubric_path.read_text())
    dimensions = {**rubric['frozen_v2_dimensions'], **rubric['supplemental_dimensions']}
    return {'status': 'PENDING_ACTUAL_OUTPUT_AND_HUMAN_REVIEW',
            'protocol': 'Exploratory development pilot; no independent benchmark accuracy claim',
            'rubric_path': str(rubric_path), 'rubric_sha256': sha(rubric_path),
            'reviews': [{'incident_id': ident, 'condition': 'enhanced', 'output_sha256': None,
                         'reviewer_id': None, 'timestamp': None,
                         'dimension_ratings': {k: None for k in dimensions},
                         'rationale': None, 'hold_adjudication': None,
                         'unsafe_credential_request': None} for ident in ('V2-01-01', 'V2-03-04')]}


if __name__ == '__main__':
    OUTPUT.mkdir(exist_ok=True)
    # Exclusive creation preserves artifacts from every preparation.
    template = OUTPUT / 'human-review-template.json'
    if template.exists():
        if json.loads(template.read_text()) != blank_review():
            raise ValueError('Existing review template differs; never overwrite human material')
    else:
        with template.open('x') as stream:
            json.dump(blank_review(), stream, indent=2)
    manifest = prepare()
    with (OUTPUT / ('manifest-' + manifest['manifest_sha256'] + '.json')).open('x') as stream:
        json.dump(manifest, stream, indent=2)
    print(json.dumps({'manifest_sha256': manifest['manifest_sha256'], 'budget': manifest['budget']}))
