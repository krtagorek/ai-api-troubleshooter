"""Offline V2 family isolation, reproducibility, leakage and audit regressions."""
import copy
import json
from pathlib import Path

import pytest

from data.benchmark_v2.build import generate,write,audit,prepare_plan,DEFAULT,rows,diagnostic_facts
from evaluation.dataset import contains_labels
from agents.common import reference_value
from models.incident import Incident


def replace_rows(path,records):
    path.write_text(''.join(json.dumps(r)+'\n' for r in records))


def test_v2_counts_family_split_and_scalar_review_references():
    inputs,labels,manifest=generate()
    assert len(inputs['development'])==len(inputs['test'])==40
    assert generate()==(inputs,labels,manifest)
    dev={r['family_id'] for r in labels['development']}
    test={r['family_id'] for r in labels['test']}
    assert len(dev)==len(test)==8 and not dev & test
    for split in inputs:
        assert sum(r['evidence_quality']=='complete' for r in inputs[split])==24
        assert sum(r['evidence_quality']=='ambiguous' for r in inputs[split])==16
        for row,label in zip(inputs[split],labels[split]):
            assert not contains_labels(row) and 'family_id' not in row
            incident=Incident.model_validate(row)
            for pointer in label['evidence_pointers']:
                assert not isinstance(reference_value(incident,pointer),(dict,list))


def test_generation_matches_frozen_files_and_never_overwrites(tmp_path):
    target=tmp_path/'dataset'
    result=write(target)
    assert not result['blocking_errors']
    assert result['file_sha256']==audit(DEFAULT)['file_sha256']
    with pytest.raises(FileExistsError): write(target)


def test_audit_rejects_family_split_tampering(tmp_path):
    target=tmp_path/'dataset';write(target)
    manifest=json.loads((target/'manifest.json').read_text())
    member=next(v for v in manifest['membership'].values() if v['split']=='test')
    member['family_id']='issuer-canonicalization'
    (target/'manifest.json').write_text(json.dumps(manifest))
    result=audit(target)
    assert 'family_assignment_mismatch' in result['blocking_errors']
    assert 'cross_split_family_overlap' in result['blocking_errors']


def test_audit_rejects_nested_labels(tmp_path):
    target=tmp_path/'dataset';write(target)
    path=target/'inference/test.jsonl';records=rows(path)
    records[0]['response_body']['root_cause']='do not expose'
    replace_rows(path,records)
    assert 'answer_field_leakage' in audit(target)['blocking_errors']


def test_audit_catches_copied_evidence_despite_changed_ids_and_layout(tmp_path):
    target=tmp_path/'dataset';write(target)
    dev=rows(target/'inference/development.jsonl')[0]
    path=target/'inference/test.jsonl';records=rows(path)
    records[0]['logs']=[{'attributes':{'diagnostics':diagnostic_facts(dev)}, 'trace':'changed'}]
    replace_rows(path,records)
    assert 'cross_split_diagnostic_duplicate' in audit(target)['blocking_errors']


def test_audit_catches_near_duplicates(tmp_path):
    target=tmp_path/'dataset';write(target)
    dev=rows(target/'inference/development.jsonl')[20]
    facts=copy.deepcopy(diagnostic_facts(dev));facts['extra']=256
    path=target/'inference/test.jsonl';records=rows(path)
    records[0]['logs']=[{'fields':facts}]; replace_rows(path,records)
    assert 'cross_split_near_duplicate' in audit(target)['blocking_errors']


def test_five_case_plan_does_not_dispatch_and_carries_ledger(tmp_path,monkeypatch):
    import evaluation.provider as provider
    monkeypatch.setattr(provider,'ChatOpenAI',lambda *a,**kw:pytest.fail('No model calls'))
    path=tmp_path/'plan.json';plan=prepare_plan(DEFAULT,path)
    assert len(plan['selected_ids'])==len(set(plan['selected_ids']))==5
    assert plan['carried_prior_spend_usd']=='0.0170944'
    assert plan['aggregate_budget_usd']=='2'
    assert not plan['execution_enabled'] and not plan['paid_calls_authorized']
    assert plan['coverage']['evidence_quality']=={'complete':3,'ambiguous':2}
    assert not list(tmp_path.glob('**/.dispatch.lock'))
    with pytest.raises(FileExistsError): prepare_plan(DEFAULT,path)


def test_semantic_rubric_does_not_claim_completed_review():
    rubric=json.loads((Path(__file__).parents[1]/'data/benchmark_v2/semantic_rubric.json').read_text())
    assert rubric['review_status']=='NOT_REVIEWED'
    assert 'uncertainty_ambiguous' in rubric['dimensions']
    assert 'scored_coverage' in rubric['metrics']
