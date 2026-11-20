"""T07 worker／适配器测试：拒绝任意脚本、最小环境无密钥、缺能力收费前 blocked、
超时／TERM→KILL、输出与内存上限、取消不提交成功缓存。

这里**不**跳过环境："真实渲染前置环境不能 SKIP 后宣称 T07 完成"（计划 §8 T07）。
环境缺失时用 `pytest.fail` 明确失败并指出缺哪一项能力，而不是把红灯藏成 skip。

秒级的用例放在本文件：适配器源码校验、计划白名单、bwrap 参数、隔离探针、限额与取消。
真出片（draft／final 30–60s）在 `tests/integration/test_manim_render.py`。
"""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
import inspect
import json
import os
import re
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "visual"
for extra in (ROOT / "src", FIXTURES):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from aiforstudy.blocks.visual import manim_adapter as adapter  # noqa: E402
from aiforstudy.blocks.visual import worker as worker_module  # noqa: E402
from aiforstudy.blocks.visual.compiler import compile_scene  # noqa: E402
from aiforstudy.blocks.visual.spec import parse_visual_spec  # noqa: E402
from aiforstudy.core.workspace import Workspace  # noqa: E402
from aiforstudy.errors import CapabilityError, ContractError  # noqa: E402
from aiforstudy.providers.capabilities import (probe,  # noqa: E402
                                              resolve_manim_interpreter)

WORKER_DIR = ROOT / "tools" / "manim_worker"
FULL_LIMITS = worker_module.RenderLimits(
    timeout_seconds=120.0, cpu_cores=2, memory_bytes=2 * 1024 ** 3,
    output_bytes=128 * 1024 ** 2)


@pytest.fixture(autouse=True)
def serial_worker_capacity():
    previous_capacity = worker_module._WORKER_CAPACITY
    worker_module.configure_worker_concurrency(1)
    yield
    worker_module.configure_worker_concurrency(previous_capacity)


def interpreter_or_fail() -> str:
    interpreter = os.environ.get("AIFORSTUDY_MANIM_PYTHON", "") or \
        resolve_manim_interpreter(ROOT)
    if not interpreter or not Path(interpreter).is_file():
        pytest.fail("T07 前置环境缺失：找不到独立 Manim 解释器（AIFORSTUDY_MANIM_PYTHON "
                    "或 config/capabilities.json 的 render_environment）。真实渲染测试"
                    "**不允许**用 skip 冒充通过（计划 §8 T07）。")
    return interpreter


def services_for(name: str, *, tmp_path: Path | None = None, **kwargs) -> adapter.ManimServices:
    scratch = Path(tmp_path or (Path(os.environ.get("TMPDIR", "/tmp")) / "aifs-t07"))
    return adapter.ManimServices(worker_dir=WORKER_DIR, interpreter=interpreter_or_fail(),
                                 scratch_root=scratch / name, **kwargs)


def load_spec(name: str):
    return parse_visual_spec(
        json.loads((FIXTURES / f"{name}.spec.json").read_text(encoding="utf-8")))


def fixture_source(name: str) -> str:
    return (FIXTURES / f"{name}.source.py").read_text(encoding="utf-8")


def tiny_spec() -> dict:
    """1.5 秒小样：够触发编译／渲染／缓存，但秒级完成（限额与取消测试用它）。"""
    return {
        "schema_version": "1.0", "request_id": "vis-tiny-01", "unit_id": "CH04",
        "source_refs": ["s"], "lesson_anchor": "a", "source_claims": ["c"],
        "dependency_paths": [], "objective": "小样", "parameters": {},
        "objects": [
            {"object_id": "axes2d", "kind": "axes2d", "x_range": [-2, 4], "y_range": [-2, 4],
             "x_label": "x", "y_label": "y"},
            {"object_id": "v1", "kind": "vector", "space": "math2d", "start": [0, 0],
             "end": [1, 2], "color": "blue"},
            {"object_id": "v2", "kind": "vector", "space": "math2d", "start": [0, 0],
             "end": [1, 1], "color": "green"},
            {"object_id": "vsum", "kind": "vector", "space": "math2d", "start": [0, 0],
             "end": [2, 3], "color": "red"},
        ],
        "steps": [
            {"step_id": "s1", "caption": "先看 v1 与 v2。", "start": 0, "end": 0.75,
             "actions": [{"action": "reveal", "object_id": "axes2d"},
                         {"action": "reveal", "object_id": "v1"},
                         {"action": "reveal", "object_id": "v2"}],
             "checkpoints": [{"at": 0.5, "note": "两个分向量"}], "expected_change": "出现分向量"},
            # 相机动作是有意放进来的：2D 场景若用普通 `Scene`，`camera.frame` 不存在，
            # 会以 `AttributeError: 'Camera' object has no attribute 'frame'` 直接崩
            # （matrix-apply 小样实测踩过）。这条小样让该路径每次都跑一遍。
            {"step_id": "s2", "caption": "和向量终点是 (2, 3)。", "start": 0.75, "end": 1.5,
             "actions": [{"action": "reveal", "object_id": "vsum"},
                         {"action": "camera", "zoom": 1.3}],
             "checkpoints": [{"at": 1.2, "note": "终点"}], "expected_change": "出现和向量"},
        ],
        "math_assertions": [
            {"assertion_id": "sum", "kind": "vector_sum", "operands": ["v1", "v2"],
             "result_object": "vsum", "expected": [2, 3]},
        ],
        "render_profile": "draft",
    }


def tiny_scene():
    spec = parse_visual_spec(tiny_spec())
    return spec, compile_scene(spec, "draft")


class CancelToken:
    """最小取消信号（与 pipeline 的语义一致：`.cancelled`）。"""

    def __init__(self, cancelled: bool = False):
        self.cancelled = cancelled


def run_probe(tmp_path: Path, *, name: str = "probe", collect_environment: bool = False,
              probe_paths: list[str] | None = None, limits=None):
    services = services_for(name, tmp_path=tmp_path)
    return worker_module.probe_isolation(
        services=services.worker_services(), limits=limits or FULL_LIMITS,
        collect_environment=collect_environment,
        probe_paths=probe_paths if probe_paths is not None else [str(Path.home() / ".bashrc")])


# ── 红：能力缺失必须在收费前 blocked ──────────────────────────────────────
def test_render_backend_requires_only_real_probed_names():
    declared = json.loads((ROOT / "config" / "capabilities.json").read_text(encoding="utf-8"))
    requires: set[str] = set()
    for spec in (declared.get("render_backends") or {}).values():
        requires.update(spec.get("requires") or [])
    names = {item["name"] for item in probe(Workspace(root=ROOT)).as_dict()["items"]}
    assert requires, "配置里必须声明渲染后端需要哪些能力"
    unknown = sorted(requires - names)
    assert not unknown, f"render_backends 的 requires 指向了不存在的探测名：{unknown}"
    assert probe(Workspace(root=ROOT)).render_backends() == ["manim"]


def test_missing_render_capability_blocks_before_any_model_call(tmp_path):
    """缺渲染能力 → **收费前** blocked：探测阶段不许有任何模型调用。"""
    calls: list[str] = []

    class SpyTransport:
        def __call__(self, **kwargs):
            calls.append(str(kwargs.get("tag") or "call"))
            return "", {}

    workspace_dir = tmp_path / "ws"
    (workspace_dir / "config").mkdir(parents=True)
    (workspace_dir / "config" / "books.json").write_text("{}\n", encoding="utf-8")
    config = json.loads((ROOT / "config" / "capabilities.json").read_text(encoding="utf-8"))
    config["render_environment"]["interpreter_candidates"] = [
        "{workspace}/no-such-venv/bin/python"]
    config["render_environment"]["interpreter_env"] = "AIFORSTUDY_MANIM_PYTHON_MISSING"
    (workspace_dir / "config" / "capabilities.json").write_text(
        json.dumps(config, ensure_ascii=False), encoding="utf-8")
    capabilities = probe(Workspace(root=workspace_dir))
    transport = SpyTransport()
    with pytest.raises(CapabilityError) as excinfo:
        capabilities.require("render")
        transport(tag="不该发生")
    assert calls == [], "缺能力时不允许已经发生模型调用"
    message = str(excinfo.value)
    assert "render:manim" in message and "env:manim" in message
    assert capabilities.render_backends() == []


def test_render_scene_refuses_to_run_without_isolation(tmp_path):
    """隔离不可用 → blocked，**绝不**无隔离执行模型输入。"""
    spec, scene = tiny_scene()
    output = tmp_path / "out"
    services = adapter.ManimServices(worker_dir=WORKER_DIR,
                                     interpreter=interpreter_or_fail(),
                                     scratch_root=tmp_path / "scratch", isolation="none")
    result = adapter.render_scene(scene, FULL_LIMITS, services, spec=spec, output_dir=output)
    assert result["status"] == "blocked"
    assert result["technical_report"]["kind"] == "isolation"
    assert not (output / "video.mp4").exists(), "blocked 时不许有产物"


# ── 适配器：只接受 T06 可信源码 ────────────────────────────────────────────
def test_trusted_source_round_trips_for_every_fixture():
    for path in sorted(FIXTURES.glob("*.source.py")):
        name = path.name[: -len(".source.py")]
        scene = compile_scene(load_spec(name), "draft")
        assert scene.source == fixture_source(name), name
        payload = adapter.load_trusted_scene(scene.source,
                                            expected_source_hash=scene.source_hash,
                                            profile="draft")
        assert payload["format"] == "visual-scene-1.0"
        assert payload["compiler_version"] == scene.compiler_version


@pytest.mark.parametrize("mutation", [
    "import os\nos.system('echo pwned')\n",
    "\nRUN = __import__('subprocess').run(['echo', 'x'])\n",
    "\nSCENE['objects']['axes2d']['x_range'] = [-99, 99]\n",
    "\ndef extra():\n    return 1\n",
    "\n# 只加一条注释也不行\n",
    "\nSCENE = {**SCENE, 'format': 'other-1.0'}\n",
])
def test_adapter_rejects_arbitrary_script_content(mutation):
    with pytest.raises(adapter.UntrustedVisualScene):
        adapter.load_trusted_scene(fixture_source("saddle-a1-h0-k0") + mutation)


def test_adapter_rejects_modified_scene_literals_even_if_shape_is_legal():
    """形状合法但改了数字（画面会被改）也要拒绝。

    注意这条防线在哪：模板比对只能挡"模板之外还有代码"；**改数字**是合法模板对另一份
    载荷的重渲染，必须靠 `CompiledScene.source_hash` 挡住 —— 所以这里带上真实哈希。
    """
    scene = compile_scene(load_spec("saddle-a1-h0-k0"), "draft")
    original = scene.source
    mutated = original.replace("'x_range': [-2, 2]", "'x_range': [-3, 3]", 1)
    assert mutated != original, "夹具文本变了，这条测试需要更新匹配串"
    with pytest.raises(adapter.UntrustedVisualScene) as excinfo:
        adapter.load_trusted_scene(mutated, expected_source_hash=scene.source_hash)
    assert "哈希" in str(excinfo.value)
    # 反过来说清楚：不带哈希时它仍是一份"结构合法"的 T06 模板产物（内容校验靠哈希）
    payload = adapter.load_trusted_scene(mutated)
    assert payload["objects"]["axes2d"]["x_range"] == [-3, 3]


def test_adapter_rejects_extra_code_even_in_canonical_shape():
    """模板之外多一条语句（哪怕只是注释）必须被逐字节比对挡住。"""
    source = fixture_source("saddle-a1-h0-k0")
    with pytest.raises(adapter.UntrustedVisualScene) as excinfo:
        adapter.load_trusted_scene(source + "\n# 注释\n")
    assert "逐字节" in str(excinfo.value) or "不允许的语句" in str(excinfo.value)


def test_adapter_rejects_source_hash_mismatch():
    with pytest.raises(adapter.UntrustedVisualScene):
        adapter.load_trusted_scene(fixture_source("saddle-a1-h0-k0"),
                                   expected_source_hash="sha256:" + "0" * 64)


def test_adapter_has_no_script_or_host_path_entry_point():
    """适配器没有"传一个脚本／宿主路径给我跑"的入口。"""
    forbidden = re.compile(r"(exec|eval|compile_file|run_script|from_path|script_path|"
                           r"import_module)")
    for name, _member in inspect.getmembers(adapter, inspect.isfunction):
        assert not forbidden.search(name), f"适配器不该暴露 {name}()"
    signature = inspect.signature(adapter.render_scene)
    assert "source" not in signature.parameters
    assert list(signature.parameters)[0] == "scene"
    # 用 AST 判"有没有调用内置 exec/eval"，而不是字符串匹配（`ast.literal_eval` 是合法的
    # 字面量求值，不能和内置 `eval` 混为一谈 —— 这正是第一版测试假红的原因）。
    import ast as ast_module

    tree = ast_module.parse(inspect.getsource(adapter))
    called = {node.func.id for node in ast_module.walk(tree)
              if isinstance(node, ast_module.Call) and isinstance(node.func, ast_module.Name)}
    assert not (called & {"exec", "eval", "compile", "__import__"}), called
    imported = {alias.name for node in ast_module.walk(tree)
                if isinstance(node, ast_module.Import) for alias in node.names}
    imported |= {node.module or "" for node in ast_module.walk(tree)
                 if isinstance(node, ast_module.ImportFrom)}
    assert "importlib" not in imported, "适配器不许按需 import 外部模块"
    with pytest.raises(ContractError):
        adapter.compile_plan(str(FIXTURES / "vector-sum.source.py"), load_spec("vector-sum"),
                             worker_dir=WORKER_DIR)


def test_plan_rejects_unknown_payload_fields_objects_and_actions():
    builder = worker_module.load_asset(WORKER_DIR, "scene_builder")
    _spec, scene = tiny_scene()
    payload = adapter.load_trusted_scene(scene.source, profile="draft")
    builder.validate_payload(payload, profile="draft")

    def mutated_payload():
        return json.loads(json.dumps(payload))

    cases = []
    injected = mutated_payload()
    injected["script"] = "/bin/sh"
    cases.append(injected)
    kind = mutated_payload()
    kind["objects"]["v1"]["kind"] = "shell"
    cases.append(kind)
    action = mutated_payload()
    action["steps"][0]["actions"].append({"action": "system", "object_id": "v1"})
    cases.append(action)
    host_path = mutated_payload()
    host_path["objects"]["v1"]["path"] = "/etc/passwd"
    cases.append(host_path)
    for case in cases:
        with pytest.raises(builder.ScenePlanError):
            builder.validate_payload(case, profile="draft")


def test_plan_keeps_spec_timeline_exactly():
    builder = worker_module.load_asset(WORKER_DIR, "scene_builder")
    for name in ("saddle-a1-h0-k0", "saddle-a0.5-h0.5-k-0.5", "vector-sum", "matrix-apply"):
        scene = compile_scene(load_spec(name), "draft")
        payload = adapter.load_trusted_scene(scene.source, profile="draft")
        plan = builder.plan_scene(payload, profile="draft")
        assert plan["total_seconds"] == max(float(step["end"]) for step in payload["steps"])
        for planned, source_step in zip(plan["timeline"], payload["steps"]):
            assert planned["start"] == source_step["start"]
            assert planned["end"] == source_step["end"]
            parts = planned["parts"]
            assert parts[0]["action"] == "caption_in" and parts[-1]["action"] == "caption_out"
            for previous, following in zip(parts, parts[1:]):
                assert previous["end"] <= following["start"] + 1e-9
            for part in parts:
                assert part["end"] > part["start"], part


def test_plan_rejects_overlapping_steps_and_pause_only_steps():
    builder = worker_module.load_asset(WORKER_DIR, "scene_builder")
    overlapping = tiny_spec()
    overlapping["steps"][1]["start"] = 0.1
    with pytest.raises(ContractError):
        parse_visual_spec(overlapping)
    payload = {"format": "visual-scene-1.0", "compiler_version": "visual-compiler-1.0",
               "profile": "draft", "parameters": {}, "captions": ["a", "b"], "formulas": [],
               "math_values": {},
               "objects": {"axes2d": {"kind": "axes2d", "x_range": [-1, 1], "y_range": [-1, 1]}},
               "steps": [{"step_id": "s1", "start": 0, "end": 1, "caption": "a",
                          "actions": [{"action": "pause", "seconds": 0.98},
                                      {"action": "pause", "seconds": 0.98}],
                          "checkpoints": []},
                         {"step_id": "s2", "start": 1, "end": 2, "caption": "b",
                          "actions": [], "checkpoints": []}]}
    with pytest.raises(builder.ScenePlanError):
        builder.plan_scene(payload, profile="draft")


# ── bwrap 参数 ─────────────────────────────────────────────────────────────
def test_bwrap_argv_is_whitelisted_never_binds_home_or_root():
    bwrap = worker_module.load_asset(WORKER_DIR, "bwrap")
    scratch = "/tmp/aifs-bwrap-test/scratch"
    output = "/tmp/aifs-bwrap-test/out"
    argv = bwrap.build_argv(read_only=["/usr", "/etc", str(WORKER_DIR)], read_write=[],
                            scratch=scratch, output=output,
                            entry=str(WORKER_DIR / "run_scene.py"),
                            job_path=scratch + "/j.json", python="/usr/bin/python3",
                            env={"HOME": scratch}, chdir=scratch)
    assert "--unshare-all" in argv, "必须没有网络命名空间"
    assert "--clearenv" in argv, "不许把宿主环境原样带进沙箱"
    assert "--die-with-parent" in argv
    joined = " ".join(argv)
    assert "--ro-bind / /" not in joined
    assert "--ro-bind-try /home " not in joined and "--ro-bind /home " not in joined
    bound = {argv[index + 2] for index, item in enumerate(argv) if item == "--ro-bind-try"}
    assert {"/usr", "/etc", str(WORKER_DIR)} <= bound
    writable = {argv[index + 2] for index, item in enumerate(argv) if item == "--bind-try"}
    assert {scratch, output} <= writable


def test_bwrap_selftest_reports_the_real_reason_when_unavailable():
    bwrap = worker_module.load_asset(WORKER_DIR, "bwrap")
    report = bwrap.selftest()
    assert set(report) >= {"available", "detail", "argv"}
    if not report["available"]:
        assert report["detail"], "不可用时必须留下**原始**失败原因，不许只说一句不可用"


# ── 隔离探针：哨兵、网络、最小环境 ────────────────────────────────────────
@pytest.fixture(scope="module")
def isolation_probe(tmp_path_factory):
    return run_probe(tmp_path_factory.mktemp("probe"), name="probe",
                     collect_environment=True)


def test_probe_sentinel_is_readable_outside_but_not_inside(isolation_probe):
    assert isolation_probe["sentinel_readable_outside"], "对照：哨兵在沙箱外必须可读"
    isolation = isolation_probe["isolation"]
    assert isolation["safe"] is True, isolation.get("reason")
    assert isolation["probed_paths"], "探针必须至少有一个白名单之外的真实目标"
    for item in isolation["probed_paths"]:
        assert item["readable"] is False, f"{item['path']} 在沙箱里可读，隔离没生效"
    assert isolation["paths_blocked"] is True


def test_probe_proves_the_network_is_unreachable(isolation_probe):
    network = isolation_probe["isolation"]["network"]
    assert network["network_reachable"] is False
    assert network["tcp_connected"] is False
    assert network["dns_result"] == ""
    assert "permitted" in (network.get("socket_error") or "") or \
        "permitted" in (network.get("tcp_error") or "")


def test_probe_environment_has_no_provider_secret(tmp_path, monkeypatch):
    """宿主**故意**带着假凭据跑；沙箱里必须看不见它（不看真实密钥）。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "FAKE-SENTINEL-NOT-A-REAL-SECRET")
    monkeypatch.setenv("AIFORSTUDY_FAKE_TOKEN", "FAKE-TOKEN")
    probe_result = run_probe(tmp_path, name="secret", collect_environment=True)
    assert os.environ["DEEPSEEK_API_KEY"] == "FAKE-SENTINEL-NOT-A-REAL-SECRET"
    environment = probe_result["environment"]
    sandbox_report = environment.get("sandbox", {})
    assert sandbox_report.get("safe") is True
    assert sandbox_report.get("env_keys"), "探针必须回报沙箱里的环境变量名单"
    assert "DEEPSEEK_API_KEY" not in sandbox_report["env_keys"]
    assert "AIFORSTUDY_FAKE_TOKEN" not in sandbox_report["env_keys"]
    assert sandbox_report.get("provider_secret_present") is False
    assert sandbox_report.get("provider_secret_keys") == []


def test_worker_entry_scrubs_the_environment_before_importing_manim():
    """入口的第一步就是清空环境（Landlock 后端会继承宿主环境，bwrap 用 --clearenv）。"""
    run_scene = worker_module.load_asset(WORKER_DIR, "run_scene")
    original = dict(os.environ)
    try:
        os.environ["DEEPSEEK_API_KEY"] = "FAKE-SENTINEL-NOT-A-REAL-SECRET"
        run_scene._scrub_environment({"PATH": "/usr/bin", "HOME": "/tmp/x"})
        assert "DEEPSEEK_API_KEY" not in os.environ
        assert os.environ["PATH"] == "/usr/bin"
        assert sorted(os.environ) == ["HOME", "PATH"]
    finally:
        os.environ.clear()
        os.environ.update(original)


def test_environment_fingerprint_records_tools_font_and_math_tex(isolation_probe):
    environment = isolation_probe["environment"]
    assert environment["packages"]["manim"] == "0.21.0"
    assert environment["packages"]["pycairo"]
    assert environment["packages"]["ManimPango"]
    assert environment["native"]["cairo"] and environment["native"]["pango"]
    assert environment["font"]["family"] == "Noto Sans CJK SC"
    assert environment["font"]["sha256"].startswith("sha256:")
    assert environment["font"]["in_manimpango"] is True
    assert Path(environment["font"]["fc_match"]).resolve() == Path(environment["font"]["file"]).resolve()
    assert environment["math_tex"]["ok"] is True
    assert environment["math_tex"]["latex_ok"] is True
    assert environment["tex_packages"]["preview.sty"]["found"] is True
    for tool in ("ffmpeg", "ffprobe", "latex", "dvisvgm"):
        assert environment["tools"][tool]["available"] is True, tool
        assert environment["tools"][tool]["path"]


def test_environment_hash_is_stable_and_profile_specific(isolation_probe):
    first = worker_module.environment_hash(isolation_probe, profile="final")
    second = worker_module.environment_hash(isolation_probe, profile="final")
    assert first == second
    assert worker_module.environment_hash(isolation_probe, profile="draft") != first


def test_environment_hash_ignores_run_specific_paths(isolation_probe):
    """环境指纹不能混进临时目录／netns 一类每次都变的字段（否则缓存永远不命中）。"""
    tampered = json.loads(json.dumps({key: value for key, value in isolation_probe.items()
                                      if key in ("environment", "isolation")}))
    tampered["isolation"]["netns"] = "net:[999999]"
    tampered["isolation"]["probed_paths"] = [{"path": "/tmp/other", "readable": False}]
    for rule in tampered["isolation"]["applied"].get("landlock", {}).get("rules", []):
        rule["path"] = "/tmp/somewhere-else"
    if "seatbelt" in tampered["isolation"]["applied"]:
        tampered["isolation"]["applied"]["seatbelt"]["profile_sha256"] = "a different job path"
    assert worker_module.environment_hash(tampered, profile="final") == \
        worker_module.environment_hash(isolation_probe, profile="final")


def test_environment_fingerprint_file_is_written_with_sha256(isolation_probe):
    path = Path(isolation_probe["environment_path"])
    assert path.is_file(), path
    assert isolation_probe["environment_sha256"] == \
        "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


# ── 限额、超时、TERM→KILL、取消 ───────────────────────────────────────────
def test_monitor_decision_covers_every_limit():
    decide = worker_module.monitor_decision
    limits = worker_module.RenderLimits(timeout_seconds=10.0, memory_bytes=100,
                                        output_bytes=50)
    assert decide(elapsed=0.0, rss=0, output=0, cancelled=False, limits=limits) == ""
    assert decide(elapsed=11.0, rss=0, output=0, cancelled=False, limits=limits) == "timeout"
    assert decide(elapsed=0.0, rss=0, output=99, cancelled=False, limits=limits) == "output_limit"
    assert decide(elapsed=0.0, rss=200, output=0, cancelled=False, limits=limits) == "memory_limit"
    assert decide(elapsed=0.0, rss=0, output=0, cancelled=True, limits=limits) == "cancelled"


def test_probe_reports_the_rlimits_and_seccomp_it_really_set(tmp_path):
    services = services_for("limits", tmp_path=tmp_path)
    limits = worker_module.RenderLimits(timeout_seconds=45.0, cpu_cores=2,
                                        memory_bytes=700 * 1024 ** 2,
                                        output_bytes=12 * 1024 ** 2)
    probe_result = worker_module.probe_isolation(
        services=services.worker_services(), limits=limits,
        probe_paths=[str(Path.home() / ".bashrc")])
    applied = probe_result["isolation"]["applied"]
    assert applied["rlimits"]["single_file_bytes"] == 12 * 1024 ** 2
    if sys.platform == "darwin":
        assert applied["rlimits"]["address_space_bytes"] is None
        assert applied["rlimits"]["memory_enforcement"] == "host-process-group-rss"
        assert applied["seatbelt"]["network"] == "deny"
        assert {"setpgid", "setsid", "posix_spawn"} <= set(applied["seatbelt"]["denied_syscalls"])
    else:
        assert applied["rlimits"]["address_space_bytes"] == 700 * 1024 ** 2
        assert "socket" in applied["seccomp"]["denied_calls"]
        assert applied["landlock"]["abi"] >= 1


@pytest.mark.parametrize('preflight_complete',[False,True],ids=['probe-timeout','render-timeout'])
def test_timeout_terminates_with_term_then_kill_and_leaves_no_orphan(tmp_path,isolation_probe,preflight_complete):
    spec, scene = tiny_scene()
    services = services_for("timeout", tmp_path=tmp_path)
    if not preflight_complete:
        # A warm real environment can finish its probe in less than one second.
        # Keep the actual sandbox/process monitor, but make this probe's work
        # deterministically slow so the receipt must describe the probe phase.
        import shutil
        from dataclasses import replace
        fixture_root=tmp_path/'slow-probe'
        services=replace(services,worker_dir=fixture_root/'tools/manim_worker')
        shutil.copytree(WORKER_DIR,services.worker_dir)
        presentation=fixture_root/'src/aiforstudy/blocks/media/presentation_policy.py'
        presentation.parent.mkdir(parents=True)
        shutil.copyfile(ROOT/'src/aiforstudy/blocks/media/presentation_policy.py',presentation)
        (services.worker_dir/'probe_env.py').write_text(
            "import time\ndef collect(**kwargs):\n"
            "    print('controlled slow environment probe', flush=True)\n"
            "    time.sleep(60)\n",encoding='utf-8')
    limits = worker_module.RenderLimits(timeout_seconds=1.0, cpu_cores=2,
                                        memory_bytes=2 * 1024 ** 3,
                                        output_bytes=128 * 1024 ** 2)
    result = adapter.render_scene(scene, limits, services, spec=spec,
                                  output_dir=tmp_path / "out",
                                  environment=isolation_probe if preflight_complete else None)
    worker = result["technical_report"]["worker"]
    assert worker["terminated_by"].startswith("timeout"), worker
    assert worker["orphans_remaining"] == 0, "超时后不许留下孤儿进程"
    assert result["status"] == "failed"
    # 超时可能发生在已经写出部分产物之后：要紧的不是"没有字节落盘"（那是骗自己），
    # 而是**不提交成功缓存**、不留孤儿、不给 fulfilled。
    assert result["technical_report"]["worker"]["cache_committed"] is False
    assert result["frame_report"]["status"] != "pass"
    assert Path(worker['log_path']).is_file(), '终止日志仍应可追溯'
    if not preflight_complete:
        assert result['technical_report']['phase']=='environment_probe'
        assert 'controlled slow environment probe' in Path(worker['log_path']).read_text()


def test_memory_limit_that_starves_the_entry_is_reported_readably(tmp_path):
    """内存上限真的压到子进程时，宿主必须给出可读原因（不许空 reason）。"""
    services = services_for("memcap", tmp_path=tmp_path)
    tiny_limits = worker_module.RenderLimits(timeout_seconds=30.0, cpu_cores=1,
                                             memory_bytes=8 * 1024 * 1024,
                                             output_bytes=1024 * 1024)
    probe_result = worker_module.probe_isolation(
        services=services.worker_services(), limits=tiny_limits,
        probe_paths=[str(Path.home() / ".bashrc")])
    outcome = probe_result["outcome"]
    assert outcome["status"] in {"failed", "blocked", "timeout"}, outcome
    assert outcome["reason"] or outcome.get("result"), outcome


def test_parent_deadline_wins_over_profile_timeout():
    limits = worker_module.RenderLimits(timeout_seconds=300.0,
                                        parent_deadline=__import__("time").monotonic() + 3.0)
    assert 0 < limits.effective_timeout() <= 3.0
    expired = worker_module.RenderLimits(timeout_seconds=300.0,
                                         parent_deadline=__import__("time").monotonic() - 1.0)
    assert expired.effective_timeout() <= 0


def test_cancelled_render_reports_cancelled_and_does_not_commit_cache(tmp_path):
    spec, scene = tiny_scene()
    cache = worker_module.AnimationCache(tmp_path / "cache")
    services = services_for("cancel", tmp_path=tmp_path, cache_dir=tmp_path / "cache",
                            cancel=CancelToken(True))
    result = adapter.render_scene(scene, FULL_LIMITS, services, spec=spec,
                                  output_dir=tmp_path / "out", cache=cache)
    assert result["status"] == "cancelled", result["technical_report"]["worker"]
    worker = result["technical_report"]["worker"]
    assert worker["terminated_by"].startswith("cancelled")
    key = worker_module.AnimationCache.key(
        spec_hash=scene.spec_hash, compiler_hash=result["compiler_hash"],
        environment_hash=result["environment_hash"], profile="draft")
    assert cache.lookup(key) is None, "取消不许提交成功缓存"
    assert result["technical_report"]["worker"]["cache_committed"] is False


def test_successful_render_commits_cache_and_tampering_is_rejected(tmp_path):
    spec, scene = tiny_scene()
    cache = worker_module.AnimationCache(tmp_path / "cache")
    services = services_for("cache", tmp_path=tmp_path, cache_dir=tmp_path / "cache")
    result = adapter.render_scene(scene, FULL_LIMITS, services, spec=spec,
                                  output_dir=tmp_path / "out", cache=cache)
    assert result["status"] == "fulfilled", result["technical_report"]["worker"]
    key = result["technical_report"]["cache_key"]
    hit = cache.lookup(key)
    assert hit is not None and hit["cache_hit"] is True
    relative = {item["relative_path"] for item in hit["resources"]}
    assert {"video.mp4", "poster.png", "captions.vtt", "transcript.txt"} <= relative
    victim = Path(hit["cache_dir"]) / "transcript.txt"
    victim.write_text("tampered", encoding="utf-8")
    assert cache.lookup(key) is None, "被篡改的缓存不能被当成产物"


def test_single_worker_concurrency_is_enforced(tmp_path):
    """A configured capacity of one still serializes workers across services."""
    spec, scene = tiny_scene()
    outcomes: list[dict] = []
    errors: list[BaseException] = []

    def run(index: int) -> None:
        try:
            services = services_for(f"conc-{index}", tmp_path=tmp_path / f"c{index}")
            outcomes.append(adapter.render_scene(scene, FULL_LIMITS, services, spec=spec,
                                                 output_dir=tmp_path / f"out-{index}"))
        except BaseException as exc:                             # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=300)
    assert not errors, errors
    assert len(outcomes) == 2
    assert all(item["status"] == "fulfilled" for item in outcomes), \
        [item["technical_report"]["worker"] for item in outcomes]
    assert max(item["technical_report"]["worker"]["concurrency_observed"] for item in outcomes) == 1, \
        "同时活着的 worker 峰值必须恰好是 1"


def test_environment_receipt_uses_the_host_worker_capacity(tmp_path):
    worker_module.configure_worker_concurrency(3)
    probe_result = run_probe(tmp_path, collect_environment=True)
    assert probe_result["outcome"]["status"] == "ok", probe_result["outcome"]
    assert probe_result["environment"]["limits"]["max_concurrent_workers"] == 3
    recorded = json.loads(Path(probe_result["environment_path"]).read_text())
    assert recorded["limits"]["max_concurrent_workers"] == 3


def test_same_legacy_source_renders_in_independent_scratch_directories(tmp_path):
    worker_module.configure_worker_concurrency(3)
    services = services_for("shared-scratch", tmp_path=tmp_path)
    scenes = []
    for index in range(3):
        raw = tiny_spec()
        raw["request_id"] = f"same-source-{index}"
        spec = parse_visual_spec(raw)
        scenes.append((spec, compile_scene(spec, "draft")))
    assert len({scene.source_hash for _, scene in scenes}) == 1
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(adapter.render_scene, scene, FULL_LIMITS, services,
                               spec=spec, output_dir=tmp_path / f"out-{index}")
                   for index, (spec, scene) in enumerate(scenes)]
        outcomes = [future.result() for future in futures]
    workers = [item["technical_report"]["worker"] for item in outcomes]
    assert len({Path(item["log_path"]).parent for item in workers}) == 3
    assert all(item["status"] == "fulfilled" for item in outcomes), workers
    assert all(item["orphans_remaining"] == 0 for item in workers)
    assert len({item["compiler_hash"] for item in outcomes}) == 1


def test_worker_result_missing_is_reported_with_log_tail(tmp_path):
    """子进程崩在写结果之前：宿主必须给出**可读**原因，而不是一个空 reason。"""
    services = services_for("crash", tmp_path=tmp_path)
    job = {"schema": "manim-worker-job-1.0", "mode": "render", "profile": "draft",
           "scratch": str(tmp_path / "crash"), "output": str(tmp_path / "out-crash"),
           "plan": {"timeline": []},
           "limits": {"pixel_width": 64, "pixel_height": 64, "frame_rate": 5},
           "font": {"family": "x", "file": "y"}, "ffmpeg": "/usr/bin/ffmpeg"}
    outcome = worker_module.run_worker_job(services=services.worker_services(), job=job,
                                          limits=FULL_LIMITS, job_id="crash")
    assert outcome["status"] == "failed"
    assert outcome["reason"].strip(), "缺结果文件时必须带上日志里的原因"
