import json
from collections import Counter
from datetime import datetime

import pytest
from pydantic import ValidationError

from data.generate_incidents import (
    CAUSES, DEFAULT_OUTPUT, Incident, InferenceIncident, generate,
    inference_view, load_inference, write_dataset,
)


def test_counts_unique_ids_and_stratification():
    splits = generate()
    assert {key: len(value) for key, value in splits.items()} == {
        "development": 80, "test": 40}
    all_records = [r for records in splits.values() for r in records]
    assert len({r.incident_id for r in all_records}) == 120
    for split, records in splits.items():
        assert Counter(r.status_code for r in records) == {
            status: 10 if split == "development" else 5 for status in CAUSES}
        assert {r.request_metadata.evidence_quality for r in records} == {
            "complete", "missing", "ambiguous"}
        for status in CAUSES:
            assert len({r.root_cause for r in records if r.status_code == status}) == 3


def test_schema_logs_and_correlations():
    for records in generate().values():
        for record in records:
            assert Incident.model_validate_json(record.model_dump_json()) == record
            assert record.root_cause and record.expected_resolution
            assert record.expected_behavior and record.actual_behavior
            datetime.fromisoformat(record.request_metadata.timestamp)
            assert record.response_body["request_id"] == record.request_metadata.request_id
            assert record.response_body["timestamp"] == record.request_metadata.timestamp
            assert record.logs[0].event == "request_started"
            assert record.logs[-1].fields["status_code"] == record.status_code
            for log in record.logs:
                assert log.correlation_id == record.request_metadata.request_id
                assert log.service == record.service
                assert datetime.fromisoformat(log.timestamp).tzinfo is not None
            events = {log.event for log in record.logs}
            quality = record.request_metadata.evidence_quality
            assert ("diagnostic_observation" in events) == (quality == "complete")
            assert ("telemetry_incomplete" in events) == (quality == "ambiguous")


def test_reproducibility_and_seed_variation(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    write_dataset(first)
    write_dataset(second)
    for file in first.rglob("*.jsonl"):
        assert file.read_bytes() == (second / file.relative_to(first)).read_bytes()
    assert generate(1) != generate(2)


def test_saved_dataset_matches_generator():
    for split, records in generate().items():
        labeled = [Incident.model_validate_json(line) for line in
                   (DEFAULT_OUTPUT / "labels" / f"{split}.jsonl").read_text().splitlines()]
        assert labeled == records
        assert load_inference(split) == [inference_view(r) for r in records]


def test_inference_does_not_expose_labels():
    for records in generate().values():
        for record in records:
            view = inference_view(record)
            serialized = view.model_dump_json()
            assert "root_cause" not in serialized
            assert "expected_resolution" not in serialized
            assert record.root_cause not in serialized
            assert record.expected_resolution not in serialized
            with pytest.raises(ValidationError):
                InferenceIncident.model_validate_json(record.model_dump_json())


@pytest.mark.parametrize("mutation", [
    {"status_code": 999}, {"severity": "urgent"}, {"http_method": "DELETE"},
    {"unexpected": "field"}, {"incident_id": 12},
])
def test_reject_invalid_schema(mutation):
    record = generate()["development"][0].model_dump()
    with pytest.raises(ValidationError):
        Incident.model_validate({**record, **mutation})


def test_required_fields_and_loader_rejection(tmp_path):
    record = generate()["development"][0].model_dump()
    for field in Incident.model_fields:
        incomplete = {key: value for key, value in record.items() if key != field}
        with pytest.raises(ValidationError):
            Incident.model_validate(incomplete)
    destination = tmp_path / "inference"
    destination.mkdir()
    (destination / "test.jsonl").write_text(json.dumps(record) + "\n")
    with pytest.raises(ValidationError):
        load_inference("test", tmp_path)
    with pytest.raises(ValueError):
        load_inference("../labels/test", tmp_path)
