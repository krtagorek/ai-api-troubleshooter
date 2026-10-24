"""Development-only selection tuning. No labels, test cases or providers used."""
import pytest
from data.benchmark_v2.build import DEFAULT,rows
from models.incident import Incident
from graph.state import initialize_state
from graph.routing import evidence_sufficient
from tools.evidence_selection import assess_selection
from tools.evidence_catalog import resolve_evidence,handle_catalog
from agents import common,log_investigator,root_cause
from tests.test_agents import FakeLLM


@pytest.fixture(autouse=True)
def no_paid(monkeypatch):
    monkeypatch.setattr(common,'ChatOpenAI',lambda *a,**kw:pytest.fail('No provider calls'))


def development(index=0):return Incident.model_validate(rows(DEFAULT/'inference/development.jsonl')[index])


@pytest.mark.parametrize('index',[0,5,10,15,20,25,30,35])
def test_development_comparisons_delivered_not_status_only(index):
    i=development(index);s=initialize_state(i,'dev-selection')
    env=handle_catalog(i);h=next(e['handle'] for e in env['evidence_catalog'] if e['reference']=='/status_code')
    raw={'evidence_contract':env['evidence_contract'],'evidence_snapshot':env['evidence_snapshot'],
         'selected_evidence_handles':[h],'completeness':'complete','diagnostic_gaps':[]}
    llm=FakeLLM(raw);update=log_investigator.log_investigator(s,llm=llm);s.update(update)
    if index==10:
        # Existing redaction treats a ten-digit timestamp observation as a
        # possible personal identifier. Never bypass it to deliver a comparison.
        assert s['evidence'].completeness=='partial' and s['evidence'].missing
        assert not evidence_sufficient(s)
        return
    assert len(s['evidence'].items)>1
    assessment=assess_selection(i,[e.evidence_id for e in s['evidence'].items],max_added=0)
    assert assessment.review_complete and assessment.has_discriminating_candidates
    assert evidence_sufficient(s)
    assert raw['selected_evidence_handles']==[h] # Original model output is not rewritten.
    for item in s['evidence'].items:
        assert resolve_evidence(i,item.evidence_id).observation==item.observation


def test_sides_of_comparison_false_values_and_contradictions_kept():
    i=development();i.logs=[{'correlation_id':'same','fields':{'observed_latency_ms':800,'budget_ms':400,'healthy':False}},
                          {'correlation_id':'same','fields':{'observed_latency_ms':200,'healthy':True}}]
    result=assess_selection(i,[resolve_evidence(i,'/status_code').evidence_id])
    observations=[resolve_evidence(i,e).observation for e in result.added_ids]
    assert {'800','400','200','false','true'}<=set(observations)


def test_routing_does_not_trust_complete_status_only():
    from models.evidence import Evidence,EvidenceCollection
    i=development();s=initialize_state(i,'trusted-check')
    e=resolve_evidence(i,'/status_code')
    s['evidence']=EvidenceCollection(completeness='complete',items=[Evidence(evidence_id=e.evidence_id,source=e.source,reference=e.reference,observation=e.observation,reliability=.5)])
    assert not evidence_sufficient(s)


def test_no_candidates_no_false_sufficiency_and_no_empty_selection_override():
    i=development();i.logs=[];i.response_body={'status':401,'result':'request unsuccessful'}
    env=handle_catalog(i);h=next(e['handle'] for e in env['evidence_catalog'] if e['reference']=='/status_code')
    s=initialize_state(i,'no-diagnostics')
    result=log_investigator.log_investigator(s,llm=FakeLLM({'evidence_contract':env['evidence_contract'],'evidence_snapshot':env['evidence_snapshot'],
                'selected_evidence_handles':[h],'completeness':'complete','diagnostic_gaps':[]}))
    assert result['evidence'].completeness=='partial' and result['evidence'].missing
    i=development();env=handle_catalog(i)
    result=log_investigator.log_investigator(initialize_state(i,'empty'),llm=FakeLLM({'evidence_contract':env['evidence_contract'],
            'evidence_snapshot':env['evidence_snapshot'],'selected_evidence_handles':[],'completeness':'complete','diagnostic_gaps':[]}))
    assert not result['evidence'].items and result['evidence'].completeness=='missing'


def test_bounded_augmentation_never_splits_comparison_bundle():
    i=development();selected=[resolve_evidence(i,'/status_code').evidence_id]
    result=assess_selection(i,selected,max_added=1)
    assert result.omitted_ids and not result.added_ids and not result.review_complete


def test_one_fact_record_is_not_two_discriminating_observations():
    i=development();i.logs=[{'records':[{'name':'operation','value':'read'}]}]
    selected=[resolve_evidence(i,'/status_code').evidence_id]
    result=assess_selection(i,selected)
    assert result.review_complete and not result.analysis_ready


def test_downstream_can_cite_augmented_development_evidence():
    i=development();env=handle_catalog(i);s=initialize_state(i,'dev-analysis')
    h=next(e['handle'] for e in env['evidence_catalog'] if e['reference']=='/status_code')
    s.update(log_investigator.log_investigator(s,llm=FakeLLM({'evidence_contract':env['evidence_contract'],
      'evidence_snapshot':env['evidence_snapshot'],'selected_evidence_handles':[h],'completeness':'complete','diagnostic_gaps':[]})))
    fact=next(e for e in s['evidence'].items if e.reference.startswith('/logs/0/fields/'))
    handle=next(e['handle'] for e in env['evidence_catalog'] if e['reference']==fact.reference)
    output={'evidence_contract':env['evidence_contract'],'evidence_snapshot':env['evidence_snapshot'],
      'hypotheses':[{'hypothesis_id':'h1','description':'Possible configuration discrepancy','confidence':.4,'supporting_evidence_ids':[handle]}],
      'diagnosis':{'status':'tentative','summary':'Compare observed configuration','selected_hypothesis_id':'h1','confidence':.4}}
    llm=FakeLLM(output);result=root_cause.root_cause(s,llm=llm)
    assert result['hypotheses'][0].supporting_evidence_ids==[fact.evidence_id]
    assert fact.observation in str(llm.calls) and 'ev:' not in str(llm.calls)
