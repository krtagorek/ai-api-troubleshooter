"""Blinded human-only packet from immutable recorded outputs; no model calls."""
import copy
import json
import os
from pathlib import Path
import secrets

from evaluation.dataset import digest
from evaluation.quality_pilot import OUTPUT,sha

SOURCE=Path('reports/diagnostic-quality-paired-live-v2/audit.jsonl')


def prepare_packet(destination):
    reconciliation=json.loads((OUTPUT/'budget-reconciliation-v3.json').read_text())
    if sha(SOURCE)!=reconciliation['source_sha256'][str(SOURCE)]:
        raise ValueError('Original audit changed')
    events=[json.loads(s) for s in SOURCE.read_text().splitlines()]
    previous=None
    for n,event in enumerate(events):
        if event['sequence']!=n or event['previous']!=previous or event['sha256']!=digest({k:v for k,v in event.items() if k!='sha256'}):
            raise ValueError('Original audit chain failed')
        previous=event['sha256']
    arms=[e for e in events if e['kind']=='arm_completed' and e['key'].startswith('V2-01-01/')]
    if len(arms)!=2:raise ValueError('Exactly two recorded V2-01-01 arms required')
    secrets.SystemRandom().shuffle(arms)
    calls=next(e for e in reversed(events) if e['kind']=='actual_provider_accounting')['calls']
    rubric=json.loads((OUTPUT/'human-review-rubric.json').read_text())
    reference=next(c for c in json.loads((OUTPUT/'human-review-worksheet.json').read_text())['cases'] if c['incident_id']=='V2-01-01')
    packet={'version':'paired-human-review-1','case':'V2-01-01','status':'AWAITING_REAL_HUMAN_RATINGS',
            'human_only':True,'source_sha256':sha(SOURCE),
            'complete_source_evidence':arms[0]['sanitized_result']['state']['sanitized_inputs'],
            'confirmed_preoutput_reference':reference['preoutput_reference_review'],
            'arms':[]}
    key=[];reviews=[]
    for n,event in enumerate(arms):
        label=f'ARM-{chr(65+n)}'
        result=copy.deepcopy(event['sanitized_result'])
        result['blind_id']=label;result['state']['thread_id']=label
        original=[{'stage':c['stage'],'output':c['provider_structured_output']} for c in calls if c.get('key','').startswith(event['key']+'/')]
        packet['arms'].append({'label':label,'recorded_result':result,'original_provider_outputs':original})
        key.append({'label':label,'condition':event['key'].split('/')[1]})
        dimensions={**rubric['frozen_v2_dimensions'],**rubric['supplemental_dimensions']}
        reviews.append({'label':label,'reviewer_id':None,'timestamp':None,
                        'dimension_ratings':{d:None for d in dimensions},
                        'applicability':{d:None for d in dimensions},
                        'rationales':{d:None for d in dimensions},
                        'evidence_references':None,'hold_adjudication':None,
                        'hold_rationale':None,'uncertainty_assessment':None,
                        'unsafe_credential_request':None,'credential_wording_rationale':None,
                        'safe_alternative_wording':None,'unsupported_claims':None})
    worksheet={'status':'PENDING_REAL_HUMAN_REVIEW','packet_sha256':digest(packet),
               'reviews':reviews,'paired_preference':None,'paired_rationale':None,
               'independent_second_reviews':[],'adjudication':None}
    path=Path(destination);path.mkdir(mode=0o700)
    for name,value in {'reviewer-packet.json':packet,'human-review.json':worksheet,
                       'rubric.json':rubric,'organizer-key.json':key}.items():
        fd=os.open(path/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f:json.dump(value,f,indent=2)
    return {'case':'V2-01-01','arms':2,'human_ratings':'NOT_FILLED','packet_sha256':digest(packet)}
