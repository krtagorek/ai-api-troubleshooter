"""Approval-gated live transport; importing/preparing this module makes no calls."""
import fcntl
import json
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from evaluation.dataset import digest
from evaluation.paired_harness import _run
from evaluation.provider import MeteredOpenAI, configuration_check, provider_schema
from evaluation.quality_pilot import prepare, sha
from evaluation.runner import ConnectivityReply
from evaluation.paired_accounting import CARRIED_SPEND


class LiveContext:
    def __init__(self, manifest):
        self.manifest = manifest
        self.seen = set()
        self.current = None
        self.dispatch_guard = None

    def start(self, budget, audit):
        if self.dispatch_guard is None:
            raise ValueError('Authorized RUNNING dispatch claim required before provider initialization')
        self.dispatch_guard()
        self.budget, self.audit = budget, audit
        def journal():
            if budget.calls: budget.calls[-1].setdefault('key', self.current)
            audit.append('actual_provider_accounting', calls=budget.calls, usage=budget.summary(),
                         carried_prior_spend_usd=str(CARRIED_SPEND),
                         aggregate_conservative_spend_usd=str(CARRIED_SPEND+budget.reserved_or_spent))
        self.provider = MeteredOpenAI(self.manifest['model']['name'], budget, journal)
        answer = self.boundary('connectivity_preflight').with_structured_output(ConnectivityReply).invoke([
            SystemMessage(content='Return the requested structured connectivity response.'),
            HumanMessage(content='Return connectivity=true.')])
        if answer.connectivity is not True: raise ValueError('Connectivity preflight failed')

    def boundary(self, key):
        owner = self
        class Boundary:
            called = False
            succeeded = False
            failed = False
            def with_structured_output(self, schema, **options):
                self.schema = schema
                self.client = owner.provider.with_structured_output(schema, **options)
                return self
            def invoke(self, messages):
                if key in owner.seen: raise ValueError('Repeated call prohibited')
                owner.seen.add(key)
                owner.current = key
                self.called = True
                try:
                    if owner.dispatch_guard is not None:
                        owner.dispatch_guard()
                    if any(sha(path) != expected for path,expected in owner.manifest['bindings_sha256'].items()):
                        raise ValueError('Bound input/code/review changed during execution')
                    if not configuration_check()['ready']:
                        raise ValueError('Provider configuration changed')
                    result = self.client.invoke(messages)
                    import jsonschema
                    wire = provider_schema(self.schema, messages)
                    wire = wire.model_json_schema() if isinstance(wire,type) else wire
                    jsonschema.validate(result.model_dump(mode='json'), wire)
                    if self.schema.__name__ == 'ValidatorOutput' and (
                        result.validation.status != 'NOT_TESTED' or result.validation.checks):
                        raise ValueError('Unexecuted validation cannot pass')
                    if owner.budget.calls[-1].get('provider_model') != owner.manifest['model']['name']:
                        row = owner.budget.calls[-1]
                        estimated = Decimal(row['observed_usd'])
                        owner.budget.observed_cost -= estimated
                        owner.budget.reserved_or_spent += Decimal(row['reserved_usd'])-estimated
                        row.update(status='unexpected_model_cost_unresolved', observed_usd=None)
                        owner.budget.stopped = 'unexpected_model'
                        raise ValueError('Unexpected provider model')
                    self.succeeded = True
                    return result
                except Exception:
                    self.failed = True
                    raise
        return Boundary()

    def summary(self):
        return {'mode':'REAL_PROVIDER', 'actual_provider_calls':len(self.budget.calls),
                'actual_usage':self.budget.summary(),
                'new_usage_priced_estimated_spend_usd':str(self.budget.observed_cost),
                'carried_prior_spend_usd':str(CARRIED_SPEND),
                'aggregate_conservative_spend_usd':str(CARRIED_SPEND+self.budget.reserved_or_spent),
                'cost_basis':'Provider-reported usage priced at verified rates, not an invoice'}


def validate_pricing(record, manifest):
    b = manifest['budget']
    expected = {'model':manifest['model']['name'], 'source':b['pricing_source'],
                'input_usd_per_million':b['input_usd_per_million'],
                'output_usd_per_million':b['output_usd_per_million']}
    if any(record.get(k) != v for k,v in expected.items()):
        raise ValueError('Pricing mismatch; stop before dispatch')
    timestamp = datetime.fromisoformat(record['verified_at'].replace('Z','+00:00'))
    now = datetime.now(timezone.utc)
    if timestamp.tzinfo is None or not now-timedelta(hours=24) <= timestamp <= now:
        raise ValueError('Fresh timezone-aware pricing verification required')
    if not isinstance(record.get('verified_by'),str) or not record['verified_by'].strip():
        raise ValueError('Pricing verification identity required')


def run_approved(manifest, approval, pricing_verification, output):
    """Only explicit user approval of a READY manifest can enter paid transport.

    pricing_verification is a trusted operator's fresh official-rate verification,
    not an LLM/tool-supplied authorization. This function never verifies rates by
    making an inference call. No CLI auto-dispatch option exists.
    """
    def gate():
        if digest({k:v for k,v in manifest.items() if k!='manifest_sha256'}) != manifest.get('manifest_sha256'):
            raise ValueError('Invalid manifest digest')
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
        if manifest != expected: raise ValueError('Manifest hashes or history changed')
        if approval != {'manifest_sha256':manifest['manifest_sha256'],
                        'maximum_new_spend_usd':manifest['budget']['maximum_new_reservations_usd'],
                        'explicit_user_approval':True}:
            raise ValueError('Exact fresh user approval required')
        if not manifest['reference_review_validation']['complete']:
            raise ValueError('Human reference review incomplete')
        if Decimal(manifest['budget']['aggregate_conservative_ceiling_usd']) > Decimal('2'):
            raise ValueError('Aggregate budget exceeded')
        validate_pricing(pricing_verification, manifest)
        if not configuration_check()['ready']: raise ValueError('Private provider configuration blocked')
    gate()  # Fail without locks/files/provider initialization on missing prerequisites.
    reports = Path('reports')
    with (reports/'.v2-aggregate-budget.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        from evaluation.dispatch_lifecycle import DispatchClaim, reject_orphans
        reject_orphans(reports)
        gate()  # Revalidate all hashes/history under the common historical budget lock.
        if Path(output).exists(): raise ValueError('Output already exists')
        marker = reports/('.paired-live-dispatch-'+manifest['manifest_sha256']+'.json')
        claim = DispatchClaim(marker, {
            'manifest_sha256':manifest['manifest_sha256'], 'output':str(Path(output).resolve()),
            'prior_spend_usd':str(CARRIED_SPEND),
            'maximum_new_reservations_usd':manifest['budget']['maximum_new_reservations_usd'],
            'pricing_verification':pricing_verification})
        try:
            claim.transition('RUNNING')
            claim.guard()
            context = LiveContext(manifest)
            context.dispatch_guard = claim.guard
            result = _run(manifest, {}, output, live=context)
            claim.guard()
            claim.transition('COMPLETED')
            return result
        except BaseException:
            claim.transition('FAILED')
            raise
        finally:
            claim.close()
