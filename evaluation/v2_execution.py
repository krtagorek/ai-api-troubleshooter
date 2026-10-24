"""Frozen V2 runner integration. Preparation is offline; dispatch needs new approval."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import fcntl

from evaluation.dataset import digest
from evaluation.v2 import V2Adapter, ROOT
from evaluation.v2_preexecution import validate_review, PLAN
from evaluation.v2_review import worksheet as semantic_worksheet
from evaluation.budget import Budget
from evaluation.provider import configuration_check
from evaluation.reporting import write_report


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(review_path,plan_path=PLAN):
    adapter=V2Adapter(plan_path)
    review=json.loads(Path(review_path).read_text())
    result=validate_review(review,plan_path)
    if not result['labels_accepted']: raise ValueError('All five human label decisions must be accepted')
    files=[Path('evaluation/runner.py'),Path('evaluation/v2.py'),Path('evaluation/v2_execution.py'),
           Path('evaluation/provider.py'),Path('evaluation/budget.py'),Path('evaluation/v2_preexecution.py')]
    files+=sorted(Path('agents').glob('*.py'))+sorted(Path('models').glob('*.py'))+sorted(Path('graph').glob('*.py'))
    files=sorted(set(files+list(Path('evaluation').glob('*.py'))+list(Path('tools').glob('*.py'))+list(Path('data/benchmark_v2').glob('*.py'))))
    manifest={'version':'v2-real-run-1','plan_path':str(Path(plan_path).resolve()),
              'review_path':str(Path(review_path).resolve()),'plan_file_sha256':sha(plan_path),
              'worksheet_file_sha256':sha(review_path),'worksheet_content_sha256':digest(review),
              'dataset_sha256':adapter.plan['dataset_sha256'],'rubric_sha256':adapter.plan['semantic_rubric_sha256'],
              'code_sha256':{str(p):sha(p) for p in files},'selected_ids':adapter.plan['selected_ids'],
              'model':adapter.plan['model'],'maximum_new_calls':26,'new_reservation_ceiling_usd':'0.4046848',
              'prior_estimated_spend_usd':'0.0170944','aggregate_budget_usd':'2','retries':0,
              'stop_on_first_failure':True,'external_tools':False,'status':'AWAITING_EXPLICIT_PAID_APPROVAL',
              'paid_approval_granted':False}
    manifest['approval_sha256']=digest(manifest)
    return manifest


class ReviewedAdapter:
    """Post-inference grading context; never included in graph inputs."""
    def __init__(self,adapter,review):
        self.adapter=adapter
        self.reviews={c['material']['incident_id']:c['human_review'] for c in review['cases']}

    def score(self,state):
        metrics=self.adapter.score(state)
        human=self.reviews[state['incident_id']]
        metrics['human_approved_alternatives']=human['accepted_alternatives']
        metrics['label_review_rationale']=human['rationale']
        metrics['label_review_sha256']=digest(human)
        metrics['alternative_acceptance']='HUMAN_SEMANTIC_REVIEW_REQUIRED; do not mark an approved alternative wrong by reference-string mismatch'
        return metrics

    def summarize(self,cases): return self.adapter.summarize(cases)


def postexecution_worksheets(report,review):
    contexts={c['material']['incident_id']:c for c in review['cases']}
    result=[]
    for case in report.get('cases',[]):
        record=semantic_worksheet(case['incident_id'])
        record['workflow_status']=case['status']
        record['human_only_reference']=contexts[case['incident_id']]['material']
        record['approved_label_review']=contexts[case['incident_id']]['human_review']
        # No reviewer identity, ratings or confirmation timestamp are copied into
        # the new post-output human review. Output binding is technical metadata.
        record['output_sha256']=case['metrics']['output_sha256'] if case.get('metrics') else None
        record['sanitized_output']=case.get('sanitized_output')
        record['status']='PENDING_POSTEXECUTION_HUMAN_REVIEW' if case.get('metrics') else 'WORKFLOW_FAILED_NOT_DIAGNOSTICALLY_SCORED'
        result.append(record)
    return result


def run_approved(manifest,approval_sha256,output):
    """Not called by preparation. Approval digest must be explicitly authorized.

    Cooperative process lock and a single-use aggregate marker prevent a second
    V2 runner from reusing the old carry-forward ledger, even after interruption.
    """
    unsigned={k:v for k,v in manifest.items() if k!='approval_sha256'}
    if approval_sha256!=manifest.get('approval_sha256') or digest(unsigned)!=approval_sha256:
        raise ValueError('Fresh explicit approval digest required')
    if prepare(manifest['review_path'],manifest['plan_path'])!=manifest: raise ValueError('Frozen execution inputs changed')
    if not configuration_check()['ready']: raise ValueError('Provider configuration blocked')
    # No dispatch, directories or budget mutation occur before these checks.
    lockpath=ROOT/'reports/.v2-aggregate-budget.lock'
    with lockpath.open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        marker=ROOT/'reports/.v2-aggregate-dispatch.json'
        output=Path(output)
        if marker.exists() or output.exists(): raise ValueError('Existing dispatch/output: reconcile spend and obtain a new plan before continuation')
        # Detect intervening evaluation runs outside this integrated runner.
        prior=ROOT/'reports/phase133-real-pilot/evaluation.json'
        for candidate in (ROOT/'reports').rglob('evaluation.json'):
            record=json.loads(candidate.read_text())
            if candidate!=prior and record.get('calls') and candidate.parent.name not in ('phase13-heldout20','phase131-real-pilot'):
                raise ValueError('Additional spending ledger found; reconcile aggregate budget')
        output.mkdir(parents=True,exist_ok=False)
        with marker.open('x') as f:json.dump({'approval_sha256':approval_sha256,'output':str(output.resolve()),'prior_spend_usd':'0.0170944'},f)
        marker.chmod(0o600)
        report={'status':'approved_dispatch','notes':['Explicit approval required; V2-only scoring; original aggregate budget carried forward.'],
                'selected_ids':manifest['selected_ids'],'real_model_execution':False,'cases':[],
                'approval_sha256':approval_sha256,'execution_manifest':manifest}
        budget=Budget(Decimal('2'),Decimal('.40'),Decimal('1.60'),26,
                      reserved_or_spent=Decimal('.0170944'),observed_cost=Decimal('.0170944'))
        adapter=V2Adapter(manifest['plan_path']); review=json.loads(Path(manifest['review_path']).read_text())
        from evaluation.runner import run_cases
        try:
            run_cases([i.model_dump(mode='json') for i in adapter.inputs()],None,manifest['model'],budget,output,report,
                      stop_on_failure=True,scoring_adapter=ReviewedAdapter(adapter,review))
        except Exception:
            report['status']='stopped_preflight_or_execution'
            report['notes'].append('Execution stopped safely; raw errors withheld; no retry or fallback.')
        finally:
            report.update(usage=budget.summary(),calls=budget.calls,
                          new_estimated_cost_usd=str(budget.observed_cost-Decimal('.0170944')),
                          carried_prior_spend_usd='0.0170944')
            write_report(output,report)
            path=output/'postexecution-human-review.json'
            path.write_text(json.dumps(postexecution_worksheets(report,review),indent=2)+'\n');path.chmod(0o600)
        return report
