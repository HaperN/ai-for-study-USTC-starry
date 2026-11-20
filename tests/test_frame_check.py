"""T08：**实际帧**检查的行为测试（§5.3、§5.4/V02）。

改造前的"画面审核"输入是**规格 JSON**，没人看过渲染出来的图。这一组测试要钉住的正是
那条界线：

* 本地没有视觉评审时，整体状态**只能**是 `needs_review`，**绝不能**是 `pass`；
* 空报告不能当通过；
* 动作段的关键帧必须真的不同，而**纯 `pause` 允许静止**（这是唯一允许帧相同的情形）；
* 抽帧要有上限、要按固定步长，但**每个步骤至少留一帧**，且丢弃逐条记录 ——
  某一步的错误画面不能因为抽帧而根本没人看到；
* 像素层面能发现"整帧空白"与"尺寸不对"，并如实说明**字形／遮挡／裁切的语义判断
  需要视觉评审**，不许拿像素检查冒充"看过图"。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from aiforstudy.blocks.visual.frame_check import (  # noqa: E402
    FrameCheck,
    FrameReport,
    select_frames,
    verify_frames,
)
from aiforstudy.errors import ContractError  # noqa: E402

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _png(path: Path, *, size=(640, 360), mode="pattern", colour=(10, 20, 30),
         variant: int = 0) -> str:
    """造一张最小的真 PNG，返回它的 sha256。

    `pattern` 给多色（不空白），`flat` 给单色（整帧空白），`variant` 改变图案 ——
    **同一 variant 的两次调用会产生完全相同的字节**（同一个 hash），这正是
    "动作段没有变化"与"pause 静止"两种情况各自要用的素材。
    """
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, colour)
    if mode == "pattern":
        pixels = image.load()
        for x in range(0, size[0], 8):
            for y in range(0, size[1], 8):
                pixels[x, y] = ((x * 7 + variant * 31) % 256, (y * 5 + variant * 17) % 256,
                                ((x + y) * 3 + variant * 11) % 256)
    image.save(path, format="PNG")
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _spec(*, steps=None, profile="draft") -> dict:
    steps = steps if steps is not None else [
        {"step_id": "s1", "caption": "x、y 是两个平面方向，u 是高度。", "start": 0, "end": 4,
         "actions": [{"kind": "reveal", "target": "axes2d"}],
         "expected_change": "出现坐标系"},
        {"step_id": "s2", "caption": "固定 y：u_xx=2，截线开口向上。", "start": 4.5, "end": 9,
         "actions": [{"kind": "transform", "target": "trace_y"}],
         "expected_change": "出现开口向上的截线"},
        {"step_id": "s3", "caption": "两方向二阶变化相加为零。", "start": 9.5, "end": 14,
         "actions": [{"kind": "pause"}],
         "expected_change": "停住让读者看清"},
    ]
    return {"schema_version": "1.0", "request_id": "req-1", "unit_id": "CH04-S01",
            "render_profile": profile, "steps": steps, "objects": [], "math_assertions": []}


def _result(tmp_path: Path, frames=None, *, size=(640, 360), kinds=("mp4", "poster",
                                                                   "captions", "transcript"),
            captions=("x、y 是两个平面方向，u 是高度。", "固定 y：u_xx=2，截线开口向上。",
                      "两方向二阶变化相加为零。")) -> dict:
    resources = []
    for index, kind in enumerate(kinds):
        rel = f"assets/{kind}-{index}.bin"
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PNG_MAGIC + kind.encode())
        resources.append({"kind": kind, "relative_path": rel,
                          "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                          "bytes": path.stat().st_size,
                          "mime": {"mp4": "video/mp4", "captions": "text/vtt; charset=utf-8",
                                   "transcript": "text/plain; charset=utf-8"}.get(
                                       kind, "image/png")})
    for timestamp, variant in (frames or [(0.5, 0), (5.0, 1), (10.0, 2)]):
        rel = f"assets/frame-{timestamp}.png"
        if variant == "flat":
            digest = _png(tmp_path / rel, size=size, mode="flat")
        else:
            digest = _png(tmp_path / rel, size=size, variant=int(variant))
        resources.append({"kind": "keyframe", "relative_path": rel, "sha256": digest,
                          "bytes": (tmp_path / rel).stat().st_size, "mime": "image/png",
                          "timestamp": timestamp})
    return {"schema_version": "1.0", "request_id": "req-1", "status": "fulfilled",
            "resources": resources, "steps": [{"step_id": "s1"}, {"step_id": "s2"},
                                              {"step_id": "s3"}],
            "captions": list(captions), "base_dir": str(tmp_path)}


# --------------------------------------------------------------------------- #
# 空报告 / 状态语义
# --------------------------------------------------------------------------- #
def test_empty_report_is_not_a_pass():
    with pytest.raises(ContractError):
        FrameReport(status="pass", checks=())


def test_pass_requires_a_real_visual_check():
    """没有一条通过的**视觉**检查，就不许写 pass —— 像素检查不等于看过画面。"""
    with pytest.raises(ContractError) as excinfo:
        FrameReport(status="pass", checks=(
            FrameCheck(check_id="c", kind="pixels", status="pass", reason="尺寸对"),))
    assert "视觉" in str(excinfo.value)


def test_check_requires_a_reason_and_valid_kind():
    with pytest.raises(ContractError):
        FrameCheck(check_id="c", kind="pixels", status="pass", reason="  ")
    with pytest.raises(ContractError):
        FrameCheck(check_id="c", kind="vibes", status="pass", reason="r")
    with pytest.raises(ContractError):
        FrameCheck(check_id="c", kind="pixels", status="maybe", reason="r")


# --------------------------------------------------------------------------- #
# 没有视觉评审 → 只能 needs_review
# --------------------------------------------------------------------------- #
def test_without_a_reviewer_the_status_can_never_be_pass(tmp_path):
    """这是本模块最重要的一条：**本地检查不能冒充"看过画面"**。"""
    report = verify_frames(_spec(), _result(tmp_path))
    assert report.status == "needs_review", report.as_dict()["counts"]
    visual = [c for c in report.checks if c.kind == "visual"]
    assert visual and all(c.status == "needs_review" for c in visual)
    assert "不能" in visual[0].reason and "看过画面" in visual[0].reason
    # 结构、像素、字幕这些本地能判的应当是通过的：不能"全都 needs_review"糊过去
    assert any(c.kind == "structure" and c.status == "pass" for c in report.checks)
    assert any(c.kind == "pixels" and c.status == "pass" for c in report.checks)


def test_reviewer_receives_real_image_paths_and_can_pass(tmp_path):
    seen: dict = {}

    def reviewer(*, frames, steps, spec):
        seen["frames"] = list(frames)
        seen["steps"] = [s["step_id"] for s in steps]
        return [{"step_id": step["step_id"], "status": "pass",
                 "reason": "画面与字幕一致，无遮挡"} for step in steps]

    # 动作步给两张**不同**的帧、pause 步给两张**相同**的帧：
    # 这样本地能判的 motion 检查全通过，总状态才可能落到 pass
    frames = [(0.5, 0), (3.5, 1), (5.0, 2), (8.0, 3), (10.0, 4), (13.0, 4)]
    report = verify_frames(_spec(), _result(tmp_path, frames=frames), reviewer=reviewer)
    assert seen["steps"] == ["s1", "s2", "s3"]
    assert seen["frames"] and all(str(f).endswith(".png") for f in seen["frames"])
    assert all(Path(f).is_file() for f in seen["frames"]), (
        "视觉评审必须收到**真实存在的图片路径**；收到路径但文件不存在等于没看图")
    assert report.status == "pass", report.as_dict()["counts"]
    assert report.reviewer_inputs


def test_blank_reviewer_verdicts_cannot_pass(tmp_path):
    report = verify_frames(_spec(), _result(tmp_path), reviewer=lambda **_kw: [])
    assert report.status == "needs_review"
    assert any("空白判词" in c.reason for c in report.checks)


# --------------------------------------------------------------------------- #
# 动作段必须变化；pause 允许静止
# --------------------------------------------------------------------------- #
def test_moving_step_with_identical_frames_fails(tmp_path):
    """有动作但关键帧完全相同 = 画面上什么都没发生。"""
    result = _result(tmp_path, frames=[(0.5, 0), (0.5, 0), (5.0, 1), (10.0, 2)])
    # 让 s1 的两帧内容完全相同（同一份字节 → 同一个 hash）
    first = [r for r in result["resources"]
             if r["kind"] == "keyframe" and r["timestamp"] == 0.5]
    assert len(first) == 2
    first[1]["sha256"] = first[0]["sha256"]
    report = verify_frames(_spec(), result)
    motion = [c for c in report.checks if c.check_id == "step.s1.motion"]
    assert motion and motion[0].status == "fail", motion
    assert report.status == "fail"


def test_pause_only_step_may_stand_still(tmp_path):
    """`pause` 是**唯一**允许帧相同的情形（冻结画面的教学停顿）。"""
    result = _result(tmp_path, frames=[(0.5, 0), (5.0, 1),
                                       (10.0, 2), (10.4, 2)])
    same = [r for r in result["resources"]
            if r["kind"] == "keyframe" and r["timestamp"] >= 10.0]
    assert len(same) == 2
    same[1]["sha256"] = same[0]["sha256"]
    report = verify_frames(_spec(), result)
    motion = [c for c in report.checks if c.check_id == "step.s3.motion"]
    assert motion and motion[0].status == "pass", motion
    assert "pause" in motion[0].reason


def test_step_without_any_keyframe_fails(tmp_path):
    """某一步时段内没有关键帧 = 这一步的真实画面没人看到。"""
    result = _result(tmp_path, frames=[(0.5, 0)])               # 只有 s1 有帧
    report = verify_frames(_spec(), result)
    missing = [c for c in report.checks if c.check_id == "step.s2.frame_in_window"]
    assert missing and missing[0].status == "fail"
    assert "没人看到" in missing[0].reason
    assert report.status == "fail"


# --------------------------------------------------------------------------- #
# 像素层：整帧空白 / 尺寸不符
# --------------------------------------------------------------------------- #
def test_flat_frame_is_reported_as_blank(tmp_path):
    result = _result(tmp_path, frames=[(0.5, "flat"), (5.0, 1), (10.0, 2)])
    report = verify_frames(_spec(), result)
    blank = [c for c in report.checks if c.kind == "pixels" and c.status == "fail"]
    assert blank, report.as_dict()["counts"]
    assert any("空白" in c.reason for c in blank)
    assert report.status == "fail"


def test_wrong_frame_size_is_reported_as_cropping(tmp_path):
    result = _result(tmp_path, frames=[(0.5, 0), (5.0, 1), (10.0, 2)], size=(320, 180))
    report = verify_frames(_spec(), result)
    bad = [c for c in report.checks if c.kind == "pixels" and c.status == "fail"]
    assert bad and any("裁切" in c.reason or "尺寸" in c.reason for c in bad)


def test_missing_frame_file_is_a_failure_not_a_silent_skip(tmp_path):
    result = _result(tmp_path)
    for resource in result["resources"]:
        if resource["kind"] == "keyframe":
            (tmp_path / resource["relative_path"]).unlink()
    report = verify_frames(_spec(), result)
    assert report.status == "fail"
    assert any("不存在" in c.reason for c in report.checks)


def test_richtly_coloured_frame_is_not_mistaken_for_blank(tmp_path):
    """`getcolors(maxcolors=N)` 颜色超限时返回 None —— 不能当成"0 种颜色，空白"。"""
    from PIL import Image
    path = tmp_path / "assets/rich.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (640, 360))              # 尺寸必须与 draft 档一致，
    pixels = image.load()                            # 否则测到的是尺寸而不是颜色
    for x in range(640):
        for y in range(360):
            pixels[x, y] = (x % 256, y % 256, (x * y) % 256)
    image.save(path)
    result = _result(tmp_path, frames=[(0.5, 0)])
    result["resources"].append({"kind": "keyframe", "relative_path": "assets/rich.png",
                                "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                                "bytes": path.stat().st_size, "mime": "image/png",
                                "timestamp": 0.6})
    report = verify_frames(_spec(), result)
    rich = [c for c in report.checks if c.check_id == "pixels.rich"]
    assert rich and rich[0].status == "pass", rich


# --------------------------------------------------------------------------- #
# 抽帧：上限、固定步长、每步至少一帧、丢弃逐条记录
# --------------------------------------------------------------------------- #
def test_frame_selection_keeps_one_frame_per_step_and_records_drops(tmp_path):
    frames = []
    for index, step_start in enumerate((0.0, 4.5, 9.5)):
        for offset in (0.1, 0.5, 1.0, 2.0, 3.0):
            frames.append((step_start + offset, index * 10 + int(offset * 10)))
    result = _result(tmp_path, frames=frames)
    selected, dropped = select_frames(_spec(), result, max_images=3)
    assert len(selected) == 3
    assert {round(f["timestamp"]) for f in selected} == {0, 5, 10}, (
        "每步至少留一帧，且取该步最早的一张（确定性）")
    assert dropped, "被丢弃的帧必须逐条记录，不许静默截断"
    assert all("reason" in d for d in dropped)
    # 每步都还在
    report = verify_frames(_spec(), result)
    for step_id in ("s1", "s2", "s3"):
        check = [c for c in report.checks if c.check_id == f"step.{step_id}.frame_in_window"][0]
        assert check.status == "pass"


def test_dropped_frames_are_reported_in_the_report(tmp_path):
    frames = [(0.1, 0), (0.5, 1), (5.0, 2), (5.4, 3), (10.0, 4), (10.4, 4)]
    report = verify_frames(_spec(), _result(tmp_path, frames=frames), max_images=3)
    assert report.dropped_frames, "抽帧丢弃必须出现在报告里"
    assert any("固定步长" in note or "抽帧" in note for note in report.notes)


# --------------------------------------------------------------------------- #
# 结构：缺字幕 / 超长字幕 / 缺预期变化
# --------------------------------------------------------------------------- #
def test_missing_caption_fails(tmp_path):
    spec = _spec()
    spec["steps"][0]["caption"] = ""
    report = verify_frames(spec, _result(tmp_path))
    check = [c for c in report.checks if c.check_id == "step.s1.caption"][0]
    assert check.status == "fail" and "缺字幕" in check.reason


def test_overlong_caption_fails(tmp_path):
    spec = _spec()
    spec["steps"][0]["caption"] = "很长的字幕" * 40
    report = verify_frames(spec, _result(tmp_path))
    check = [c for c in report.checks if c.check_id == "step.s1.caption"][0]
    assert check.status == "fail" and "120" in check.reason


def test_caption_missing_from_captions_resource_is_needs_review(tmp_path):
    result = _result(tmp_path, captions=("完全不相干的一段字幕",))
    report = verify_frames(_spec(), result)
    check = [c for c in report.checks if c.check_id == "step.s1.caption_in_resource"][0]
    assert check.status == "needs_review"
    assert "播放器读不到" in check.reason


def test_missing_expected_change_fails(tmp_path):
    spec = _spec()
    spec["steps"][1]["expected_change"] = ""
    report = verify_frames(spec, _result(tmp_path))
    check = [c for c in report.checks if c.check_id == "step.s2.expected_change"][0]
    assert check.status == "fail"


def test_missing_required_resource_fails(tmp_path):
    report = verify_frames(_spec(), _result(tmp_path, kinds=("mp4",)))
    structural = [c for c in report.checks if c.kind == "structure"]
    missing = [c.check_id for c in structural if c.status == "fail"]
    assert "structure.poster" in missing and "structure.captions" in missing
    assert "structure.transcript" in missing
    assert report.status == "fail"


def test_report_is_json_serializable_with_counts(tmp_path):
    import json
    report = verify_frames(_spec(), _result(tmp_path))
    payload = json.loads(json.dumps(report.as_dict(), ensure_ascii=False))
    assert set(payload["counts"]) == {"pass", "fail", "needs_review"}
    assert payload["evidence_hashes"]
    assert all("reason" in c for c in payload["checks"])

@pytest.mark.parametrize('verdicts', [
    [{'step_id': 'not-a-real-step', 'status': 'pass', 'reason': 'checked'}],
    [{'step_id': 's1', 'status': 'pass', 'reason': 'checked'}],
    [{'step_id': 's1', 'status': 'pass', 'reason': 'checked'}] * 3,
])
def test_visual_pass_must_cover_each_actual_step_once(tmp_path, verdicts):
    report = verify_frames(_spec(), _result(tmp_path, frames=[(0.5, 0), (3.5, 1), (5.0, 2), (8.0, 3), (10.0, 4), (13.0, 4)]), reviewer=lambda **kwargs: verdicts)
    assert report.status != 'pass'
