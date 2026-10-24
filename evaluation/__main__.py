"""Offline audit/plan/config preflight; explicitly approved real 20-case evaluation."""
import argparse
from datetime import datetime,timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
from uuid import uuid4
from dotenv import load_dotenv
from data.generate_incidents import DEFAULT_OUTPUT
from evaluation.dataset import read_dataset,select_cases,digest
from evaluation.budget import Budget
from evaluation.provider import configuration_check
from evaluation.reporting import write_report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    audit=sub.add_parser('audit');audit.add_argument('--dataset',type=Path,default=DEFAULT_OUTPUT)
    plan=sub.add_parser('plan');plan.add_argument('--dataset',type=Path,default=DEFAULT_OUTPUT)
    plan.add_argument('--model',required=True);plan.add_argument('--input-rate',type=Decimal,required=True,help='USD per million tokens')
    plan.add_argument('--output-rate',type=Decimal,required=True);plan.add_argument('--budget-usd',type=Decimal,required=True)
    plan.add_argument('--output',type=Path,required=True);plan.add_argument('--seed',type=int,default=314159)
    preflight=sub.add_parser('preflight');preflight.add_argument('--plan',type=Path,required=True)
    run=sub.add_parser('run');run.add_argument('--plan',type=Path,required=True);run.add_argument('--approve-paid',required=True)
    run.add_argument('--acknowledge-overlap',action='store_true')
    run.add_argument('--case-limit',type=int,choices=[3,20],default=20,
                     help='Run the first 3 planned cases as a single-use pilot, or all 20')
    args=parser.parse_args(argv)
    load_dotenv()
    try:
        if args.command=='audit':
            _,_,audit=read_dataset(args.dataset);print(json.dumps(audit,indent=2));return 1 if audit['blocking_errors'] else 0
        if args.command=='plan':
            if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]{0,99}',args.model): raise ValueError('Invalid model identifier')
            inputs,_,audit=read_dataset(args.dataset)
            if audit['blocking_errors']: raise ValueError('Dataset audit failed; run audit for controlled error codes')
            selected=select_cases(inputs['test'],20,args.seed)
            budget=Budget(args.budget_usd,args.input_rate,args.output_rate,101)
            value={'run_id':str(uuid4()),'created_at':datetime.now(timezone.utc).isoformat(),'model':args.model,
                'dataset':str(args.dataset.resolve()),'dataset_sha256':audit['dataset_sha256'],
                'selected_ids':[r['incident_id'] for r in selected],'selection_seed':args.seed,'case_count':20,
                'budget_usd':str(args.budget_usd),'input_rate_per_million':str(args.input_rate),'output_rate_per_million':str(args.output_rate),
                'input_cap':budget.input_cap,'output_cap':budget.output_cap,'max_calls':101,
                'endpoint':'https://api.openai.com/v1','execution':'real_OpenAI_only','tools_enabled':False,
                'pricing_source':'https://developers.openai.com/api/docs/pricing',
                'pricing_note':'User-supplied rates must be verified before approval; no cached-input discount assumed.'}
            value['approval_sha256']=digest(value)
            args.output.mkdir(parents=True,exist_ok=True)
            with (args.output/'plan.json').open('x') as handle: json.dump(value,handle,indent=2)
            os.chmod(args.output/'plan.json',0o600)
            report={'status':'awaiting_approval','real_model_execution':False,'selected_ids':value['selected_ids'],'dataset_audit':audit,'metrics':None,'cases':[],
                'notes':['No real model call made. Paid connectivity preflight is included in the approved run.',
                         'Missing/ambiguous evidence is evaluated for abstention; labels never enter prompts.',
                         'Lexical quality metrics require human semantic review.']}
            write_report(args.output,report)
            print(json.dumps({'plan':str(args.output/'plan.json'),'approval_sha256':value['approval_sha256'],'case_count':20,
                'budget_usd':str(budget.limit),'maximum_calls_including_preflight':101,'heldout_independent':audit['heldout_independent']}));return 0
        plan=json.loads(args.plan.read_text());claimed=plan.pop('approval_sha256')
        if digest(plan)!=claimed: raise ValueError('Plan digest mismatch; create and review a new plan')
        inputs,labels,audit=read_dataset(Path(plan['dataset']))
        if audit['blocking_errors'] or audit['dataset_sha256']!=plan['dataset_sha256']: raise ValueError('Dataset changed or audit failed')
        selected=select_cases(inputs['test'],20,plan['selection_seed'])
        if [r['incident_id'] for r in selected]!=plan['selected_ids']: raise ValueError('Selection mismatch')
        configuration=configuration_check()
        if args.command=='preflight':
            print(json.dumps({'mode':'offline_configuration_only','configuration':configuration,'live_connectivity':'NOT_TESTED',
                'paid_approval_required':True,'dataset_independent':audit['heldout_independent']}));return 0 if configuration['ready'] else 2
        if args.approve_paid!=claimed: raise ValueError('Explicit approval must match reviewed plan digest')
        if audit['cross_split_normalized_overlap_groups'] and not args.acknowledge_overlap: raise ValueError('Acknowledge normalized dataset overlap before real execution')
        directory=args.plan.parent;report=json.loads((directory/'evaluation.json').read_text())
        if report.get('calls') or report.get('real_model_execution'): raise ValueError('Run already dispatched calls; use a new plan and approval to avoid accidental double billing')
        if not configuration['ready']:
            report['status']='blocked_configuration';report['notes'].append(configuration['reason']);write_report(directory,report)
            print(configuration['reason']);return 2
        with (directory/'.dispatch.lock').open('x') as lock:
            lock.write('Single-use paid-run dispatch; create a new plan and approval after interruption.\n')
        selected=selected[:args.case_limit]
        budget=Budget(Decimal(plan['budget_usd']),Decimal(plan['input_rate_per_million']),Decimal(plan['output_rate_per_million']),min(plan['max_calls'],1+5*len(selected)),plan['input_cap'],plan['output_cap'])
        report['plan']=plan;report['approval_sha256']=claimed
        report['selected_ids']=[r['incident_id'] for r in selected]
        report['remaining_ids']=plan['selected_ids'][len(selected):]
        report['scope']='three_case_pilot' if args.case_limit==3 else 'full_plan'
        from evaluation.runner import run_cases
        try:
            result=run_cases(selected,{r['incident_id']:r for r in labels['test']},plan['model'],budget,directory,report,stop_on_failure=args.case_limit==3)
        except Exception:
            report.update(status='stopped_preflight_or_execution',usage=budget.summary(),calls=budget.calls)
            report['notes'].append('Real provider preflight/execution failed safely; raw error details withheld. No fallback used.')
            write_report(directory,report);return 2
        print(json.dumps({'status':result['status'],'scored':sum(bool(c.get('metrics')) for c in result['cases']),'usage':budget.summary()}))
        return 0 if result['status']=='completed' else 2
    except Exception:
        print('Evaluation command rejected or failed. Check the dataset, plan, approval, configuration and output directory. Sensitive details withheld.')
        return 2


if __name__=='__main__': raise SystemExit(main())
