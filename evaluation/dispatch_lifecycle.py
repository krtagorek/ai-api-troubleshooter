"""Durable single-use claims. Recovery inspects; it never replays a provider call."""
import json
import os
from pathlib import Path

from evaluation.dataset import digest
from evaluation.paired_harness import Audit
from evaluation.quality_pilot import sha


def history_snapshot():
    paths = set(Path('reports').rglob('evaluation.json')) | set(Path('reports').glob('.*dispatch*.json'))
    return {str(p): sha(p) for p in sorted(paths)}


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def reject_orphans(reports):
    for path in reports.glob('.paired-live-dispatch-*.json.lifecycle.jsonl'):
        marker = Path(str(path).removesuffix('.lifecycle.jsonl'))
        if not marker.exists():
            raise ValueError('Orphan dispatch lifecycle; manual reconciliation required')


class DispatchClaim:
    """Caller must hold the aggregate lock for this object's entire lifetime.

    The exclusive marker claims the FULL pilot ceiling before provider startup.
    Torn marker/event writes fail closed. A crash never permits automatic resume;
    uncertain usage retains the reservation pending explicit reconciliation.
    """
    def __init__(self, marker, record):
        self.marker = Path(marker)
        self.path = Path(str(marker) + '.lifecycle.jsonl')
        if self.marker.exists() or self.path.exists():
            raise FileExistsError('Approval already claimed or lifecycle incomplete')
        self.before = history_snapshot()
        self.audit = Audit(self.path)
        self.state = None
        try:
            self.transition('PREPARED')
            sync_directory(self.marker.parent)
            fd = os.open(self.marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump({**record, 'state': 'CLAIMED',
                           'budget_reservation': 'FULL_PILOT_CEILING_UNTIL_RECONCILED'}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            sync_directory(self.marker.parent)
            self.marker_sha256 = sha(self.marker)
            self.transition('CLAIMED')
        except BaseException:
            self.audit.close()
            raise

    def transition(self, state):
        allowed = {None: {'PREPARED'}, 'PREPARED': {'CLAIMED'},
                   'CLAIMED': {'RUNNING', 'FAILED'}, 'RUNNING': {'COMPLETED', 'FAILED'}}
        if state not in allowed.get(self.state, set()):
            raise ValueError('Invalid dispatch lifecycle transition')
        self.audit.append('dispatch_state', state=state)
        self.state = state

    def guard(self):
        reject_orphans(self.marker.parent)
        expected = {**self.before, str(self.marker): self.marker_sha256}
        if history_snapshot() != expected:
            raise ValueError('Unexpected dispatch/history activity during authorized run')
        if self.state != 'RUNNING':
            raise ValueError('Dispatch is not running')
        status = inspect_claim(self.marker)
        if status['state'] != 'RUNNING':
            raise ValueError('Dispatch journal changed; stop')

    def close(self):
        self.audit.close()


def inspect_claim(marker):
    """Read-only crash inspection. No retries, state edits, or provider startup."""
    try:
        record = json.loads(Path(marker).read_text())
        if record.get('state') != 'CLAIMED':
            raise ValueError('Invalid claim')
        events = [json.loads(s) for s in Path(str(marker) + '.lifecycle.jsonl').read_text().splitlines()]
        previous, state = None, None
        allowed = {None: {'PREPARED'}, 'PREPARED': {'CLAIMED'},
                   'CLAIMED': {'RUNNING', 'FAILED'}, 'RUNNING': {'COMPLETED', 'FAILED'}}
        for n, event in enumerate(events):
            if (event['sequence'] != n or event['previous'] != previous or
                    event['sha256'] != digest({k:v for k,v in event.items() if k != 'sha256'}) or
                    event['state'] not in allowed.get(state, set())):
                raise ValueError('Invalid lifecycle chain')
            previous, state = event['sha256'], event['state']
        if state not in ('CLAIMED', 'RUNNING', 'FAILED', 'COMPLETED'):
            raise ValueError('Incomplete claim')
        return {'state': state, 'automatic_resume_allowed': False,
                'manual_reconciliation_required': True,
                'maximum_reserved_usd': record['maximum_new_reservations_usd']}
    except (OSError, ValueError, KeyError, TypeError):
        return {'state': 'CORRUPT_OR_PARTIAL', 'automatic_resume_allowed': False,
                'manual_reconciliation_required': True}
