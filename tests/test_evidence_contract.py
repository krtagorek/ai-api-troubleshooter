"""Offline provenance regressions; no provider or database access."""
import copy
import json
from pathlib import Path

import pytest

from agents import common, log_investigator
from evaluation.dataset import read_dataset
from graph.state import initialize_state, serialize_state, LogInvestigatorOutput
from models.evidence import REFERENCE_SOURCES, reference_source


class RecordedLLM:
    def __init__(self, output):
        self.output = output

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages):
        return copy.deepcopy(self.output)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No provider calls permitted")
    monkeypatch.setattr(common, "ChatOpenAI", forbidden)


@pytest.fixture
def recorded():
    # Sanitized snapshot of the real pilot response; CI needs no generated reports.
    output = json.loads((Path(__file__).parent / "fixtures/inc0110_investigator.json").read_text())
    incident = next(r for r in read_dataset()[0]["test"] if r["incident_id"] == "INC-0110")
    return initialize_state(incident, "offline-provenance-replay"), output


@pytest.mark.parametrize("root,source", list(REFERENCE_SOURCES.items()))
def test_canonical_mapping(root, source):
    assert reference_source('/' + root) == source


def test_recorded_inc0110_replay_derives_user_provenance(recorded):
    state, output = recorded
    original = copy.deepcopy(output)
    before = serialize_state(state)
    update = log_investigator.legacy_log_investigator(state, llm=RecordedLLM(output))
    assert len(update['evidence'].items) == 7
    for item in update['evidence'].items:
        assert item.source == reference_source(item.reference)
    assert all(i.source == 'user' for i in update['evidence'].items
               if i.reference in ('/expected_behavior', '/actual_behavior'))
    assert output == original and serialize_state(state) == before


@pytest.mark.parametrize('reference', [None, '', 'logs/0', '/root_cause',
    '/expected_resolution', '/endpoint', '/response_body/~2bad', '/logs/999/message',
    '/response_body/nonexistent', '/logs/-1/message'])
def test_unsupported_missing_or_invalid_references_still_fail(recorded, reference):
    state, output = recorded
    output['evidence']['items'][0]['reference'] = reference
    with pytest.raises(common.AgentOutputError):
        log_investigator.legacy_log_investigator(state, llm=RecordedLLM(output))


def test_derived_provenance_never_accepts_fabricated_observation(recorded):
    state, output = recorded
    output['evidence']['items'][0].update(source='documentation', observation='Invented validation passed')
    with pytest.raises(common.AgentOutputError, match='not present'):
        log_investigator.legacy_log_investigator(state, llm=RecordedLLM(output))


def test_structured_schema_and_prompt_explain_provenance():
    schema = LogInvestigatorOutput.model_json_schema()['$defs']['Evidence']['properties']
    assert 'Trusted code derives' in schema['source']['description']
    assert 'expected_behavior' in schema['reference']['description']
    assert 'never documentation' in log_investigator.SYSTEM_PROMPT


def test_pointer_escaped_keys_resolve_and_unknown_roots_reject(recorded):
    state, _ = recorded
    state['sanitized_inputs'].response_body = {'a/b': {'~key': 'quoted'}}
    assert common.reference_value(state['sanitized_inputs'], '/response_body/a~1b/~0key') == 'quoted'
    with pytest.raises(ValueError):
        reference_source('/response_bodyevil/a')
