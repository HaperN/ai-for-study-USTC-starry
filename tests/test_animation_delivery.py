"""T09 资源与 manifest：**内容寻址的媒体产物登记**（计划 §5.3 / §T09）。

这一层钉住五件"出错不会报错、只会悄悄交付错东西"的事：

1. **manifest 漏项 / 重复 request 必须拒绝** —— AnimationResult 是接口契约，
   少一条 `resources[]` 意味着播放器拿不到文件、页面却看起来正常。
2. **相对路径**：资源一律独立相对路径文件；`..`／绝对路径／盘符一律拒绝。
   把 base64 视频塞进单个 HTML 是本计划明确禁止的形态。
3. **symlink 逃逸**：登记的"相对路径"不能通过符号链接指到治理目录之外。
   只查字符串是不够的 —— 落盘时 `Path.resolve()` 才会暴露真相。
4. **内容与 hash 不符（篡改）必须拒绝**：文件存在**不是**有效证据（§11）。
5. **分块读取**：服务端不许 `read_bytes()` 整段吞视频。

真实 MP4／PNG／VTT 由 ffmpeg 生成（T07 尚未落地真实渲染，这里是**合成夹具**）；
缺 ffmpeg 时相关用例 skip 并写明理由，不假装通过。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from aiforstudy.core.digest import digest_file
from aiforstudy.delivery.resources import (
    ANIMATION_KINDS,
    DEFAULT_CHUNK_BYTES,
    AnimationManifest,
    AnimationResource,
    collect_animation_resources,
    iter_chunks,
    mime_for,
    parse_range_header,
    range_for_response,
)
from aiforstudy.errors import ContractError

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = None


@pytest.fixture(autouse=True)
def isolated_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "EVIDENCE", tmp_path / "media-evidence")


# ────────────────────────────────────────── 合成夹具（真实字节，不用 ffmpeg 也能跑）

def _write_png(path: Path, width: int = 8, height: int = 8) -> None:
    import struct
    import zlib

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + bytes([30, 60, 120] * width) for _ in range(height))
    blob = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))
    path.write_bytes(blob)


VTT_TEXT = """WEBVTT

00:00:00.000 --> 00:00:02.000
第一步：x、y 是两个方向

00:00:02.000 --> 00:00:04.000
第二步：u 是高度
"""

TRANSCRIPT_TEXT = "第一步：x、y 是两个方向\n第二步：u 是高度\n"


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict:
    """合成媒体夹具：mp4（ffmpeg，缺则 None）／poster.png／captions.vtt／transcript.txt。"""
    base = tmp_path_factory.mktemp("t09-media")
    directory = base / "render" / "media"
    directory.mkdir(parents=True)
    _write_png(directory / "poster.png")
    (directory / "captions.vtt").write_text(VTT_TEXT, encoding="utf-8")
    (directory / "transcript.txt").write_text(TRANSCRIPT_TEXT, encoding="utf-8")
    video = directory / "scene.mp4"
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        result = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-f", "lavfi",
             "-i", "color=c=navy:s=160x90:d=4:r=15", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(video)],
            capture_output=True, text=True, timeout=120)
        if result.returncode != 0 or not video.is_file():
            video = None
    else:
        video = None
    ffprobe = shutil.which("ffprobe")
    probe = None
    if video is not None and ffprobe:
        out = subprocess.run([ffprobe, "-v", "error", "-show_entries",
                              "stream=codec_name,pix_fmt,width,height,r_frame_rate",
                              "-of", "json", str(video)],
                             capture_output=True, text=True, timeout=60)
        probe = json.loads(out.stdout or "{}").get("streams", [{}])[0]
    return {"root": directory, "video": video, "probe": probe,
            "poster": directory / "poster.png",
            "captions": directory / "captions.vtt",
            "transcript": directory / "transcript.txt"}


def build_result(media: dict, *, request_id: str = "req-saddle-1",
                 include: tuple[str, ...] = ("mp4", "poster", "captions", "transcript"),
                 relative_names: dict | None = None) -> dict:
    """按冻结契约造一份 AnimationResult（schema_version 1.0）。"""
    names = {"mp4": "media/scene.mp4", "poster": "media/poster.png",
             "captions": "media/captions.vtt", "transcript": "media/transcript.txt",
             **(relative_names or {})}
    paths = {"mp4": media["video"], "poster": media["poster"],
             "captions": media["captions"], "transcript": media["transcript"]}
    resources = []
    for kind in include:
        source = paths[kind]
        if source is None:
            continue
        resources.append({"kind": kind, "relative_path": names[kind],
                          "sha256": digest_file(source), "bytes": source.stat().st_size,
                          "mime": mime_for(names[kind])})
    return {
        "schema_version": "1.0", "request_id": request_id, "status": "fulfilled",
        "spec_hash": "sha256:" + "a" * 64, "compiler_hash": "sha256:" + "b" * 64,
        "environment_hash": "sha256:" + "c" * 64,
        "source_binding": {"book_id": "mini-book", "unit_id": "CH01-S01"},
        "resources": resources,
        "steps": [{"step_id": "s1", "caption": "x、y 是两个方向", "start": 0.0, "end": 2.0},
                  {"step_id": "s2", "caption": "u 是高度", "start": 2.0, "end": 4.0}],
        "math_report": {"status": "pass"}, "frame_report": {"status": "pass"},
        "technical_report": {"status": "pass"},
    }


def _sources(media: dict) -> dict:
    return {kind: path for kind, path in
            (("mp4", media["video"]), ("poster", media["poster"]),
             ("captions", media["captions"]), ("transcript", media["transcript"]))
            if path is not None}


# ────────────────────────────────────────── 1 正常路径

def test_collect_copies_individual_relative_files_and_hashes_content(tmp_path, media):
    """资源各自成文件、写成**相对路径**、sha256 对内容取。"""
    if media["video"] is None:
        pytest.skip("ffmpeg 不可用：合成 MP4 不能生成（不假装有真实视频）")
    target = tmp_path / "delivery"
    manifest = collect_animation_resources(
        results=[build_result(media)], target_root=target, sources=_sources(media),
        book_id="mini-book", unit_id="CH01-S01")

    kinds = {entry.kind for entry in manifest.entries}
    assert kinds == set(ANIMATION_KINDS) - {"keyframe"}
    for entry in manifest.entries:
        assert not entry.relative_path.startswith("/")
        assert ".." not in entry.relative_path.split("/")
        written = target / entry.relative_path
        assert written.is_file(), f"{entry.kind} 必须各自成文件"
        assert digest_file(written) == entry.sha256
        assert written.stat().st_size == entry.bytes
        assert entry.sha256.startswith("sha256:")

    # 单个 HTML 里塞 base64 视频是被禁止的形态：这里必须是 0
    assert manifest.inline_base64_bytes() == 0
    payload = manifest.payload_for("CH01-S01")
    assert payload["request_id"] == "req-saddle-1"
    assert payload["resources"]["mp4"]["relative_path"] == "media/scene.mp4"
    assert payload["endpoints"] == {"management_api": None, "origin": None}, \
        "播放器只读本地 manifest，载荷里不许出现管理 API 地址"
    assert "mp4" not in payload["media_src"] and payload["media_src"] == ["'self'"]
    assert manifest.request_ids() == ("req-saddle-1",)

    # manifest 自身可复核（文件存在**不算**证据，要 hash 对得上）
    verified = manifest.verify(target)
    assert verified["ok"] is True, verified["problems"]


def test_mime_is_by_kind_not_by_host_guessing(tmp_path):
    """MIME 按**后缀**定死：这台机器的 /etc/mime.types 不认识 vtt。"""
    assert mime_for("media/scene.mp4") == "video/mp4"
    assert mime_for("media/captions.vtt") == "text/vtt; charset=utf-8"
    assert mime_for("media/poster.png") == "image/png"
    assert mime_for("media/transcript.txt") == "text/plain; charset=utf-8"


# ────────────────────────────────────────── 2 manifest 漏项 / 重复 request

def test_manifest_rejects_a_missing_declared_resource(tmp_path, media):
    """manifest 漏项 → 拒绝（不许静默交付缺件）。"""
    if media["video"] is None:
        pytest.skip("ffmpeg 不可用：合成 MP4 不能生成")
    target = tmp_path / "delivery"
    result = build_result(media, include=("mp4",))
    # 先正常收集（本次只声明 mp4，required 也随之收窄），再把盘上那件删掉 ——
    # 这正是"清单说有、盘上没有"。
    manifest = collect_animation_resources(results=[result], target_root=target,
                                           sources={"mp4": media["video"]},
                                           book_id="mini-book", unit_id="CH01-S01",
                                           required=("mp4",))
    entry = manifest.entries[0]
    (target / entry.relative_path).unlink()
    verified = manifest.verify(target)
    assert verified["ok"] is False
    assert any("不在盘上" in problem for problem in verified["problems"])

    # 缺件要能逐条报出来（播放器据此显示错误 + 文字稿）
    assert "mp4" in manifest.missing_kinds(target)
    with pytest.raises(ContractError) as failure:
        manifest.require_kinds(target, required=("mp4", "captions"))
    assert "缺" in str(failure.value)


def test_manifest_rejects_missing_kind_in_declaration(tmp_path, media):
    """AnimationResult 声明 `status=fulfilled` 却没有 captions → 拒绝，不当成合格。"""
    result = build_result(media, include=("poster",))
    with pytest.raises(ContractError) as failure:
        collect_animation_resources(results=[result], target_root=tmp_path / "d",
                                    sources={"poster": media["poster"]},
                                    book_id="mini-book", unit_id="CH01-S01",
                                    required=("mp4", "poster", "captions", "transcript"))
    assert "captions" in str(failure.value)


def test_manifest_rejects_duplicate_request_id(tmp_path, media):
    """重复 request_id → 拒绝（同一请求两份结果，谁覆盖谁都是错）。"""
    source = media["poster"]
    results = [build_result(media, request_id="req-dup", include=("poster",)),
               build_result(media, request_id="req-dup", include=("poster",))]
    with pytest.raises(ContractError) as failure:
        collect_animation_resources(results=results, target_root=tmp_path / "d",
                                    sources={"poster": source}, book_id="mini-book",
                                    unit_id="CH01-S01", required=("poster",))
    assert "重复" in str(failure.value) and "req-dup" in str(failure.value)


def test_manifest_rejects_missing_or_non_object_request_id(tmp_path, media):
    result = build_result(media, include=("poster",))
    result["request_id"] = ""
    with pytest.raises(ContractError):
        AnimationManifest.from_result(result, sources={"poster": media["poster"]},
                                      book_id="mini-book", unit_id="CH01-S01")


def test_manifest_rejects_unknown_kind_and_unknown_schema(tmp_path, media):
    result = build_result(media, include=("poster",))
    result["resources"][0]["kind"] = "gif"
    with pytest.raises(ContractError) as failure:
        AnimationManifest.from_result(result, sources={"poster": media["poster"]},
                                      book_id="mini-book", unit_id="CH01-S01")
    assert "gif" in str(failure.value)
    assert "kind" in str(failure.value)

    result = build_result(media, include=("poster",))
    result["schema_version"] = "2.0"
    with pytest.raises(ContractError) as failure:
        AnimationManifest.from_result(result, sources={"poster": media["poster"]},
                                      book_id="mini-book", unit_id="CH01-S01")
    assert "schema_version" in str(failure.value)


def test_manifest_rejects_declared_hash_that_does_not_match_disk(tmp_path, media):
    """登记时 hash 与盘上不符 → 拒绝（不能先记一个假指纹）。"""
    tampered = tmp_path / "tampered.png"
    tampered.write_bytes(media["poster"].read_bytes())
    result = build_result(media, include=("poster",))
    result["resources"][0]["sha256"] = "sha256:" + hashlib.sha256(b"other").hexdigest()
    with pytest.raises(ContractError) as failure:
        AnimationManifest.from_result(result, sources={"poster": tampered},
                                      book_id="mini-book", unit_id="CH01-S01")
    assert "sha256" in str(failure.value) or "内容" in str(failure.value)


# ────────────────────────────────────────── 3 路径穿越 / symlink 逃逸

@pytest.mark.parametrize("bad", ["../escape.mp4", "media/../../escape.mp4", "/abs/scene.mp4",
                                 "C:/windows/scene.mp4", "media\\scene.mp4", ".",
                                 "media//scene.mp4", "media/./scene.mp4"])
def test_manifest_rejects_traversal_and_absolute_paths(tmp_path, media, bad):
    result = build_result(media, include=("poster",),
                          relative_names={"poster": bad})
    with pytest.raises(ContractError):
        AnimationManifest.from_result(result, sources={"poster": media["poster"]},
                                      book_id="mini-book", unit_id="CH01-S01")


def test_collect_refuses_a_symlink_escaping_the_governed_root(tmp_path, media):
    """**symlink 逃逸**：相对路径本身合法，但盘上那一份是指向治理目录之外的软链。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.png"
    secret.write_bytes(media["poster"].read_bytes())
    staging = tmp_path / "staging"
    staging.mkdir()
    link = staging / "poster.png"
    link.symlink_to(secret)

    result = build_result(media, include=("poster",),
                          relative_names={"poster": "media/poster.png"})
    with pytest.raises(ContractError) as failure:
        collect_animation_resources(results=[result], target_root=tmp_path / "delivery",
                                    sources={"poster": link}, book_id="mini-book",
                                    unit_id="CH01-S01", required=("poster",))
    assert "符号链接" in str(failure.value) or "symlink" in str(failure.value).lower()
    # 目标目录里不能出现任何东西（拒绝要发生**在拷之前**）
    assert not (tmp_path / "delivery" / "media" / "poster.png").exists()


def test_collect_refuses_symlinked_target_directory(tmp_path, media):
    """落点目录本身是 symlink（指向治理目录之外）→ 拒绝，不跟着写出去。"""
    outside = tmp_path / "outside-target"
    outside.mkdir()
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    (delivery / "media").symlink_to(outside, target_is_directory=True)
    result = build_result(media, include=("poster",))
    with pytest.raises(ContractError):
        collect_animation_resources(results=[result], target_root=delivery,
                                    sources={"poster": media["poster"]},
                                    book_id="mini-book", unit_id="CH01-S01",
                                    required=("poster",))
    assert list(outside.iterdir()) == []


# ────────────────────────────────────────── 4 篡改

def test_tampered_content_is_rejected_on_verify_and_on_require(tmp_path, media):
    """内容被篡改（同长度改写）→ verify 失败 + require_kinds 拒绝。"""
    if media["video"] is None:
        pytest.skip("ffmpeg 不可用：合成 MP4 不能生成")
    target = tmp_path / "delivery"
    manifest = collect_animation_resources(results=[build_result(media)], target_root=target,
                                           sources=_sources(media), book_id="mini-book",
                                           unit_id="CH01-S01")
    video = target / "media" / "scene.mp4"
    blob = bytearray(video.read_bytes())
    blob[0] ^= 0xFF                       # 长度不变，内容变了
    video.write_bytes(bytes(blob))

    verified = manifest.verify(target)
    assert verified["ok"] is False
    assert any("不一致" in problem or "sha256" in problem for problem in verified["problems"])
    with pytest.raises(ContractError):
        manifest.require_kinds(target, required=("mp4",))


def test_hashing_uses_streaming_not_a_whole_file_read(tmp_path, media, monkeypatch):
    """**分块读取**：登记与自检都不许 `read_bytes()` 整段吞视频。"""
    if media["video"] is None:
        pytest.skip("ffmpeg 不可用：合成 MP4 不能生成")
    from aiforstudy.core import digest as digest_module

    seen: list[int] = []
    original = digest_module.digest_file

    def spy(path, *args, **kwargs):
        seen.append(Path(path).stat().st_size)
        return original(path, *args, **kwargs)

    from aiforstudy.delivery import resources as resources_module

    monkeypatch.setattr(resources_module, "digest_file", spy)

    # 更硬的证据：把 `Path.read_bytes` 换成炸弹 —— 只要实现整段吞过媒体文件就会炸。
    real_read_bytes = Path.read_bytes

    delivery_root = tmp_path / "delivery"

    def bomb(self):
        # 只对**交付目录里的媒体**开火：夹具自己准备源文件时读不算问题
        if str(self).startswith(str(delivery_root)) and self.suffix in {".mp4", ".png", ".vtt", ".txt"}:
            raise AssertionError(f"不许整段 read_bytes()：{self}")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", bomb)

    target = delivery_root
    manifest = collect_animation_resources(results=[build_result(media)], target_root=target,
                                           sources=_sources(media), book_id="mini-book",
                                           unit_id="CH01-S01")
    assert seen, "登记必须核对内容指纹"
    assert manifest.verify(target)["ok"] is True

    # 分块读取工具本身：块大小可控、按顺序拼回原文
    target_file = target / "media" / "scene.mp4"
    chunks = list(iter_chunks(target_file, chunk_bytes=64))
    assert len(chunks) > 1
    assert all(len(chunk) <= 64 for chunk in chunks)
    assert b"".join(chunks) == media["video"].read_bytes()
    assert DEFAULT_CHUNK_BYTES >= 64 * 1024, "默认分块要不小于 64 KiB，否则大视频会被拆成过多系统调用"


# ────────────────────────────────────────── 5 Range 解析（纯逻辑，HTTP 层复用）

def test_parse_range_header_closed_open_and_suffix():
    with pytest.raises(TypeError):
        parse_range_header(None)          # 调用方必须先判"有没有 Range"
    assert parse_range_header("") is None
    assert parse_range_header("items=0-1") is None          # 不是 bytes
    assert parse_range_header("bytes=abc") is None
    assert parse_range_header("bytes=0-9,20-29") is None    # 多范围 → 按已声明策略忽略
    assert parse_range_header("bytes=0-9") == (0, 9)
    assert parse_range_header("bytes=100-") == (100, None)
    assert parse_range_header("bytes=-50") == (-50, None)
    assert parse_range_header("bytes= 0 - 9 ") == (0, 9)


def test_range_resolution_covers_206_and_416():
    total = 1000
    assert range_for_response((0, 9), total) == (0, 9)
    assert range_for_response((100, None), total) == (100, 999)
    assert range_for_response((-50, None), total) == (950, 999)
    # 闭区间右端超出 → 夹到末尾（RFC 9110：satisfiable）
    assert range_for_response((990, 5000), total) == (990, 999)
    # 起点超出 → 416
    assert range_for_response((1000, None), total) is None
    assert range_for_response((2000, 3000), total) is None
    assert range_for_response((-1, None), total) == (999, 999)


def test_manifest_sample_and_hashes_are_recorded_as_evidence(tmp_path, media):
    """证据：manifest 样例（逐条 sha256／bytes／mime／relative_path）与盘上实测值。"""
    if media["video"] is None:
        pytest.skip("ffmpeg 不可用：合成 MP4 不能生成")
    target = tmp_path / "delivery"
    manifest = collect_animation_resources(results=[build_result(media)], target_root=target,
                                           sources=_sources(media), book_id="mini-book",
                                           unit_id="CH01-S01")
    lines = ["# T09 资源 manifest 样例（合成夹具；§5.3 AnimationResult.resources[]）", ""]
    lines.append("kind\trelative_path\tbytes\tmime\tsha256(清单)\tsha256(盘上实测)")
    for entry in manifest.entries:
        written = target / entry.relative_path
        lines.append("\t".join([entry.kind, entry.relative_path, str(entry.bytes), entry.mime,
                                entry.sha256, digest_file(written)]))
    verify = manifest.verify(target)
    lines += ["", f"verify.ok={verify['ok']} counts={verify['counts']}",
              f"inline_base64_bytes={manifest.inline_base64_bytes()}",
              f"request_ids={list(manifest.request_ids())}", "",
              "（合成夹具：MP4 由 ffmpeg 生成，PNG 由本文件手写，VTT／TXT 为内联文本。）"]
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "resource-manifest-sample.txt").write_text("\n".join(lines) + "\n",
                                                           encoding="utf-8")
    assert verify["ok"] is True
    rows = [line for line in lines if line.startswith(("mp4\t", "poster\t", "captions\t",
                                                      "transcript\t"))]
    assert len(rows) == len(manifest.entries) == 4
    for row in rows:                      # 每条都要**清单值 = 盘上实测值**
        columns = row.split("\t")
        assert columns[-1] == columns[-2] and columns[-1].startswith("sha256:")


def test_entries_for_unit_and_json_roundtrip(tmp_path, media):
    if media["video"] is None:
        pytest.skip("ffmpeg 不可用：合成 MP4 不能生成")
    target = tmp_path / "delivery"
    manifest = collect_animation_resources(results=[build_result(media)], target_root=target,
                                           sources=_sources(media), book_id="mini-book",
                                           unit_id="CH01-S01")
    entry = AnimationResource(kind="mp4", relative_path="media/scene.mp4",
                              sha256=manifest.entries[0].sha256, bytes=1,
                              mime="video/mp4", request_id="req-saddle-1")
    assert entry.kind == "mp4"
    dumped = manifest.as_dict()
    assert dumped["by_request"]["req-saddle-1"], "清单要能按 request_id 反查"
    again = AnimationManifest.from_dict(dumped)
    assert again.resources_for_unit("CH01-S01") == manifest.resources_for_unit("CH01-S01")
    assert json.dumps(dumped, ensure_ascii=False)


# ────────────────────────────────────────── 6 章页集成：真跑一次 build


def test_app_boot_mounts_the_player_for_the_selected_unit(tmp_path, media):
    """`assets/app.js` 真的把播放器挂上了：构造 D.animations → 出现播放器 DOM。"""
    import shutil
    import subprocess
    import sys

    node = shutil.which("node")
    if not node or not (ROOT / "node_modules" / "jsdom").is_dir():
        pytest.skip("播放器接线验收需要声明的 Node/jsdom 依赖")
    script = ROOT / "tests" / "ui" / "animations_wiring.test.cjs"
    result = subprocess.run([node, str(script)], cwd=ROOT, text=True,
                            capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
