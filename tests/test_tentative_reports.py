"""Known recorded regression plus strict negative report eligibility."""
import json
from pathlib import Path
import pytest
from graph.state import validate_state
from graph.report_policy import tentative_report_eligible
from graph.workflow import _final_report


def recorded():
    return validate_state(json.loads((Path(__file__).parent/'fixtures/publication/tentative-report-state.json').read_text()))


def test_recorded_not_tested_case_has_safe_qualified_report_without_claiming_validation():
    s=recorded();assert tentative_report_eligible(s)
    update=_final_report(s)
    assert update['status']=='completed' and update['stop_reason']=='tentative_diagnostic_report'
    assert update['final_report'].diagnosis.status=='tentative'
    assert update['final_report'].validation_results[-1].status=='NOT_TESTED'
    assert update['final_report'].proposed_fix.requires_human_approval
    assert 'NOT_TESTED' in update['final_report'].summary

@pytest.mark.parametrize('failure',['no_diagnosis','inconclusive','no_plan','remediation','no_support','no_discrimination','unqualified','ambiguous','missing','validation_failed','not_run','inconclusive_validation','no_uncertainty','unsafe_request'])
def test_not_every_not_tested_output_can_publish(failure):
    s=recorded()
    if failure=='no_diagnosis':s['diagnosis']=None
    if failure=='inconclusive':s['diagnosis']=s['diagnosis'].model_copy(update={'status':'inconclusive'})
    if failure=='no_plan':s['proposed_fix']=None
    if failure=='remediation':s['proposed_fix']=s['proposed_fix'].model_copy(update={'plan_kind':'remediation'})
    if failure=='no_support':s['hypotheses'][0]=s['hypotheses'][0].model_copy(update={'supporting_evidence_ids':[]})
    if failure=='no_discrimination':s['diagnosis']=s['diagnosis'].model_copy(update={'discriminating_evidence_ids':[]})
    if failure=='unqualified':s['trusted_semantic']=None
    if failure=='ambiguous':s['evidence'].items[0]=s['evidence'].items[0].model_copy(update={'ambiguous':True})
    if failure=='missing':s['evidence']=s['evidence'].model_copy(update={'completeness':'missing'})
    if failure in ('validation_failed','not_run','inconclusive_validation'):
        s['validation_results'][-1]=s['validation_results'][-1].model_copy(update={'status':{'validation_failed':'failed','not_run':'not_run','inconclusive_validation':'inconclusive'}[failure]})
    if failure=='no_uncertainty':s['diagnosis']=s['diagnosis'].model_copy(update={'uncertainty':[]})
    if failure=='unsafe_request':s['proposed_fix']=s['proposed_fix'].model_copy(update={'steps':['Provide the complete JWT']})
    assert not tentative_report_eligible(s)
