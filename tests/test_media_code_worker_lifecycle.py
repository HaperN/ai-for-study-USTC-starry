"""Real disposable processes exercise the host's generated-code lifecycle boundary."""
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace

from aiforstudy.blocks.visual import worker


def _alive(pid):
    if sys.platform == "darwin":
        return any(row[0] == pid for row in worker._darwin_processes())
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def _cleanup(pid):
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


CHILD = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(10)"


def test_kill_group_escalates_when_leader_exits_but_child_ignores_term():
    source = (
        "import subprocess,sys,time\n"
        f"p=subprocess.Popen([sys.executable,'-c',{CHILD!r}],stdout=subprocess.PIPE,text=True)\n"
        "p.stdout.readline()\nprint(p.pid,flush=True)\ntime.sleep(10)\n"
    )
    process = subprocess.Popen([sys.executable, "-c", source], start_new_session=True,
                               stdout=subprocess.PIPE, text=True)
    child_pid = int(process.stdout.readline())
    try:
        how, remaining = worker._kill_group(process, grace=.2)
        assert how == "kill"
        assert remaining == 0
        assert not _alive(child_pid)
    finally:
        _cleanup(child_pid)
        _cleanup(process.pid)
        process.wait(timeout=3)


def _run_controlled(monkeypatch, tmp_path, source, *, limit=1024 * 1024):
    real_popen = subprocess.Popen
    monkeypatch.setattr(worker, "TERM_GRACE_SECONDS", .2)
    monkeypatch.setattr(worker, "select_isolation", lambda *a, **kw:
                        worker.WorkerIsolation("landlock-seccomp", "controlled fixture", True))
    policy = SimpleNamespace(
        sandbox_paths=lambda **kw: {"read_only": [], "read_write": []},
        worker_env=lambda **kw: {"PATH": os.defpath},
    )
    monkeypatch.setattr(worker, "load_asset", lambda *a: policy)
    monkeypatch.setattr(worker.subprocess, "Popen", lambda argv, **kw:
                        real_popen(argv if argv[0] == "/bin/ps" else [sys.executable, "-c", source, argv[-1]], **kw))
    return worker.run_worker_job(
        services=worker.WorkerServices(tmp_path, sys.executable, tmp_path),
        job={"mode": "code", "scratch": str(tmp_path / "scratch"), "output": str(tmp_path / "out")},
        limits=worker.RenderLimits(timeout_seconds=1, output_bytes=limit), job_id="controlled")


def test_successful_leader_exit_still_cleans_its_live_child(monkeypatch, tmp_path):
    source = (
        "import subprocess,sys,json\nfrom pathlib import Path\n"
        "job=json.loads(Path(sys.argv[1]).read_text())\n"
        f"p=subprocess.Popen([sys.executable,'-c',{CHILD!r}],stdout=subprocess.PIPE,text=True)\n"
        "p.stdout.readline()\n"
        "Path(job['scratch'],'child.pid').write_text(str(p.pid))\n"
        "Path(job['result_path']).write_text(json.dumps({'status':'ok'}))\n"
    )
    try:
        outcome = _run_controlled(monkeypatch, tmp_path, source)
        child_pid = int((tmp_path / "scratch/child.pid").read_text())
        assert outcome["status"] == "ok"
        assert outcome["orphans_remaining"] == 0
        assert not _alive(child_pid)
    finally:
        child_file = tmp_path / "scratch/child.pid"
        if child_file.exists():
            _cleanup(int(child_file.read_text()))


def test_scratch_files_count_toward_the_job_output_limit(monkeypatch, tmp_path):
    source = (
        "import sys,json,time\nfrom pathlib import Path\n"
        "job=json.loads(Path(sys.argv[1]).read_text())\n"
        "for i in range(10): Path(job['scratch'],str(i)).write_bytes(b'x'*8192)\n"
        "time.sleep(10)\n"
    )
    outcome = _run_controlled(monkeypatch, tmp_path, source, limit=32768)
    assert outcome["terminated_by"].startswith("output_limit")
    assert outcome["job_bytes"] >= 81920
    assert outcome["orphans_remaining"] == 0


def test_group_rss_includes_the_child_allocation():
    source = (
        "import subprocess,sys,time\n"
        "p=subprocess.Popen([sys.executable,'-c',\"import time; a=bytearray(24*1024*1024); print('ready',flush=True); time.sleep(10)\"],stdout=subprocess.PIPE,text=True)\n"
        "p.stdout.readline()\nprint(p.pid,flush=True)\ntime.sleep(10)\n"
    )
    process = subprocess.Popen([sys.executable, "-c", source], start_new_session=True,
                               stdout=subprocess.PIPE, text=True)
    child_pid = int(process.stdout.readline())
    try:
        assert worker._group_rss_bytes(process.pid) >= worker._rss_bytes(process.pid) + 24 * 1024 * 1024
    finally:
        _cleanup(child_pid)
        _cleanup(process.pid)
        process.wait(timeout=3)


def test_bwrap_preserves_the_host_worker_group(tmp_path):
    worker_dir = Path(__file__).resolve().parents[1] / "tools/manim_worker"
    bwrap = worker.load_asset(worker_dir, "bwrap")
    argv = bwrap.build_argv(read_only=[], read_write=[], scratch=str(tmp_path / "scratch"),
                           output=str(tmp_path / "out"), entry="entry.py", job_path="job.json",
                           python=sys.executable, env={})
    assert "--new-session" not in argv
    assert "--unshare-all" in argv
