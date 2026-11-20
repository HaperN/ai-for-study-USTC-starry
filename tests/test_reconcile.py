"""Historical reconcile is a read-only inventory, never a quality upgrade."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('retired_reconcile_under_test', ROOT / 'tools/reconcile.py')
rec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rec)


def historical_book(root, drift="all"):
    locations = [root / part for part in ('parse', 'lesson', 'audit')]
    for directory in locations:
        directory.mkdir()
    rows = {
        locations[0] / 'concepts.json': {'concepts': [{'id': 'C', 'occurrences': [] if drift in ('all', 'occurrences') else [{'unit_id':'U'}]}]},
        locations[1] / 'lessons.index.json': {'units': [] if drift in ('all', 'index') else [{'unit_id':'U'}]},
        locations[1] / 'snapshot.json': {'progress': {'done': ['stale-unit'] if drift in ('all', 'snapshot') else ['U']}},
        locations[1] / 'U.json': {'_meta': {'schema_version': '2.0'}, 'unit_id': 'U', 'checks': {'passed': True}},
        locations[2] / 'U.json': {'_meta': {'schema_version': '3.0'}, 'passed': True},
    }
    for path, value in rows.items():
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return locations


def bytes_in(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_repeated_readonly_inventory_is_idempotent_and_does_not_inherit_approval(tmp_path):
    locations = historical_book(tmp_path)
    before = bytes_in(tmp_path)
    first = rec.reconcile(*locations, check=True)
    assert first == rec.reconcile(*locations, check=True)
    assert bytes_in(tmp_path) == before
    assert first['read_only'] is True and first['approval_inherited'] is False
    assert len(first['files']) == len(before)


@pytest.mark.parametrize('drift', ['index', 'occurrences', 'snapshot', 'all'])
def test_historical_drift_is_readable_but_never_repaired_in_place(tmp_path, drift):
    locations = historical_book(tmp_path, drift)
    before = bytes_in(tmp_path)
    with pytest.raises(ValueError, match='只读'):
        rec.reconcile(*locations)
    result = rec.reconcile(*locations, check=True)
    assert all(row['readable'] for row in result['files'])
    assert result['approval_inherited'] is False
    assert bytes_in(tmp_path) == before


def test_bad_json_is_reported_without_losing_other_inventory_rows(tmp_path):
    locations = historical_book(tmp_path)
    (locations[1] / 'broken.json').write_text('{bad', encoding='utf-8')
    before = bytes_in(tmp_path)
    rows = rec.reconcile(*locations, check=True)['files']
    assert len(rows) == 6
    bad = [row for row in rows if not row['readable']]
    assert len(bad) == 1 and bad[0]['error'] and bad[0]['path'].endswith('broken.json')
    assert bytes_in(tmp_path) == before


def test_relative_and_absolute_paths_inventory_same_files(tmp_path, monkeypatch):
    locations = historical_book(tmp_path)
    absolute = rec.reconcile(*locations, check=True)['files']
    monkeypatch.chdir(tmp_path)
    relative = rec.reconcile('parse', 'lesson', 'audit', check=True)['files']
    assert [(r['kind'], Path(r['path']).resolve(), r['readable']) for r in absolute] == [
        (r['kind'], Path(r['path']).resolve(), r['readable']) for r in relative]


def test_cli_check_signals_corrupt_history_without_rewriting(tmp_path, capsys):
    locations = historical_book(tmp_path)
    (locations[2] / 'corrupt.json').write_text('{bad', encoding='utf-8')
    before = bytes_in(tmp_path)
    assert rec.main([str(locations[0]), '--lessons', str(locations[1]), '--audit', str(locations[2]), '--check']) == 1
    assert json.loads(capsys.readouterr().out)['read_only'] is True
    assert bytes_in(tmp_path) == before
