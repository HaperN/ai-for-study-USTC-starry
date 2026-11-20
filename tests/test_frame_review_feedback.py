"""T08：画面失败反馈必须是**可派发**的（计划 §T08／§5.1／§5.2）。

改造前的独立评审输入是规格 JSON，失败时只回一句"画面不对"。这一组测试用三份
**真的把缺陷画进像素**的夹具把三类问题分开钉住：

| 夹具 | 应当被判成 | 应当派给谁 |
|---|---|---|
| 故意遮挡（公式被纯黑块盖住） | `visual_layout`（遮挡／构图） | 纯取景动作 → `visual_renderer`；改布局 → `visual_planner` |
| 错误公式（公式带的值是错的） | `visual_math` | `visual_planner`（**不能只调镜头**） |
| 错误高亮（高亮画在 `trace_x` 而不是 `trace_y`） | `visual_math` | `visual_planner`，且工单要点名 `trace_y` |

每条失败都必须同时具备六件事：**step / timestamp / 具体对象 / 修复动作 / 预期画面 /
通过标准**。缺一件就抛 `ContractError` —— 含糊判词不是反馈，它只会让返修循环空转。

另外钉住两条容易"看起来正常"的：
* 没有真实字幕／文字稿就**不许开单**（`EvidenceSpan.check_against` 必须能过）；
* `verify_frames` 在 `images_required=True` 时，只吃 JSON 的假评审拿不到发言权。

全程 0 次真实模型调用、0 token：评审是受控替身，渲染是受控替身。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "src", ROOT / "tests" / "fixtures" / "frame_review"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

# ⚠️ 模块名必须唯一：`tests/fixtures/visual/builders.py` 也注册在 sys.modules 的
#    `builders` 上，裸名 `import builders` 在**整套 suite** 里会拿到 T06 那一份
#    （单跑文件时不会 —— 这正是 `pytest tests/` 才暴露出来的坑）。
import frame_review_builders as builders  # noqa: E402

from aiforstudy.blocks.figures import animation as media  # noqa: E402
from aiforstudy.blocks.visual.frame_check import (  # noqa: E402
    FrameFinding, finding_from_verdict, is_camera_only, owner_for_kind, verify_frames)
from aiforstudy.errors import CapabilityError, ContractError  # noqa: E402
from aiforstudy.providers.capabilities import Capability, Capabilities  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n"
STEP_CAPTIONS = {"s1": "先看清坐标系与曲面 u(x,y)=x^2-y^2。",
                 "s2": "固定 y：沿 x 的二阶变化 u_xx=2，截线开口向上。"}


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    """三份缺陷夹具 + 一份正确参考帧（现场生成，确定性）。"""
    root = tmp_path_factory.mktemp("frame-review-fixtures")
    manifest = builders.build(root)
    assert Path(manifest["frames"]["occlusion"]["path"]).is_file()
    return manifest


def _spec(steps=("s1", "s2")) -> dict:
    windows = {"s1": (0.0, 9.0), "s2": (10.0, 19.0)}
    return {"schema_version": "1.0", "request_id": "vis-1", "render_profile": "draft",
            "objects": [{"object_id": "u", "kind": "surface"},
                        {"object_id": "trace_y", "kind": "curve"},
                        {"object_id": "trace_x", "kind": "curve"},
                        {"object_id": "formula_u", "kind": "formula"}],
            "steps": [{"step_id": step, "caption": STEP_CAPTIONS[step],
                       "start": windows[step][0], "end": windows[step][1],
                       "actions": [{"action": "transform", "object_id": "u"}],
                       "expected_change": f"{step} 应当出现的变化"} for step in steps]}


def _result(entries, *, status="fulfilled") -> dict:
    resources = []
    for kind, name, mime in (("mp4", "video.mp4", "video/mp4"),
                             ("poster", "poster.png", "image/png"),
                             ("captions", "captions.vtt", "text/vtt; charset=utf-8"),
                             ("transcript", "transcript.txt", "text/plain; charset=utf-8")):
        resources.append({"kind": kind, "relative_path": name,
                          "sha256": "sha256:" + hashlib.sha256((kind + name).encode()).hexdigest(),
                          "bytes": 3, "mime": mime})
    for entry in entries:
        resources.append(builders.frame_resource(entry))
    return {"schema_version": "1.0", "request_id": "vis-1", "status": status,
            "resources": resources, "base_dir": str(Path(entries[0]["path"]).parent)}


def _verdict(entry, **overrides) -> dict:
    """由夹具 manifest 生成一条**可派发**的失败判词（评审替身的输出）。"""
    data = {"step_id": entry["step_id"], "status": "fail", "severity": "P1",
            "timestamp": entry["timestamp"], "frame_hash": entry["sha256"],
            "object_id": entry["object_id"], "problem": f"{entry['defect']}：画面与规格不符",
            "action": "spec.formula: 按规格修正公式的取值",
            "expected_screen": f"{entry['object_id']} 与规格一致",
            "criteria": [{"criterion_id": f"{entry['step_id']}-c1", "version": 1,
                          "required": True, "expected": "画面与规格逐项一致",
                          "method": "frame", "evidence_type": "frame"}]}
    data.update(overrides)
    return data


def _finding(entry, **overrides) -> FrameFinding:
    return finding_from_verdict(_verdict(entry, **overrides),
                                step={"step_id": entry["step_id"]},
                                frame=builders.frame_resource(entry))


# --------------------------------------------------------------------------- #
# 0. 夹具本身是真的坏的（不依赖任何评审）
# --------------------------------------------------------------------------- #
def test_defect_fixtures_are_really_broken(fixtures):
    """先证明夹具真的把缺陷画进像素，再谈"评审发现了它"。"""
    from PIL import Image

    occluded = Image.open(fixtures["frames"]["occlusion"]["path"]).convert("RGB")
    left, top, right, bottom = fixtures["formula_bbox"]
    colours = occluded.crop((left, top, right, bottom)).getcolors(maxcolors=4096)
    assert colours == [((right - left) * (bottom - top), tuple(fixtures["occlusion_rgb"]))], \
        "遮挡夹具的公式区域必须是**纯黑一整块**（而不是「看起来像」被挡住）"

    correct = Image.open(fixtures["frames"]["correct"]["path"]).convert("RGB")
    wrong = Image.open(fixtures["frames"]["wrong_formula"]["path"]).convert("RGB")
    box = tuple(fixtures["formula_bbox"])
    from PIL import ImageChops
    difference = ImageChops.difference(correct.crop(box), wrong.crop(box)).getbbox()
    assert difference is not None, "错误公式夹具必须在公式区域与正确帧有像素差异"

    highlight = Image.open(fixtures["frames"]["wrong_highlight"]["path"]).convert("RGB")
    wanted = tuple(fixtures["highlight_rgb"])
    wrong_box = tuple(fixtures["frames"]["wrong_highlight"]["bbox"])
    right_box = tuple(fixtures["frames"]["wrong_highlight"]["should_be_bbox"])
    assert any(c[1] == wanted for c in highlight.crop(wrong_box).getcolors(4096)), \
        "错误高亮夹具必须在 trace_x 上画出高亮色"
    assert not any(c[1] == wanted for c in highlight.crop(right_box).getcolors(4096)), \
        "错误高亮夹具不该在 trace_y 上画高亮（否则测的就不是「高亮错对象」）"


# --------------------------------------------------------------------------- #
# 1. 六件事齐全 + 正确 owner
# --------------------------------------------------------------------------- #
def test_occlusion_feedback_carries_all_six_fields(fixtures):
    entry = fixtures["frames"]["occlusion"]
    finding = finding_from_verdict(
        _verdict(entry, kind="visual_layout",
                 action="spec.layout: 把公式移到不被曲面遮挡的位置",
                 problem="公式带被曲面的黑色块完全盖住，学生看不到公式",
                 expected_screen="公式带完整可见，无遮挡"),
        step={"step_id": "s1"}, frame=builders.frame_resource(entry))

    payload = finding.as_dict()
    for field in ("step_id", "timestamp", "object_id", "action", "expected_screen",
                  "criteria"):
        assert payload[field] not in (None, "", [], {}), f"失败反馈缺 {field}"
    assert payload["step_id"] == "s1"
    assert payload["timestamp"] == entry["timestamp"]
    assert payload["object_id"] == "formula_u"
    assert payload["frame_hash"] == entry["sha256"]
    assert payload["owner"] == "visual_planner"
    assert payload["camera_only"] is False


def test_occlusion_fixable_by_camera_goes_to_the_renderer(fixtures):
    entry = fixtures["frames"]["occlusion"]
    finding = _finding(entry, kind="visual_layout",
                       action="camera.reframe: 把公式带移出曲面遮挡范围")
    assert finding.owner == "visual_renderer"
    assert is_camera_only(finding.action)
    assert owner_for_kind("visual_layout", finding.action) == "visual_renderer"


# --------------------------------------------------------------------------- #
# 2. 错误公式：数学错不能只调镜头
# --------------------------------------------------------------------------- #
def test_wrong_formula_with_camera_only_action_is_rejected(fixtures):
    entry = fixtures["frames"]["wrong_formula"]
    # 声明 owner=visual_planner 但动作只是镜头 => 必须被"数学错不能只调镜头"挡住
    with pytest.raises(ContractError) as excinfo:
        _finding(entry, kind="visual_math", owner="visual_planner",
                 action="camera.zoom: 拉近一点把公式看清楚",
                 problem="公式带显示 u_xx = -2，与规格的 u_xx = 2 不符")
    message = str(excinfo.value)
    assert "不能只调镜头" in message

    # 想把它塞给取景方也不行：visual_math 的 owner 只能是 visual_planner
    with pytest.raises(ContractError) as excinfo:
        _finding(entry, kind="visual_math", owner="visual_renderer",
                 action="camera.zoom: 拉近一点把公式看清楚")
    assert "visual_planner" in str(excinfo.value)


def test_wrong_formula_with_spec_action_is_dispatchable(fixtures):
    entry = fixtures["frames"]["wrong_formula"]
    finding = _finding(entry, kind="visual_math",
                       action="spec.formula: 把公式带的 u_xx 从 -2 改回 2",
                       problem=f"公式带显示 {fixtures['wrong_formula']}，"
                               f"规格要求 {fixtures['correct_formula']}",
                       expected_screen="公式带显示 u_xx = 2, u_yy = -2")
    assert finding.owner == "visual_planner"

    ticket = finding.to_repair_ticket(
        unit_id="CH04-S01", artifact_id="figures/CH04-S01", artifact_hash="sha256:" + "b" * 64,
        rule="frame-visual-math", transcript=STEP_CAPTIONS["s2"])
    assert ticket.owner == "visual_planner"
    assert ticket.is_dispatchable, ticket.missing_answers
    assert ticket.target["step_id"] == "s2"
    assert ticket.target["frame_hash"] == entry["sha256"]
    assert "formula_u" in ticket.target["json_pointer"]
    assert ticket.criteria and ticket.criteria[0].version == 1
    assert ticket.source_evidence and ticket.source_evidence[0].step_id == "s2"
    assert ticket.source_evidence[0].frame_hash == entry["sha256"]
    assert "不能只调镜头" not in json.dumps(ticket.as_dict(), ensure_ascii=False)


def test_repair_ticket_refuses_to_open_without_real_evidence(fixtures):
    entry = fixtures["frames"]["wrong_formula"]
    finding = _finding(entry, kind="visual_math")
    with pytest.raises(ContractError) as excinfo:
        finding.to_repair_ticket(unit_id="CH04-S01", artifact_id="a", artifact_hash="h",
                                 rule="r", transcript="")
    assert "证据" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 3. 错误高亮：要点名**正确**的对象
# --------------------------------------------------------------------------- #
def test_wrong_highlight_points_at_the_object_that_should_be_highlighted(fixtures):
    entry = fixtures["frames"]["wrong_highlight"]
    finding = _finding(
        entry, kind="visual_math", object_id=fixtures["highlight_target"],
        action="spec.highlight: 把高亮从 trace_x 换到 trace_y",
        problem="这一步要讲固定 y 的截线，高亮却画在 trace_x 上",
        expected_screen="trace_y 高亮，trace_x 不高亮")
    assert finding.object_id == "trace_y"
    ticket = finding.to_repair_ticket(
        unit_id="CH04-S01", artifact_id="figures/CH04-S01", artifact_hash="sha256:" + "c" * 64,
        rule="frame-visual-math", transcript=STEP_CAPTIONS["s2"])
    assert "trace_y" in ticket.target["json_pointer"]
    assert "trace_y" in ticket.repair_instructions["locate"]
    assert "trace_x" in ticket.repair_instructions["action"]


# --------------------------------------------------------------------------- #
# 4. 含糊判词一律拒绝
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("missing", ["object_id", "action", "expected_screen", "criteria",
                                     "timestamp", "frame_hash"])
def test_vague_failure_feedback_is_rejected(fixtures, missing):
    entry = fixtures["frames"]["occlusion"]
    verdict = _verdict(entry)
    frame = builders.frame_resource(entry)
    verdict.pop(missing)
    # timestamp / frame_hash 允许由"这一帧的上下文"补上；要测"缺了就必须报错"，
    # 就得连上下文里也拿掉（否则测的其实是"回退到上下文"这条正确的行为）
    frame.pop({"timestamp": "timestamp", "frame_hash": "sha256"}.get(missing, missing), None)
    with pytest.raises(ContractError) as excinfo:
        finding_from_verdict(verdict, step={"step_id": "s1"}, frame=frame)
    assert missing in str(excinfo.value) or "必填" in str(excinfo.value)


def test_finding_rejects_nonfinite_timestamp_and_unknown_owner(fixtures):
    entry = fixtures["frames"]["occlusion"]
    with pytest.raises(ContractError):
        _finding(entry, timestamp=float("nan"))
    with pytest.raises(ContractError):
        _finding(entry, timestamp=-1.0)
    with pytest.raises(ContractError):
        _finding(entry, owner="whoever")


def test_finding_requires_unique_usable_criteria(fixtures):
    entry = fixtures["frames"]["occlusion"]
    with pytest.raises(ContractError):
        _finding(entry, criteria=[{"criterion_id": "c1", "version": 1, "required": True,
                                   "expected": "", "method": "frame"}])
    with pytest.raises(ContractError):
        _finding(entry, criteria=[{"criterion_id": "c1", "version": 1, "required": True,
                                   "expected": "x", "method": "vibes"}])
    with pytest.raises(ContractError):
        _finding(entry, criteria=[{"criterion_id": "c1", "version": 1, "required": True,
                                   "expected": "x", "method": "frame"},
                                  {"criterion_id": "c1", "version": 1, "required": True,
                                   "expected": "y", "method": "frame"}])


# --------------------------------------------------------------------------- #
# 5. 与 verify_frames / 媒体闭环的接线
# --------------------------------------------------------------------------- #
def test_verify_frames_refuses_vague_failure_verdict(fixtures):
    entry = fixtures["frames"]["occlusion"]

    def vague(*, frames, steps, spec):                      # noqa: ANN001
        return [{"step_id": "s1", "status": "fail", "reason": "画面不对"}]

    with pytest.raises(ContractError):
        verify_frames(_spec(), _result([entry, fixtures["frames"]["correct"]]),
                      reviewer=vague, profile="draft")


def test_verify_frames_turns_actionable_failures_into_findings(fixtures):
    entry = fixtures["frames"]["occlusion"]

    def reviewer(*, frames, steps, spec, images):           # noqa: ANN001
        return [_verdict(entry, kind="visual_layout",
                         action="camera.reframe: 把公式带移出遮挡范围")]

    report = verify_frames(_spec(), _result([entry, fixtures["frames"]["correct"]]),
                           reviewer=reviewer, profile="draft", images_required=True)
    assert report.status == "fail"
    assert report.findings and report.findings[0].step_id == "s1"
    assert report.findings[0].owner == "visual_renderer"
    assert report.images, "报告里必须留下送审图片的真实 bytes/hash"


def test_verify_frames_rejects_a_json_only_reviewer_when_images_are_required(fixtures):
    entry = fixtures["frames"]["occlusion"]

    def json_only(*, frames, steps, spec):                  # noqa: ANN001
        return [{"step_id": "s1", "status": "pass", "reason": "规格看着没问题"}]

    with pytest.raises(CapabilityError):
        verify_frames(_spec(), _result([entry, fixtures["frames"]["correct"]]),
                      reviewer=json_only, profile="draft", images_required=True)


def test_media_loop_propagates_a_math_error_that_tries_to_fix_by_camera(fixtures, tmp_path):
    """数学错只调镜头 => 判词本身不合法，不能进返修循环，更不许开单。"""
    entry = fixtures["frames"]["wrong_formula"]

    def renderer(profile, spec):                            # noqa: ANN001
        from aiforstudy.core.digest import digest_of
        result = _result([entry, fixtures["frames"]["correct"]])
        result.update({"schema_version": "1.0", "spec_hash": digest_of(spec),
                       "compiler_hash": digest_of({"compiler": "test"}),
                       "environment_hash": digest_of({"environment": "test-draft"}),
                       "source_binding": {"unit_id": "CH04-S01"},
                       "math_report": {"status": "pass", "checks": []},
                       "frame_report": {"status": "pass", "checks": []},
                       "technical_report": {"ffmpeg_decode_ok": True}})
        return result, result["base_dir"]

    def reviewer(*, frames, steps, spec, images):           # noqa: ANN001
        return [_verdict(entry, kind="visual_math",
                         action="camera.zoom: 拉近把公式看清楚")]

    caps = Capabilities(items=(Capability(name="render:manim", kind="render_backend",
                                          available=True, detail="依赖齐备",
                                          required_for=("render",)),))
    with pytest.raises(ContractError) as excinfo:
        media.run_media_pipeline(spec=_spec(), render=renderer, reviewer=reviewer,
                                 capabilities=caps, request_id="vis-1")
    assert "不能只调镜头" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 6. 序列化
# --------------------------------------------------------------------------- #
def test_findings_are_json_serializable(fixtures):
    finding = _finding(fixtures["frames"]["wrong_highlight"], kind="visual_math",
                       object_id=fixtures["highlight_target"],
                       action="spec.highlight: 换到 trace_y")
    payload = json.loads(json.dumps(finding.as_dict(), ensure_ascii=False))
    assert payload["owner"] == "visual_planner"
    assert payload["criteria"][0]["method"] == "frame"
    assert payload["frame_hash"].startswith("sha256:")
    assert payload["blocking"] is True
    assert isinstance(payload["timestamp"], float)
