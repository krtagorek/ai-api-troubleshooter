"""Deterministic CI only: no model connectivity and no paid calls permitted."""
import copy
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from data.generate_incidents import DEFAULT_OUTPUT,generate,write_dataset
from graph.state import initialize_state
from models.diagnosis import Classification,Diagnosis,Hypothesis,ProposedFix
from models.evidence import Evidence,EvidenceCollection
from models.validation import ValidationResult
from evaluation import provider
from evaluation.__main__ import main
from evaluation.budget import Budget,BudgetStopped
from evaluation.dataset import read_dataset,select_cases,digest
from evaluation.metrics import score,aggregate
from evaluation.reporting import write_report


@pytest.fixture(autouse=True)
def no_paid_provider(monkeypatch):
    monkeypatch.setattr(provider,'ChatOpenAI',Mock(side_effect=AssertionError('Paid calls forbidden in CI')))


def test_original_dataset_audit_detects_weak_holdout_without_rewriting():
    inputs,labels,audit=read_dataset()
    assert audit['counts']=={'development':80,'test':40}
    assert not audit['blocking_errors'] and not audit['exact_duplicate_groups']
    assert len(audit['cross_split_normalized_overlap_groups'])==34
    assert audit['heldout_independent'] is False
    chosen=select_cases(inputs['test'])
    assert len(chosen)==20 and chosen==select_cases(inputs['test'])
    assert {r['status_code'] for r in chosen}=={200,401,403,422,429,500,503,504}
    assert not ({r['incident_id'] for r in chosen}&{r['incident_id'] for r in inputs['development']})
    assert all('root_cause' not in json.dumps(r) and 'expected_resolution' not in json.dumps(r) for r in chosen)


def test_answer_leakage_and_payload_mismatch_rejected(tmp_path):
    write_dataset(tmp_path)
    p=tmp_path/'inference/test.jsonl';rows=[json.loads(s) for s in p.read_text().splitlines()]
    rows[0]['response_body']['root_cause']='Injected answer'
    p.write_text('\n'.join(json.dumps(r) for r in rows))
    audit=read_dataset(tmp_path)[2]
    assert 'test_nested_answer_field_leakage' in audit['blocking_errors']
    assert 'test_inference_label_payload_mismatch' in audit['blocking_errors']


def test_duplicate_id_and_exact_payload_checks(tmp_path):
    write_dataset(tmp_path)
    p=tmp_path/'inference/test.jsonl';lines=p.read_text().splitlines();lines[1]=lines[0];p.write_text('\n'.join(lines))
    errors=read_dataset(tmp_path)[2]['blocking_errors']
    assert 'test_inference_duplicate_ids' in errors and 'exact_payload_duplicates' in errors


def budget(limit='1',calls=3): return Budget(Decimal(limit),Decimal('.4'),Decimal('1.6'),calls)


def test_pre_dispatch_reservation_and_uncertain_usage_budget():
    b=budget('.02');first=b.reserve()
    with pytest.raises(BudgetStopped): b.reserve()
    assert len(b.calls)==1
    b=budget();first=b.reserve();reserved=b.reserved_or_spent
    with pytest.raises(BudgetStopped):b.settle(first,None,None,.1)
    assert b.reserved_or_spent==reserved and b.summary()['unresolved_calls']==1
    with pytest.raises(BudgetStopped):b.reserve()


def test_known_usage_returns_only_unused_reservation():
    b=budget(calls=1);row=b.reserve();b.settle(row,100,50,.25)
    assert b.reserved_or_spent==Decimal('.00012')
    assert b.summary()['observed_input_tokens']==100
    with pytest.raises(BudgetStopped):b.reserve()


@pytest.mark.parametrize('limit',['0','-1','NaN','Infinity'])
def test_invalid_budget_fails_closed(limit):
    with pytest.raises(ValueError):budget(limit)


def test_real_preflight_configuration_is_explicit_and_safe(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    assert configuration_blocked()
    monkeypatch.setenv('OPENAI_API_KEY','test-secret-never-print')
    monkeypatch.setenv('OPENAI_BASE_URL','http://untrusted')
    result=provider.configuration_check()
    assert not result['ready'] and 'test-secret' not in json.dumps(result)


def configuration_blocked():return not provider.configuration_check()['ready']


def state_and_label():
    label=next(r for r in generate()['test'] if r.request_metadata.evidence_quality=='complete')
    state=initialize_state(label,'test-eval')
    state['classification']=Classification(category='unknown',reasoning='Unknown')
    message=label.logs[1].message
    state['evidence']=EvidenceCollection(completeness='complete',items=[Evidence(evidence_id='e1',source='log',observation=message,reference='/logs/1/message',reliability=1)])
    state['hypotheses']=[Hypothesis(hypothesis_id='h1',description=label.root_cause,confidence=.8,supporting_evidence_ids=['e1'])]
    state['diagnosis']=Diagnosis(status='supported',summary=label.root_cause,selected_hypothesis_id='h1',confidence=.8)
    state['proposed_fix']=ProposedFix(summary=label.expected_resolution,steps=[label.expected_resolution],verification_steps=['Review safely'],evidence_ids=['e1'])
    state['validation_results']=[ValidationResult(status='NOT_TESTED')]
    return state,label.model_dump()


def test_grounding_and_quality_scores_are_label_separated():
    state,label=state_and_label();metrics=score(state,label)
    assert metrics['root_cause_lexical_correct'] and metrics['resolution_lexical_correct']
    assert metrics['grounded_items']==1 and metrics['validation_truthful']
    assert metrics['human_semantic_review_required'] and metrics['fix_effectiveness'] is None
    state['evidence'].items[0].observation='Invented observation'
    metrics=score(state,label)
    assert metrics['grounded_items']==0 and 'unsupported_evidence_observation' in metrics['hallucination_flags']


def test_uncertain_cases_do_not_reward_confident_guessing():
    state,label=state_and_label();state['sanitized_inputs'].evidence_quality='missing'
    metrics=score(state,label);assert not metrics['root_cause_eligible'] and metrics['abstention_appropriate'] is False
    state['diagnosis']=Diagnosis(status='inconclusive',summary='Unknown',confidence=0,uncertainty=['Need trace'])
    assert score(state,label)['abstention_appropriate'] is True


def test_failed_and_unattempted_cases_are_separate_from_diagnostic_accuracy():
    inputs,_,_=read_dataset();planned=select_cases(inputs['test'])
    metrics=aggregate([{'status':'failed','metrics':None}],planned)
    assert metrics['classification_accuracy']=={'numerator':0,'denominator':0,'value':None}
    assert metrics['root_cause_lexical_accuracy_complete_evidence']['value'] is None
    assert metrics['scored_case_coverage']['denominator']==20
    assert metrics['workflow_failure_rate']['value']==1
    assert metrics['semantic_accuracy']=='NOT_HUMAN_REVIEWED'


def test_report_zero_calls_is_not_a_quality_result(tmp_path):
    write_report(tmp_path,{'status':'awaiting_approval','metrics':None,'selected_ids':['x'],'real_model_execution':False,'notes':['No calls made.']})
    value=json.loads((tmp_path/'evaluation.json').read_text())
    assert value['metrics'] is None
    assert 'awaiting_approval' in (tmp_path/'evaluation.md').read_text()


def test_cli_requires_reviewed_plan_and_explicit_overlap_acknowledgment(tmp_path,capsys):
    directory=tmp_path/'run'
    assert main(['plan','--model','gpt-4.1-mini-2025-04-14','--input-rate','.4','--output-rate','1.6','--budget-usd','2','--output',str(directory)])==0
    plan=json.loads((directory/'plan.json').read_text())
    assert len(plan['selected_ids'])==20
    assert main(['run','--plan',str(directory/'plan.json'),'--approve-paid','wrong'])==2
    assert main(['run','--plan',str(directory/'plan.json'),'--approve-paid',plan['approval_sha256']])==2
    plan['budget_usd']='999';(directory/'plan.json').write_text(json.dumps(plan))
    assert main(['preflight','--plan',str(directory/'plan.json')])==2
    provider.ChatOpenAI.assert_not_called()


def test_provider_metering_uses_real_adapter_protocol_only_with_explicit_test_double(monkeypatch):
    # This is an isolated CI fake, never a CLI fallback or evaluation result.
    from evaluation.runner import ConnectivityReply
    class Fake:
        def __init__(self,**kwargs):
            assert kwargs['base_url']=='https://api.openai.com/v1' and kwargs['max_retries']==0
        def with_structured_output(self,*args,**kwargs):return self
        def invoke(self,messages):return {'raw':SimpleNamespace(usage_metadata={'input_tokens':10,'output_tokens':5},response_metadata={'model_name':'explicit-test-double'}),'parsed':ConnectivityReply(connectivity=True),'parsing_error':None}
    monkeypatch.setenv('OPENAI_API_KEY','test-only')
    monkeypatch.setattr(provider,'ChatOpenAI',Fake)
    from langchain_core.messages import HumanMessage
    b=budget();result=provider.MeteredOpenAI('test-only',b,lambda:None).with_structured_output(ConnectivityReply).invoke([HumanMessage(content='test')])
    assert result.connectivity and b.summary()['calls']==1 and b.summary()['observed_input_tokens']==10


def test_real_workflow_runner_does_not_send_labels_to_explicit_ci_fake(monkeypatch,tmp_path):
    from evaluation.runner import run_cases,ConnectivityReply
    inspected=[]
    class Fake:
        def __init__(self,**kwargs):pass
        def with_structured_output(self,schema,**kwargs):self.schema=schema;return self
        def invoke(self,messages):
            text=str(messages);assert 'root_cause' not in text and 'expected_resolution' not in text
            inspected.append(text)
            content=({'connectivity':True} if self.schema is ConnectivityReply else
                {'classification':{'category':'unknown','reasoning':'Uncertain'}} if (self.schema.get('title') if isinstance(self.schema,dict) else self.schema.__name__)=='ClassifierOutput' else
                {'evidence_contract':'evidence-handles-v1','evidence_snapshot':__import__('json').loads(messages[-1].content.split('\n',1)[1])['evidence_snapshot'],'selected_evidence_handles':[],'completeness':'missing','diagnostic_gaps':[]})
            return {'raw':SimpleNamespace(usage_metadata={'input_tokens':100,'output_tokens':20},response_metadata={'model_name':'explicit-CI-fake'}),'parsed':content if isinstance(self.schema,dict) else self.schema.model_validate(content),'parsing_error':None}
    monkeypatch.setenv('OPENAI_API_KEY','CI-only-not-a-real-key');monkeypatch.setattr(provider,'ChatOpenAI',Fake)
    inputs,labels,audit=read_dataset();chosen=select_cases(inputs['test'],1)
    report={'status':'offline_CI_test','notes':[],'selected_ids':[r['incident_id'] for r in chosen]}
    result=run_cases(chosen,{r['incident_id']:r for r in labels['test']},'explicit-CI-fake',budget(),tmp_path,report)
    assert result['status']=='completed' and len(inspected)==3
    assert result['cases'][0]['workflow_status']=='awaiting_evidence'
    assert result['metrics']['classification_accuracy']['value']==0


def test_provider_failure_consumes_reservation_and_never_logs_raw_exception(monkeypatch):
    class Failed:
        def __init__(self,**kwargs):pass
        def with_structured_output(self,*args,**kwargs):return self
        def invoke(self,messages):raise RuntimeError('password=sensitive-provider-error')
    monkeypatch.setattr(provider,'ChatOpenAI',Failed);monkeypatch.setenv('OPENAI_API_KEY','CI-only')
    from evaluation.runner import ConnectivityReply
    from langchain_core.messages import HumanMessage
    b=budget();client=provider.MeteredOpenAI('CI-only',b,lambda:None)
    with pytest.raises(provider.ProviderStopped):client.with_structured_output(ConnectivityReply).invoke([HumanMessage(content='test')])
    assert client.stopped and b.summary()['unresolved_calls']==1
    assert b.reserved_or_spent>0 and 'sensitive-provider-error' not in json.dumps(b.calls)
    with pytest.raises(provider.ProviderStopped):client.with_structured_output(ConnectivityReply).invoke([HumanMessage(content='test')])
    assert len(b.calls)==1


def test_three_case_pilot_limits_dispatch_and_preserves_plan(monkeypatch,tmp_path):
    import importlib
    cli=importlib.import_module('evaluation.__main__')
    runner=importlib.import_module('evaluation.runner')
    directory=tmp_path/'pilot'
    assert main(['plan','--model','gpt-4.1-mini-2025-04-14','--input-rate','.4','--output-rate','1.6','--budget-usd','2','--output',str(directory)])==0
    path=directory/'plan.json';original=path.read_bytes();plan=json.loads(original)
    monkeypatch.setattr(cli,'configuration_check',lambda:{'ready':True})
    def offline_runner(selected,labels,model,budget,directory,report,**kwargs):
        assert [r['incident_id'] for r in selected]==plan['selected_ids'][:3]
        assert budget.max_calls==16 and budget.limit==Decimal('2')
        assert kwargs['stop_on_failure'] is True
        assert report['remaining_ids']==plan['selected_ids'][3:]
        assert report['scope']=='three_case_pilot'
        return dict(report,status='completed',cases=[])
    monkeypatch.setattr(runner,'run_cases',offline_runner)
    args=['run','--plan',str(path),'--approve-paid',plan['approval_sha256'],'--acknowledge-overlap','--case-limit','3']
    assert main(args)==0
    assert path.read_bytes()==original
    assert main(args)==2 # Single-use dispatch cannot silently reset its budget.
    provider.ChatOpenAI.assert_not_called()
