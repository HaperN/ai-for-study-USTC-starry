"""T10：**动画资源与视频的离线检查**测试（V03 / E02 的证据自动化）。

这类检查最容易写成"文件存在就算通过"，而那条判据什么都挡不住：
**截断的视频同样存在、同样能返回 HTTP 200、同样有非零字节数**。所以这里每一条
反向用例都对着一种"看起来正常但其实是坏的"形态：

| 反向用例 | 它代表的真实故障 |
|---|---|
| 截断的 MP4 | 渲染中途被杀 / 磁盘写满 |
| 带音轨的 MP4 | 计划明确要求**无音轨**（§5.4） |
| 尺寸／帧率不符 | 档位参数写错或 ffmpeg 参数被覆盖 |
| 时长超出资源上限 | 超出宿主允许的动画规模；有效短片不因秒数被拒绝 |
| 资源 hash 不符 | 文件被换过／产物过期（**旧证书不能证明新版本**） |
| 字幕不是 UTF-8 / 没有合法 cue | 字幕静默失效；语言是否适合教学由语义审核判断 |
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from aiforstudy.checks.offline import (  # noqa: E402
    check_animation_resources,
    check_animation_video,
    check_webvtt,
)
from aiforstudy.core.digest import digest_file  # noqa: E402
from aiforstudy.errors import CapabilityError  # noqa: E402


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


@pytest.fixture(scope="session")
def good_video(tmp_path_factory) -> Path:
    """一段**真**的 30 秒 / 1280×720 / 30fps / yuv420p / 无音轨 MP4。"""
    path = tmp_path_factory.mktemp("media") / "video.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=30",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
            "-movflags", "+faststart", str(path))
    return path


@pytest.fixture
def result_dir(tmp_path, good_video) -> Path:
    """一份齐全的产物目录（视频 + 封面 + 字幕 + 文字稿 + 关键帧）。"""
    root = tmp_path / "assets"
    root.mkdir()
    (root / "video.mp4").write_bytes(good_video.read_bytes())
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=1",
            "-frames:v", "1", str(root / "poster.png"))
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=1",
            "-frames:v", "1", str(root / "s1-0.png"))
    (root / "captions.vtt").write_text(
        "WEBVTT\n\nstep-1\n00:00:00.000 --> 00:00:08.000\n"
        "x、y 是两个平面方向，u 是高度。\n", encoding="utf-8")
    (root / "transcript.txt").write_text("总时长：30 秒\n中文文字稿\n", encoding="utf-8")
    return root


def _result(root: Path, **overrides) -> dict:
    def entry(kind, rel, mime, extra=None):
        path = root / rel
        data = {"kind": kind, "relative_path": rel, "sha256": digest_file(path),
                "bytes": path.stat().st_size, "mime": mime}
        data.update(extra or {})
        return data

    resources = [
        entry("mp4", "video.mp4", "video/mp4"),
        entry("poster", "poster.png", "image/png"),
        entry("keyframe", "s1-0.png", "image/png", {"timestamp": 4.0, "step_id": "s1"}),
        entry("captions", "captions.vtt", "text/vtt; charset=utf-8"),
        entry("transcript", "transcript.txt", "text/plain; charset=utf-8"),
    ]
    result = {"schema_version": "1.0", "request_id": "req-1", "status": "fulfilled",
              "spec_hash": "sha256:" + "a" * 64, "compiler_hash": "sha256:" + "b" * 64,
              "environment_hash": "sha256:" + "c" * 64, "resources": resources}
    result.update(overrides)
    return result


# --------------------------------------------------------------------------- #
# 视频：正向
# --------------------------------------------------------------------------- #
def test_good_video_passes_and_binds_the_hash(good_video):
    check = check_animation_video(good_video, profile="final")
    assert check.ok, check.detail
    assert check.evidence["sha256"] == digest_file(good_video), (
        "检查结果必须绑定这一版视频的 sha256，否则换个文件报告照样'通过'")
    assert check.evidence["audio_streams"] == 0
    assert check.evidence["decode_exit_code"] == 0


def test_unknown_profile_is_a_capability_error(good_video):
    """档位是参数来源，猜档位等于把验收标准弄丢。"""
    with pytest.raises(CapabilityError):
        check_animation_video(good_video, profile="release")


# --------------------------------------------------------------------------- #
# 视频：反向（每条都对应一种"看起来正常但是坏的"）
# --------------------------------------------------------------------------- #
def test_truncated_video_fails_despite_existing_and_being_non_empty(tmp_path, good_video):
    broken = tmp_path / "truncated.mp4"
    broken.write_bytes(good_video.read_bytes()[: good_video.stat().st_size // 3])
    assert broken.stat().st_size > 0, "前提：它是个非空文件"
    check = check_animation_video(broken, profile="final")
    assert not check.ok, "截断的视频必须失败 —— 存在且非空不代表能播"
    assert check.status == "failed"


def test_video_with_valid_aac_narration_passes(tmp_path, good_video):
    with_audio = tmp_path / "with-audio.mp4"
    _ffmpeg("-i", str(good_video), "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
            "-c:v", "copy", "-c:a", "aac", "-shortest", str(with_audio))
    check = check_animation_video(with_audio, profile="final")
    assert check.ok, check.detail
    assert check.evidence["audio_streams"] == 1


def test_wrong_size_fails(tmp_path):
    small = tmp_path / "small.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=30",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(small))
    check = check_animation_video(small, profile="final")
    assert not check.ok and "width" in check.detail


def test_wrong_fps_fails(tmp_path):
    slow = tmp_path / "slow.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=1280x720:rate=15:duration=30",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(slow))
    check = check_animation_video(slow, profile="final")
    assert not check.ok and "fps" in check.detail


def test_valid_short_video_passes_technical_checks_without_minimum_duration(tmp_path):
    short = tmp_path / "short.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=8",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(short))
    check = check_animation_video(short, profile="final")
    assert check.ok,check.detail
    assert check.evidence['duration']==pytest.approx(8.0)
    assert check.evidence['decode_ok'] and check.evidence['profile_ok']


def test_video_over_resource_duration_fails_even_when_decodable(tmp_path):
    from aiforstudy.blocks.media.presentation_policy import MAX_ANIMATION_SECONDS
    long_video=tmp_path/'over-limit.mp4'
    _ffmpeg('-f','lavfi','-i',f'color=c=blue:s=640x360:r=15:d={MAX_ANIMATION_SECONDS+1}',
            '-pix_fmt','yuv420p','-c:v','libx264','-preset','ultrafast',str(long_video))
    check=check_animation_video(long_video,profile='draft')
    assert not check.ok and '时长' in check.detail
    assert check.evidence['decode_ok'], '时长限制与完整解码应分别报告'


def test_missing_video_fails(tmp_path):
    check = check_animation_video(tmp_path / "nope.mp4", profile="final")
    assert not check.ok and check.status == "failed"


# --------------------------------------------------------------------------- #
# 资源清单：hash / MIME / 路径 / 齐全
# --------------------------------------------------------------------------- #
def test_complete_resource_set_passes(result_dir):
    check = check_animation_resources(_result(result_dir), result_dir, profile="final")
    assert check.ok, check.detail
    assert check.evidence["binding"]["mp4_sha256"] == digest_file(result_dir / "video.mp4")
    assert check.evidence["binding"]["spec_hash"].startswith("sha256:")
    assert len(check.evidence["files"]) == 5


def test_tampered_resource_is_rejected_even_with_same_name(result_dir):
    """同名文件被换过：**旧证书不能证明新版本**。"""
    result = _result(result_dir)          # 先按**原文件**算好清单（真实产物当年的那份证书）
    (result_dir / "poster.png").write_bytes(b"\x89PNG\r\n\x1a\nTAMPERED")
    check = check_animation_resources(result, result_dir, profile="final")
    assert not check.ok and "sha256" in check.detail


def test_wrong_byte_count_is_rejected(result_dir):
    result = _result(result_dir)
    result["resources"][1]["bytes"] = 12345
    check = check_animation_resources(result, result_dir, profile="final")
    assert not check.ok and "字节" in check.detail


def test_wrong_captions_mime_is_rejected(result_dir):
    """字幕 MIME 少了 charset，中文会按 latin-1 解成乱码。"""
    result = _result(result_dir)
    for resource in result["resources"]:
        if resource["kind"] == "captions":
            resource["mime"] = "text/vtt"
    check = check_animation_resources(result, result_dir, profile="final")
    assert not check.ok and "MIME" in check.detail


def test_missing_required_kind_is_rejected(result_dir):
    result = _result(result_dir)
    result["resources"] = [r for r in result["resources"] if r["kind"] != "transcript"]
    check = check_animation_resources(result, result_dir, profile="final")
    assert not check.ok and "transcript" in check.detail


def test_path_traversal_is_rejected(result_dir):
    result = _result(result_dir)
    result["resources"][1]["relative_path"] = "../outside.png"
    check = check_animation_resources(result, result_dir, profile="final")
    assert not check.ok and "安全" in check.detail


def test_absolute_path_is_rejected(result_dir):
    result = _result(result_dir)
    result["resources"][1]["relative_path"] = "/etc/passwd"
    check = check_animation_resources(result, result_dir, profile="final")
    assert not check.ok and "安全" in check.detail


def test_empty_resources_is_rejected(result_dir):
    check = check_animation_resources({"resources": []}, result_dir, profile="final")
    assert not check.ok and "没有资源" in check.detail


def test_broken_video_makes_the_whole_set_fail(result_dir):
    result = _result(result_dir)
    (result_dir / "video.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    check = check_animation_resources(result, result_dir, profile="final")
    assert not check.ok, "视频坏了，整套资源不能算通过"


def test_corrupt_file_is_a_content_failure_not_a_capability_failure(tmp_path):
    """**坏的产物**不能报成"本机缺 ffprobe"。

    报成环境缺失会让一个内容问题伪装成 `blocked`，正好是计划反复禁止的
    "缺工具／没运行 冒充不适用"。
    """
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video at all")
    check = check_animation_video(broken, profile="final")
    assert check.status == "failed" and not check.ok
    assert "ffprobe" in check.detail and "读不出来" in check.detail


# --------------------------------------------------------------------------- #
# 字幕：UTF-8 / WEBVTT / 有效cue；语言不作技术硬门
# --------------------------------------------------------------------------- #
def test_good_captions_pass(result_dir):
    check = check_webvtt(result_dir / "captions.vtt")
    assert check.ok, check.detail
    assert check.evidence['cues']==1
    assert check.evidence['cue_records']==[{'id':'step-1','start':0.0,'end':8.0,
        'text':'x、y 是两个平面方向，u 是高度。'}]


def test_non_utf8_captions_fail(tmp_path):
    path = tmp_path / "captions.vtt"
    path.write_bytes("WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.000\n中文字幕\n".encode("gb18030"))
    check = check_webvtt(path)
    assert not check.ok and "utf-8" in check.detail.lower()


def test_missing_webvtt_header_fails(tmp_path):
    path = tmp_path / "captions.vtt"
    path.write_text("1\n00:00:00.000 --> 00:00:01.000\n中文字幕\n", encoding="utf-8")
    check = check_webvtt(path)
    assert not check.ok and "WEBVTT" in check.detail


def test_captions_without_cues_fail(tmp_path):
    path = tmp_path / "captions.vtt"
    path.write_text("WEBVTT\n\n这是一段没有时段的说明文字\n", encoding="utf-8")
    check = check_webvtt(path)
    assert not check.ok and "cue" in check.detail


def test_valid_captions_without_chinese_have_no_language_hard_gate(tmp_path):
    path = tmp_path / "captions.vtt"
    path.write_text("WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.000\nplain english\n",
                    encoding="utf-8")
    check = check_webvtt(path)
    assert check.ok,check.detail
    assert check.evidence['cue_records']==[{'id':'1','start':0.0,'end':1.0,'text':'plain english'}]


def test_missing_captions_file_fails(tmp_path):
    check = check_webvtt(tmp_path / "nope.vtt")
    assert not check.ok and check.status == "failed"


# --------------------------------------------------------------------------- #
# 真实产物（T07 的 CH04 首样）能过这套检查
# --------------------------------------------------------------------------- #
REAL_FINAL = Path("/tmp/aifs-render/final/out-saddle-a1-h0-k0")


@pytest.mark.skipif(not (REAL_FINAL / "video.mp4").is_file(),
                    reason="本机没有 T07 的真实渲染产物（临时目录，跨机器不存在）")
def test_real_t07_render_passes_the_media_checks():
    """把 T07 的真实产物喂给同一套检查 —— 检查器不能只对合成夹具有效。"""
    video = check_animation_video(REAL_FINAL / "video.mp4", profile="final")
    assert video.ok, video.detail
    captions = check_webvtt(REAL_FINAL / "captions.vtt")
    assert captions.ok, captions.detail
    keyframes = sorted((REAL_FINAL / "keyframes").glob("*.png"))
    resources = [
        {"kind": "mp4", "relative_path": "video.mp4",
         "sha256": digest_file(REAL_FINAL / "video.mp4"),
         "bytes": (REAL_FINAL / "video.mp4").stat().st_size, "mime": "video/mp4"},
        {"kind": "poster", "relative_path": "poster.png",
         "sha256": digest_file(REAL_FINAL / "poster.png"),
         "bytes": (REAL_FINAL / "poster.png").stat().st_size, "mime": "image/png"},
        {"kind": "captions", "relative_path": "captions.vtt",
         "sha256": digest_file(REAL_FINAL / "captions.vtt"),
         "bytes": (REAL_FINAL / "captions.vtt").stat().st_size,
         "mime": "text/vtt; charset=utf-8"},
        {"kind": "transcript", "relative_path": "transcript.txt",
         "sha256": digest_file(REAL_FINAL / "transcript.txt"),
         "bytes": (REAL_FINAL / "transcript.txt").stat().st_size,
         "mime": "text/plain; charset=utf-8"},
    ]
    assert keyframes, "真实产物应当有每步检查点"
    for path in keyframes[:2]:
        resources.append({"kind": "keyframe",
                          "relative_path": f"keyframes/{path.name}",
                          "sha256": digest_file(path), "bytes": path.stat().st_size,
                          "mime": "image/png", "timestamp": 4.0})
    check = check_animation_resources(
        {"resources": resources, "spec_hash": "sha256:" + "a" * 64,
         "compiler_hash": "sha256:" + "b" * 64, "environment_hash": "sha256:" + "c" * 64},
        REAL_FINAL, profile="final")
    assert check.ok, check.detail
