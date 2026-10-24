"""Ephemeral RC identity for offline transport tests, never paid authorization."""
import json
import pytest


@pytest.fixture(autouse=True)
def isolated_release_candidate(tmp_path,monkeypatch):
    from evaluation import release_candidate,rc_benchmark,next_v2_pilot
    path=tmp_path/'synthetic-test-only-code-identity.json'
    path.write_text(json.dumps(release_candidate.snapshot()))
    monkeypatch.setattr(release_candidate,'PATH',path)
    monkeypatch.setattr(rc_benchmark,'RC_PATH',path)
    monkeypatch.setattr(next_v2_pilot,'RC_PATH',path)
    return path
