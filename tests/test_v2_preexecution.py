"""Offline pre-execution label review; simulated identities exist only in tests."""
import copy
import json

import pytest

from evaluation.v2_preexecution import worksheet,validate_review,prepare_runner,revision_proposal,main
from evaluation.dataset import contains_labels
from data.benchmark_v2.build import DEFAULT,rows
from evaluation import provider


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    def forbidden(*a,**kw): raise AssertionError('No model calls permitted')
    monkeypatch.setattr(provider,'ChatOpenAI',forbidden)


def completed():
    value=worksheet()
    for case in value['cases']:
        case['human_review']={'decision':'ACCEPT','reviewer_id':'test-only-reviewer',
                              'rationale':'Test-only human input contract simulation.',
                              'timestamp':'2025-01-01T00:00:00+00:00','accepted_alternatives':[]}
    return value


def test_pending_worksheet_contains_required_material_and_no_decisions():
    value=worksheet()
    assert len(value['cases'])==5
    for case in value['cases']:
        m=case['material']
        assert m['known_injected_cause'] and m['cause_inferable_from_evidence']
        assert m['cause_requiring_additional_evidence'] and m['expected_diagnostic_checks']
        assert m['potential_unsupported_assumptions'] and m['acceptable_safe_resolutions']
        assert not contains_labels(m['sanitized_inference_evidence'])
        assert case['human_review']['decision'] is None
    result=validate_review(value)
    assert result['reviewed_cases']==0 and len(result['errors'])==5
    with pytest.raises(ValueError):prepare_runner(value)


def test_all_five_valid_entries_do_not_approve_execution_or_expose_labels():
    value=completed();result=validate_review(value)
    assert result['all_cases_reviewed'] and result['labels_accepted']
    assert result['execution_approved'] is False
    handoff=prepare_runner(value)
    assert not handoff['execution_enabled'] and handoff['paid_approval_required']
    assert handoff['budget']['carried_prior_spend_usd']=='0.0170944'
    assert len(handoff['inputs'])==5 and not contains_labels(handoff)
    assert 'reference_cause_human_only' not in json.dumps(handoff)


@pytest.mark.parametrize('field,value',[('reviewer_id',' '),('rationale','short'),('timestamp',None),
 ('timestamp','2025-01-01T00:00:00'),('timestamp','2999-01-01T00:00:00Z'),('decision','APPROVE')])
def test_human_identity_rationale_timestamp_and_decision_required(field,value):
    doc=completed();doc['cases'][0]['human_review'][field]=value
    assert not validate_review(doc)['all_cases_reviewed']


@pytest.mark.parametrize('decision',['NEEDS_REVISION','REJECT'])
def test_reviewed_but_unaccepted_blocks_original_plan(decision):
    value=completed();value['cases'][0]['human_review']['decision']=decision
    result=validate_review(value)
    assert result['all_cases_reviewed'] and not result['labels_accepted']
    with pytest.raises(ValueError):prepare_runner(value)


def test_alternative_acceptance_requires_explicit_alternatives():
    value=completed();review=value['cases'][0]['human_review'];review['decision']='ACCEPT_WITH_ALTERNATIVES'
    assert not validate_review(value)['all_cases_reviewed']
    review['accepted_alternatives']=['Discovery metadata origin remains unverified']
    assert validate_review(value)['labels_accepted']


def test_missing_duplicate_unknown_and_modified_material_rejected():
    value=completed();value['cases'].pop()
    with pytest.raises(ValueError):validate_review(value)
    value=completed();value['cases'][1]=copy.deepcopy(value['cases'][0])
    with pytest.raises(ValueError):validate_review(value)
    value=completed();value['cases'][0]['material']['known_injected_cause']='altered'
    with pytest.raises(ValueError):validate_review(value)
    value=completed();value['plan_sha256']='wrong'
    with pytest.raises(ValueError):validate_review(value)


def test_revision_proposal_never_changes_original():
    original=rows(DEFAULT/'labels/test.jsonl')[0];before=copy.deepcopy(original)
    proposal={**original,'root_cause':'Proposed clarification for a future version only'}
    result=revision_proposal(original['incident_id'],proposal,'Draft clarification requiring human adjudication.')
    assert result['applied'] is False and result['human_decision'] is None
    assert result['proposal_version']=='v2.1-label-proposal-1'
    assert original==before


def test_offline_commands_create_validate_and_block_unreviewed_runner(tmp_path,capsys):
    path=tmp_path/'worksheet.json'
    assert main(['create','--worksheet',str(path)])==0
    assert main(['create','--worksheet',str(path)])==2
    assert main(['validate','--worksheet',str(path)])==2
    output=tmp_path/'handoff.json'
    assert main(['prepare-runner','--worksheet',str(path),'--output',str(output)])==2
    assert not output.exists()
