"""T06 可信编译器测试：确定性、结构校验、零模型调用。

编译器只吃 `VisualSpecV1`，输出可信 Python 源码与几何采样数据。
关键性质：同规格两次编译逐字节相同；unit_id / request_id 不参与源码；
动作与时段引用必须自洽；**编译器不做任何模型调用**。
"""
from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "visual"
for extra in (ROOT / "src", FIXTURES):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import builders  # noqa: E402

from aiforstudy.blocks.visual.compiler import (  # noqa: E402
    COMPILER_VERSION, CompiledScene, RenderProfile, compile_scene)
from aiforstudy.blocks.visual.spec import parse_visual_spec  # noqa: E402
from aiforstudy.errors import ContractError  # noqa: E402


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.spec.json").read_text(encoding="utf-8"))


def spec_of(name: str):
    return parse_visual_spec(load(name))


#: 可信编译层里不允许出现的调用名与模块名（按 AST 判，不按字符串猜）
_FORBIDDEN_CALLS = {"eval", "exec", "open", "compile", "__import__", "input"}
_FORBIDDEN_MODULES = {"socket", "subprocess", "urllib", "http", "ftplib", "smtplib",
                      "requests", "ctypes", "pickle", "shutil", "providers"}


def forbidden_calls(path: Path) -> list[str]:
    import ast as pyast

    tree = pyast.parse(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    for node in pyast.walk(tree):
        if isinstance(node, pyast.Call) and isinstance(node.func, pyast.Name) \
                and node.func.id in _FORBIDDEN_CALLS:
            problems.append(f"line {node.lineno}: 调用 {node.func.id}")
        if isinstance(node, pyast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _FORBIDDEN_MODULES:
                    problems.append(f"line {node.lineno}: import {alias.name}")
        if isinstance(node, pyast.ImportFrom):
            module = node.module or ""
            if module.split(".")[0] in _FORBIDDEN_MODULES or "providers" in module:
                problems.append(f"line {node.lineno}: from {module} import ...")
    return problems


# ── 确定性 ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", ["saddle-a1-h0-k0", "vector-sum", "matrix-apply"])
def test_source_is_deterministic(name):
    first = compile_scene(spec_of(name), RenderProfile.DRAFT)
    second = compile_scene(spec_of(name), RenderProfile.DRAFT)
    assert first.source == second.source
    assert first.source_hash == second.source_hash
    assert first.spec_hash == second.spec_hash


def test_source_hash_matches_content():
    scene = compile_scene(spec_of("saddle-a1-h0-k0"), RenderProfile.FINAL)
    digest = "sha256:" + hashlib.sha256(scene.source.encode("utf-8")).hexdigest()
    assert scene.source_hash == digest
    assert scene.compiler_version == COMPILER_VERSION
    assert scene.spec_hash == spec_of("saddle-a1-h0-k0").spec_hash
    assert isinstance(scene, CompiledScene)


def test_trusted_source_is_valid_python():
    """可信源码必须是**能编译的** Python（T07 在里面塞 Manim 对象）。"""
    scene = compile_scene(spec_of("saddle-a1-h0-k0"), RenderProfile.DRAFT)
    compile(scene.source, "<trusted-scene>", "exec")


def test_source_does_not_depend_on_unit_or_request_id():
    """unit_id 从 CH04 换成任意名字，编译结果不变 —— 证明没有按书名/单元特判。"""
    ch04 = spec_of("saddle-a1-h0-k0")
    other = parse_visual_spec(
        builders.saddle_spec(1.0, 0.0, 0.0, unit_id="UNIT-XYZ-42",
                             request_id="another-request"))
    a = compile_scene(ch04, RenderProfile.DRAFT)
    b = compile_scene(other, RenderProfile.DRAFT)
    assert a.source == b.source
    assert a.source_hash == b.source_hash
    assert a.spec_hash != b.spec_hash, "spec_hash 仍然绑定完整规格身份"


def test_no_ch04_hardcoding_anywhere_in_visual_package():
    for path in sorted((ROOT / "src" / "aiforstudy" / "blocks" / "visual").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "CH04" not in text, f"{path.name} 出现 CH04 特判"
        assert "complex-analysis" not in text, f"{path.name} 出现书名特判"


def test_bad_profile_is_rejected():
    with pytest.raises(ContractError):
        compile_scene(spec_of("saddle-a1-h0-k0"), "cinematic")
    with pytest.raises(ContractError):
        RenderProfile("release")


def test_profile_changes_layout_parameters_only():
    draft = compile_scene(spec_of("saddle-a1-h0-k0"), RenderProfile.DRAFT)
    final = compile_scene(spec_of("saddle-a1-h0-k0"), RenderProfile.FINAL)
    assert draft.profile == "draft" and final.profile == "final"
    assert draft.spec_hash == final.spec_hash
    assert draft.source != final.source
    assert draft.samples == final.samples, "换成品档不能改数学内容"


# ── 结构校验（伪造的规格也要被拒）─────────────────────────────────────────
def test_forged_action_with_missing_object_is_rejected():
    spec = spec_of("saddle-a1-h0-k0")
    steps = [dict(step) for step in spec.steps]
    actions = [dict(a) for a in steps[0]["actions"]]
    actions[0] = {**actions[0], "object_id": "nope"}
    steps[0] = {**steps[0], "actions": actions}
    forged = dataclasses.replace(spec, steps=tuple(steps))
    with pytest.raises(ContractError) as excinfo:
        compile_scene(forged, RenderProfile.DRAFT)
    assert "steps[0].actions[0].object_id" in str(excinfo.value)


def test_forged_overlapping_steps_are_rejected():
    spec = spec_of("saddle-a1-h0-k0")
    steps = [dict(step) for step in spec.steps]
    steps[1] = {**steps[1], "start": 1.0, "end": 20.0}
    forged = dataclasses.replace(spec, steps=tuple(steps))
    with pytest.raises(ContractError) as excinfo:
        compile_scene(forged, RenderProfile.DRAFT)
    assert "steps[1]" in str(excinfo.value)


def test_forged_reversed_steps_are_rejected():
    spec = spec_of("saddle-a1-h0-k0")
    steps = [dict(step) for step in spec.steps]
    steps[2] = {**steps[2], "start": 30.0, "end": 20.0}
    forged = dataclasses.replace(spec, steps=tuple(steps))
    with pytest.raises(ContractError) as excinfo:
        compile_scene(forged, RenderProfile.DRAFT)
    assert "steps[2]" in str(excinfo.value)


# ── 零模型调用 ─────────────────────────────────────────────────────────────
def test_compiler_makes_zero_model_calls(monkeypatch):
    import socket

    import aiforstudy.providers.gateway as gateway
    import aiforstudy.providers.transport as transport

    calls: list[str] = []

    def _no_socket(*_args, **_kwargs):
        calls.append("socket")
        raise RuntimeError("T06 不允许任何网络")

    monkeypatch.setattr(transport.RealTransport, "__call__",
                        lambda self, **kw: calls.append("transport"), raising=False)
    monkeypatch.setattr(gateway.Gateway, "call",
                        lambda self, request: calls.append("gateway"), raising=False)
    monkeypatch.setattr(socket, "socket", _no_socket)

    scene = compile_scene(spec_of("saddle-a1-h0-k0"), RenderProfile.FINAL)
    assert scene.source
    assert calls == [], f"编译器/检查器不该发起任何调用：{calls}"

    # 接口层面：没有可注入的 client / transport / gateway 参数
    parameters=inspect.signature(compile_scene).parameters
    assert set(parameters)=={"spec","profile","math_context","cancel"}
    for name in ('math_context','cancel'):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is None

    # 结构层面：**模型面**的三层（规格／可信编译／独立数学）既不调用 eval/exec/open，
    # 也不 import 网络/进程模块。
    #
    # ⚠️ 扫描范围限定在这三个文件，不是整个 `visual/` 包：`manim_adapter.py` 与
    #    `worker.py` 是 T07 的**执行边界**，它们必须 `subprocess` 起隔离 worker
    #    （bwrap）—— 那正是"可信编译器"与"执行"分开的意义。把执行边界也要求成
    #    "不许 import subprocess"，等于要求它不要执行，只能逼出一个假实现。
    #    它们自己的约束在 `tests/test_manim_worker.py`：不许模型调用、不许网络。
    model_facing = ("spec.py", "compiler.py", "math_check.py")
    for name in model_facing:
        path = ROOT / "src" / "aiforstudy" / "blocks" / "visual" / name
        assert path.is_file(), f"缺模型面模块 {name}"
        problems = forbidden_calls(path)
        assert problems == [], f"{name} 出现危险调用/导入：{problems}"


# ── 采样数据 ───────────────────────────────────────────────────────────────
def test_samples_cover_objects_for_manim():
    scene = compile_scene(spec_of("saddle-a1-h0-k0"), RenderProfile.DRAFT)
    assert set(scene.samples["objects"]) >= {"u", "trace_y", "trace_x", "origin"}
    surface = scene.samples["objects"]["u"]
    assert len(surface["x_values"]) <= 64 and len(surface["y_values"]) <= 64
    assert len(surface["u"]) == len(surface["y_values"])
    section = scene.samples["objects"]["trace_y"]
    assert section["fixed_variable"] == "y"
    assert len(section["points3d"]) == len(section["points2d"]) == 48


def test_formulas_are_printed_from_ast_not_from_model_tex():
    scene = compile_scene(spec_of("saddle-a1-h0-k0"), RenderProfile.DRAFT)
    assert scene.formulas, "formula 对象必须由可信排版生成"
    assert any("x" in text and "^2" in text for text in scene.formulas)
    assert not any("\\" in text for text in scene.formulas), "不得回吐任意 TeX"


def test_parameter_change_changes_source_and_samples():
    low = compile_scene(spec_of("saddle-a0.5-h0.5-k-0.5"), RenderProfile.DRAFT)
    high = compile_scene(spec_of("saddle-a2-h0.5-k-0.5"), RenderProfile.DRAFT)
    assert low.source_hash != high.source_hash
    assert low.samples["objects"]["u"]["u"] != high.samples["objects"]["u"]["u"]
    assert low.samples["objects"]["trace_y"]["points2d"] != \
        high.samples["objects"]["trace_y"]["points2d"]
    assert low.captions != high.captions


def test_compiled_scene_serializes_for_manifest():
    scene = compile_scene(spec_of("vector-sum"), RenderProfile.DRAFT)
    payload = scene.as_dict()
    json.dumps(payload, ensure_ascii=False)
    assert payload["spec_hash"] == scene.spec_hash
    assert payload["source_hash"] == scene.source_hash
    assert payload["compiler_version"] == COMPILER_VERSION
    assert payload["profile"] == "draft"


# ── 留下的夹具必须可复现（T07 直接消费）────────────────────────────────────
@pytest.mark.parametrize("name", ["saddle-a1-h0-k0", "saddle-a0.5-h0.5-k-0.5",
                                  "saddle-a2-h0.5-k-0.5", "pseudo-cancel-a2-b3",
                                  "vector-sum", "matrix-apply"])
def test_fixture_sources_are_reproducible(name):
    stored_source = (FIXTURES / f"{name}.source.py").read_text(encoding="utf-8")
    stored_hash = (FIXTURES / f"{name}.source.sha256").read_text(
        encoding="utf-8").strip()
    scene = compile_scene(spec_of(name), RenderProfile.DRAFT)
    assert scene.source == stored_source, "夹具源码与重新编译结果不一致"
    assert scene.source_hash == stored_hash
    compile(scene.source, f"<{name}>", "exec")
