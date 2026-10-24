from decimal import Decimal
from unittest.mock import patch

from evaluation.dataset import digest
from evaluation.quality_pilot import prepare


def test_offline_manifest_is_deterministic_bound_and_unapproved():
    with patch('socket.socket.connect', side_effect=AssertionError('No network')):
        first = prepare()
        assert prepare() == first
    assert not first['execution_enabled'] and not first['paid_approval_granted']
    assert first['manifest_sha256'] == digest({k:v for k,v in first.items() if k != 'manifest_sha256'})
    assert [c['incident_id'] for c in first['cases']] == ['V2-01-01','V2-03-04']
    b = first['budget']
    assert b['maximum_new_calls_including_preflight'] == 1 + 2 * 2 * 5
    assert Decimal(b['maximum_new_reservations_usd']) == Decimal('0.3268608')
    assert Decimal(b['aggregate_conservative_ceiling_usd']) == Decimal('0.3819160') < Decimal('2')
    assert first['model']['retries'] == 0
    assert len(first['model']['schema_fields']) == 5
    assert set(first['conditions']) == {'baseline','enhanced'}
    assert first['conditions']['baseline']['trusted_augmentation'] is False
    assert first['conditions']['enhanced']['trusted_augmentation'] is True
