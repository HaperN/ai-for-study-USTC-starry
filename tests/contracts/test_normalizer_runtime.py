"""The production normalization adapter runs without the historical runtime."""
import json
import subprocess
import sys
from pathlib import Path


def test_normalizer_runs_when_upstream_packages_are_unavailable(tmp_path):
    source = tmp_path / 'structured.md'
    source.write_text('# 第1章 复数\n\n章导言。\n\n## §1.1 定义\n\n复数 $z=x+iy$。\n')
    project = tmp_path / 'out'
    code = '''
import sys
class Deny:
 def find_spec(self,name,*args):
  if name.split('.')[0] in {'stem_runner','courseforge','courseforge_paths','courseforge_publication'}:
   raise ImportError('upstream unavailable')
sys.meta_path.insert(0,Deny())
from aiforstudy.providers.normalizer import normalize
normalize(sys.argv[1],sys.argv[2])
'''
    result = subprocess.run([sys.executable, '-c', code, str(source), str(project)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in (project / 'sources/normalized-source.jsonl').read_text().splitlines()]
    assert len(rows) >= 2
    assert any('复数 $z=x+iy$' in str(row) for row in rows)
