"""T08：显式动画请求 → `AnimationResult` 分流 + 画面评审接线的行为测试。

计划 §T08 的每一条判据都在这里钉住：

* **显式 animation 请求恰好一个结果**；身份重复直接拒绝（不许同一条请求渲两次）；
* **缺环境不能记 `not_applicable`** —— 那是"按政策不需要做"的意思，缺能力是 `blocked`；
* **没有动画请求不强制所有章节创建视频**（`plan_media` 只认显式声明）；
* **实际图片 bytes/hash 进入 reviewer transport**：评审收到的是真实 PNG 的
  `(sha256, bytes)`，与盘上的关键帧逐条对得上；`RealTransport` 的请求体里能
  解出同样的字节（0 次调用、0 token）；
* **仅 JSON 的假视觉评审不得通过 capability 检查**（`CapabilityError`），
  而且要在**任何渲染之前**挡住；
* **超图像数量时按固定步骤抽帧**，装不下就明确报错，**不许悄悄丢掉整步**；
* **draft → 关键帧审核 → 局部 patch → final**：同规格的 final 是编码档位变化，
  **不另消耗内容返修批次**；final **仍检查自己那批关键帧**，draft 合格不能替它签字。

全程 0 次真实模型调用、0 token、0 次真实渲染：渲染器与评审都是受控替身，
并且本测试还会把 `RealTransport` 换成一个"一被构造就炸"的类来证明没有隐藏调用。
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "src", ROOT / "tests" / "fixtures" / "frame_review"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from aiforstudy.blocks.figures import animation as media  # noqa: E402
from aiforstudy.blocks.visual.frame_check import select_frames  # noqa: E402
from aiforstudy.blocks.visual.result import parse_animation_result  # noqa: E402
from aiforstudy.core.digest import digest_of  # noqa: E402
from aiforstudy.errors import CapabilityError, ContractError  # noqa: E402
from aiforstudy.providers.capabilities import (Capability, Capabilities,  # noqa: E402
                                               require_image_input, with_image_input)
from aiforstudy.providers.transport import RealTransport  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n"


# --------------------------------------------------------------------------- #
# 替身与夹具
# --------------------------------------------------------------------------- #
def _caps(*, available: bool = True) -> Capabilities:
    return Capabilities(items=(Capability(
        name="render:manim", kind="render_backend", available=available,
        detail="依赖齐备" if available else "缺 env:manim；sandbox:worker",
        probe=("env:manim",), required_for=("render",)),))


def _png(path: Path, *, size, variant: int) -> dict:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, (11, 13, 17))
    pixels = image.load()
    for x in range(0, size[0], 8):
        for y in range(0, size[1], 8):
            pixels[x, y] = ((x * 7 + variant * 31) % 256, (y * 5 + variant * 17) % 256,
                            ((x + y) * 3 + variant * 11) % 256)
    image.save(path, format="PNG")
    data = path.read_bytes()
    return {"kind": "keyframe", "relative_path": str(path.name),
            "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "mime": "image/png"}


def _spec(*, steps=2, request_id="vis-1", unit_id="CH04-S01") -> dict:
    return {"schema_version": "1.0", "request_id": request_id, "unit_id": unit_id,
            "render_profile": "draft",
            "objects": [{"object_id": "u", "kind": "surface"}] if steps else [],
            "steps": [{"step_id": f"s{i}", "caption": f"第 {i} 步的讲解字幕。",
                       "start": i * 10.0, "end": i * 10.0 + 9.0,
                       "actions": [{"action": "transform", "object_id": "u"}],
                       "expected_change": f"第 {i} 步应当出现的变化"}
                      for i in range(steps)]}


def _animation_dict(tmp: Path, *, request_id: str, spec_hash: str, profile: str,
                    steps: int, variant: int, status: str = "fulfilled") -> dict:
    """一份**满足冻结契约**的 `AnimationResult` dict（含每步 2 张真 PNG 关键帧）。"""
    size = (640, 360) if profile == "draft" else (1280, 720)
    root = tmp / f"{profile}-{variant}"
    root.mkdir(parents=True, exist_ok=True)
    resources: list[dict] = []
    for kind, name, mime in (("mp4", "video.mp4", "video/mp4"),
                             ("poster", "poster.png", "image/png"),
                             ("captions", "captions.vtt", "text/vtt; charset=utf-8"),
                             ("transcript", "transcript.txt", "text/plain; charset=utf-8")):
        path = root / name
        path.write_bytes(PNG + kind.encode())
        resources.append({"kind": kind, "relative_path": name,
                          "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                          "bytes": path.stat().st_size, "mime": mime})
    step_rows = []
    for index in range(steps):
        step_id = f"s{index}"
        keyframes = []
        for frame_index in range(2):
            frame = _png(root / f"keyframes/{step_id}-{frame_index}.png", size=size,
                         variant=variant * 10 + index * 2 + frame_index)
            frame["timestamp"] = index * 10.0 + 1.0 + frame_index * 2.0
            frame["relative_path"] = f"keyframes/{step_id}-{frame_index}.png"
            resources.append(frame)
            keyframes.append({"relative_path": frame["relative_path"],
                              "sha256": frame["sha256"]})
        step_rows.append({"step_id": step_id, "start": index * 10.0,
                          "end": index * 10.0 + 9.0, "caption": f"第 {index} 步的讲解字幕。",
                          "expected_change": f"第 {index} 步应当出现的变化",
                          "keyframes": keyframes})
    return {
        "schema_version": "1.0", "request_id": request_id, "status": status,
        "spec_hash": spec_hash, "compiler_hash": digest_of({"compiler": "test"}),
        "environment_hash": digest_of({"environment": "test", "profile": profile}),
        "source_binding": {"unit_id": "CH04-S01", "request_id": request_id},
        "resources": resources, "steps": step_rows,
        "math_report": {"status": "pass", "checks": []},
        "frame_report": {"status": "pass", "checks": [{"check_id": "technical"}]},
        "technical_report": {"ffmpeg_decode_ok": True},
        "base_dir": str(root)}


def _pass_all(images, steps, spec):                       # noqa: ANN001
    return [{"step_id": step["step_id"], "status": "pass",
             "reason": f"看过 {len(images)} 张真实帧，画面与字幕一致"}
            for step in steps]


def _fail_step(step_id: str, *, object_id: str = "u", kind: str = "visual_math",
               action: str = "spec.formula: 把公式里的 -2 改回 2") -> dict:
    return {"step_id": step_id, "status": "fail", "kind": kind, "severity": "P1",
            "object_id": object_id, "timestamp": 1.0,
            "problem": f"步骤 {step_id} 的画面与规格不符",
            "action": action, "expected_screen": "公式带显示 u_xx = 2",
            "criteria": [{"criterion_id": f"{step_id}-formula", "version": 1,
                          "required": True, "expected": "u_xx = 2",
                          "method": "frame", "evidence_type": "frame"}]}


class ScriptedReviewer:
    """受控替身：显式接收 `images`，记录**实际收到的真实图片 bytes/hash**。"""

    def __init__(self, script) -> None:                    # noqa: ANN001
        self.script = list(script)
        self.calls: list[dict] = []

    def __call__(self, *, frames, steps, spec, images):     # noqa: ANN001
        record = {"frames": list(frames),
                  "images": [dict(item) for item in images],
                  "steps": [step["step_id"] for step in steps],
                  "frame_hashes": sorted({item["sha256"] for item in images})}
        self.calls.append(record)
        entry = self.script[len(self.calls) - 1] if len(self.calls) <= len(self.script) else []
        verdicts = entry(images, steps, spec) if callable(entry) else entry
        return [dict(v) for v in verdicts]

    @property
    def received_hashes(self) -> set[str]:
        return {digest for call in self.calls for digest in call["frame_hashes"]}


class ScriptedRenderer:
    """受控替身：按 (profile, spec) 产出满足契约的 `AnimationResult` dict + base_dir。"""

    def __init__(self, *, steps: int = 2, spec_request="vis-1", variants=None) -> None:
        self.steps = steps
        self.spec_request = spec_request
        self.variants = variants or {"draft": 1, "final": 2}
        self.calls: list[dict] = []

    def __call__(self, profile: str, spec) -> tuple[dict, str]:   # noqa: ANN001
        spec_hash = digest_of(spec)
        variant = self.variants.get(profile, 1) + int(spec_hash[7:9], 16) % 97
        self.calls.append({"profile": profile, "spec_hash": spec_hash, "variant": variant})
        payload = _animation_dict(Path(self.tmp_root), request_id=self.spec_request,
                                  spec_hash=spec_hash, profile=profile,
                                  steps=self.steps, variant=variant)
        return payload, payload["base_dir"]

    def with_root(self, root: Path) -> "ScriptedRenderer":
        self.tmp_root = root
        return self


class ExplodingTransport:
    """任何"偷偷发真请求"的尝试都会在这里炸。"""

    def __init__(self, *args, **kwargs) -> None:                 # noqa: ANN002, ANN003
        raise AssertionError("测试里构造了真实传输 —— 付费边界必须走受控替身")


def _install_exploding_transport(monkeypatch) -> None:           # noqa: ANN001
    from aiforstudy.providers import transport as transport_module
    monkeypatch.setattr(transport_module, "RealTransport", ExplodingTransport)


# --------------------------------------------------------------------------- #
# 1. 分流：显式请求恰好一个结果
# --------------------------------------------------------------------------- #
def test_explicit_animation_request_yields_exactly_one_result(tmp_path, monkeypatch):
    _install_exploding_transport(monkeypatch)
    request = {"request_id": "anim-1", "kind": "animation", "where": "p1",
               "quote": "x、y 是两个平面方向"}
    routes = media.route_requests([request])
    assert [route.destination for route in routes] == [media.ROUTE_ANIMATION]

    result = media.fulfill_animation_request(
        request, capabilities=_caps(), unit_id="CH04-S01",
        renderer=lambda profile, spec: _animation_dict(
            tmp_path, request_id="anim-1", spec_hash=digest_of({"s": 1}),
            profile=profile, steps=1, variant=3))
    assert isinstance(result.request_id, str) and result.request_id == "anim-1"
    assert result.status == "fulfilled"
    assert result.is_fulfilled and result.resources
    assert {r.kind for r in result.resources} >= {"mp4", "poster", "keyframe", "captions",
                                                  "transcript"}


def test_duplicate_animation_request_identity_is_rejected():
    """同一条请求渲两次既花钱又会让播放器拿到两份媒体 —— 身份重复直接拒绝。"""
    requests = [{"request_id": "same", "kind": "animation"},
                {"request_id": "same", "kind": "animation"}]
    with pytest.raises(ContractError) as excinfo:
        media.route_requests(requests)
    assert "身份重复" in str(excinfo.value) and "same" in str(excinfo.value)


def test_animation_request_without_id_falls_back_to_unit_scope_index():
    routes = media.route_requests([{"kind": "animation"}, {"kind": "animation"}],
                                 unit_id="CH04-S01", scope="lesson")
    assert len({r.request_id for r in routes}) == 2
    assert all(r.request_id.startswith("vis-") and ":" not in r.request_id for r in routes)


# --------------------------------------------------------------------------- #
# 2. 缺环境 → blocked，绝不 not_applicable
# --------------------------------------------------------------------------- #
def test_missing_render_environment_is_blocked_not_not_applicable():
    result = media.fulfill_animation_request(
        {"request_id": "anim-2", "kind": "animation"}, capabilities=_caps(available=False),
        unit_id="CH04-S01", renderer=lambda *a, **k: pytest.fail("缺环境时不许渲染"))
    assert result.status == "blocked", result.as_dict()
    assert result.status != "not_applicable"
    assert result.technical_report["kind"] == "capability"
    assert "env:manim" in result.technical_report["reason"]
    assert not result.resources
    # 顺带钉住"not_applicable 不能伪造"：它必须带 policy + evidence（空的会被契约拒绝）
    with pytest.raises(ContractError):
        parse_animation_result({**result.as_dict(), "status": "not_applicable"})


def test_no_capabilities_at_all_is_also_blocked():
    """"没有预检"不等于"环境齐备" —— 把没检查当可用正是要治的假绿。"""
    result = media.fulfill_animation_request(
        {"request_id": "anim-3", "kind": "animation"}, capabilities=None,
        renderer=lambda *a, **k: pytest.fail("没有预检就不许渲染"))
    assert result.status == "blocked"
    assert "预检" in result.technical_report["reason"]


# --------------------------------------------------------------------------- #
# 3. 没有动画请求就不建视频；SVG 路返回含义不变
# --------------------------------------------------------------------------- #
def test_no_animation_request_means_no_video_is_created(monkeypatch):
    _install_exploding_transport(monkeypatch)
    svg_request = {"request_id": "fig-1", "concept_id": "C-1", "where": "p1",
                   "quote": "相等向量"}
    lesson_requests = [svg_request, {"request_id": "fig-2", "where": "p2"}]

    assert media.animation_requests(lesson_requests) == ()
    assert media.plan_media({"CH01-S01": lesson_requests,
                             "CH02-S01": []}) == {"CH01-S01": (), "CH02-S01": ()}
    assert media.units_needing_animation({"CH01-S01": lesson_requests}) == ()

    def boom(**_kwargs):
        raise AssertionError("没有显式动画请求时不许渲染任何视频")

    assert media.fulfill_animation_requests(lesson_requests, capabilities=_caps(),
                                            renderer=boom) == ()


def test_svg_requests_keep_the_old_return_meaning():
    """绘图请求的路由仍指向 SVG 出口；显式动画分流不碰它。

    注：旧渲染器 `declarative.py` 已删除（D10），这里只保留**路由契约**的断言；
    渲染本身现在统一走"模型写代码"（pipeline/media_stages.py:804）。
    """
    route = media.route_request({"request_id": "fig-1", "where": "p1"})
    assert route.destination == media.ROUTE_SVG
    assert "返回含义不变" in route.reason


# --------------------------------------------------------------------------- #
# 4. 实际图片 bytes/hash 进入评审与传输
# --------------------------------------------------------------------------- #
def test_reviewer_receives_real_image_bytes_and_hashes(tmp_path, monkeypatch):
    _install_exploding_transport(monkeypatch)
    spec = _spec(steps=2)
    payload = _animation_dict(tmp_path, request_id="vis-1", spec_hash=digest_of(spec),
                              profile="draft", steps=2, variant=1)
    on_disk = {row["sha256"]: row["bytes"] for row in payload["resources"]
               if row["kind"] == "keyframe"}
    reviewer = ScriptedReviewer([_pass_all, _pass_all])

    outcome = media.run_media_pipeline(
        spec=spec, render=lambda profile, current: (payload, payload["base_dir"]),
        reviewer=reviewer, capabilities=_caps(), request_id="vis-1")

    seen = reviewer.calls[0]["images"]
    assert seen, "视觉评审没有收到任何图片"
    for item in seen:
        assert item["sha256"] in on_disk, "评审收到的图不是本次渲染的关键帧"
        assert item["bytes"] == on_disk[item["sha256"]]
        assert Path(item["path"]).is_file()
        assert item["mime"] == "image/png"
    assert reviewer.received_hashes == set(on_disk), (
        "评审必须恰好收到这一档的关键帧集合（含 hash），而不是凭规格想象")
    assert outcome.reports["draft"].images, "报告里要留下送审图片的 bytes/hash 证据"


def test_transport_embeds_the_actual_bytes_in_the_request_body(tmp_path):
    """`RealTransport` 的请求体里能解出与盘上一致的图片字节 —— 0 次调用、0 token。"""
    path = tmp_path / "keyframe.png"
    _png(path, size=(640, 360), variant=5)
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    transport = RealTransport(model="deepseek-flash",
                              api_key="test-key-not-used")

    body = transport.request_body(messages=[{"role": "user", "content": "看这张关键帧"}],
                                  images=[str(path)], max_tokens=64, effort="off")
    parts = body["messages"][-1]["content"]
    images = [p for p in parts if p["type"] == "image_url"]
    assert len(images) == 1
    encoded = images[0]["image_url"]["url"].split("base64,", 1)[1]
    assert hashlib.sha256(base64.b64decode(encoded)).hexdigest() == expected
    assert transport.last_image_payloads[0].sha256 == "sha256:" + expected
    assert transport.report()["last_image_hashes"] == ["sha256:" + expected]
    assert transport.calls == 0, "构造请求体不该产生调用"


# --------------------------------------------------------------------------- #
# 5. 仅 JSON 的假视觉评审不许通过 capability 检查
# --------------------------------------------------------------------------- #
def _json_only_reviewer(*, frames, steps, spec):          # noqa: ANN001
    """只吃 JSON／路径文本、从不接收图片字节的假视觉评审。"""
    return [{"step_id": step["step_id"], "status": "pass", "reason": "规格看着没问题"}
            for step in steps]


def test_json_only_reviewer_fails_the_image_input_capability_check():
    with pytest.raises(CapabilityError) as excinfo:
        require_image_input(_json_only_reviewer, purpose="visual_review")
    assert "图片输入" in str(excinfo.value)

    caps = with_image_input(_caps(), _json_only_reviewer)
    with pytest.raises(CapabilityError):
        caps.require("visual_review")
    assert caps.missing_for("visual_review"), "能力报告里要能看出缺的是图片输入"
    # 真的能收图片的传输则通过（示例：真实传输的接口就有 images 参数）
    transport = RealTransport(model="deepseek-flash",
                              api_key="test-key-not-used")
    assert require_image_input(transport).available


def test_media_pipeline_blocks_a_json_only_reviewer_before_any_render(tmp_path,
                                                                     monkeypatch):
    _install_exploding_transport(monkeypatch)
    renderer = ScriptedRenderer(steps=1).with_root(tmp_path)
    with pytest.raises(CapabilityError):
        media.run_media_pipeline(spec=_spec(steps=1), render=renderer,
                                 reviewer=_json_only_reviewer, capabilities=_caps(),
                                 request_id="vis-1")
    assert renderer.calls == [], "假评审应当在**任何渲染之前**被挡住"


# --------------------------------------------------------------------------- #
# 6. 超图像数量：按固定步长抽帧，装不下就报错
# --------------------------------------------------------------------------- #
def test_over_image_budget_raises_instead_of_dropping_a_whole_step():
    spec = _spec(steps=8)
    result = {"request_id": "vis-1", "status": "fulfilled", "base_dir": "/tmp",
              "resources": [{"kind": "keyframe", "relative_path": f"keyframes/s{i}-0.png",
                             "sha256": f"sha256:{i:064x}", "bytes": 10,
                             "mime": "image/png", "timestamp": i * 10.0 + 1.0}
                            for i in range(8)]}
    with pytest.raises(ContractError) as excinfo:
        select_frames(spec, result, max_images=3)
    assert "上限" in str(excinfo.value) and "没人看到" in str(excinfo.value)


def test_review_sees_at_most_max_images_and_keeps_one_frame_per_step(tmp_path, monkeypatch):
    _install_exploding_transport(monkeypatch)
    spec = _spec(steps=3)
    payload = _animation_dict(tmp_path, request_id="vis-1", spec_hash=digest_of(spec),
                              profile="draft", steps=3, variant=4)
    reviewer = ScriptedReviewer([_pass_all, _pass_all])
    outcome = media.run_media_pipeline(
        spec=spec, render=lambda profile, current: (payload, payload["base_dir"]),
        reviewer=reviewer, capabilities=_caps(), request_id="vis-1", max_images=4)
    report = outcome.reports["draft"]
    assert len(report.frame_hashes) <= 4
    assert len(reviewer.calls[0]["images"]) <= 4
    assert report.dropped_frames, "超过上限的帧必须逐条记录，不许静默截断"
    for step_id in ("s0", "s1", "s2"):
        check = [c for c in report.checks if c.check_id == f"step.{step_id}.frame_in_window"][0]
        assert check.status == "pass", "每个步骤至少要留一帧送审"


# --------------------------------------------------------------------------- #
# 7. draft → 关键帧审核 → 局部 patch → final
# --------------------------------------------------------------------------- #
def _patch_objects(spec, findings):                        # noqa: ANN001
    """局部 patch：只给被发现点名的对象改一项内容（走 `apply_local_patch` 的越界校验）。"""
    return media.apply_local_patch(
        spec, findings, {"objects": {f.object_id: {"label": "已按发现修正"}
                                     for f in findings}})


def test_draft_patch_final_loop_counts_content_rounds_once(tmp_path, monkeypatch):
    _install_exploding_transport(monkeypatch)
    spec = _spec(steps=1)
    renderer = ScriptedRenderer(steps=1, spec_request="vis-1").with_root(tmp_path)

    def verdicts(images, steps, current_spec):              # noqa: ANN001
        # 第一轮：失败（一步的公式错）；修好后的第二轮与 final：通过
        return _pass_all(images, steps, current_spec)

    reviewer = ScriptedReviewer([
        lambda images, steps, current: [_fail_step("s0", object_id="u")],
        verdicts, verdicts])
    outcome = media.run_media_pipeline(
        spec=spec, render=renderer, reviewer=reviewer, capabilities=_caps(),
        patch=_patch_objects, request_id="vis-1", max_content_rounds=2)

    state = outcome.state
    assert state.content_rounds_used == 1, "只做了一次局部 patch"
    assert state.rounds_by_profile == {"draft": 2, "final": 1}
    assert outcome.accepted, outcome.as_dict()
    assert outcome.final_status == "pass"
    assert [row["profile"] for row in state.reviews] == ["draft", "draft", "final"]


def test_same_spec_final_does_not_consume_a_content_round(tmp_path, monkeypatch):
    _install_exploding_transport(monkeypatch)
    renderer = ScriptedRenderer(steps=2, spec_request="vis-1").with_root(tmp_path)
    reviewer = ScriptedReviewer([_pass_all, _pass_all])
    outcome = media.run_media_pipeline(
        spec=_spec(steps=2), render=renderer, reviewer=reviewer, capabilities=_caps(),
        patch=_patch_objects, request_id="vis-1")

    assert outcome.state.content_rounds_used == 0, "draft 一次过，没有内容返修批次"
    assert outcome.state.rounds_by_profile == {"draft": 1, "final": 1}
    assert renderer.calls[0]["spec_hash"] == renderer.calls[1]["spec_hash"], (
        "final 用的是同一份规格：只有编码档位变化")
    assert any("编码档位" in note or "内容返修批次" in note for note in outcome.notes)


def test_final_is_reviewed_on_its_own_keyframes_even_when_draft_passed(tmp_path,
                                                                      monkeypatch):
    _install_exploding_transport(monkeypatch)
    renderer = ScriptedRenderer(steps=1, spec_request="vis-1").with_root(tmp_path)
    reviewer = ScriptedReviewer([
        _pass_all,                                          # draft 通过
        lambda images, steps, current: [_fail_step("s0", object_id="u",
                                                   action="spec.object: 修正曲面的开口方向")],
    ])
    outcome = media.run_media_pipeline(
        spec=_spec(steps=1), render=renderer, reviewer=reviewer, capabilities=_caps(),
        request_id="vis-1")

    assert not outcome.accepted, "draft 合格不能替 final 签字"
    assert outcome.final_status == "fail"
    final_hashes = outcome.state.reviewed_frames["final"]
    draft_hashes = outcome.state.reviewed_frames["draft"]
    assert final_hashes and set(final_hashes).isdisjoint(draft_hashes), (
        "final 必须评审**自己那批**关键帧（档位不同 → 帧 hash 不同）")
    assert set(final_hashes) <= set(reviewer.calls[-1]["frame_hashes"])
    assert outcome.reports["final"].profile == "final"


def test_local_patch_refuses_to_touch_steps_it_was_not_asked_about():
    """局部返修的"局部"是构造期约束：越界就报错，不许顺手重排规格。"""
    finding = media.FrameFinding(
        finding_id="FR-1", kind="visual_math", severity="P1", owner="visual_planner",
        step_id="s1", timestamp=1.0, object_id="u", problem="公式错",
        action="spec.formula: 改公式", expected_screen="显示 u_xx = 2",
        criteria=({"criterion_id": "c1", "version": 1, "required": True,
                   "expected": "u_xx = 2", "method": "frame"},),
        frame_hash="sha256:" + "a" * 64)
    spec = _spec(steps=3)
    with pytest.raises(ContractError) as excinfo:
        media.apply_local_patch(spec, [finding], {"steps": {"s0": {"caption": "顺手改"}}})
    assert "没有被发现点名" in str(excinfo.value)
    with pytest.raises(ContractError):
        media.apply_local_patch(spec, [finding], {"canvas": {"width": 1}})


# --------------------------------------------------------------------------- #
# 8. 台账终态与"0 隐藏调用"
# --------------------------------------------------------------------------- #
def test_figure_ledger_gets_animation_terminal_states(tmp_path):
    from aiforstudy.blocks.figures.request import FigureLedger

    payload = _animation_dict(tmp_path, request_id="anim-1", spec_hash=digest_of({"s": 1}),
                              profile="draft", steps=1, variant=2)
    fulfilled = media.figure_result_from_animation(
        parse_animation_result(payload), unit_id="CH04-S01", serves=("鞍面",))
    assert fulfilled.state == "fulfilled" and fulfilled.is_complete
    assert fulfilled.binding["unit_key"] == "CH04-S01"
    ledger = FigureLedger()
    ledger.record(fulfilled)
    ledger.assert_releaseable()                              # 不抛 = 可以发布

    blocked = media.fulfill_animation_request(
        {"request_id": "anim-2", "kind": "animation"}, capabilities=_caps(available=False),
        unit_id="CH04-S01")
    ledger.record(media.figure_result_from_animation(blocked, unit_id="CH04-S01"))
    assert [r.request_id for r in ledger.incomplete()] == ["anim-2"]
    with pytest.raises(ContractError):
        ledger.assert_releaseable()

    rows = media.ledger_rows([parse_animation_result(payload), blocked])
    assert [row["animation_status"] for row in rows] == ["fulfilled", "blocked"]
    assert json.loads(json.dumps(rows, ensure_ascii=False))[0]["animation"]["schema_version"]
    assert media.attachable_animations([parse_animation_result(payload), blocked]) == {
        "CH04-S01": [parse_animation_result(payload).as_dict()]}


def test_media_pipeline_makes_no_hidden_model_calls(tmp_path, monkeypatch):
    """受控替身之外**没有任何**调用：真实传输一被构造就炸，而且离线守卫是装上的。"""
    import offline_guard
    assert offline_guard.is_installed(), "离线守卫没装上，本测试的结论不成立"
    _install_exploding_transport(monkeypatch)

    renderer = ScriptedRenderer(steps=2, spec_request="vis-1").with_root(tmp_path)
    reviewer = ScriptedReviewer([_pass_all, _pass_all])
    outcome = media.run_media_pipeline(
        spec=_spec(steps=2), render=renderer, reviewer=reviewer, capabilities=_caps(),
        request_id="vis-1")

    assert outcome.accepted
    assert len(renderer.calls) == 2 and len(reviewer.calls) == 2, (
        "恰好 draft + final 两次渲染、两次评审；多一次都说明有隐藏调用")
    assert {call["profile"] for call in renderer.calls} == {"draft", "final"}
    assert outcome.state.rounds_by_profile == {"draft": 1, "final": 1}


def test_failed_draft_does_not_encode_final(tmp_path):
    renderer = ScriptedRenderer(steps=1, spec_request='vis-1').with_root(tmp_path)
    reviewer = ScriptedReviewer([lambda *args: [_fail_step('s0', object_id='u')]])
    outcome = media.run_media_pipeline(spec=_spec(steps=1), render=renderer,
        reviewer=reviewer, capabilities=_caps(), patch=None, request_id='vis-1', max_content_rounds=0)
    assert not outcome.accepted
    assert outcome.state.rounds_by_profile == {'draft': 1}
    assert 'final' not in outcome.reports


def test_layout_ticket_cannot_change_math_coordinates():
    finding = media.FrameFinding(
        finding_id='layout-1', kind='visual_layout', severity='P1', owner='visual_planner',
        step_id='s0', timestamp=1, object_id='u', problem='遮挡',
        action='layout.color: 改用蓝色', expected_screen='对象清晰可见',
        criteria=({'criterion_id':'visible','version':1,'required':True,'expected':'对象清晰','method':'frame'},),
        frame_hash='sha256:'+'a'*64)
    with pytest.raises(ContractError, match='数学'):
        media.apply_local_patch(_spec(steps=1), [finding], {'objects':{'u':{'expr':{'op':'const','value':0}}}})
