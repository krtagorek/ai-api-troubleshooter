"""Audit original files without rewriting them; inference and labels stay separate."""
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from data.generate_incidents import DEFAULT_OUTPUT, InferenceIncident, Incident

VOLATILE = {'incident_id','request_id','timestamp','correlation_id','duration_ms','X-Request-ID'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def canonical(value):
    if isinstance(value,dict): return {k:canonical(v) for k,v in value.items() if k not in VOLATILE}
    if isinstance(value,list): return [canonical(v) for v in value]
    return value


def contains_labels(value):
    if isinstance(value,dict): return bool({'root_cause','expected_resolution'} & value.keys()) or any(contains_labels(v) for v in value.values())
    if isinstance(value,list): return any(contains_labels(v) for v in value)
    return False


def read_dataset(directory=DEFAULT_OUTPUT):
    directory=Path(directory)
    inputs={}; labels={}; errors=[]; fingerprint={}
    for split,expected in [('development',80),('test',40)]:
        inference_file=directory/'inference'/f'{split}.jsonl'
        label_file=directory/'labels'/f'{split}.jsonl'
        fingerprint[split]={name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in [('inference',inference_file),('labels',label_file)]}
        inputs[split]=[InferenceIncident.model_validate_json(line).model_dump() for line in inference_file.read_text().splitlines()]
        labels[split]=[Incident.model_validate_json(line).model_dump() for line in label_file.read_text().splitlines()]
        if len(inputs[split])!=expected or len(labels[split])!=expected: errors.append(f'{split}_count_mismatch')
        for name,rows in [('inference',inputs[split]),('labels',labels[split])]:
            if len({r['incident_id'] for r in rows})!=len(rows): errors.append(f'{split}_{name}_duplicate_ids')
        lookup={r['incident_id']:r for r in labels[split]}
        if set(lookup)!={r['incident_id'] for r in inputs[split]}: errors.append(f'{split}_label_join_mismatch')
        for row in inputs[split]:
            label=lookup.get(row['incident_id'],{})
            if any(row[k]!=label.get(k) for k in row): errors.append(f'{split}_inference_label_payload_mismatch')
            if contains_labels(row): errors.append(f'{split}_nested_answer_field_leakage')
            text=json.dumps(row)
            if any(label.get(k) and label[k] in text for k in ('root_cause','expected_resolution')): errors.append(f'{split}_answer_text_leakage')
    if {r['incident_id'] for r in inputs['test']} & {r['incident_id'] for r in inputs['development']}: errors.append('cross_split_id_overlap')
    groups=defaultdict(list); exact=defaultdict(list)
    for split,rows in inputs.items():
        for row in rows:
            groups[digest(canonical(row))].append({'split':split,'incident_id':row['incident_id']})
            exact[digest({k:v for k,v in row.items() if k!='incident_id'})].append(row['incident_id'])
    overlaps=[rows for rows in groups.values() if len({r['split'] for r in rows})>1]
    exact_duplicates=[ids for ids in exact.values() if len(ids)>1]
    if exact_duplicates: errors.append('exact_payload_duplicates')
    audit={'counts':{k:len(v) for k,v in inputs.items()},'blocking_errors':sorted(set(errors)),
        'exact_duplicate_groups':exact_duplicates,'normalized_duplicate_groups':[v for v in groups.values() if len(v)>1],
        'cross_split_normalized_overlap_groups':overlaps,'heldout_independent':not overlaps,
        'dataset_sha256':digest(fingerprint),'file_sha256':fingerprint,
        'normalization_ignored_keys':sorted(VOLATILE),
        'warning':'Normalized cross-split duplicates undermine independence; results measure this synthetic benchmark, not generalization.' if overlaps else None}
    return inputs,labels,audit


def select_cases(inputs,count=20,seed=314159):
    if not 1<=count<=40: raise ValueError('Select between 1 and 40 held-out cases')
    buckets=defaultdict(list)
    for row in inputs: buckets[row['status_code']].append(row)
    for rows in buckets.values(): rows.sort(key=lambda r:digest([seed,r['incident_id']]))
    chosen=[]
    while len(chosen)<count:
        for status in sorted(buckets):
            if buckets[status] and len(chosen)<count: chosen.append(buckets[status].pop(0))
    return chosen
