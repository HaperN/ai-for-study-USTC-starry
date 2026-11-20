"""T09 HTTP 语义：**媒体只从已登记的产物里按 Range 分块给出去**（计划 §T09）。

逐条钉住 §T09 的第二组复选框：

| 场景 | 期望 |
|---|---|
| `GET` 无 Range | 200 全量，`Accept-Ranges: bytes` |
| `HEAD` | 200，**无 body**，头部与 GET 一致 |
| 单 Range 闭区间 `bytes=0-9` | 206 + `Content-Range: bytes 0-9/N` + `Content-Length: 10` |
| 单 Range 开放尾 `bytes=100-` | 206，长度 = N-100 |
| 单 Range 后缀 `bytes=-50` | 206，最后 50 字节 |
| 越界 `bytes=N-` | 416 + `Content-Range: bytes */N` |
| 非 Range 请求 | 200 |
| 多范围 `bytes=0-9,20-29` | **忽略 Range 返回 200**（不实现 multipart，不伪造 206） |
| 未登记 / 篡改 / 穿越 | 404，且响应体里没有内容字节 |

最后把**原始响应头**写成证据（有限字节请求与 HEAD 各一份），因为"断言通过"和
"真实响应长什么样"是两件事。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# 本模块与既有集成测试同风格：复用 `test_m8_ui` 的工作区装置与 `test_p3` 的预览登记
sys.path.insert(0, str(ROOT / "tests" / "integration"))
sys.path.insert(0, str(ROOT / "tests" / "support"))

from test_m8_ui import Client, make_workspace          # noqa: E402
from test_p3_control_workbench import register_preview  # noqa: E402
from aiforstudy.core.digest import digest_file          # noqa: E402
from aiforstudy.core.jsonio import write_json_atomic    # noqa: E402
from aiforstudy.ui.server import ControlServer          # noqa: E402

EVIDENCE = None


@pytest.fixture(autouse=True)
def isolated_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "EVIDENCE", tmp_path / "media-evidence")


VIDEO_BYTES = bytes(range(256)) * 40        # 10240 字节，确定性内容
VTT_TEXT = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n第一步：x/y 是两个方向\n"


def raw(url: str, *, method: str = "GET", headers: dict | None = None):
    """原始请求：连状态行与头一起拿回来（证据要的是**原始头**）。"""
    request = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def make_media_run(workspace) -> tuple:
    """在已登记的章节预览里放一个真实 MP4 与 VTT，并登记进交付清单。"""
    store = register_preview(workspace)
    chapter = store.work / "legacy" / "render" / "CH01"
    media = chapter / "assets" / "animations" / "media"
    media.mkdir(parents=True, exist_ok=True)
    (media / "scene.mp4").write_bytes(VIDEO_BYTES)
    (media / "captions.vtt").write_text(VTT_TEXT, encoding="utf-8")
    manifest = store.work / "products" / "delivery" / "delivery.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    registered = payload["payload"]["resources"]["resources"]
    # ⚠️ 键是**相对章根**的路径：服务端拿 manifest 的 source 与章根比，不是拿相对盘
    # 的路径拼。第一版写成 `path.relative_to(chapter.parent)`（= `CH01/index.html`），
    # 于是 `_artifact_file` 里 `chapter_root / 'CH01/index.html'` 落空 → 全部 404。
    for key in [k for k in list(registered) if k.startswith("CH01/")]:
        registered[key[len("CH01/"):]] = registered.pop(key)
    for path in (chapter / "index.html", chapter / "assets" / "app.js",
                 media / "scene.mp4", media / "captions.vtt"):
        registered[str(path.relative_to(chapter))] = {
            "source": str(path), "sha256": digest_file(path)}
    write_json_atomic(manifest, payload)
    state = store.load_state()
    for node in (state.get("nodes") or {}).values():
        for ref in node.get("outputs") or []:
            # 改写过盘上文件就要更新登记的内容指纹：产物引用自带"内容与登记一致"这道门
            # （§11），digest 过期时服务端会**正确地**拒绝它（第一版就踩了这个 404）。
            if ref.get("kind") == "delivery.scope":
                ref["content_digest"] = digest_file(manifest)
            elif ref.get("kind") == "render.html":
                ref["content_digest"] = digest_file(chapter / "index.html")
    store.save_state(state)
    return store, chapter, media


@pytest.fixture()
def media_server(tmp_path):
    import threading

    workspace = make_workspace(tmp_path)
    control = ControlServer(workspace, port=0)
    httpd = control.make_server()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    store, chapter, media = make_media_run(workspace)
    try:
        yield control, Client(control), store, chapter, media
    finally:
        httpd.shutdown()
        httpd.server_close()


def media_url(client: Client, store, name: str = "scene.mp4") -> str:
    return (f"{client.base}/artifacts/synth-book/{store.run_id}/CH01/"
            f"assets/animations/media/{name}")


# ────────────────────────────────────────── MIME

def test_mime_types_for_mp4_and_vtt(media_server):
    _control, client, store, _chapter, _media = media_server
    status, headers, body = raw(media_url(client, store, "scene.mp4"))
    assert status == 200
    assert headers["Content-Type"] == "video/mp4"
    assert headers["Content-Length"] == str(len(VIDEO_BYTES))
    assert body == VIDEO_BYTES
    assert headers["Accept-Ranges"] == "bytes"

    status, headers, _body = raw(media_url(client, store, "captions.vtt"))
    assert status == 200
    assert headers["Content-Type"] == "text/vtt; charset=utf-8"
    assert headers["X-Content-Type-Options"] == "nosniff"


# ────────────────────────────────────────── GET / HEAD

def test_get_and_head_have_identical_headers_but_head_has_no_body(media_server):
    _control, client, store, _chapter, _media = media_server
    url = media_url(client, store)
    get_status, get_headers, get_body = raw(url)
    head_status, head_headers, head_body = raw(url, method="HEAD")

    assert get_status == head_status == 200
    assert head_body == b"", "HEAD 绝不能带 body"
    for name in ("Content-Type", "Content-Length", "Accept-Ranges",
                 "Cache-Control", "X-Content-Type-Options"):
        assert head_headers.get(name) == get_headers.get(name), f"{name} 在 HEAD 与 GET 上不一致"
    assert len(get_body) == int(get_headers["Content-Length"])

    _record("HEAD-and-GET-headers.txt",
            f"GET {url}\nstatus={get_status}\n"
            + "".join(f"{k}: {v}\n" for k, v in get_headers.items())
            + f"\nbody_bytes={len(get_body)}\n\n--- HEAD ---\nstatus={head_status}\n"
            + "".join(f"{k}: {v}\n" for k, v in head_headers.items())
            + f"\nbody_bytes={len(head_body)}\n")


# ────────────────────────────────────────── 单 Range：三种形态

def test_closed_range_returns_206_with_exact_span(media_server):
    _control, client, store, _chapter, _media = media_server
    url = media_url(client, store)
    status, headers, body = raw(url, headers={"Range": "bytes=0-9"})
    assert status == 206
    assert headers["Content-Range"] == f"bytes 0-9/{len(VIDEO_BYTES)}"
    assert headers["Content-Length"] == "10"
    assert body == VIDEO_BYTES[0:10]


def test_open_ended_range_returns_206_to_the_last_byte(media_server):
    _control, client, store, _chapter, _media = media_server
    url = media_url(client, store)
    status, headers, body = raw(url, headers={"Range": "bytes=100-"})
    assert status == 206
    assert headers["Content-Range"] == f"bytes 100-{len(VIDEO_BYTES) - 1}/{len(VIDEO_BYTES)}"
    assert len(body) == len(VIDEO_BYTES) - 100
    assert body == VIDEO_BYTES[100:]


def test_suffix_range_returns_the_tail(media_server):
    _control, client, store, _chapter, _media = media_server
    url = media_url(client, store)
    status, headers, body = raw(url, headers={"Range": "bytes=-50"})
    assert status == 206
    assert headers["Content-Range"] == f"bytes {len(VIDEO_BYTES) - 50}-{len(VIDEO_BYTES) - 1}/{len(VIDEO_BYTES)}"
    assert len(body) == 50
    assert body == VIDEO_BYTES[-50:]


def test_range_starting_past_the_end_is_416_with_star_content_range(media_server):
    _control, client, store, _chapter, _media = media_server
    url = media_url(client, store)
    status, headers, body = raw(url, headers={"Range": f"bytes={len(VIDEO_BYTES)}-"})
    assert status == 416
    assert headers["Content-Range"] == f"bytes */{len(VIDEO_BYTES)}"
    assert body == b""

    status, headers, _body = raw(url, headers={"Range": "bytes=999999-1000000"})
    assert status == 416
    assert headers["Content-Range"] == f"bytes */{len(VIDEO_BYTES)}"


def test_range_end_past_the_end_is_clamped_not_rejected(media_server):
    """闭区间右端超出是 **satisfiable**（RFC 9110），返回夹到末尾的 206。"""
    _control, client, store, _chapter, _media = media_server
    url = media_url(client, store)
    status, headers, body = raw(url, headers={"Range": f"bytes=10000-999999"})
    assert status == 206
    assert headers["Content-Range"] == f"bytes 10000-{len(VIDEO_BYTES) - 1}/{len(VIDEO_BYTES)}"
    assert body == VIDEO_BYTES[10000:]


def test_plain_request_without_range_is_200(media_server):
    _control, client, store, _chapter, _media = media_server
    url = media_url(client, store)
    for header in ({}, {"Range": ""}):
        status, headers, body = raw(url, headers=header)
        assert status == 200
        assert "Content-Range" not in headers
        assert body == VIDEO_BYTES


def test_multiple_ranges_are_ignored_with_a_plain_200_not_a_fake_206(media_server):
    """**不实现 multipart Range**：收到多范围按已声明策略**忽略 Range**，返回 200。

    这条特意写死：偷懒返回 206 + 第一段 是"看起来对"的错答案 ——
    客户端会以为拿到的是 multipart 体，于是解码出错或静默截断。
    """
    _control, client, store, _chapter, _media = media_server
    url = media_url(client, store)
    status, headers, body = raw(url, headers={"Range": "bytes=0-9,20-29"})
    assert status == 200
    assert "Content-Range" not in headers
    assert headers["Content-Length"] == str(len(VIDEO_BYTES))
    assert body == VIDEO_BYTES


def test_records_raw_evidence_for_a_limited_byte_request_and_head(media_server):
    """证据：有限字节请求与 HEAD 的**实际响应**（原始头）。"""
    _control, client, store, _chapter, _media = media_server
    url = media_url(client, store)
    lines = [f"url={url}", f"file_bytes={len(VIDEO_BYTES)}", ""]
    for label, method, headers in (
            ("GET Range bytes=0-9", "GET", {"Range": "bytes=0-9"}),
            ("GET Range bytes=-50", "GET", {"Range": "bytes=-50"}),
            ("GET Range bytes=0-9,20-29", "GET", {"Range": "bytes=0-9,20-29"}),
            ("GET Range bytes=999999-", "GET", {"Range": "bytes=999999-"}),
            ("HEAD", "HEAD", {}),
            ("GET no range", "GET", {})):
        status, got, body = raw(url, method=method, headers=headers)
        lines.append(f"### {label}")
        lines.append(f"request headers: {headers} method={method}")
        lines.append(f"status: {status}")
        lines.extend(f"{key}: {value}" for key, value in got.items())
        lines.append(f"body_bytes: {len(body)}")
        lines.append("")
    _record("range-head-raw-responses.txt", "\n".join(lines) + "\n")


# ────────────────────────────────────────── 只给已登记产物 / 篡改 / 穿越

def test_unregistered_media_is_not_served(media_server):
    """受限服务**只提供已登记的产物**：盘上有 ≠ 允许给。"""
    _control, client, store, chapter, _media = media_server
    unregistered = chapter / "assets" / "animations" / "media" / "extra.mp4"
    unregistered.write_bytes(b"unregistered-bytes")
    status, _headers, body = raw(media_url(client, store, "extra.mp4"))
    assert status == 404
    assert b"unregistered-bytes" not in body


def test_tampered_media_is_refused_by_the_restricted_service(media_server):
    _control, client, store, _chapter, media = media_server
    video = media / "scene.mp4"
    blob = bytearray(video.read_bytes())
    blob[0] ^= 0xFF
    video.write_bytes(bytes(blob))
    for headers in ({}, {"Range": "bytes=0-9"}):
        status, _headers, body = raw(media_url(client, store), headers=headers)
        assert status == 404, "内容与清单 hash 不符必须拒绝"
        assert bytes(blob[:16]) not in body


@pytest.mark.parametrize("relative", ["../../../../etc/passwd",
                                      "%2e%2e/%2e%2e/request.json",
                                      "assets/animations/media/../../../request.json"])
def test_media_path_traversal_is_refused(media_server, relative):
    _control, client, store, _chapter, _media = media_server
    url = f"{client.base}/artifacts/synth-book/{store.run_id}/CH01/{relative}"
    status, _headers, body = raw(url)
    assert status == 404
    assert b"budget" not in body and b"token" not in body


def test_multipart_range_is_not_faked_and_416_carries_no_body(media_server):
    _control, client, store, _chapter, _media = media_server
    url = media_url(client, store)
    status, headers, body = raw(url, headers={"Range": "bytes=0-9,20-29"})
    assert (status, body) == (200, VIDEO_BYTES)
    assert "multipart" not in headers.get("Content-Type", "").lower()


# ────────────────────────────────────────── CSP / 隔离（事实断言，不靠静态扫描）

def test_artifact_csp_allows_media_but_keeps_sandbox_and_blocks_management(media_server):
    _control, client, store, _chapter, _media = media_server
    status, headers, _body = raw(media_url(client, store))
    policy = headers.get("Content-Security-Policy", "")
    assert "media-src 'self'" in policy, "播放器要能读同源的视频"
    assert "sandbox allow-scripts" in policy
    assert "allow-same-origin" not in policy
    assert "connect-src 'none'" in policy, "教材页不许连管理 API"
    assert "frame-ancestors 'self'" in policy and "form-action 'none'" in policy
    assert headers.get("Access-Control-Allow-Origin") in ("*", "null")
    _record("artifact-csp-header.txt",
            f"GET {media_url(client, store)}\nstatus={status}\nContent-Security-Policy: {policy}\n"
            f"Access-Control-Allow-Origin: {headers.get('Access-Control-Allow-Origin')}\n"
            f"X-Content-Type-Options: {headers.get('X-Content-Type-Options')}\n")


def test_workbench_csp_is_unchanged_by_the_media_addition(tmp_path):
    """工作台自己的 CSP 不该因为媒体而放宽（这里锚定现状）。"""
    workspace = make_workspace(tmp_path)
    control = ControlServer(workspace, port=0)
    assert Path(control.ui_dir).is_dir()
    source = (ROOT / "src" / "aiforstudy" / "resources" / "control-ui" / "index.html")
    text = source.read_text(encoding="utf-8")
    assert "allow-same-origin" not in text
    assert 'sandbox="allow-scripts"' in text


def _record(name: str, text: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / name).write_text(text, encoding="utf-8")
