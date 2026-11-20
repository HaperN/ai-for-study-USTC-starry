"""Paid code generation is preceded by one trusted, real runtime self-test."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from aiforstudy.blocks.media import code_runtime
from aiforstudy.blocks.visual import manim_adapter
from aiforstudy.errors import Cancelled, CapabilityError, ConfigError, ContractError


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("dependency", ["独立 Manim 解释器不存在", "ffmpeg 不存在"])
def test_preflight_names_missing_shared_dependency(monkeypatch, dependency):
    def missing(**kwargs):
        raise ConfigError(dependency)
    monkeypatch.setattr(code_runtime, "default_services", missing)
    with pytest.raises(CapabilityError, match=dependency) as failure:
        code_runtime.preflight_generated_code("matplotlib", workspace_root=ROOT)
    assert failure.value.report["status"] == "blocked"


def test_preflight_cancel_does_not_dispatch(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("a cancelled preflight dispatched work")
    monkeypatch.setattr(code_runtime, "render_generated_code", unexpected)
    with pytest.raises(Cancelled):
        code_runtime.preflight_generated_code("matplotlib", cancel=SimpleNamespace(cancelled=True))


def test_preflight_rejects_unknown_backend_before_dispatch(monkeypatch):
    monkeypatch.setattr(code_runtime, "render_generated_code", lambda *a, **k: pytest.fail("dispatch"))
    with pytest.raises(ContractError, match="backend"):
        code_runtime.preflight_generated_code("unapproved")


def test_preflight_blocked_isolation_is_a_capability_failure(tmp_path, monkeypatch):
    from dataclasses import replace
    services = replace(manim_adapter.default_services(workspace_root=ROOT), isolation="none",
                       scratch_root=tmp_path / "scratch")
    monkeypatch.setattr(code_runtime, "default_services", lambda **kwargs: services)
    with pytest.raises(CapabilityError) as failure:
        code_runtime.preflight_generated_code("matplotlib", workspace_root=ROOT)
    assert failure.value.report["runtime"]["technical_report"]["phase"] == "environment_probe"
    assert failure.value.report["runtime"]["technical_report"]["worker"]["kind"] == "isolation"


def disable_tex(monkeypatch):
    """Make both actual worker executables fail without changing the installation."""
    services = manim_adapter.default_services(workspace_root=ROOT)
    policy = code_runtime.load_asset(services.worker_dir, "policy")
    original = policy.worker_env
    def no_tex(**kwargs):
        env = original(**kwargs)
        bin_dir = Path(kwargs["scratch"]) / "tex-unavailable"
        bin_dir.mkdir(exist_ok=True)
        for name in ("latex", "dvisvgm"):
            executable = bin_dir / name
            executable.write_text("#!/bin/sh\necho 'TEST: TeX unavailable' >&2\nexit 47\n")
            executable.chmod(0o755)
        env["PATH"] = str(bin_dir) + ":" + env["PATH"]
        return env
    monkeypatch.setattr(policy, "worker_env", no_tex)


def test_real_static_preflight_works_when_latex_and_dvisvgm_fail(monkeypatch):
    disable_tex(monkeypatch)
    report = code_runtime.preflight_generated_code("matplotlib", workspace_root=ROOT)
    assert report["status"] == "ok"
    runtime = report["runtime"]
    environment = runtime["technical_report"]["environment"]
    assert environment["tools"]["latex"]["available"] is False
    assert environment["tools"]["dvisvgm"]["available"] is False
    assert environment["math_tex"]["ok"] is False
    assert runtime["technical_report"]["image_decode_ok"] is True
    assert {resource["kind"] for resource in runtime["resources"]} == {"svg", "image"}
    assert runtime["technical_report"]["mathematical_status"] == "requires_visual_review"


def test_real_animation_preflight_exposes_broken_latex(monkeypatch):
    disable_tex(monkeypatch)
    with pytest.raises(CapabilityError, match="(?i)latex") as failure:
        code_runtime.preflight_generated_code("manim", workspace_root=ROOT)
    assert failure.value.report["status"] == "blocked"


def test_real_animation_preflight_verifies_code_sandbox_and_video():
    report = code_runtime.preflight_generated_code("manim", workspace_root=ROOT)
    assert report["status"] == "ok"
    technical = report["runtime"]["technical_report"]
    assert technical["isolation"]["safe"] is True
    assert technical["isolation"]["signals"]["blocked"] is True
    assert technical["worker"]["result"]["mode"] == "code"
    applied = technical["worker"]["result"]["sandbox"]
    if technical["isolation"]["backend"] == "seatbelt":
        assert applied["seatbelt"]["signal_scope"] == "same-sandbox"
        assert {"setpgid", "setsid", "posix_spawn"} <= set(applied["seatbelt"]["denied_syscalls"])
    else:
        assert applied["landlock"]["scoped"] & 2
    assert technical["ffmpeg_decode_ok"] is True
    assert technical["decoded_frames"] > 0
    assert technical["environment"]["math_tex"]["ok"] is True
