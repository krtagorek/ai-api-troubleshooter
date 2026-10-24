"""Phase 13.9 single-case preparation only. No paid dispatch capability."""
import json
from decimal import Decimal
from pathlib import Path

from evaluation.dataset import digest
from evaluation.v2_execution import prepare as prepare_base,sha,ReviewedAdapter
from evaluation.v2 import V2Adapter,aggregate,ROOT
from tools.evidence_catalog import catalog_payload

CASE='V2-02-01'
REVIEW=Path('reports/phase136-v2/pre-execution-label-review.json')
LEDGER=Path('reports/phase137-v2-real-run/evaluation.json')
MARKER=Path('reports/.v2-aggregate-dispatch.json')


class SingleCaseAdapter:
    """Restrict inference, scoring and coverage to one reviewed V2 case."""
    def __init__(self,base): self.base=base
    def inputs(self): return [i for i in self.base.inputs() if i.incident_id==CASE]
    def score(self,state):
        if state['incident_id']!=CASE: raise ValueError('Only the single selected case is permitted')
        return self.base.score(state)
    def summarize(self,cases):return aggregate(cases,self.inputs())


def prepare(ledger_path=None,marker_path=None,expected_spend=Decimal('.0175852')):
    ledger_path=Path(ledger_path) if ledger_path else LEDGER
    marker_path=Path(marker_path) if marker_path else MARKER
    base=prepare_base(REVIEW)
    ledger=json.loads(ledger_path.read_text())
    usage=ledger['usage']
    carried=Decimal(usage['conservative_spend_usd'])
    if carried!=expected_spend or usage['unresolved_calls'] or Decimal(usage['observed_cost_usd'])!=carried:
        raise ValueError('Latest spending requires reconciliation')
    if Decimal(ledger['carried_prior_spend_usd'])+Decimal(ledger['new_estimated_cost_usd'])!=carried:
        raise ValueError('Carry-forward chain mismatch')
    marker=json.loads(marker_path.read_text())
    if Path(marker['output']).resolve()!=ledger_path.parent.resolve() or marker.get('approval_sha256',marker.get('manifest_sha256'))!=ledger['approval_sha256']:
        raise ValueError('Prior dispatch/ledger mismatch')
    single=SingleCaseAdapter(V2Adapter(base['plan_path']))
    if len(single.inputs())!=1: raise ValueError('Single case missing')
    catalog=catalog_payload(single.inputs()[0])
    cap=(Decimal(32768)*Decimal('.40')+Decimal(1536)*Decimal('1.60'))/Decimal(1000000)*6
    result={k:v for k,v in base.items() if k!='approval_sha256'}
    result.update(version='v2-single-verification-1',selected_ids=[CASE],source_plan_subset=True,
                  maximum_new_calls=6,prior_estimated_spend_usd=str(carried),
                  latest_ledger_path=str(ledger_path.resolve()),latest_ledger_sha256=sha(ledger_path),
                  previous_dispatch_marker_path=str(marker_path.resolve()),previous_dispatch_marker_sha256=sha(marker_path),
                  new_reservation_ceiling_usd=str(cap),conservative_aggregate_ceiling_usd=str(carried+cap),
                  catalog_sha256=digest(catalog),catalog_entries=len(catalog),
                  execution_enabled=False,paid_approval_granted=False,
                  status='PREPARED_NOT_APPROVED_NOT_EXECUTED',
                  human_semantic_review='PENDING_REAL_OUTPUT_AND_NEW_HUMAN_REVIEW',
                  dispatch_requirements=['New explicit paid approval','Reverify rates and model availability',
                                         'New single-use dispatch linked to reconciled historical marker; never delete prior marker',
                                         'Check intervening spend and atomically reserve carried budget'])
    if carried+cap>Decimal('2'): raise ValueError('Budget ceiling exceeded')
    result['approval_sha256']=digest(result)
    return result


def prepared_inputs(manifest):
    current_prepare=prepare
    if manifest.get('version')=='v2-live-handles-verification-1':
        from evaluation.v2_handles_verification import prepare as current_prepare
    elif manifest.get('version')=='v2-minimal-investigator-pilot-1':
        from evaluation.v2_minimal_verification import prepare as current_prepare
    if current_prepare()!=manifest: raise ValueError('Prepared bindings changed')
    return SingleCaseAdapter(V2Adapter(manifest['plan_path'])).inputs()


def grading_adapter(manifest):
    """Future runner injection only; no graph or provider invocation."""
    prepared_inputs(manifest)
    return ReviewedAdapter(SingleCaseAdapter(V2Adapter(manifest['plan_path'])),json.loads(REVIEW.read_text()))


def run_approved_single(manifest,approval_sha256,output):
    """Dormant dispatcher: never call without the user's new scoped approval."""
    if approval_sha256!=manifest.get('approval_sha256') or digest({k:v for k,v in manifest.items() if k!='approval_sha256'})!=approval_sha256:
        raise ValueError('New single-case paid approval required')
    inputs=prepared_inputs(manifest)
    from evaluation.provider import configuration_check
    if not configuration_check()['ready']: raise ValueError('Provider configuration blocked')
    import fcntl
    from evaluation.budget import Budget
    from evaluation.reporting import write_report
    from evaluation.v2_execution import postexecution_worksheets
    reports=ROOT/'reports'; output=Path(output)
    with (reports/'.v2-aggregate-budget.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        prepared_inputs(manifest) # Revalidate under lock, including latest ledger and old marker.
        allowed_previous=Path(manifest.get('previous_dispatch_marker_path',str(MARKER))).resolve()
        allowed_markers={allowed_previous}
        if manifest.get('version')=='v2-minimal-investigator-pilot-1':
            allowed_markers.update(Path(p).resolve() for p in manifest['reconciled_dispatches'])
        if output.exists() or any(p.resolve() not in allowed_markers for p in reports.glob('.v2-single-dispatch-*.json')):
            raise ValueError('Existing single-case dispatch: reconcile spend before another approval')
        historical={'phase13-heldout20','phase131-real-pilot','phase133-real-pilot','phase137-v2-real-run'}
        if manifest.get('version')=='v2-live-handles-verification-1':historical.add('phase139-single-real-run')
        elif manifest.get('version')=='v2-minimal-investigator-pilot-1':historical.update({'phase139-single-real-run','phase1311-live-handles-real-run'})
        for path in reports.rglob('evaluation.json'):
            if path.parent.name not in historical and json.loads(path.read_text()).get('calls'):
                raise ValueError('Intervening spending record requires reconciliation')
        output.mkdir(parents=True,exist_ok=False)
        marker=reports/('.v2-single-dispatch-'+approval_sha256+'.json')
        carried=Decimal(manifest['prior_estimated_spend_usd'])
        with marker.open('x') as f:json.dump({'manifest_sha256':approval_sha256,'prior_ledger_sha256':manifest['latest_ledger_sha256'],
                                             'prior_spend_usd':str(carried),'output':str(output.resolve())},f)
        marker.chmod(0o600)
        budget=Budget(Decimal('2'),Decimal('.40'),Decimal('1.60'),6,
                      reserved_or_spent=carried,observed_cost=carried)
        report={'status':'approved_single_case_dispatch','selected_ids':[CASE],'cases':[],
                'notes':['Single reviewed V2 case only; no retries or external tools.'],
                'real_model_execution':False,'approval_sha256':approval_sha256,'execution_manifest':manifest}
        try:
            from evaluation.runner import run_cases
            run_cases([i.model_dump(mode='json') for i in inputs],None,manifest['model'],budget,output,report,
                      stop_on_failure=True,scoring_adapter=grading_adapter(manifest))
        except Exception:
            report['status']='stopped_preflight_or_execution'
            report['notes'].append('Stopped safely; no raw error details, retry or fallback.')
        finally:
            report.update(usage=budget.summary(),calls=budget.calls,carried_prior_spend_usd=str(carried),
                          new_estimated_cost_usd=str(budget.observed_cost-carried))
            write_report(output,report)
            review=json.loads(REVIEW.read_text());path=output/'postexecution-human-review.json'
            path.write_text(json.dumps(postexecution_worksheets(report,review),indent=2)+'\n');path.chmod(0o600)
        return report
