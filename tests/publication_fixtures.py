"""Portable, explicitly synthetic preparation context. Never a paid approval.

Only tests monkeypatch preparation to this context. The application and real
preparation/accounting gates remain unchanged and require the private archive.
"""
import copy
import hashlib
import json
from pathlib import Path

from evaluation.dataset import digest

FIXTURES = Path(__file__).parent / 'fixtures' / 'publication'


def read(name):
    return json.loads((FIXTURES / name).read_text())


def manifest(kind, directory):
    result = read(f'{kind}-manifest-template.json')
    directory.mkdir(exist_ok=True)
    reference = directory / 'synthetic-reference-review.json'
    reference.write_text(json.dumps({'fixture_only': True, 'cases': [
        {'incident_id': c['incident_id'], 'preoutput_reference_review': {}}
        for c in result['cases']]}))
    template = directory / 'blank-postexecution-review.json'
    template.write_text(json.dumps({'reviews': [
        {'reviewer_id': None, 'timestamp': None, 'output_sha256': None,
         'dimension_ratings': {'evidence_grounding': None}}]}))
    result.update(status='SYNTHETIC_UNIT_FIXTURE_NOT_PAID_AUTHORIZATION',
                  execution_enabled=False, paid_approval_granted=False,
                  reference_review_path=str(reference),
                  human_review_template_path=str(template),
                  reference_review_validation={'complete': True, 'reviewed_cases': 2,
                                               'errors': [], 'fixture_only': True},
                  bindings_sha256={str(reference): hashlib.sha256(reference.read_bytes()).hexdigest()},
                  paid_history_sha256={})
    result['manifest_sha256'] = digest(result)
    return copy.deepcopy(result)
