"""Historical audit writer positives retired by Q10.

Exact retired-case inventory and current equivalents: Q10-test-migration.md.
Historical files remain readable; current production must never execute them.
"""
import pytest
from legacy_graph_support import assert_read_only_run


@pytest.mark.parametrize('version',['3.0','4.0'])
def test_historical_audit_writer_is_read_only_and_resume_writes_nothing(tmp_path,version):
    assert_read_only_run(tmp_path,version,stage='audit')
