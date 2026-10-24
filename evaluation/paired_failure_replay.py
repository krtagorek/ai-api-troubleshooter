"""Offline explicit corrected-schema example; never repair live provider replies."""
import copy
import json
from pathlib import Path

import jsonschema
from pydantic import ValidationError

from agents import error_classifier, root_cause
from agents.common import AgentOutputError,ask,classification_or_unknown
from graph.state import RootCauseAnalystInput
from data.benchmark_v2.build import DEFAULT, rows
from evaluation.dataset import digest
from evaluation.paired_harness import _baseline
from evaluation.provider import provider_schema
from evaluation.selection_comparison import RecordedOutput
from graph.state import RootCauseAnalystOutput, initialize_state, validate_state
from tools.evidence_catalog import handle_catalog

AUDIT=Path('reports/diagnostic-quality-paired-live-run/audit.jsonl')


class Capture(RecordedOutput):
    def invoke(self,messages):
        self.messages=messages
        return super().invoke(messages)


def replay():
    events=[json.loads(s) for s in AUDIT.read_text().splitlines()]
    calls=next(e for e in reversed(events) if e['kind']=='actual_provider_accounting')['calls']
    original=calls[-1]['provider_structured_output']
    try: RootCauseAnalystOutput.model_validate(original)
    except ValidationError as exc:
        errors=[{'location':list(e['loc']),'type':e['type']} for e in exc.errors()]
    else: raise AssertionError('Historical failure must stay rejected')
    corrected=copy.deepcopy(original)
    # Offline example only: no runtime parser performs this migration.
    for hypothesis in corrected['hypotheses']:
        for gap in hypothesis.pop('missing_evidence',[]):
            corrected['diagnosis']['missing_evidence'].append({
                'description':gap,
                'required_to_resolve':'Obtain sanitized corroborating observations before treating this hypothesis as established'})
    corrected=RootCauseAnalystOutput.model_validate(corrected).model_dump(mode='json')
    incident=next(r for r in rows(DEFAULT/'inference/development.jsonl') if r['incident_id']=='V2-01-01')
    state=initialize_state(incident,'offline-schema-replay',max_retries=0)
    state.update(error_classifier.error_classifier(state,llm=RecordedOutput(calls[1]['structured_output'])))
    state.update(_baseline(state,RecordedOutput(calls[2]['structured_output']),'Select supplied handles only.'))
    capture=Capture(corrected)
    current_reasoning_accepted=True
    rejection=None
    try:state.update(root_cause.root_cause(state,llm=capture))
    except AgentOutputError as exc:
        current_reasoning_accepted=False;rejection=str(exc)
        # Legacy schema/grounding replay ONLY, not a bypass in any live agent.
        grounded=ask(Capture(corrected),RootCauseAnalystOutput,root_cause.SYSTEM_PROMPT,
            RootCauseAnalystInput(incident=state['sanitized_inputs'],classification=classification_or_unknown(state),
                                 evidence=state['evidence']))
        state.update(hypotheses=grounded.hypotheses,diagnosis=grounded.diagnosis)
    state=validate_state(state)
    schema=provider_schema(RootCauseAnalystOutput,capture.messages)
    jsonschema.validate(corrected,schema)
    try:jsonschema.validate(original,schema)
    except jsonschema.ValidationError:pass
    else:raise AssertionError('Strict wire schema must reject historical output')
    return {'mode':'OFFLINE_REPLAY_NO_PROVIDER_CALLS','original_output_sha256':digest(original),
            'original_rejected':True,'original_errors':errors,'corrected_schema_example':corrected,
            'corrected_pydantic_valid':True,'corrected_wire_valid':True,
            'corrected_agent_and_grounding_valid':current_reasoning_accepted,
            'corrected_grounding_valid':True,'current_reasoning_rejection':rejection,
            'snapshot':handle_catalog(state['sanitized_inputs'])['evidence_snapshot'],
            'semantic_accuracy':None,'warning':'Schema example only, not an automatic repair, new model response or human-reviewed diagnosis'}
