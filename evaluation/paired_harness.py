"""Offline CLI and shared finite paired engine.

run_offline uses JSON fixtures and simulated reservations only. The separate
paired_live dispatcher supplies live transport only after its approval gates.
"""
import copy
import json
import os
from decimal import Decimal
from pathlib import Path
import time
import secrets

from agents import error_classifier, log_investigator, root_cause, resolution, validator
from agents.common import ask, checked_update, classification_or_unknown
from data.benchmark_v2.build import DEFAULT, rows
from evaluation.budget import Budget
from evaluation.audit_metrics import accounting_event, artifact
from evaluation.paired_accounting import CARRIED_SPEND
from evaluation.dataset import digest
from evaluation.provider import provider_schema
from evaluation.quality_pilot import prepare
from graph.routing import evidence_sufficient, route_diagnosis
from graph.state import initialize_state, validate_state, serialize_state, LogInvestigatorInput, LiveLogInvestigatorOutput
from models.evidence import Evidence, EvidenceCollection, EvidenceGap
from tools.evidence_catalog import handle_catalog, resolve_handles
from tools.evidence_selection import assess_selection
from tools.redaction import redact


class Audit:
    """Single-owner exclusive journal; append-only, fsynced, hash chained."""
    def __init__(self, path):
        self.fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_APPEND, 0o600)
        self.previous = None
        self.sequence = 0

    def append(self, kind, **data):
        row = {'sequence': self.sequence, 'previous': self.previous, 'kind': kind, **accounting_event(kind,data)}
        row['sha256'] = digest(row)
        with os.fdopen(os.dup(self.fd), 'a') as f:
            f.write(json.dumps(row, sort_keys=True) + '\n')
            f.flush()
            os.fsync(f.fileno())
        self.previous = row['sha256']
        self.sequence += 1

    def close(self):
        os.close(self.fd)


def _baseline(state, llm, prompt):
    state = validate_state(state)
    # Same log-size check as enhanced; no legacy output or provenance adapter.
    from tools.log_parser import parse_logs
    if parse_logs(state['sanitized_inputs'].logs).status == 'error':
        raise ValueError('Unsafe log size')
    output = ask(llm, LiveLogInvestigatorOutput, prompt, LogInvestigatorInput(
        incident=state['sanitized_inputs'], classification=classification_or_unknown(state),
        **handle_catalog(state['sanitized_inputs'])))
    entries = resolve_handles(state['sanitized_inputs'], output.selected_evidence_handles,
                              output.evidence_contract, output.evidence_snapshot)
    assessment = assess_selection(state['sanitized_inputs'], [e.evidence_id for e in entries], max_added=0)
    gaps = list(output.diagnostic_gaps)
    completeness = output.completeness if entries else 'missing'
    if completeness == 'complete' and not assessment.analysis_ready:
        completeness = 'partial'
        gaps.append(EvidenceGap(description='Trusted coverage check cannot establish diagnostic sufficiency',
                                required_to_resolve='Collect specific correlated diagnostics or review omitted comparison evidence'))
    if not entries and not gaps:
        gaps.append(EvidenceGap(description='No grounded observations selected', required_to_resolve='Collect sanitized diagnostic evidence'))
    return checked_update(state, {'evidence': EvidenceCollection(completeness=completeness, missing=gaps,
        items=[Evidence(evidence_id=e.evidence_id, reference=e.reference, source=e.source,
                        observation=e.observation, reliability=.5) for e in entries])})


class FixtureBoundary:
    def __init__(self, response, budget, audit, key):
        self.response, self.budget, self.audit, self.key = response, budget, audit, key
        self.called = False
        self.failed = False
        self.succeeded = False

    def with_structured_output(self, schema, **options):
        self.schema = schema
        if schema.__name__ in ('LiveLogInvestigatorOutput','RootCauseAnalystOutput') and options != {'method':'json_schema','strict':True}:
            raise ValueError('Strict schema required')
        return self

    def invoke(self, messages):
        if self.called:
            raise ValueError('Repeated node call prohibited')
        self.called = True
        started = time.monotonic()
        schema = provider_schema(self.schema, messages)
        schema = schema.model_json_schema() if isinstance(schema, type) else schema
        envelope = {'model':'gpt-4.1-mini-2025-04-14', 'messages':[
            {'role':m.type,'content':m.content} for m in messages], 'response_format':schema,
            'temperature':0, 'max_completion_tokens':1536, 'store':False}
        size = len(json.dumps(envelope, ensure_ascii=False).encode())
        if size + 4096 > self.budget.input_cap:
            raise ValueError('Complete offline request envelope exceeds reservation')
        row = self.budget.reserve()
        self.audit.append('simulated_reservation', key=self.key, reserved_usd=row['reserved_usd'],
                          request_bytes=size, envelope_margin_bytes=4096)
        try:
            if self.response is None:
                raise ValueError('Missing fixture; no provider fallback')
            # Strict live wire-schema enum/additionalProperties validation too.
            import jsonschema
            jsonschema.validate(self.response, schema)
            result = self.schema.model_validate(copy.deepcopy(self.response))
            if self.schema.__name__ == 'ValidatorOutput' and (
                result.validation.status != 'NOT_TESTED' or result.validation.checks
            ):
                raise ValueError('Unexecuted validation must be NOT_TESTED')
            self.audit.append('fixture_response', key=self.key, fixture_output=self.response,
                              original_output_sha256=digest(self.response), actual_provider_usage=None,
                              offline_latency_seconds=time.monotonic()-started)
            self.succeeded = True
            return result
        except Exception:
            self.failed = True
            raise


def run_offline(manifest, fixtures, output):
    """fixtures: JSON object keyed CASE/ARM/NODE; no callables/LLM injection."""
    return _run(manifest, fixtures, output)


def _run(manifest, fixtures, output, *, live=None):
    """Shared finite execution path; live context supplied only by gated dispatcher."""
    # The dispatcher validates under the budget lock BEFORE claiming approval.
    # Preparation after that claim would mistake our marker for new history.
    if live is None:
        if manifest.get('version') == 'next-controlled-v2-preparation-1':
            from evaluation.next_v2_pilot import prepare as prepare_next
            expected = prepare_next()
        elif manifest.get('version') == 'rc-v2-test-two-case-1':
            from evaluation.rc_benchmark import prepare as prepare_rc
            expected = prepare_rc()
        elif manifest.get('version') == 'diagnostic-quality-enhanced-live-1':
            from evaluation.enhanced_pilot import prepare as prepare_enhanced
            expected = prepare_enhanced()
        elif manifest.get('version') == 'diagnostic-quality-paired-live-4':
            expected = prepare()
        else:
            raise ValueError('Unsupported proposal version')
        if manifest != expected:
            raise ValueError('Stale or altered manifest')
    if type(fixtures) is not dict:
        raise ValueError('JSON fixtures required')
    fixtures = json.loads(json.dumps(fixtures, allow_nan=False))
    allowed = {f"{s['incident_id']}/{arm}/{node}" for s in manifest['schedule']
               for arm in s['conditions'] for node in ('classifier','investigator','root_cause','resolution','validator')}
    if not set(fixtures) <= allowed:
        raise ValueError('Unapproved case, arm or node')
    output = Path(output)
    output.mkdir(mode=0o700)  # Exclusive: never overwrite prior runs.
    audit = Audit(output/'audit.jsonl')
    b = manifest['budget']
    budget = Budget(Decimal(b['maximum_new_reservations_usd']), Decimal(b['input_usd_per_million']),
                    Decimal(b['output_usd_per_million']), b['maximum_new_calls_including_preflight'])
    results, mapping = [], []
    try:
        audit.append('run_start', manifest_sha256=manifest['manifest_sha256'],
                     mode='REAL_PROVIDER' if live else 'OFFLINE_FIXTURES_NOT_REAL_MODEL')
        if live:
            live.start(budget, audit)
        else:
            reservation = budget.reserve()  # Exercise allocation; no connectivity call.
            audit.append('simulated_preflight_reservation', connectivity='NOT_TESTED', reserved_usd=reservation['reserved_usd'])
        if manifest.get('version') == 'next-controlled-v2-preparation-1':
            from evaluation.next_v2_pilot import inputs as next_inputs
            source = {i.incident_id:i.model_dump(mode='json') for i in next_inputs()}
        elif manifest.get('version') == 'rc-v2-test-two-case-1':
            from evaluation.rc_benchmark import inputs as rc_inputs
            source = {i.incident_id:i.model_dump(mode='json') for i in rc_inputs()}
        else:
            source = {r['incident_id']:r for r in rows(DEFAULT/'inference/development.jsonl')}
        for schedule in manifest['schedule']:
            ident = schedule['incident_id']
            for arm in schedule['conditions']:
                prefix = 'paired-live' if live else 'offline'
                state = initialize_state(copy.deepcopy(source[ident]), f'{prefix}-{ident}-{arm}', max_retries=0)
                state['available_evidence_sources'] = []
                seen = []
                for node, agent in [('classifier',error_classifier.error_classifier),
                                    ('investigator',log_investigator.log_investigator),
                                    ('root_cause',root_cause.root_cause),
                                    ('resolution',resolution.resolution), ('validator',validator.validator)]:
                    if node == 'root_cause' and not evidence_sufficient(state): break
                    if node == 'resolution' and route_diagnosis(state) != 'resolution_generator': break
                    if node == 'validator' and not state['proposed_fix']: break
                    key = f'{ident}/{arm}/{node}'
                    boundary = live.boundary(key) if live else FixtureBoundary(fixtures.get(key), budget, audit, key)
                    audit.append('node_started', key=key)
                    if node == 'investigator' and arm == 'baseline':
                        update = _baseline(state, boundary, manifest['conditions'][arm]['investigator_prompt'])
                    else:
                        update = agent(state, llm=boundary)
                    # Validator's defensive swallowing must not hide a failed call.
                    if boundary.failed or not boundary.succeeded or budget.stopped:
                        raise ValueError('Node boundary failed')
                    state = validate_state({**state, **update})
                    seen.append(node)
                    audit.append('node_completed', key=key)
                outcome = ('validation_not_tested' if 'validator' in seen else
                           'evidence_hold' if 'root_cause' not in seen else
                           'diagnosis_or_plan_incomplete')
                state['status'] = 'awaiting_evidence'
                state['stop_reason'] = outcome
                serialized = json.loads(serialize_state(state))
                blind = 'OUTPUT-' + secrets.token_hex(8).translate(str.maketrans('0123456789','ghijklmnop'))
                result = {'blind_id':blind, 'state':serialized, 'executed_nodes':seen,
                          'analysis_ready':evidence_sufficient(state), 'outcome':outcome, 'review':None}
                results.append(result)
                mapping.append({'blind_id':blind, 'incident_id':ident, 'arm':arm})
                audit.append('arm_completed', key=f'{ident}/{arm}', output_sha256=digest(result),
                             sanitized_result=result)
        # Blinding removes thread IDs and all investigator/Python selection-arm metadata.
        blinded = []
        for result in results:
            clean = copy.deepcopy(result)
            clean['state']['thread_id'] = clean['blind_id']
            blinded.append(clean)
        blinded.sort(key=lambda r:r['blind_id'])
        artifacts = {'outputs.json':results, 'reviewer-outputs.json':blinded,
                     'organizer-key.json':mapping,
                     'human-review.json':{'mode':'REAL_PROVIDER' if live else 'OFFLINE_FIXTURE_REHEARSAL', 'reviews':[
                         {'blind_id':r['blind_id'], 'reviewer_id':None, 'timestamp':None,
                          'ratings':None, 'rationale':None, 'hold_adjudication':None} for r in blinded]},
                     'summary.json':{'mode':'OFFLINE_FIXTURE_REHEARSAL','actual_provider_calls':0,
                                     'actual_new_spend_usd':'0','carried_spend_usd':str(CARRIED_SPEND),
                                     'simulated_calls_reserved':len(budget.calls),
                                     'simulated_reservations_usd':str(budget.reserved_or_spent)}}
        if live:
            artifacts['summary.json'] = live.summary()
            artifacts['human-reference-material.json'] = json.loads(Path(manifest['reference_review_path']).read_text())
        for name, value in artifacts.items():
            with (output/name).open('x') as f:
                os.chmod(output/name, 0o600)
                json.dump(artifact(name,value), f, indent=2)
        audit.append('run_completed', arms=len(results), actual_provider_calls=len(budget.calls) if live else 0)
        return artifacts['summary.json']
    except Exception:
        audit.append('offline_stopped' if live is None else 'live_stopped', reason='First failure; details withheld',
                     calls_reserved=len(budget.calls), conservative_reservations_usd=str(budget.reserved_or_spent))
        raise ValueError('Pilot stopped on first failure; sanitized journal retained') from None
    finally:
        audit.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Offline JSON-fixture rehearsal only; no provider transport')
    parser.add_argument('--offline', action='store_true', required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_offline(json.loads(args.manifest.read_text()),
                                json.loads(args.fixtures.read_text()), args.output), indent=2))
