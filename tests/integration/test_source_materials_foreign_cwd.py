"""Prepared source manifests must load adapters from the installed application."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from aiforstudy.core.registry import load_registry
from aiforstudy.pipeline.graph import GRAPH_VERSION
from aiforstudy.pipeline.preparation import _tree_hashes, source_fingerprint
from support.preparation_book import _mini_book


@pytest.mark.parametrize('entry', ['source_materials', 'cli_plan'])
def test_current_prepared_source_materials_load_from_foreign_cwd(tmp_path, entry):
    workspace = _mini_book(tmp_path / 'fixture')
    original = load_registry(workspace).resolve('mini-book')
    cached = workspace.root / 'data/books/mini-book/runs/prepared/work/prepared-parse'
    shutil.copytree(original.parse, cached)
    pointer = workspace.root / 'data/books/mini-book/prepared/current.json'
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({'graph_version': GRAPH_VERSION, 'parse': str(cached),
        'source_digest': source_fingerprint(original), 'files': _tree_hashes(cached)}))
    foreign = tmp_path / 'outside'
    foreign.mkdir()
    env = {k:v for k,v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME', 'AIFORSTUDY_WORKSPACE')}
    before = {str(p.relative_to(workspace.root)):(p.read_bytes(),p.stat().st_mtime_ns)
              for p in workspace.root.rglob('*') if p.is_file()}
    if entry == 'source_materials':
        args = ['-c', 'from aiforstudy.core.source_materials import is_current; import sys; assert is_current(sys.argv[1])', str(cached)]
    else:
        args = ['-m', 'aiforstudy', '--workspace', str(workspace.root), 'plan',
                '--book', 'mini-book', '--all', '--json']
    result = subprocess.run([sys.executable, '-I', *args], cwd=foreign, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    assert result.returncode == 0, result.stderr.decode()
    if entry == 'cli_plan':
        plan = json.loads(result.stdout)
        assert plan['requires_preparation'] is False
        assert str(cached) in plan['read_dependencies']
    after = {str(p.relative_to(workspace.root)):(p.read_bytes(),p.stat().st_mtime_ns)
             for p in workspace.root.rglob('*') if p.is_file()}
    assert before == after
