"""Refusal checks never call an historical writer or overwrite old evidence."""
from pathlib import Path
import subprocess
import sys

def assert_retired_cli(tmp_path,name):
    parse=tmp_path/'parse';parse.mkdir()
    old=parse/'historical.json';old.write_text('{"quality_verified":false,"schema_version":"3.0"}')
    target=tmp_path/'report.md'
    before={str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    proc=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[2]/'tools'/f'{name}.py'),str(parse),'--md',str(target)],capture_output=True,text=True,timeout=15)
    assert proc.returncode==2,proc.stdout+proc.stderr
    assert '退役' in proc.stderr and '只读' in proc.stderr
    assert not target.exists()
    assert before=={str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
