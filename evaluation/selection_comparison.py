"""Offline selection comparison, not a model evaluation or causal accuracy score.

Development stress responses are held fixed. One approved historical response is
a known regression fixture only. No human relevance labels are synthesized.
"""
from dataclasses import asdict
import json
from pathlib import Path
import tiktoken

from data.benchmark_v2.build import DEFAULT,rows
from models.incident import Incident
from models.evidence import Evidence,EvidenceCollection
from graph.state import initialize_state
from graph.routing import evidence_sufficient
from agents import log_investigator,root_cause
from tools.evidence_catalog import build_catalog,handle_catalog,resolve_handles
from tools.evidence_selection import assess_selection
from evaluation.provider import provider_schema
from evaluation.dataset import digest


class RecordedOutput:
    def __init__(self,output):self.output=output
    def with_structured_output(self,schema,**kwargs):self.schema=schema;return self
    def invoke(self,messages):return self.schema.model_validate(self.output)


class RootInputCapture:
    """Capture current model boundary; return explicit CI abstention, no provider."""
    def with_structured_output(self,schema,**kwargs):self.schema=schema;return self
    def invoke(self,messages):
        self.messages=messages
        return self.schema.model_validate({'hypotheses':[],'diagnosis':{
            'status':'inconclusive','summary':'Offline capture fixture only','uncertainty':['Not a real model result']}})


def _token_measure(state):
    capture=RootInputCapture();root_cause.root_cause(state,llm=capture)
    messages=[{'role':m.type,'content':m.content} for m in capture.messages]
    schema=provider_schema(capture.schema,capture.messages)
    schema=schema.model_json_schema() if isinstance(schema,type) else schema
    serialized=json.dumps({'messages':messages,'schema':schema},ensure_ascii=False)
    # Cached tokenizer only; never permit a vocabulary download in this tool.
    import requests
    from unittest.mock import patch
    with patch.object(requests,'get',side_effect=RuntimeError('Tokenizer network prohibited')):
        encoder=tiktoken.encoding_for_model('gpt-4.1-mini-2025-04-14')
    return {'tokens':len(encoder.encode(serialized)),'bytes':len(serialized.encode()),
            'scope':'Current root prompt/messages + dynamic output schema; excludes SDK wrappers/provider framing. Comparison holds the prompt fixed.',
            'selected_reference_count':len(state['evidence'].items)}


def compare(row,output,*,source_kind):
    incident=Incident.model_validate(row)
    selected=resolve_handles(incident,output['selected_evidence_handles'],output['evidence_contract'],output['evidence_snapshot'])
    before=initialize_state(incident,'offline-before-selection')
    before['evidence']=EvidenceCollection(items=[Evidence(evidence_id=e.evidence_id,reference=e.reference,
        source=e.source,observation=e.observation,reliability=.5) for e in selected],
        completeness=output['completeness'] if selected else 'missing',missing=output['diagnostic_gaps'])
    after=initialize_state(incident,'offline-after-selection')
    after.update(log_investigator.log_investigator(after,llm=RecordedOutput(output)))
    candidate_ids=set(assess_selection(incident,[],max_added=0).omitted_ids)
    assessment=assess_selection(incident,[e.evidence_id for e in selected],max_added=32 if selected else 0)
    source_catalog=build_catalog(incident)
    sides={}
    for name,state in [('before',before),('after',after)]:
        delivered={e.evidence_id for e in state['evidence'].items}
        heuristic=assess_selection(incident,list(delivered),max_added=0)
        ready=(bool(state['evidence'].items) and state['evidence'].completeness=='complete' and not state['evidence'].missing) if name=='before' else evidence_sufficient(state)
        covered=delivered & candidate_ids
        sides[name]={'investigator_selected_count':len(selected),'python_added_count':0 if name=='before' else len(assessment.added_ids),
                     'delivered_count':len(delivered),'delivered_ids':sorted(delivered),
                     'candidate_coverage':{'n':len(covered),'d':len(candidate_ids),'value':len(covered)/len(candidate_ids) if candidate_ids else None},
                     'missed_candidate_ids':sorted(candidate_ids-delivered),'analysis_ready':ready,
                     'completeness':state['evidence'].completeness,
                     'complete_with_uncovered_or_insufficient_candidates':state['evidence'].completeness=='complete' and not heuristic.analysis_ready,
                     'held_on_generator_ambiguous_case':not ready and incident.evidence_quality=='ambiguous',
                     'ready_on_generator_ambiguous_case':ready and incident.evidence_quality=='ambiguous',
                     'held_on_generator_complete_case':not ready and incident.evidence_quality=='complete',
                     'root_input_estimate':_token_measure(state) if state['evidence'].items else None}
    return {'incident_id':incident.incident_id,'source_kind':source_kind,
            'complete_source_evidence':incident.model_dump(mode='json'),'source_catalog_count':len(source_catalog),
            'source_sha256':digest(incident.model_dump(mode='json')),'investigator_response_held_fixed':output,
            'selected_ids':[e.evidence_id for e in selected],'trusted_additions':list(assessment.added_ids),
            'selected_facts':[asdict(e) for e in selected],
            'python_added_facts':[asdict(source_catalog[i]) for i in assessment.added_ids],
            'missed_candidate_facts':[asdict(source_catalog[i]) for i in sides['after']['missed_candidate_ids']],
            'trusted_coverage_assessment':asdict(assessment),'candidate_ids':sorted(candidate_ids),'before':sides['before'],'after':sides['after'],
            'human_selection_precision':None,'human_selection_recall':None,
            'reference_selection_status':'NO_HUMAN_ADJUDICATED_EXHAUSTIVE_SELECTION_SET',
            'causal_accuracy':None,'unsupported_claim_comparison':'NOT_MEASURED: no before/after real root-cause outputs generated'}


def run_comparison():
    cases=[]
    for row in rows(DEFAULT/'inference/development.jsonl'):
        incident=Incident.model_validate(row);envelope=handle_catalog(incident)
        status=next(e['handle'] for e in envelope['evidence_catalog'] if e['reference']=='/status_code')
        output={'evidence_contract':envelope['evidence_contract'],'evidence_snapshot':envelope['evidence_snapshot'],
                'selected_evidence_handles':[status],'completeness':'complete','diagnostic_gaps':[]}
        cases.append(compare(row,output,source_kind='DEVELOPMENT_STATUS_ONLY_STRESS_FIXTURE_NOT_REAL_LLM'))
    # User-approved historical fixture: never sample other held-out records.
    path=Path('reports/phase1312-minimal-real-run/evaluation.json')
    historical=json.loads(path.read_text());known=historical['cases'][0]['sanitized_output']['sanitized_inputs']
    original=next(c['structured_output'] for c in historical['calls'] if c['stage']=='LiveLogInvestigatorOutput')
    fixture=compare(known,original,source_kind='KNOWN_REVIEWED_REGRESSION_NOT_INDEPENDENT_HOLDOUT')
    def summarize(population,side):
        return {'cases':len(population),'investigator_selected':sum(c[side]['investigator_selected_count'] for c in population),
                'python_added':sum(c[side]['python_added_count'] for c in population),
                'delivered':sum(c[side]['delivered_count'] for c in population),
                'candidate_covered':sum(c[side]['candidate_coverage']['n'] for c in population),
                'candidate_total':sum(c[side]['candidate_coverage']['d'] for c in population),
                'missed_candidates':sum(len(c[side]['missed_candidate_ids']) for c in population),
                'analysis_ready':sum(c[side]['analysis_ready'] for c in population),
                'held_generator_ambiguous':sum(c[side]['held_on_generator_ambiguous_case'] for c in population),
                'ready_generator_ambiguous':sum(c[side]['ready_on_generator_ambiguous_case'] for c in population),
                'held_generator_complete':sum(c[side]['held_on_generator_complete_case'] for c in population),
                'complete_but_candidate_check_fails':sum(c[side]['complete_with_uncovered_or_insufficient_candidates'] for c in population),
                'root_prompt_schema_tokens_estimate':sum(c[side]['root_input_estimate']['tokens'] for c in population if c[side]['root_input_estimate'])}
    return {'mode':'OFFLINE_CONTROLLED_SELECTION_COMPARISON','development_cases':cases,'known_regression':fixture,
            'development_summary':{side:summarize(cases,side) for side in ('before','after')},
            'known_regression_summary':{side:summarize([fixture],side) for side in ('before','after')},
            'human_precision_recall':'NOT_AVAILABLE: prior human diagnosis ratings and label pointers are not relevance annotations.',
            'limits':['Same simulated investigator response before/after; no claim the new prompt improves real selection.',
                      'Candidate coverage is defined by the current heuristic and is not independent relevance or causal accuracy.',
                      'Generator ambiguous/complete flags are synthetic expectations, not human sufficiency judgments.',
                      'Root input already includes complete source incident in both conditions; explicit citeable selection differs.',
                      'A current-prompt capture fixture is used only for token measurement, not semantic evaluation.'],
            'paid_calls':0,'carried_spend_usd':'0.0260008','aggregate_budget_usd':'2','dispatch_prepared':False}
