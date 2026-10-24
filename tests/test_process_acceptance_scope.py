"""Test-only process hooks cannot target primary databases or arbitrary outputs."""
from pathlib import Path
import pytest
from tests.phase14_6_2_process import validate_config


def config():
    return {'role':'worker','out':str(Path('reports/phase14_6_2-acceptance/unit').resolve()),'database_url':'postgresql+asyncpg://synthetic@localhost:55432/phase8_fixture_test','checkpoint_database_url':'postgresql://synthetic@localhost:55432/phase8_fixture_test','crash_point':'claimed'}


def test_isolated_scope_accepts_known_fixture():validate_config(config())


@pytest.mark.parametrize('change',[
 {'role':'production'}, {'out':'/tmp/unrestricted'},
 {'database_url':'postgresql://synthetic@localhost:55432/api_troubleshooter'},
 {'database_url':'postgresql://synthetic@external.invalid:55432/phase8_fixture_test'},
 {'database_url':'postgresql://synthetic@localhost:5432/phase8_fixture_test'},
 {'checkpoint_database_url':'postgresql://synthetic@localhost:55432/phase8_other_test'},
 {'crash_point':'arbitrary_expression'},
])
def test_process_scope_rejects_unsafe_targets(change):
    with pytest.raises(ValueError):validate_config({**config(),**change})
