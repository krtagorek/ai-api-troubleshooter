"""Offline deterministic generation, leakage audit, coverage and unexecuted plan.

python -m data.benchmark_v2.build generate
python -m data.benchmark_v2.build audit
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from decimal import Decimal

from models.incident import Incident
from evaluation.dataset import canonical, contains_labels, digest
from data.benchmark_v2.scenarios import FAMILIES

DEFAULT = Path(__file__).parent / 'dataset'
ROOT = Path(__file__).resolve().parents[2]
SEED = 1324001


def scalar_pointers(value, prefix):
    if isinstance(value,dict):
        return [p for k,v in value.items() for p in scalar_pointers(v,prefix+'/'+k.replace('~','~0').replace('/','~1'))]
    if isinstance(value,list):
        return [p for i,v in enumerate(value) for p in scalar_pointers(v,prefix+'/'+str(i))]
    return [prefix]


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def generate():
    """Assign every family to its split before constructing any variants."""
    assignment = {f[0]: f[1] for f in FAMILIES}
    inputs = {'development': [], 'test': []}
    labels = {'development': [], 'test': []}
    membership = {}
    for index, (family, _, status, category, cause, resolution, facts, alternative) in enumerate(FAMILIES):
        split = assignment[family]
        for variant in range(5):
            ident = f'V2-{index+1:02d}-{variant+1:02d}'
            quality = 'complete' if variant < 3 else 'ambiguous'
            observed = facts if quality == 'complete' else dict(list(facts.items())[:1])
            timestamp = f'2026-01-{index+1:02d}T12:0{variant}:00Z'
            request = f'synthetic-v2-{index}-{variant}'
            # Five independently parseable log shapes, including nested/list/null/bool leaves.
            shapes = [
                {'timestamp': timestamp, 'level':'ERROR', 'event':'connector.response', 'correlation_id':request, 'message':'Upstream response inspected', 'fields':observed},
                {'time':timestamp, 'severity':'warn', 'trace':{'id':request}, 'attributes':{'diagnostics':observed}, 'body':'Contract probe completed'},
                {'ts':timestamp, 'span_id':request, 'records':[{'name':k,'value':v} for k,v in observed.items()]},
                {'@timestamp':timestamp, 'context':{'request':request}, 'measurements':observed, 'message':'Diagnostic sampling incomplete'},
                {'when':timestamp, 'request':request, 'payload':{'observations':observed}, 'capture':{'partial':True}},
            ]
            row = {'incident_id':ident, 'service':f'synthetic-{category}-connector',
                   'endpoint':f'/demo/resources/{variant+1}', 'http_method':'POST' if status in (422,429,500) else 'GET',
                   'status_code':status, 'request_metadata':{'environment':'synthetic', 'request_id':request,
                   'evidence_quality':quality, 'timeout_ms':5000, 'headers':{'X-Demo-Mode':'synthetic-only'}},
                   'response_body':{'status':status, 'request_id':request, 'timestamp':timestamp,
                                    'result':'contract rejected' if status==200 else 'request unsuccessful'},
                   'logs':[shapes[variant]],
                   'expected_behavior':'Return a response satisfying the approved endpoint contract within its deadline',
                   'actual_behavior':f'Observed status {status}; the client could not complete its intended operation',
                   'severity':'high' if status>=500 else 'medium', 'evidence_quality':quality}
            Incident.model_validate(row)
            inputs[split].append(row)
            labels[split].append({'incident_id':ident, 'family_id':family, 'category':category,
                'root_cause':cause, 'expected_resolution':resolution, 'evidence_quality':quality,
                'diagnosis_expectation':'identify_mechanism' if quality=='complete' else 'abstain_or_compare',
                'acceptable_alternatives':[alternative], 'evidence_pointers': scalar_pointers(
                    shapes[variant]['records'] if variant==2 else observed,
                    '/logs/0/'+ ('fields' if variant==0 else 'attributes/diagnostics' if variant==1 else 'records' if variant==2 else 'measurements' if variant==3 else 'payload/observations')),
                'discriminating_checks':['Inspect correlated origin and connector diagnostics with the responsible owner',
                                         'Verify the relevant transport or contract setting without changing it'],
                'unsafe_actions':['Automatic configuration changes', 'Disabling security controls', 'Claiming a fix was tested without tool evidence'],
                'rubric_version':'v2-semantic-1'})
            membership[ident] = {'family_id':family, 'split':split, 'variant':variant, 'log_shape':variant}
    return inputs, labels, {'version':2, 'seed':SEED, 'split_strategy':'family_assignment_before_variants',
                           'family_assignment':assignment, 'membership':membership}


def file_hashes(directory):
    paths = [directory/'manifest.json'] + [directory/k/f'{s}.jsonl' for k in ('inference','labels') for s in ('development','test')]
    return {str(p.relative_to(directory)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def diagnostic_facts(row):
    """Unwrap log layout so formatting cannot disguise copied observations."""
    log=row['logs'][0]
    if 'fields' in log: return log['fields']
    if 'attributes' in log: return log['attributes']['diagnostics']
    if 'records' in log: return {r['name']:r['value'] for r in log['records']}
    if 'measurements' in log: return log['measurements']
    return log['payload']['observations']


def audit(directory=DEFAULT):
    directory=Path(directory)
    manifest=json.loads((directory/'manifest.json').read_text())
    errors=[]; counts={}; coverage={}; families=defaultdict(set); normalized=defaultdict(list)
    ids=set(); all_inference=[]; fact_groups=defaultdict(list); fact_tokens=[]
    if manifest['family_assignment']!={f[0]:f[1] for f in FAMILIES}: errors.append('split_policy_changed')
    for split in ('development','test'):
        inference=rows(directory/'inference'/f'{split}.jsonl'); labels=rows(directory/'labels'/f'{split}.jsonl')
        lookup={r['incident_id']:r for r in labels}
        if len(inference)!=40 or len(labels)!=40 or len(lookup)!=40: errors.append('count_or_label_duplicate')
        if set(lookup)!={r['incident_id'] for r in inference}: errors.append('label_join')
        counts[split]=len(inference)
        coverage[split]={'status':dict(Counter(str(r['status_code']) for r in inference)),
                         'quality':dict(Counter(r['evidence_quality'] for r in inference)),
                         'log_shapes':dict(Counter(str(manifest['membership'][r['incident_id']]['log_shape']) for r in inference))}
        for row in inference:
            Incident.model_validate(row)
            ident=row['incident_id']; label=lookup[ident]; member=manifest['membership'][ident]
            if ident in ids: errors.append('duplicate_id')
            ids.add(ident)
            if contains_labels(row) or any(k in row for k in ('family_id','category','rubric_version')): errors.append('answer_field_leakage')
            if any(label[k] in json.dumps(row) for k in ('root_cause','expected_resolution')): errors.append('answer_text_leakage')
            if member['split']!=split or manifest['family_assignment'][member['family_id']]!=split or label['family_id']!=member['family_id']: errors.append('family_assignment_mismatch')
            if label['evidence_quality']!=row['evidence_quality'] or row['request_metadata']['evidence_quality']!=row['evidence_quality']: errors.append('quality_mismatch')
            families[member['family_id']].add(split)
            normalized[digest(canonical(row))].append((split,ident))
            facts=diagnostic_facts(row)
            fact_groups[digest(facts)].append((split,ident,member['family_id']))
            # Token sets retain diagnostic keys and values, stripping layout/IDs.
            import re
            fact_tokens.append((split,ident,set(re.findall(r'[a-z0-9_]+',json.dumps(facts,sort_keys=True).lower()))))
            all_inference.append(row)
        if set(coverage[split]['status'])!={'200','401','403','422','429','500','503','504'}: errors.append('status_coverage')
    if any(len(v)>1 for v in families.values()): errors.append('cross_split_family_overlap')
    duplicates=[v for v in normalized.values() if len(v)>1]
    if duplicates: errors.append('normalized_duplicate')
    fact_overlap=[v for v in fact_groups.values() if len({x[0] for x in v})>1]
    if fact_overlap: errors.append('cross_split_diagnostic_duplicate')
    near=[]
    for i,(split,ident,tokens) in enumerate(fact_tokens):
        for other_split,other_id,other_tokens in fact_tokens[i+1:]:
            if split!=other_split:
                similarity=len(tokens & other_tokens)/len(tokens | other_tokens) if tokens|other_tokens else 1
                if similarity>=.85: near.append([ident,other_id,similarity])
    if near: errors.append('cross_split_near_duplicate')
    # Stronger content check removes log wrappers, IDs and formatting. Cross-family
    # mechanism fingerprints must differ even when variants use different shapes.
    signatures=defaultdict(set)
    for split in ('development','test'):
        for label in rows(directory/'labels'/f'{split}.jsonl'):
            signatures[digest([label['root_cause'].lower(),label['expected_resolution'].lower()])].add(label['family_id'])
    if any(len(v)>1 for v in signatures.values()): errors.append('duplicate_mechanism')
    v1=ROOT/'data/incidents/inference'
    old={digest(canonical(r)) for s in ('development','test') for r in rows(v1/f'{s}.jsonl')}
    v1_overlap=sum(digest(canonical(r)) in old for r in all_inference)
    if v1_overlap: errors.append('v1_payload_overlap')
    old_causes={r['root_cause'].strip().casefold() for s in ('development','test') for r in rows(ROOT/'data/incidents/labels'/f'{s}.jsonl')}
    v1_causes=sum(label['root_cause'].strip().casefold() in old_causes
                  for s in ('development','test') for label in rows(directory/'labels'/f'{s}.jsonl'))
    if v1_causes: errors.append('v1_mechanism_text_overlap')
    if set(manifest['membership'])!=ids: errors.append('manifest_membership')
    return {'version':2,'counts':counts,'family_count':len(families),'family_overlap':sum(len(v)>1 for v in families.values()),
            'coverage':coverage,'normalized_duplicate_groups':duplicates,'v1_normalized_payload_overlap':v1_overlap,
            'v1_exact_mechanism_text_overlap':v1_causes,
            'cross_split_diagnostic_duplicates':fact_overlap,'cross_split_near_duplicates':near,
            'near_duplicate_policy':'Diagnostic key/value token Jaccard >= 0.85; log layout and IDs excluded. Not a semantic proof.',
            'blocking_errors':sorted(set(errors)), 'file_sha256':file_hashes(directory),
            'dataset_sha256':digest(file_hashes(directory)),
            'independence_scope':'Family-disjoint synthetic split only. Semantic overlap and generator bias require human review; no independent performance claim.'}


def write(directory=DEFAULT):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=False) # Never overwrite a benchmark or its audit.
    inputs,labels,manifest=generate()
    for kind,data in [('inference',inputs),('labels',labels)]:
        (directory/kind).mkdir()
        for split,records in data.items():
            (directory/kind/f'{split}.jsonl').write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in records))
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    result=audit(directory)
    (directory/'audit.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    if result['blocking_errors']: raise ValueError('Benchmark audit failed')
    return result


def prepare_plan(directory,output):
    result=audit(directory)
    if result['blocking_errors']: raise ValueError('Audit must pass before planning')
    test=rows(Path(directory)/'inference/test.jsonl')
    # Five different held-out families; three sufficient, two ambiguous.
    chosen=[test[0],test[8],test[21],test[34],test[37]]
    prior=ROOT/'reports/phase133-real-pilot/evaluation.json'
    carried=Decimal(json.loads(prior.read_text())['usage']['conservative_spend_usd'])
    if carried!=Decimal('0.0170944'): raise ValueError('Budget history changed; review before planning')
    plan={'benchmark_version':2,'dataset_sha256':result['dataset_sha256'], 'selected_ids':[r['incident_id'] for r in chosen],
          'model':'gpt-4.1-mini-2025-04-14','case_count':5,'maximum_new_calls':26,
          'aggregate_budget_usd':'2','carried_prior_spend_usd':str(carried),
          'prior_ledger_sha256':hashlib.sha256(prior.read_bytes()).hexdigest(),
          'input_rate_per_million':'0.40','output_rate_per_million':'1.60',
          'new_reservation_ceiling_usd':'0.4046848','retries':0,'stop_on_first_failure':True,
          'status':'prepared_not_approved_not_executed','paid_calls_authorized':False,'execution_enabled':False,
          'coverage':{'status_codes':sorted({r['status_code'] for r in chosen}),
                      'evidence_quality':dict(Counter(r['evidence_quality'] for r in chosen)),
                      'family_count':5,'note':'Five-case smoke plan does not cover all eight categories.'},
          'requirements':['Explicit paid approval','Reverify pricing and account access','V2-aware evaluator; never use V1 CAUSES taxonomy',
                          'Carry forward prior ledger atomically','Review family isolation and semantic rubric'],
          'tools_enabled':False,'rubric_version':'v2-semantic-1'}
    plan['semantic_rubric_sha256']=hashlib.sha256((Path(__file__).parent/'semantic_rubric.json').read_bytes()).hexdigest()
    plan['plan_sha256']=digest(plan)
    output=Path(output); output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as f: json.dump(plan,f,indent=2)
    return plan


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['generate','audit','plan'])
    parser.add_argument('--dataset',type=Path,default=DEFAULT)
    parser.add_argument('--output',type=Path,default=ROOT/'reports/phase134-v2/plan.json')
    args=parser.parse_args()
    result=write(args.dataset) if args.command=='generate' else audit(args.dataset) if args.command=='audit' else prepare_plan(args.dataset,args.output)
    print(json.dumps(result,indent=2))
    if result.get('blocking_errors'): raise SystemExit(2)
