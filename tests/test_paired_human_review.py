import json
from unittest.mock import patch

from evaluation.paired_human_review import SOURCE,prepare_packet
from evaluation.quality_pilot import sha


def test_blinded_packet_preserves_recorded_outputs_and_waits_for_humans(tmp_path):
    before=sha(SOURCE)
    with patch('socket.socket.connect',side_effect=AssertionError('No network')):
        summary=prepare_packet(tmp_path/'review')
    assert summary['human_ratings']=='NOT_FILLED' and sha(SOURCE)==before
    packet=json.loads((tmp_path/'review/reviewer-packet.json').read_text())
    mapping=json.loads((tmp_path/'review/organizer-key.json').read_text())
    assert sorted(v['condition'] for v in mapping)==['baseline','enhanced']
    text=json.dumps(packet)
    assert 'baseline' not in text and 'enhanced' not in text
    assert len(packet['arms'])==2 and packet['case']=='V2-01-01'
    form=json.loads((tmp_path/'review/human-review.json').read_text())
    for r in form['reviews']:
        assert r['reviewer_id'] is None and r['timestamp'] is None
        assert all(v is None for v in r['dimension_ratings'].values())
        assert r['hold_adjudication'] is None and r['unsafe_credential_request'] is None
    original=[json.loads(s) for s in SOURCE.read_text().splitlines()]
    completed={e['key'].split('/')[1]:e['sanitized_result'] for e in original if e['kind']=='arm_completed'}
    for item in packet['arms']:
        condition=next(x['condition'] for x in mapping if x['label']==item['label'])
        assert item['recorded_result']['state']['evidence']==completed[condition]['state']['evidence']
        assert item['recorded_result']['state']['diagnosis']==completed[condition]['state']['diagnosis']
        assert item['recorded_result']['state']['proposed_fix']==completed[condition]['state']['proposed_fix']
