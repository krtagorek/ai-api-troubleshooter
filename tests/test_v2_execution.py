"""Real runner integration with explicit offline CI doubles, never paid calls."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import provider
from evaluation import v2_execution as execution
from evaluation.v2 import V2Adapter
from evaluation.v2_preexecution import validate_review

REVIEW=Path('reports/phase136-v2/pre-execution-label-review.json')


def test_reviewed_preparation_freezes_hashes_without_approval():
    manifest=execution.prepare(REVIEW)
    assert manifest['prior_estimated_spend_usd']=='0.0170944'
    assert manifest['aggregate_budget_usd']=='2'
    assert manifest['maximum_new_calls']==26 and manifest['new_reservation_ceiling_usd']=='0.4046848'
    assert not manifest['paid_approval_granted']
    review=json.loads(REVIEW.read_text())
    result=validate_review(review)
    assert result['reviewed_cases']==5 and set(result['decisions'].values())=={'ACCEPT_WITH_ALTERNATIVES'}
    for case in review['cases']:
        assert case['human_review']['reviewer_id']=='rxk163'
        assert case['human_review']['accepted_alternatives']


def test_rejects_no_approval_and_changed_bindings(tmp_path,monkeypatch):
    manifest=execution.prepare(REVIEW)
    with pytest.raises(ValueError): execution.run_approved(manifest,'not-approved',tmp_path/'output')
    changed=copy.deepcopy(manifest);changed['maximum_new_calls']=100
    with pytest.raises(ValueError):execution.run_approved(changed,manifest['approval_sha256'],tmp_path/'output')
    assert not (tmp_path/'output').exists()


def test_actual_runner_v2_scoring_events_and_alternatives_offline(tmp_path,monkeypatch):
    manifest=execution.prepare(REVIEW)
    (tmp_path/'reports').mkdir()
    monkeypatch.setattr(execution,'ROOT',tmp_path)
    monkeypatch.setattr(execution,'prepare',lambda *a,**kw:manifest)
    monkeypatch.setattr(execution,'configuration_check',lambda:{'ready':True})
    monkeypatch.setenv('OPENAI_API_KEY','CI-only-no-paid-key')
    class ExplicitOfflineFake:
        def __init__(self,**kwargs): assert kwargs['max_retries']==0
        def with_structured_output(self,schema,**kwargs): self.schema=schema;return self
        def invoke(self,messages):
            text=str(messages)
            for forbidden in ('reference_cause_human_only','accepted_alternatives','family_id','label_review_rationale'):
                assert forbidden not in text
            name=self.schema.get('title') if isinstance(self.schema,dict) else self.schema.__name__
            content=({'connectivity':True} if name=='ConnectivityReply' else
                     {'classification':{'category':'unknown','reasoning':'Offline CI abstention'}} if name=='ClassifierOutput' else
                     {'evidence_contract':'evidence-handles-v1','evidence_snapshot':__import__('json').loads(messages[-1].content.split('\n',1)[1])['evidence_snapshot'],'selected_evidence_handles':[],'completeness':'missing','diagnostic_gaps':[]})
            return {'raw':SimpleNamespace(usage_metadata={'input_tokens':10,'output_tokens':5},response_metadata={'model_name':'explicit-offline-CI-fake'}),
                    'parsed':content if isinstance(self.schema,dict) else self.schema.model_validate(content),'parsing_error':None}
    monkeypatch.setattr(provider,'ChatOpenAI',ExplicitOfflineFake)
    output=tmp_path/'result'
    result=execution.run_approved(manifest,manifest['approval_sha256'],output)
    assert result['status']=='completed' and len(result['cases'])==5
    assert len(result['calls'])==11 and len(result['agent_events'])==20
    assert result['metrics']['agent_execution_coverage']['classifier']['completed']==5
    assert result['metrics']['agent_execution_coverage']['validator']['started']==0
    assert result['metrics']['semantic_accuracy'] is None
    assert result['usage']['budget_usd']=='2'
    assert float(result['usage']['conservative_spend_usd'])>.0170944
    for case in result['cases']:
        assert case['metrics']['scoring_version']=='v2-scoring-1'
        assert case['metrics']['human_approved_alternatives']
        assert case['sanitized_output']
    worksheets=json.loads((output/'postexecution-human-review.json').read_text())
    assert all(w['reviewer_id'] is None and all(v is None for v in w['dimension_ratings'].values()) for w in worksheets)
    with pytest.raises(ValueError):execution.run_approved(manifest,manifest['approval_sha256'],tmp_path/'another')


def test_shared_runner_stops_on_first_failure_and_records_failed_event(tmp_path,monkeypatch):
    from evaluation.runner import run_cases
    from evaluation.budget import Budget
    from decimal import Decimal
    from agents import error_classifier
    class Offline:
        stopped=False
        def __init__(self,*a):pass
        def with_structured_output(self,*a,**kw):return self
        def invoke(self,*a):return SimpleNamespace(connectivity=True)
    import evaluation.runner as runner
    monkeypatch.setattr(runner,'MeteredOpenAI',Offline)
    def bad_node(state):raise ValueError('Rejected case')
    monkeypatch.setattr(error_classifier,'create_agent',lambda **kw:bad_node)
    adapter=V2Adapter('reports/phase134-v2/plan.json')
    report={'notes':[],'selected_ids':adapter.plan['selected_ids']}
    result=run_cases([i.model_dump() for i in adapter.inputs()],None,'offline',Budget(Decimal('2'),Decimal('.4'),Decimal('1.6'),26),tmp_path,report,
                     stop_on_failure=True,scoring_adapter=adapter)
    assert result['status']=='stopped' and len(result['cases'])==1
    assert [e['status'] for e in result['agent_events']]==['started','failed']
    assert result['metrics']['classification_accuracy']['value'] is None
