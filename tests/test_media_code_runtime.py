"""Generated Python must run in the real sandbox and yield inspectable media."""
from __future__ import annotations

import importlib
import json
import os
import signal
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from aiforstudy.blocks.visual import manim_adapter
from aiforstudy.core.digest import digest_file

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    try:
        return importlib.import_module("aiforstudy.blocks.media.code_runtime")
    except ModuleNotFoundError:
        pytest.fail("The isolated generated-code renderer has not been implemented")


def program(code, *, backend="matplotlib", steps=None):
    return {"schema_version": "GeneratedMediaCodeV1", "request_id": "R1",
            "backend": backend, "code": code, "steps": steps or [],
            "object_bindings": []}


def context(kind="drawing"):
    return {"request": {"request_id": "R1", "kind": kind, "object_refs": [],
                        "animation": {"steps": ["平移", "放大"]}}, "approved_content": {},
            "approved_models": {"vector": [1, 2]}}


def test_missing_isolation_never_executes_generated_import(tmp_path, monkeypatch):
    module = runtime()
    marker = tmp_path / "escaped.txt"
    services = replace(manim_adapter.default_services(workspace_root=ROOT), isolation="none",
                       scratch_root=tmp_path / "scratch")
    monkeypatch.setattr(module, "default_services", lambda **kw: services)
    code = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\ndef build_figure(context): return None\n"
    result = module.render_generated_code(program(code), context=context(), out_dir=tmp_path / "out")
    assert result["status"] == "blocked", result
    assert result["reason"]
    assert not marker.exists()
    assert (Path(result["source_dir"]) / "source" / "generated.py").read_text() == code


def test_real_static_context_custom_geometry_and_sandbox(tmp_path):
    secret = tmp_path / "private.env"
    secret.write_text("FAKE_PRIVATE_SENTINEL")
    code = f'''from pathlib import Path
import socket
import matplotlib.pyplot as plt
def build_figure(context):
    try:
        Path({str(secret)!r}).read_text()
    except (PermissionError, FileNotFoundError):
        pass
    else:
        raise RuntimeError("secret was readable")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(2)
            probe.connect(("198.51.100.1", 9))
    except PermissionError:
        pass
    else:
        raise RuntimeError("network was available")
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    x, y = context['approved_models']['vector']
    for offset in range(3):
        ax.arrow(offset, 0, x, y, width=.04, color='#166caa')
    ax.set(xlim=(-1, 5), ylim=(-1, 4), title='向量平移')
    ax.set_aspect('equal')
    return fig
'''
    result = runtime().render_generated_code(program(code), context=context(),
                                            out_dir=tmp_path / "out", workspace_root=ROOT)
    assert result["status"] == "needs_review", result
    assert {r["kind"] for r in result["resources"]} == {"svg", "image"}
    assert result["technical_report"]["isolation"]["safe"] is True
    assert result["technical_report"]["image_decode_ok"] is True
    assert result["technical_report"]["mathematical_status"] == "requires_visual_review"
    for resource in result["resources"]:
        path = Path(result["source_dir"]) / resource["relative_path"]
        assert path.stat().st_size == resource["bytes"] > 0
        assert digest_file(path) == resource["sha256"]
        if resource["kind"] == "svg":
            from aiforstudy.blocks.media.validation import _safe_svg
            _safe_svg(path)


ANIMATION_CODE = '''from manim import *
from aifs_media_runtime import load_context, media_step
class GeneratedScene(MovingCameraScene):
    def construct(self):
        assert load_context()['request']['request_id'] == 'R1'
        dot = Dot(LEFT, color=BLUE)
        self.add(dot)
        with media_step(self, 'S1'):
            self.play(dot.animate.shift(2*RIGHT), run_time=.8)
            self.wait(.4)
        with media_step(self, 'S2'):
            self.play(self.camera.frame.animate.scale(.9), run_time=.4)
            self.wait(.4)
'''


def animation_program(code=ANIMATION_CODE):
    return program(code, backend="manim", steps=[
        {"step_id": "S1", "author_step_index": 0, "caption": "向量向右平移",
         "observation": "观察起点终点", "expected_change": "蓝点从左侧移到右侧"},
        {"step_id": "S2", "author_step_index": 1, "caption": "放大观察",
         "observation": "观察镜头", "expected_change": "镜头放大"}])


def test_real_custom_scene_uses_render_clock_and_decodable_frames(tmp_path):
    result = runtime().render_generated_code(animation_program(), context=context("animation"),
                                            out_dir=tmp_path / "out", workspace_root=ROOT)
    assert result["status"] == "needs_review", result
    assert result["technical_report"]["ffmpeg_decode_ok"] is True
    assert result["technical_report"]["duration_ok"] is True
    assert [s["step_id"] for s in result["steps"]] == ["S1", "S2"]
    assert result["steps"][0]["start"] == 0
    assert result["steps"][0]["end"] >= 1.2
    assert result["steps"][1]["end"]-result["steps"][1]["start"] >= .8
    narration=result["technical_report"]["narration"]
    assert narration['source']['backend']=='local-qwen3-tts'
    import wave
    for index,(step,voice) in enumerate(zip(result['steps'],narration['steps']),1):
        with wave.open(str(Path(result['source_dir'])/'source'/'narration'/f'step-{index:03d}.wav'),'rb') as pcm:
            duration=pcm.getnframes()/pcm.getframerate()
        assert duration>0 and step['end']-step['start']+.001>=duration
        assert voice['sample_count']/voice['sample_rate']==pytest.approx(duration)

    resources = result["resources"]
    assert {r["kind"] for r in resources} == {"mp4", "poster", "keyframe", "captions", "transcript", "audio", "narration"}
    for step in result["steps"]:
        own = [r for r in resources if r.get("step_id") == step["step_id"]]
        assert {r["phase"] for r in own} == {"before", "observe", "after"}
        assert all(step["start"] <= r["timestamp"] < step["end"] for r in own)
    from aiforstudy.blocks.media.validation import verify_frame_pixels
    base = Path(result["source_dir"])
    for frame in (r for r in resources if r["kind"] == "keyframe"):
        assert verify_frame_pixels(base / "video.mp4", base / frame["relative_path"],
                                   frame["timestamp"], tmp_path / "decode")["ok"]
    from aiforstudy.blocks.media.code_runtime import _stamp
    first=result["steps"][0]
    assert f"S1\n{_stamp(first['start'])} --> {_stamp(first['end'])}" in (base / "captions.vtt").read_text()


def test_unrecorded_animation_time_is_not_accepted(tmp_path):
    bad = ANIMATION_CODE.replace("        dot = Dot", "        self.wait(.2)\n        dot = Dot")
    result = runtime().render_generated_code(animation_program(bad), context=context("animation"),
                                            out_dir=tmp_path / "out", workspace_root=ROOT)
    assert result["status"] == "failed", result
    assert "step" in result["reason"].lower() or "步骤" in result["reason"]
    assert (Path(result["source_dir"]) / "source" / "generated.py").is_file()


def test_cancelled_code_does_not_start_render(tmp_path):
    result = runtime().render_generated_code(animation_program(), context=context("animation"),
        out_dir=tmp_path / "out", workspace_root=ROOT, cancel=SimpleNamespace(cancelled=True))
    assert result["status"] == "cancelled", result
    assert not result["resources"]


def test_valid_creation_animation_keeps_its_blank_before_frame(tmp_path):
    code = ANIMATION_CODE.replace("        self.add(dot)\n", "").replace(
        "            self.play(dot.animate.shift(2*RIGHT), run_time=.8)",
        "            self.play(FadeIn(dot), run_time=.8)")
    result = runtime().render_generated_code(animation_program(code), context=context("animation"),
        out_dir=tmp_path / "out", workspace_root=ROOT)
    assert result["status"] == "needs_review", result
    frames = result["technical_report"]["frame_checks"]["frames"]
    assert frames[0]["variation"] == 0
    assert frames[-1]["variation"] > 0


def test_declared_input_mismatch_is_rejected_before_running(tmp_path):
    forged = program("def build_figure(context):\n    return None\n")
    forged["request_id"] = "DIFFERENT-REQUEST"
    result = runtime().render_generated_code(forged, context=context(), out_dir=tmp_path / "out", workspace_root=ROOT)
    assert result["status"] == "failed"
    assert "request_id" in result["reason"]
    assert "worker" not in result["technical_report"]


def test_default_chinese_text_contrasts_with_the_worker_background(tmp_path):
    code = ANIMATION_CODE.replace("Dot(LEFT, color=BLUE)", "Text('向量平移').move_to(LEFT)")
    result = runtime().render_generated_code(animation_program(code), context=context("animation"),
        out_dir=tmp_path / "out", workspace_root=ROOT)
    assert result["status"] == "needs_review", result
    assert min(f["variation"] for f in result["technical_report"]["frame_checks"]["frames"]) > .2


def test_renderer_cannot_overwrite_host_source_archive(tmp_path):
    out = tmp_path / "out"
    code = f'''from pathlib import Path
import matplotlib.pyplot as plt
def build_figure(context):
    try:
        Path({str(out / 'source' / 'generated.py')!r}).write_text('tampered')
    except (PermissionError, FileNotFoundError):
        pass
    else:
        raise RuntimeError('host archive was writable')
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    return fig
'''
    result = runtime().render_generated_code(program(code), context=context(), out_dir=out, workspace_root=ROOT)
    assert result["status"] == "needs_review", result
    assert (out / "source" / "generated.py").read_text() == code


def test_real_infinite_generated_code_is_stopped_by_worker_deadline(tmp_path, monkeypatch):
    module = runtime()
    real_run = module.run_worker_job

    def short_deadline(**kwargs):
        if kwargs["job"].get("mode") == "code":
            kwargs["limits"] = replace(kwargs["limits"], timeout_seconds=1.2)
        return real_run(**kwargs)

    monkeypatch.setattr(module, "run_worker_job", short_deadline)
    result = module.render_generated_code(program("def build_figure(context):\n    while True: pass\n"),
        context=context(), out_dir=tmp_path / "out", workspace_root=ROOT)
    assert result["status"] == "failed", result
    assert result["technical_report"]["worker"]["terminated_by"].startswith("timeout")
    assert result["technical_report"]["worker"]["orphans_remaining"] == 0
    assert not result["resources"]


def test_host_rejects_resource_symlinks_and_forged_render_times(tmp_path):
    module = runtime()
    outside = tmp_path / "private.txt"
    outside.write_text("private")
    output = tmp_path / "output"
    output.mkdir()
    (output / "figure.png").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes|symlink"):
        module._safe_file(output, "figure.png")
    with pytest.raises(ValueError, match="intervals|time"):
        module._steps(animation_program(), [
            {"step_id": "S1", "start": 0, "end": 1.2},
            {"step_id": "S2", "start": 1.2, "end": 900}], seconds=2, fps=15)


def test_final_profile_last_frame_seek_never_rounds_past_eof(tmp_path):
    code = ANIMATION_CODE.replace("run_time=.8", "run_time=2").replace("run_time=.4", "run_time=2").replace("self.wait(.4)", "self.wait(2)")
    result = runtime().render_generated_code(animation_program(code), context=context("animation"),
        out_dir=tmp_path / "out", workspace_root=ROOT, profile="final")
    assert result["status"] == "needs_review", result
    assert result["steps"][-1]["end"] == 8
    last = [r for r in result["resources"] if r.get("step_id") == "S2" and r.get("phase") == "after"][0]
    assert last["timestamp"] <= 8 - 1/30
    from aiforstudy.blocks.media.validation import verify_frame_pixels
    base = Path(result["source_dir"])
    assert verify_frame_pixels(base / "video.mp4", base / last["relative_path"], last["timestamp"], tmp_path / "decode")["ok"]


def test_fractional_frame_boundaries_still_cover_all_registered_frames(tmp_path):
    code = ANIMATION_CODE.replace("run_time=.8", "run_time=.3")
    result = runtime().render_generated_code(animation_program(code), context=context("animation"),
        out_dir=tmp_path / "out", workspace_root=ROOT)
    assert result["status"] == "needs_review", result
    for step in result["steps"]:
        frames = [r for r in result["resources"] if r.get("step_id") == step["step_id"]]
        assert all(step["start"] <= frame["timestamp"] < step["end"] for frame in frames)


def test_landlock_code_cannot_signal_outside_but_can_terminate_its_child(tmp_path, monkeypatch):
    module = runtime()
    services = replace(manim_adapter.default_services(workspace_root=ROOT), isolation="landlock-seccomp",
                       scratch_root=tmp_path / "scratch")
    monkeypatch.setattr(module, "default_services", lambda **kw: services)
    marker = tmp_path / "outside-signal.txt"
    victim = subprocess.Popen([sys.executable, "-c",
        "import signal,time,sys;from pathlib import Path;"
        "signal.signal(signal.SIGUSR1,lambda n,f:Path(sys.argv[1]).write_text('received'));"
        "print('ready',flush=True);time.sleep(60)", str(marker)], stdout=subprocess.PIPE, text=True)
    try:
        assert victim.stdout.readline().strip() == "ready"
        code = f'''import os, signal, subprocess, sys
import matplotlib.pyplot as plt
def build_figure(context):
    try:
        os.kill({victim.pid}, signal.SIGUSR1)
    except PermissionError:
        pass
    else:
        raise RuntimeError('outside process accepted signal')
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    child.terminate()
    child.wait(timeout=3)
    fig, ax = plt.subplots()
    ax.plot([0,1], [0,1])
    return fig
'''
        result = module.render_generated_code(program(code), context=context(), out_dir=tmp_path / "out", workspace_root=ROOT)
        if sys.platform != 'linux':
            assert result['status']=='blocked' and 'requires Linux' in result['reason']
            assert not marker.exists() and not result['resources']
            return
        assert result["status"] == "needs_review", result
        assert not marker.exists(), "a sandboxed program signalled a real outside process"
        assert result["technical_report"]["isolation"]["signals"]["blocked"] is True
        assert result["technical_report"]["isolation"]["applied"]["landlock"]["scoped"] & 2
    finally:
        victim.terminate()
        victim.wait(timeout=5)


def test_generated_code_cannot_escape_its_worker_process_group(tmp_path, monkeypatch):
    module = runtime()
    services = replace(manim_adapter.default_services(workspace_root=ROOT), isolation="landlock-seccomp",
                       scratch_root=tmp_path / "scratch")
    monkeypatch.setattr(module, "default_services", lambda **kw: services)
    code = '''import os
import matplotlib.pyplot as plt
def build_figure(context):
    shared = '/dev/shm/aifs-worker-probe-' + str(os.getpid())
    try:
        with open(shared, 'w') as handle:
            handle.write('unmonitored')
    except (PermissionError, FileNotFoundError):
        pass
    else:
        os.unlink(shared)
        raise RuntimeError('/dev/shm escaped the monitored output budget')
    try:
        os.setpgid(0, 0)
    except PermissionError:
        pass
    else:
        raise RuntimeError('setpgid was allowed')
    child = os.fork()
    if child == 0:
        try:
            os.setsid()
        except PermissionError:
            os._exit(0)
        else:
            os._exit(5)
    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 0, 'child escaped with setsid'
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    return fig
'''
    result = module.render_generated_code(program(code), context=context(), out_dir=tmp_path / "out", workspace_root=ROOT)
    if sys.platform != 'linux':
        assert result['status']=='blocked' and 'requires Linux' in result['reason']
        assert not result['resources']
        return
    assert result["status"] == "needs_review", result


def test_generated_code_requires_a_kernel_with_signal_scoping(monkeypatch):
    from aiforstudy.blocks.visual.worker import load_asset
    sandbox = load_asset(ROOT / "tools/manim_worker", "sandbox")
    monkeypatch.setattr(sandbox, "landlock_abi", lambda: 5)
    with pytest.raises(sandbox.SandboxError, match="signal|信号"):
        sandbox.apply_landlock(read_only=[], read_write=[], require_signal_scope=True)
