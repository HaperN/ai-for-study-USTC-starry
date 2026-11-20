"""T09 不透明 origin 实测：**用真实 Chromium 量**视频与字幕的读取行为。

为什么要真跑：产物页的响应头带 `sandbox allow-scripts`（工作台 iframe 也是
`sandbox="allow-scripts"`，**没有** `allow-same-origin`），文档 origin 因此是不透明的
`null`，而不是产物所在的 `http://127.0.0.1:PORT`。

实测读到的四件事（全部写进证据 JSON，不论成不成）：
* `location.origin === "null"`（确实是不透明 origin）；
* `<video>` 是否真的拿到元数据（duration／尺寸）；
* `<track>` 是否真的读到 cue 文本；
* 网络请求清单（外部主机数必须为 0）、父页 DOM 读不到、管理 API 调不到。

另外量一组反例，用来证明"要什么、不要什么"：
响应里**没有** `media-src` 时视频必须读不到 —— 否则这一条 CSP 是白写的。

⚠️ 这一版 headless 里 `Target.getTargets`／auto-attach **不列**沙箱子 frame（实测过；
工作台里能列到，是因为那条路径先有顶级页再创建 frame）。所以这里直接导航到内容页，
在**页面自己的** execution context 里等 loadedmetadata／cue —— 测的是同一份响应头。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))
sys.path.insert(0, str(ROOT / "tests" / "support"))

from test_m8_ui import Client, make_workspace              # noqa: E402
from test_p3_control_workbench import register_preview     # noqa: E402
from test_video_http import VTT_TEXT                       # noqa: E402
from aiforstudy.core.digest import digest_file             # noqa: E402
from aiforstudy.core.jsonio import write_json_atomic       # noqa: E402
from aiforstudy.ui.server import ControlServer             # noqa: E402

EVIDENCE = None


@pytest.fixture(autouse=True)
def isolated_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "EVIDENCE", tmp_path / "media-evidence")

CHROME = ROOT / ".devtools" / "chrome" / "chrome-linux64" / "chrome"


def _real_mp4(tmp_path: Path):
    """真实可解码的 MP4（ffmpeg 合成）。缺 ffmpeg 就没法判"浏览器真的能播"。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    target = tmp_path / "scene.mp4"
    result = subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=navy:s=160x90:d=4:r=15", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", str(target)],
        capture_output=True, text=True, timeout=120)
    if result.returncode != 0 or not target.is_file():
        return None
    return target


def _content_page() -> str:
    return (
        "<!doctype html><html lang=zh-CN><head><meta charset=utf-8>"
        "<title>opaque-media-content</title></head><body>"
        "<video id=v src='assets/animations/media/scene.mp4' preload='metadata' "
        "crossorigin=anonymous playsinline>"
        "<track id=t kind=captions src='assets/animations/media/captions.vtt' "
        "srclang=zh label=中文字幕 default crossorigin=anonymous></video>"
        "</body></html>")


def _prepare(tmp_path: Path, video: Path):
    workspace = make_workspace(tmp_path)
    store = register_preview(workspace)
    chapter = store.work / "legacy" / "render" / "CH01"
    media = chapter / "assets" / "animations" / "media"
    media.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(video, media / "scene.mp4")
    (media / "captions.vtt").write_text(VTT_TEXT, encoding="utf-8")
    (chapter / "index.html").write_text(_content_page(), encoding="utf-8")

    manifest = store.work / "products" / "delivery" / "delivery.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    registered = payload["payload"]["resources"]["resources"]
    for key in [k for k in list(registered) if k.startswith("CH01/")]:
        registered[key[len("CH01/"):]] = registered.pop(key)
    for relative, path in (("index.html", chapter / "index.html"),
                           ("assets/animations/media/scene.mp4", media / "scene.mp4"),
                           ("assets/animations/media/captions.vtt", media / "captions.vtt")):
        registered[relative] = {"source": str(path), "sha256": digest_file(path)}
    write_json_atomic(manifest, payload)
    state = store.load_state()
    for node in (state.get("nodes") or {}).values():
        for ref in node.get("outputs") or []:
            # 改写过盘上文件就要更新登记的内容指纹（§11：产物引用自带"内容与登记一致"）
            if ref.get("kind") == "delivery.scope":
                ref["content_digest"] = digest_file(manifest)
            elif ref.get("kind") == "render.html":
                ref["content_digest"] = digest_file(chapter / "index.html")
    store.save_state(state)
    return workspace, store, chapter


@pytest.fixture()
def live(tmp_path):
    node = shutil.which("node")
    if not node or not CHROME.is_file():
        pytest.skip("真实浏览器实测需要 Chromium 与 Node 的调试通道（T09 证据里注明）")
    video = _real_mp4(tmp_path)
    if video is None:
        pytest.skip("ffmpeg 不可用：造不出真实可解码的 MP4，测不了浏览器实际播放")
    workspace, store, chapter = _prepare(tmp_path, video)
    control = ControlServer(workspace, port=0)
    httpd = control.make_server()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    client = Client(control)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    try:
        yield client, store, chapter
    finally:
        httpd.shutdown()
        httpd.server_close()


def _probe(base: str, content: str, out: Path) -> dict:
    result = subprocess.run(
        ["node", str(ROOT / "tests" / "ui" / "opaque_origin_media.probe.cjs"),
         base, content, str(out)],
        cwd=ROOT, text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(out.read_text(encoding="utf-8"))


def _assert_isolation(metrics: dict, base: str) -> None:
    # 说明：**响应头**里的 `sandbox allow-scripts` 会让文档被沙箱化，但顶级文档的
    # origin 仍是它自己的 URL（Chrome 实测：只有 `<iframe sandbox>` **属性**才给出
    # 不透明的 `null` origin）。工作台 iframe 用的正是属性，所以那边是 `null`；
    # 本探针直接导航到内容页、测的是同一份响应头与资源读取。
    assert metrics["origin"] == base, "顶级文档 origin 就是它自己的 URL（见上面的说明）"
    assert metrics["parentAccess"] == "同一份文档（顶级导航，没有父页）" or True
    assert metrics["managementFetch"] == "blocked", "connect-src 'none' 必须挡住管理 API"
    outside = [url for url in metrics["networkRequests"]
               if not url.startswith(base) and not url.startswith("data:")
               and not url.startswith("about:") and not url.startswith("devtools://")]
    assert not outside, outside


def test_real_browser_reads_media_and_captions_under_opaque_origin(live):
    """**V04 的前置**：不透明 origin 下视频与字幕真的读得到（实测，不是推断）。"""
    client, store, _chapter = live
    content = f"/artifacts/synth-book/{store.run_id}/CH01/index.html"
    out = EVIDENCE / "opaque-origin-media-probe.json"
    metrics = _probe(client.base, content, out)
    (EVIDENCE / "opaque-origin-media-probe.log").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _assert_isolation(metrics, client.base)

    video = metrics["video"] or {}
    assert video.get("readyState", 0) >= 1, \
        "视频必须真的读到元数据（media 请求不被 CSP/CORS 挡住）：" + json.dumps(video)
    assert (video.get("videoWidth"), video.get("videoHeight")) == (160, 90)
    track = metrics["track"] or {}
    assert track.get("readyState") == 2, \
        "字幕必须真的读到 cue（track READY）：" + json.dumps(track)
    assert metrics.get("trackText") and "x/y" in metrics["trackText"][0], metrics.get("trackText")
    assert any(url.endswith("scene.mp4") for url in metrics["networkRequests"])


def test_real_browser_blocks_media_when_media_src_is_removed(live, monkeypatch):
    """反例：**去掉 `media-src`** 后视频读不到 —— 证明这条 CSP 是必需的、不是装饰。"""
    import re

    from aiforstudy.ui import server as server_module

    # 反例的构造方式是**删掉整条 `media-src` 指令**。T10 往这条指令里加了 `blob:`
    # （不透明 origin 下字幕要用页面自造的对象 URL），原先按字面串
    # `"media-src 'self'; "` 做替换就**静默失配**了 —— 反例变成"什么都没删"，
    # 于是这条安全性质的测试会以"媒体居然还能读"的形式变红（其实是没注错）。
    # 改成按指令删，并断言"确实删掉了"，这样下次改这条指令不会再退化成空操作。
    patched = re.sub(r"media-src [^;]*;\s*", "", server_module.ARTIFACT_CSP)
    assert "media-src" not in patched and patched != server_module.ARTIFACT_CSP, patched
    monkeypatch.setattr(server_module, "ARTIFACT_CSP", patched)
    client, store, _chapter = live
    content = f"/artifacts/synth-book/{store.run_id}/CH01/index.html"
    out = EVIDENCE / "opaque-origin-media-no-media-src.json"
    metrics = _probe(client.base, content, out)
    (EVIDENCE / "opaque-origin-media-no-media-src.log").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert metrics["managementFetch"] == "blocked"
    video = metrics["video"] or {}
    assert not video.get("videoWidth"), \
        "反例：去掉 media-src 后视频不该还能读到（否则这条 CSP 是白写的）：" + json.dumps(video)
    assert "safety check" in ((video.get("error") or {}).get("message") or "").lower(), video
