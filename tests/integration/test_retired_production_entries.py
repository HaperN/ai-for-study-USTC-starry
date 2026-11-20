"""Legacy production commands must stop before touching the old pipeline (0 paid calls)."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = r"""
import os, runpy, sys
sys.dont_write_bytecode = True
mode, script, *args = sys.argv[1:]
sys.argv = [script, *args]
def guard(event, args):
    forbidden = event in {
        'subprocess.Popen', 'os.system', 'socket.connect', 'socket.connect_ex',
        'os.mkdir', 'os.remove', 'os.rmdir', 'os.rename', 'os.link', 'os.symlink',
    }
    if event == 'open':
        path, mode, flags = args
        forbidden = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
    if forbidden:
        raise RuntimeError('LEGACY_SIDE_EFFECT_ATTEMPT: ' + event)
sys.addaudithook(guard)
if mode == 'script':
    runpy.run_path(script, run_name='__main__')
else:
    namespace = runpy.run_path(script, run_name='legacy_maintenance')
    raise SystemExit(namespace['main'](args))
"""


@pytest.mark.parametrize("name", ["run_book.py", "run_chapter.py"])
@pytest.mark.parametrize("mode,args", [
    ("script", []),
    ("script", ["--help"]),
    ("script", ["parse", "--book", "sentinel-book", "--chapters", "CH04",
                "--chapter", "CH04", "--lessons", "lessons", "--audit", "audit",
                "--out", "delivery", "--budget-tokens", "1"]),
    ("import", ["--help"]),
])
def test_retired_entry_refuses_before_legacy_side_effects(tmp_path, name, mode, args):
    sentinel = tmp_path / "unchanged.txt"
    sentinel.write_text("original", encoding="utf-8")
    env = {key: value for key, value in os.environ.items()
           if not any(part in key.upper() for part in ("KEY", "TOKEN", "SECRET"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-B", "-c", LAUNCHER, mode, str(ROOT / "tools" / name), *args],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15,
    )
    assert "LEGACY_SIDE_EFFECT_ATTEMPT" not in result.stderr
    assert result.returncode == 2, result.stdout + result.stderr
    assert "已退役" in result.stderr
    assert "aifs run" in result.stderr
    assert "aifs resume" in result.stderr and "旧文件只读" in result.stderr
    assert "Traceback" not in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.iterdir()) == [sentinel]


@pytest.mark.parametrize("name", ["run_book.py", "run_chapter.py"])
def test_retirement_does_not_require_legacy_import_dependencies(tmp_path, name):
    # An obsolete checkout/copy must still give migration guidance, not import errors.
    script = tmp_path / name
    script.write_bytes((ROOT / "tools" / name).read_bytes())
    result = subprocess.run([sys.executable, "-I", "-S", str(script), "--help"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert result.returncode == 2
    assert "已退役" in result.stderr
    assert "Traceback" not in result.stderr
