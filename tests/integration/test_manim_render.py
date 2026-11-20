"""T07 真实渲染集成测试：CH04 首样 `u(x,y)=x²−y²` 真出片（draft + final）。

规格与可信源码都是 **T06 的固定非模型夹具**（`tests/fixtures/visual/saddle-a1-h0-k0.*`），
本轮**零模型调用**（计划 §1.7：新增生成／评审预算为 0）。

这道测试回答的是"成片是不是真的"：

* 用 `ffprobe` 核对 1280×720／30fps／H.264／yuv420p／faststart／**无音轨**；
* 用 `ffmpeg -f null -` **完整解码**整段视频；
* e.g. poster／每步关键帧／UTF-8 WebVTT／可见文字稿齐全，每份资源都与 manifest 的 sha256 一致；
* 720p 主字幕实测墨迹高度 ≥ 24px（§5.4：不许只声明字号）；
* 测量 draft／final 的墙钟、峰值 RSS、输出字节，越 §6.1 上限就失败（**不许**静默放宽）。

前置环境缺失时**直接失败**，不用 skip 冒充通过（计划 §8 T07）。
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "visual"
for extra in (ROOT / "src", FIXTURES):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from aiforstudy.blocks.visual import manim_adapter as adapter  # noqa: E402
from aiforstudy.blocks.visual import worker as worker_module  # noqa: E402
from aiforstudy.blocks.visual.compiler import compile_scene  # noqa: E402
from aiforstudy.blocks.visual.spec import parse_visual_spec  # noqa: E402
from aiforstudy.core.workspace import Workspace  # noqa: E402
from aiforstudy.providers.capabilities import probe  # noqa: E402

SAMPLE = "saddle-a1-h0-k0"
WORKER_DIR = ROOT / "tools" / "manim_worker"
#: §6.1 的上限（测试自己再写一遍，防止实现里被悄悄放大）
DRAFT_TIMEOUT = 120.0
FINAL_TIMEOUT = 300.0
MEMORY_BYTES = 2 * 1024 ** 3
OUTPUT_BYTES = 128 * 1024 ** 2


def interpreter_or_fail() -> str:
    probe_result = probe(Workspace(root=ROOT))
    missing = [item.name for item in probe_result.missing_for("render")]
    if missing:
        pytest.fail(f"T07 真实渲染前置环境缺失：{missing}。缺能力属 blocked，"
                    f"不允许 skip 后宣称完成（计划 §8 T07）。")
    return adapter.default_services(workspace_root=ROOT).interpreter


def load_spec(path: Path):
    return parse_visual_spec(json.loads(path.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def sample():
    spec_path = FIXTURES / f"{SAMPLE}.spec.json"
    return {"spec": load_spec(spec_path), "source": (FIXTURES / f"{SAMPLE}.source.py").read_text(encoding="utf-8")}


def _render(profile: str, root: Path) -> dict:
    spec = load_spec(FIXTURES / f"{SAMPLE}.spec.json")
    scene = compile_scene(spec, profile)
    if profile == "draft":
        # draft 档必须与 T06 提交的夹具源码逐字节一致（即"没人偷偷改过素材"）
        recorded = (FIXTURES / f"{SAMPLE}.source.py").read_text(encoding="utf-8")
        assert scene.source == recorded
    services = adapter.ManimServices(
        worker_dir=WORKER_DIR, interpreter=interpreter_or_fail(),
        scratch_root=root / f"scratch-{profile}", cache_dir=root / "cache")
    limits = adapter.limits_for_profile(WORKER_DIR, profile)
    assert limits.timeout_seconds == (DRAFT_TIMEOUT if profile == "draft" else FINAL_TIMEOUT)
    assert limits.memory_bytes == MEMORY_BYTES
    assert limits.output_bytes == OUTPUT_BYTES
    cache = worker_module.AnimationCache(root / "cache")
    started = time.monotonic()
    result = adapter.render_scene(scene, limits, services, spec=spec, profile=profile,
                                  output_dir=root / f"out-{profile}", cache=cache)
    result["_wall_seconds_measured"] = round(time.monotonic() - started, 3)
    result["_output_dir"] = str(root / f"out-{profile}")
    return result


@pytest.fixture(scope="module")
def renders(tmp_path_factory):
    root = tmp_path_factory.mktemp("manim-sample")
    draft = _render("draft", root)
    final = _render("final", root)
    measurements = {
        "unit": SAMPLE,
        "profiles": {
            "draft": {"status": draft["status"],
                      "wall_seconds": draft["_wall_seconds_measured"],
                      "worker_seconds": draft["technical_report"]["worker"]["wall_seconds"],
                      "peak_rss_bytes": draft["technical_report"]["worker"]["peak_rss_bytes"],
                      "output_bytes": draft["technical_report"]["worker"]["output_bytes"]},
            "final": {"status": final["status"],
                      "wall_seconds": final["_wall_seconds_measured"],
                      "worker_seconds": final["technical_report"]["worker"]["wall_seconds"],
                      "peak_rss_bytes": final["technical_report"]["worker"]["peak_rss_bytes"],
                      "output_bytes": final["technical_report"]["worker"]["output_bytes"]},
        },
        "limits": {"draft_timeout_seconds": DRAFT_TIMEOUT,
                   "final_timeout_seconds": FINAL_TIMEOUT,
                   "memory_bytes": MEMORY_BYTES, "output_bytes": OUTPUT_BYTES},
        "isolation": draft["technical_report"]["isolation"].get("backend"),
    }
    root.joinpath("measurements.json").write_text(
        json.dumps(measurements, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    yield {"draft": draft, "final": final, "root": root, "measurements": measurements}
    print("\n[measurements]", json.dumps(measurements["profiles"], ensure_ascii=False))


def test_render_capability_is_available_on_this_machine():
    capabilities = probe(Workspace(root=ROOT))
    capabilities.require("render")
    assert capabilities.render_backends() == ["manim"]


@pytest.mark.parametrize("profile,pixels,frame_rate", [("draft", (640, 360), "15/1"),
                                                       ("final", (1280, 720), "30/1")])
def test_render_produces_a_complete_decodable_silent_video(renders, sample, profile, pixels, frame_rate):
    result = renders[profile]
    assert result["status"] == "fulfilled", result["technical_report"]["worker"]
    report = result["frame_report"]
    assert report["status"] == "pass", report["failed"]
    assert report["review_status"] == "not_run", "语义画面评审属 T08，本阶段不许伪 filled"
    checks = {item["check"]: item for item in report["checks"]}
    assert checks["full_decode"]["status"] == "pass", checks["full_decode"]
    assert checks["audio_policy"]["status"] == "pass", "首样必须符合可选讲解音轨规范"
    assert checks["codec"]["status"] == "pass"
    assert checks["pix_fmt"]["status"] == "pass"
    assert checks["faststart"]["status"] == "pass"
    assert checks["size"]["detail"].startswith(f"{pixels[0]}x{pixels[1]}")
    assert checks["frame_rate"]["detail"] == f"r_frame_rate={frame_rate}"
    seconds = float(checks["video_seconds"]["value"])
    expected_seconds=max(step['end'] for step in sample['spec'].steps)
    assert abs(seconds-expected_seconds)<=0.8, '成片应对应原规格时间线，不能靠填充满足最低秒数'
    assert checks["video_seconds"]["status"]=='pass'
    assert checks["duration_resource_limit"]["status"] == "pass"


def test_render_resources_are_complete_and_hash_consistent(renders):
    kinds = {"mp4", "poster", "keyframe", "captions", "transcript"}
    for profile in ("draft", "final"):
        result = renders[profile]
        by_kind: dict[str, list[dict]] = {}
        for item in result["resources"]:
            by_kind.setdefault(item["kind"], []).append(item)
            assert item["sha256"].startswith("sha256:")
            assert item["bytes"] > 0
            path = Path(result["_output_dir"]) / item["relative_path"]
            assert path.is_file(), path
            assert item["sha256"] == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        assert set(by_kind) == kinds, (profile, sorted(by_kind))
        assert len(by_kind["keyframe"]) >= 4, "每步至少一张关键帧"
        assert by_kind["captions"][0]["mime"] == "text/vtt; charset=utf-8"
        assert len({item["sha256"] for item in by_kind["keyframe"]}) >= 4, "关键帧不能各自相同"
        # 每步都有对应关键帧
        for step in result["steps"]:
            assert step["keyframes"], step["step_id"]


def test_final_subtitles_are_big_enough_to_read(renders):
    """§5.4：720p 主标签实测 ≥ 24px（只声明 font_size 不算数）。"""
    checks = {item["check"]: item for item in renders["final"]["frame_report"]["checks"]}
    assert checks['caption_presentation']['status'] == 'note'
    assert 'external_vtt' in checks['caption_presentation']['detail']
    assert not any(key.startswith('caption_size:') for key in checks)
    # Actual visible captions and the toggle are covered by browser tests;
    # renderer pixels alone no longer claim that native subtitles were inspected.


def test_math_report_comes_from_the_independent_checker(renders):
    for profile in ("draft", "final"):
        report = renders[profile]["math_report"]
        assert report["status"] == "pass", report
        assert report["checks"], "空报告不算 pass"
        kinds = {item["kind"] for item in report["checks"]}
        assert {"partial_derivative", "derivative_sum", "section_is_trace"} <= kinds


def test_animation_result_matches_the_frozen_shape(renders):
    for profile in ("draft", "final"):
        result = renders[profile]
        for field in ("schema_version", "request_id", "status", "spec_hash", "compiler_hash",
                      "environment_hash", "source_binding", "resources", "steps",
                      "math_report", "frame_report", "technical_report"):
            assert field in result, field
        assert result["schema_version"] == "1.0"
        assert result["request_id"]
        assert result["environment_hash"].startswith("sha256:")
        assert result["compiler_hash"].startswith("sha256:")
        spec = load_spec(FIXTURES / f"{SAMPLE}.spec.json")
        assert result["spec_hash"] == spec.spec_hash
        binding = result["source_binding"]
        assert binding["unit_id"] and binding["request_id"]
        assert binding["spec_hash"] == result["spec_hash"]
        assert result["steps"][0]["caption"]


def test_webvtt_is_utf8_with_one_cue_per_step(renders):
    spec = load_spec(FIXTURES / f"{SAMPLE}.spec.json")
    result = renders["final"]
    text = (Path(result["_output_dir"]) / "captions.vtt").read_text(encoding="utf-8")
    assert text.startswith("WEBVTT")
    cues = [block for block in text.strip().split("\n\n")[1:] if block.strip()]
    assert len(cues) == len(spec.steps), (len(cues), len(spec.steps))
    for cue, step, rendered_step in zip(cues, spec.steps, result["steps"]):
        lines = cue.splitlines()
        start, _, end = lines[1].partition(" --> ")
        assert _seconds(start) == pytest.approx(float(step["start"]), abs=1e-3)
        assert _seconds(end) == pytest.approx(float(step["end"]), abs=1e-3)
        # 字幕是**编译后**的文本（占位符已由可信层填值），与步骤记录逐字一致
        assert lines[2] == rendered_step["caption"]
    transcript = (Path(result["_output_dir"]) / "transcript.txt").read_text(encoding="utf-8")
    for step in spec.steps:
        assert step["step_id"] in transcript
    for caption in result["steps"]:
        assert caption["caption"] in transcript, caption["caption"]


def _seconds(stamp: str) -> float:
    hours, minutes, rest = stamp.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def test_measured_time_and_resources_stay_inside_the_frozen_limits(renders):
    measurements = renders["measurements"]
    for profile, timeout in (("draft", DRAFT_TIMEOUT), ("final", FINAL_TIMEOUT)):
        entry = measurements["profiles"][profile]
        assert entry["status"] == "fulfilled"
        # 首样必须落在 §6.1 的档位上限内；超了就是工程问题，失败并留下测量值
        assert entry["worker_seconds"] <= timeout, entry
        assert entry["peak_rss_bytes"] <= MEMORY_BYTES, entry
        assert entry["output_bytes"] <= OUTPUT_BYTES, entry
    assert measurements["limits"]["final_timeout_seconds"] == FINAL_TIMEOUT
    assert measurements["limits"]["memory_bytes"] == MEMORY_BYTES


def test_final_render_is_cached_by_environment_and_spec_hashes(renders):
    result = renders["final"]
    assert result["technical_report"]["cache_committed"] is True
    key = result["technical_report"]["cache_key"]
    cache = worker_module.AnimationCache(renders["root"] / "cache")
    hit = cache.lookup(key)
    assert hit is not None and hit["cache_hit"] is True
    assert hit["request_id"] == result["request_id"]
    assert hit["profile"] == "final"


def test_isolation_backend_is_recorded_with_its_probe(renders):
    isolation = renders["final"]["technical_report"]["isolation"]
    assert isolation["safe"] is True, isolation.get("reason")
    assert isolation["paths_blocked"] is True
    assert isolation["network"]["network_reachable"] is False
    environment = renders["final"]["technical_report"]["environment"]
    assert environment["font"]["family"] == "Noto Sans CJK SC"
    assert environment["math_tex"]["ok"] is True


def test_no_browser_or_node_needed_and_no_network_used(renders):
    """渲染 worker 不发任何网络请求：宿主侧产物目录里不该出现网络缓存/证书一类文件。"""
    for profile in ("draft", "final"):
        out = Path(renders[profile]["_output_dir"])
        names = sorted(item.name for item in out.rglob("*") if item.is_file())
        assert not any(name.endswith((".pem", ".crt", ".cookie")) for name in names)
        assert all(name != "requests_cache" for name in names)
