"""`AnimationResult` 冻结契约（§5.3，schema 1.0）的行为测试。

这个模块存在的理由只有一条：**T07 产出、T08 审核、T09 播放** 三方必须认同一个形状。
三方各自用自己那套 dict 键时**没有任何一处会报错** —— 播放器只是"读不到资源"、
审核只是"没看到关键帧"，而报告上一切正常。

所以这里逐条钉住最容易"看起来正常但是坏的"形态：
`fulfilled` 被伪造成完成、字幕 MIME 少 charset、资源路径逃逸、哈希与文件不符、
`not_applicable` 拿来冒充"缺工具"。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from aiforstudy.blocks.visual.result import (  # noqa: E402
    SCHEMA_VERSION,
    AnimationResource,
    AnimationResult,
    collect_resources,
    parse_animation_result,
    validate_against_files,
)
from aiforstudy.core.digest import digest_file  # noqa: E402
from aiforstudy.errors import ContractError  # noqa: E402

REAL_FINAL = Path("/tmp/aifs-render/final/out-saddle-a1-h0-k0")


def _sha(path: Path) -> str:
    return digest_file(path)


def _resource(kind: str, rel: str, *, mime: str, path: Path) -> dict:
    return {"kind": kind, "relative_path": rel, "sha256": _sha(path),
            "bytes": path.stat().st_size, "mime": mime}


def _fake_output(tmp_path: Path) -> Path:
    root = tmp_path / "out"
    (root / "keyframes").mkdir(parents=True)
    (root / "video.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42fake")
    (root / "poster.png").write_bytes(b"\x89PNG\r\n\x1a\nposter")
    (root / "keyframes" / "s1-0.png").write_bytes(b"\x89PNG\r\n\x1a\nframe")
    (root / "captions.vtt").write_text(
        "WEBVTT\n\nstep-1\n00:00:00.000 --> 00:00:04.000\n中文字幕\n", encoding="utf-8")
    (root / "transcript.txt").write_text("中文文字稿\n", encoding="utf-8")
    return root


def _full_result(root: Path, **overrides) -> dict:
    resources = [
        _resource("mp4", "video.mp4", mime="video/mp4", path=root / "video.mp4"),
        _resource("poster", "poster.png", mime="image/png", path=root / "poster.png"),
        _resource("keyframe", "keyframes/s1-0.png", mime="image/png",
                  path=root / "keyframes" / "s1-0.png"),
        _resource("captions", "captions.vtt", mime="text/vtt; charset=utf-8",
                  path=root / "captions.vtt"),
        _resource("transcript", "transcript.txt", mime="text/plain; charset=utf-8",
                  path=root / "transcript.txt"),
    ]
    data = {
        "schema_version": SCHEMA_VERSION, "request_id": "req-1", "status": "fulfilled",
        "spec_hash": "sha256:" + "a" * 64, "compiler_hash": "sha256:" + "b" * 64,
        "environment_hash": "sha256:" + "c" * 64,
        "source_binding": {"lesson_anchor": "CH04-S01"},
        "resources": resources, "steps": [{"step_id": "s1", "caption": "中文"}],
        "math_report": {"status": "pass", "checks": []},
        "frame_report": {"status": "pass", "checks": [{"check_id": "visual.review"}]},
        "technical_report": {"ffmpeg_decode_ok": True, "video_info": {"fps": 30}},
    }
    data.update(overrides)
    return data


# --------------------------------------------------------------------------- #
# 正向 + 往返
# --------------------------------------------------------------------------- #
def test_full_result_parses_and_roundtrips(tmp_path):
    root = _fake_output(tmp_path)
    result = parse_animation_result(_full_result(root))
    assert result.is_fulfilled
    assert set(result.by_kind) == {"mp4", "poster", "keyframe", "captions", "transcript"}
    again = parse_animation_result(json.loads(json.dumps(result.as_dict())))
    assert again.as_dict() == result.as_dict()


def test_collect_resources_hashes_by_content_not_by_name(tmp_path):
    root = _fake_output(tmp_path)
    resources = collect_resources(root)
    kinds = sorted(r.kind for r in resources)
    assert kinds == ["captions", "keyframe", "mp4", "poster", "transcript"]
    mp4 = next(r for r in resources if r.kind == "mp4")
    assert mp4.sha256 == _sha(root / "video.mp4")
    assert mp4.relative_path == "video.mp4"


def test_validate_against_files_passes_for_fresh_files(tmp_path):
    root = _fake_output(tmp_path)
    result = parse_animation_result(_full_result(root))
    assert validate_against_files(result, root) == []


# --------------------------------------------------------------------------- #
# 最要紧的一条：fulfilled 不许伪造
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("broken, marker", [
    ("math", "数学报告"),
    ("frame", "画面报告"),
    ("decode", "ffmpeg_decode_ok"),
    ("no_keyframe", "关键帧"),
    ("no_transcript", "缺资源"),
])
def test_fulfilled_cannot_be_faked(tmp_path, broken, marker):
    root = _fake_output(tmp_path)
    data = _full_result(root)
    if broken == "math":
        data["math_report"] = {"status": "needs_review"}
    elif broken == "frame":
        data["frame_report"] = None
    elif broken == "decode":
        data["technical_report"] = {"ffmpeg_decode_ok": False}
    elif broken == "no_keyframe":
        data["resources"] = [r for r in data["resources"] if r["kind"] != "keyframe"]
    elif broken == "no_transcript":
        data["resources"] = [r for r in data["resources"] if r["kind"] != "transcript"]
    with pytest.raises(ContractError) as excinfo:
        parse_animation_result(data)
    assert marker in str(excinfo.value), (
        f"拒绝理由必须点名 {marker}，实际：{excinfo.value}")


def test_needs_review_is_allowed_without_frame_report(tmp_path):
    """画面评审还没跑时可以是 needs_review —— 只是**不能**自称完成。"""
    root = _fake_output(tmp_path)
    data = _full_result(root, status="needs_review", frame_report=None)
    result = parse_animation_result(data)
    assert result.status == "needs_review" and not result.is_fulfilled


def test_not_applicable_requires_policy_and_evidence(tmp_path):
    root = _fake_output(tmp_path)
    with pytest.raises(ContractError) as excinfo:
        parse_animation_result(_full_result(root, status="not_applicable",
                                           frame_report=None))
    assert "not_applicable" in str(excinfo.value)
    ok = parse_animation_result(_full_result(root, status="not_applicable",
                                            frame_report=None,
                                            policy="该单元没有动画请求",
                                            evidence="figure_requests 里没有 animation 条目"))
    assert ok.policy and ok.evidence


# --------------------------------------------------------------------------- #
# 资源条目的规矩
# --------------------------------------------------------------------------- #
def test_unknown_status_is_rejected(tmp_path):
    root = _fake_output(tmp_path)
    with pytest.raises(ContractError):
        parse_animation_result(_full_result(root, status="done"))


def test_unknown_schema_version_is_rejected(tmp_path):
    root = _fake_output(tmp_path)
    with pytest.raises(ContractError):
        parse_animation_result(_full_result(root, schema_version="2.0"))


def test_unknown_resource_field_is_rejected(tmp_path):
    root = _fake_output(tmp_path)
    data = _full_result(root)
    data["resources"][0]["extra_secret"] = 1
    with pytest.raises(ContractError) as excinfo:
        parse_animation_result(data)
    assert "extra_secret" in str(excinfo.value)


def test_captions_without_charset_is_rejected(tmp_path):
    root = _fake_output(tmp_path)
    data = _full_result(root)
    for resource in data["resources"]:
        if resource["kind"] == "captions":
            resource["mime"] = "text/vtt"
    with pytest.raises(ContractError) as excinfo:
        parse_animation_result(data)
    assert "charset" in str(excinfo.value)


def test_resource_without_hash_is_rejected(tmp_path):
    with pytest.raises(ContractError) as excinfo:
        AnimationResource(kind="mp4", relative_path="video.mp4", sha256="",
                          bytes=1, mime="video/mp4")
    assert "sha256" in str(excinfo.value)


@pytest.mark.parametrize("bad", ["/etc/passwd", "../outside.mp4", "", "a/../b.mp4"])
def test_unsafe_resource_paths_are_rejected(bad):
    with pytest.raises(ContractError):
        AnimationResource(kind="mp4", relative_path=bad, sha256="sha256:" + "a" * 64,
                          bytes=1, mime="video/mp4")


def test_negative_bytes_is_rejected():
    with pytest.raises(ContractError):
        AnimationResource(kind="mp4", relative_path="v.mp4", sha256="sha256:" + "a" * 64,
                          bytes=-1, mime="video/mp4")


def test_missing_identity_hashes_are_rejected(tmp_path):
    root = _fake_output(tmp_path)
    for field in ("spec_hash", "compiler_hash", "environment_hash"):
        with pytest.raises(ContractError):
            parse_animation_result(_full_result(root, **{field: ""}))


def test_keyframe_carries_step_and_timestamp(tmp_path):
    root = _fake_output(tmp_path)
    resource = AnimationResource(kind="keyframe", relative_path="keyframes/s1-0.png",
                                sha256=_sha(root / "keyframes" / "s1-0.png"),
                                bytes=(root / "keyframes" / "s1-0.png").stat().st_size,
                                mime="image/png", timestamp=4.0, step_id="s1")
    assert resource.as_dict()["timestamp"] == 4.0
    assert resource.as_dict()["step_id"] == "s1"


# --------------------------------------------------------------------------- #
# 清单 vs 文件
# --------------------------------------------------------------------------- #
def test_tampered_file_is_caught(tmp_path):
    root = _fake_output(tmp_path)
    result = parse_animation_result(_full_result(root))
    (root / "poster.png").write_bytes(b"\x89PNG\r\n\x1a\nTAMPERED")
    problems = validate_against_files(result, root)
    assert problems, "被换过的文件必须报出来"
    assert any("sha256" in p for p in problems), (
        f"必须有一条指出哈希不符（内容被换过），实际：{problems}")


def test_missing_file_is_caught(tmp_path):
    root = _fake_output(tmp_path)
    result = parse_animation_result(_full_result(root))
    (root / "captions.vtt").unlink()
    problems = validate_against_files(result, root)
    assert problems and "缺文件" in problems[0]


def test_byte_count_mismatch_is_caught(tmp_path):
    root = _fake_output(tmp_path)
    result = parse_animation_result(_full_result(root))
    (root / "transcript.txt").write_text("更长的中文文字稿内容\n", encoding="utf-8")
    problems = validate_against_files(result, root)
    assert any("字节数不符" in p or "sha256" in p for p in problems)


# --------------------------------------------------------------------------- #
# 真实产物（T07 的 CH04 首样）必须能过同一套契约
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not (REAL_FINAL / "video.mp4").is_file(),
                    reason="本机没有 T07 的真实渲染产物（临时目录，跨机器不存在）")
def test_real_t07_output_satisfies_the_frozen_contract():
    """真实渲染产物装配出来的结果必须满足 §5.3 —— 契约不能只对合成夹具有效。"""
    resources = collect_resources(REAL_FINAL)
    result = parse_animation_result({
        "schema_version": SCHEMA_VERSION, "request_id": "vis-saddle", "status": "needs_review",
        "spec_hash": "sha256:" + "a" * 64, "compiler_hash": "sha256:" + "b" * 64,
        "environment_hash": "sha256:" + "c" * 64,
        "resources": [r.as_dict() for r in resources],
        "steps": [{"step_id": "s1"}],
        "math_report": {"status": "pass"},
        "frame_report": {"status": "needs_review"},
        "technical_report": {"ffmpeg_decode_ok": True},
    })
    assert set(result.by_kind) >= {"mp4", "poster", "captions", "transcript", "keyframe"}
    assert validate_against_files(result, REAL_FINAL) == []
    # 真实产物里**没有**画面评审（frame_report 是 needs_review），所以不许报 fulfilled
    with pytest.raises(ContractError):
        parse_animation_result({**result.as_dict(), "status": "fulfilled"})
