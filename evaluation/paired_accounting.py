"""Verify append-only carry-forward proof; never rewrite historical ledgers."""
import json
from decimal import Decimal
from pathlib import Path

from evaluation.dataset import digest

CARRIED_SPEND = Decimal('0.0550552')


def verify_reconciliation(output, sha, paid_history):
    original=_read(output/'historical-paid-inputs.json')
    seen_audits=set()
    predecessor=None
    if any(paid_history.get(p)!=expected for p,expected in original['sha256'].items()):
        raise ValueError('Original paid records changed')
    carried=Decimal(original['carried_estimated_spend_usd'])
    for name in ('budget-reconciliation-v2.json','budget-reconciliation-v3.json','budget-reconciliation-v4.json','budget-reconciliation-v5.json'):
        record=_read(output/name)
        if predecessor is not None and record.get('previous_reconciliation_sha256')!=sha(predecessor):
            raise ValueError('Historical predecessor mismatch')
        audit_path=Path(record['audit_path']).resolve()
        if audit_path in seen_audits:raise ValueError('Duplicate dispatch accounting')
        seen_audits.add(audit_path)
        predecessor=output/name
        if sha(output/'historical-paid-inputs.json') != record['original_checkpoint_sha256']:
            raise ValueError('Historical checkpoint changed')
        if any(sha(p)!=expected for p,expected in record['source_sha256'].items()):
            raise ValueError('Reconciliation source changed')
        events=[json.loads(s) for s in Path(record['audit_path']).read_text().splitlines()]
        previous=None
        for n,event in enumerate(events):
            if event['sequence']!=n or event['previous']!=previous or event['sha256']!=digest({k:v for k,v in event.items() if k!='sha256'}):
                raise ValueError('Original audit hash chain failed')
            previous=event['sha256']
        account=next(e for e in reversed(events) if e['kind']=='actual_provider_accounting')
        if account['usage']['unresolved_calls'] or any(c['status']!='observed' for c in account['calls']):
            raise ValueError('Uncertain spending must be reconciled')
        cost=sum((Decimal(c['observed_usd']) for c in account['calls']),Decimal(0))
        if (not cost.is_finite() or cost<0 or cost!=Decimal(record['new_estimated_spend_usd'])
            or Decimal(record['prior_estimated_spend_usd'])!=carried
            or carried+cost!=Decimal(record['aggregate_estimated_spend_usd'])):
            raise ValueError('Carry-forward mismatch')
        carried+=cost
    if sha(LATEST_RECONCILIATION)!=LATEST_RECONCILIATION_SHA256:
        raise ValueError('Integrated reconciliation missing or changed')
    record,carried=_verify_completed_extension(LATEST_RECONCILIATION,predecessor,record,carried,sha,paid_history,seen_audits)
    if paid_history != record['paid_history_sha256']:
        raise ValueError('Unreconciled dispatch/history; stop')
    if carried!=CARRIED_SPEND:
        raise ValueError('Carry-forward mismatch')
    return record


LATEST_RECONCILIATION = Path('reports/next-controlled-v2-live-run/budget-reconciliation.json')
LATEST_RECONCILIATION_SHA256 = 'bba9f06c4d81a002d952b964992bd2a209a3da254cdf3847df4bb296f910ad97'


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate accounting record key')
        result[key] = value
    return result


def _read(path):
    return json.loads(Path(path).read_text(), object_pairs_hook=_unique_object)


def _verify_completed_extension(path, predecessor, previous, carried, sha, paid_history, seen_audits):
    """Verify one explicitly integrated successor, never discover newest by name."""
    from evaluation.dispatch_lifecycle import inspect_claim
    record = _read(path)
    if (Path(record['previous_reconciliation_path']).resolve() != Path(predecessor).resolve()
            or record['previous_reconciliation_sha256'] != sha(predecessor)):
        raise ValueError('Reconciliation predecessor mismatch')
    if record['aggregate_budget_usd'] != '2' or record['unresolved_calls'] != 0:
        raise ValueError('Invalid reconciliation budget or unresolved usage')
    if any(sha(p) != expected for p, expected in record['source_sha256'].items()):
        raise ValueError('Reconciliation source changed')
    # Exactly one newly consumed approval; no removals or history replacement.
    old = previous['paid_history_sha256']
    new = record['paid_history_sha256']
    added = set(new) - set(old)
    if len(added) != 1 or any(new.get(p) != h for p, h in old.items()):
        raise ValueError('Conflicting or duplicate reconciliation history')
    marker = Path(next(iter(added)))
    if not marker.name.startswith('.paired-live-dispatch-'):
        raise ValueError('Unsupported successor dispatch')
    lifecycle = Path(str(marker)+'.lifecycle.jsonl')
    audit = Path(record['audit_path'])
    summary_path = audit.parent/'summary.json'
    if audit.resolve() in seen_audits:
        raise ValueError('Duplicate dispatch accounting')
    required = {str(marker), str(lifecycle), str(audit), str(summary_path)}
    if set(record['source_sha256']) != required or record['source_sha256'][str(marker)] != new[str(marker)]:
        raise ValueError('Incomplete dispatch source bindings')
    claim = _read(marker)
    if inspect_claim(marker)['state'] != 'COMPLETED':
        raise ValueError('Dispatch lifecycle not completed; manual reconciliation required')
    if Path(claim['output']).resolve() != audit.parent.resolve():
        raise ValueError('Dispatch output mismatch')
    events = [json.loads(line,object_pairs_hook=_unique_object)
              for line in audit.read_text().splitlines()]
    previous_hash = None
    for n, event in enumerate(events):
        if (event['sequence'] != n or event['previous'] != previous_hash or
                event['sha256'] != digest({k:v for k,v in event.items() if k!='sha256'})):
            raise ValueError('Successor audit hash chain failed')
        previous_hash = event['sha256']
    if not events or events[0]['kind'] != 'run_start' or events[-1]['kind'] != 'run_completed':
        raise ValueError('Incomplete successor execution')
    if events[0]['manifest_sha256'] != claim['manifest_sha256']:
        raise ValueError('Dispatch audit approval mismatch')
    account = next(e for e in reversed(events) if e['kind']=='actual_provider_accounting')
    calls, usage = account['calls'], account['usage']
    if (not calls or usage['unresolved_calls'] or any(c['status']!='observed' for c in calls)
            or len({c['key'] for c in calls}) != len(calls)
            or events[-1]['actual_provider_calls'] != len(calls)):
        raise ValueError('Unresolved or duplicate successor usage')
    prices = claim['pricing_verification']
    cost = Decimal(0)
    for c in calls:
        if any(type(c[k]) is not int or c[k]<0 for k in ('input_tokens','output_tokens')):
            raise ValueError('Invalid numeric token counters')
        if c['provider_model'] != prices['model']:
            raise ValueError('Unexpected accounted model')
        priced = (Decimal(c['input_tokens'])*Decimal(prices['input_usd_per_million'])+
                  Decimal(c['output_tokens'])*Decimal(prices['output_usd_per_million']))/Decimal(1000000)
        if not priced.is_finite() or priced < 0 or priced != Decimal(c['observed_usd']):
            raise ValueError('Usage-priced cost mismatch')
        cost += priced
    summary = _read(summary_path)
    if (summary['actual_usage'] != usage or summary['actual_provider_calls'] != len(calls)
            or usage['observed_input_tokens'] != sum(c['input_tokens'] for c in calls)
            or usage['observed_output_tokens'] != sum(c['output_tokens'] for c in calls)
            or Decimal(usage['observed_cost_usd']) != cost
            or Decimal(summary['new_usage_priced_estimated_spend_usd']) != cost
            or Decimal(summary['carried_prior_spend_usd']) != carried
            or Decimal(summary['aggregate_conservative_spend_usd']) != carried+cost):
        raise ValueError('Summary accounting mismatch')
    if (Decimal(claim['prior_spend_usd']) != carried
            or Decimal(record['prior_estimated_spend_usd']) != carried
            or Decimal(record['new_estimated_spend_usd']) != cost
            or Decimal(record['aggregate_estimated_spend_usd']) != carried+cost
            or cost > Decimal(claim['maximum_new_reservations_usd'])
            or carried+cost > Decimal('2')):
        raise ValueError('Successor carry-forward or reservation mismatch')
    if new != paid_history:
        raise ValueError('Unreconciled dispatch/history; stop')
    return record, carried+cost
