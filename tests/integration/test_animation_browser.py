"""T10 真实浏览器验收：**在生产的 sandbox iframe 里真的把视频播一遍**。

计划 §T10 的判据是 V04："独立目录、断外网、真实 sandbox 浏览器播放／暂停／seek≤0.2s／
字幕／换节停止；0 外部请求"。本文件把它做成可复跑的红→绿：

| 形态 | 夹具 | 期望 |
|---|---|---|
| 红 | 截断的 MP4（仍被登记、仍 200） | 浏览器检查**必须失败**（解不了/取不到帧） |
| 红 | 内容不是 WebVTT 的 `.vtt` | 浏览器检查**必须失败**（track 读不到 cue） |
| 红 | 漏了 poster 但 `data.js` 仍引用 | 浏览器检查**必须失败**（4xx 资源请求） |
| 红 | 换节时**故意不 dispose** 播放器的 `app.js` | 浏览器检查**必须失败**（换节后仍在播） |
| 绿 | T07 冻结的真实 1280×720/32.16s 视频，经 当前 reader 投影与共享页面壳出页 | 两个视口全过 |

两条硬纪律：

* **缺浏览器 = blocked，不是 skip**。本机没有 Chromium/Node 时用例
  `pytest.fail("blocked: …")`，并且有一条用例专门验证
  `checks.offline.check_animation_playback` 在缺浏览器时返回 `blocked`
  （而不是拿静态 DOM 检查冒充通过）。
* **红是行为缺失**：4 条红用例失败在"浏览器里真的播不了/没停/没读到字幕"，
  不是 ImportError、不是 `ModuleNotFoundError`。

产物页是从**独立的导出目录**里取出来的：构建落在 `tmp_path/build-out`，
拷进运行目录后**把构建中间目录删掉**，浏览器只看导出目录里那份拷贝。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))
sys.path.insert(0, str(ROOT / "tests" / "support"))

from test_m8_ui import Client, make_workspace            # noqa: E402
from test_p3_control_workbench import saved_run          # noqa: E402
from aiforstudy.core.digest import digest_file           # noqa: E402
from aiforstudy.core.jsonio import write_json_atomic     # noqa: E402
from aiforstudy.ui.server import ControlServer           # noqa: E402

# Test evidence must not overwrite an earlier acceptance report.
EVIDENCE = None


@pytest.fixture(scope='module', autouse=True)
def evidence_directory(tmp_path_factory):
    global EVIDENCE
    configured = os.environ.get('AIFORSTUDY_ANIMATION_TEST_EVIDENCE')
    EVIDENCE = Path(configured) if configured else tmp_path_factory.mktemp('animation-browser-evidence')
    EVIDENCE.mkdir(parents=True, exist_ok=True)
T07_MEDIA = Path(os.environ.get("AIFORSTUDY_ANIMATION_REFERENCE_MEDIA", str(ROOT / "tests/fixtures/animation-reference")))
CHROME = ROOT / ".devtools" / "chrome" / "chrome-linux64" / "chrome"
DRIVER = ROOT / "tests" / "browser" / "animation_player.browser.cjs"
CHAPTER = "CH01"          # 合成教材夹具里真实存在的章（请求要能过范围校验）
UNIT = "CH01-S01"
OTHER_UNIT = "CH01-S02"


# ────────────────────────────────────────── 环境（缺则 blocked，不 skip）

def require_real_browser() -> str:
    """缺浏览器／缺真实产物：`pytest.fail`（blocked），**不 skip**。"""
    node = shutil.which("node")
    if not node:
        pytest.fail("blocked: 没有 Node，真实浏览器验收无法执行（不许用静态 DOM 检查替代）")
    if not CHROME.is_file():
        pytest.fail(f"blocked: 缺 Chromium（{CHROME}）；真实浏览器验收未执行")
    if not T07_MEDIA.is_dir():
        pytest.fail(f"blocked: 缺 T07 冻结产物（{T07_MEDIA}）；没有真实视频可验")
    return node


# ────────────────────────────────────────── 用真实产物出一章独立导出





def _register_export(store, chapter_root: Path) -> None:
    """把导出目录里**实际存在**的每个文件登记进交付清单与运行状态。

    服务端只给已登记、且内容指纹对得上的产物（T09 的口径）；登记在这里做一次，
    红夹具是先改文件再登记 —— 所以"坏文件"仍然会被 200 地送出去，
    红必须红在**浏览器解不了**，不能红在服务端 404（那是另一条判据，T09 已覆盖）。
    """
    files = sorted(path for path in chapter_root.rglob("*") if path.is_file())
    manifest = store.work / "products" / "delivery" / "delivery.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(manifest, {"payload": {"resources": {"resources": {
        str(path.relative_to(chapter_root)): {"source": str(path), "sha256": digest_file(path)}
        for path in files}}}})
    state = store.load_state()
    nodes = state.setdefault("nodes", {})
    nodes[f"render:{CHAPTER}"] = {
        "stage": "render", "scope_key": CHAPTER, "execution_state": "succeeded",
        "quality_state": "needs_review",
        "outputs": [{"kind": "render.html", "path": str(chapter_root / "index.html"),
                     "content_digest": digest_file(chapter_root / "index.html")}]}
    nodes["delivery"] = {
        "stage": "delivery", "scope_key": "scope", "execution_state": "succeeded",
        "quality_state": "accepted",
        "outputs": [{"kind": "delivery.scope", "path": str(manifest),
                     "content_digest": digest_file(manifest)}]}
    store.save_state(state)


def build_export(tmp_path: Path, *, mutate=None, bad_captions: str | None = None):
    """真实产物 → 章导出目录（独立、可服务）→ 登记。返回 (workspace, store, Client, chapter_root)。

    `mutate` 在**构建之后**改导出目录里的文件（用于"文件坏了但仍被登记、仍 200"的红夹具）；
    `bad_captions` 同时污染导出字幕字节和内联字幕文本，以验证浏览器确实读取字幕。
    技术装配拒绝坏字幕由独立契约测试覆盖；这里专测浏览器消费。
    """
    workspace = make_workspace(tmp_path)
    store, _engine = saved_run(workspace, chapters=[CHAPTER])

    build_out = tmp_path / "build-out"
    result = json.loads((T07_MEDIA / "animation_result.json").read_text(encoding="utf-8"))
    sources = T07_MEDIA
    if bad_captions is not None:
        sources = tmp_path / "t07-sources"
        shutil.copytree(T07_MEDIA, sources)
        bad = sources / "captions.vtt"
        bad.write_text(bad_captions, encoding="utf-8")
        # 声明的指纹也要跟着改：这份红夹具模拟的是"流水线**如实地**产出了一个
        # 不是 WebVTT 的字幕"，不是"声明与盘上不一致"（那条判据技术装配
        # 已经会拒绝，属于另一层）。
        for row in result["resources"]:
            if isinstance(row, dict) and row.get("kind") == "captions":
                row["sha256"] = digest_file(bad)
                row["bytes"] = bad.stat().st_size
    from support.reader_site import write_reader_fixture
    from test_content_v4_reader_contract import candidate,catalog
    from aiforstudy.blocks.render.diagnostics import _attach_diagnostic_animation
    # Browser tests use a real technical resource projection, never an approval.
    paths={r['relative_path']:sources/r['relative_path'] for r in result['resources']}
    payload=_attach_diagnostic_animation(build_out,unit=UNIT,book_id='synth-book',animation=result,sources=paths,profile='final')
    if bad_captions is not None:
        # Intentionally mutate the delivered caption payload after technical
        # assembly so this negative test still reaches the browser decoder.
        paths={r['relative_path']:T07_MEDIA/r['relative_path'] for r in result['resources']}
        original=json.loads((T07_MEDIA/'animation_result.json').read_text())
        payload=_attach_diagnostic_animation(build_out,unit=UNIT,book_id='synth-book',animation=original,sources=paths,profile='final')
        payload['captions_text']=bad_captions
        (build_out/payload['base']/payload['resources']['captions']['relative_path']).write_text(bad_captions)
    assert not payload['missing'],payload['notes']
    contents=[]
    for unit in (UNIT,OTHER_UNIT):
        c=candidate(); c['unit_id']=unit; c['title']=unit
        contents.append(c)
    write_reader_fixture(contents,catalog(),build_out,animations={UNIT:[payload]})

    chapter_root = store.work / "legacy" / "render" / CHAPTER
    chapter_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(build_out, chapter_root)
    # 导出目录是**独立**的：构建中间目录在这里删掉，
    # 浏览器能看到的只有导出目录里那份拷贝。
    shutil.rmtree(build_out)
    if mutate is not None:
        mutate(chapter_root)
    _register_export(store, chapter_root)

    control = ControlServer(workspace, port=0)
    httpd = control.make_server()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return workspace, store, Client(control), chapter_root, httpd


def driver_config(tmp_path: Path, client: Client, store, *, name: str, viewports=None) -> dict:
    out = EVIDENCE / f"driver-{name}"
    return {
        "chrome": str(CHROME),
        "base": client.base,
        "out": str(out),
        "unit": UNIT,
        "other_unit": OTHER_UNIT,
        "unit_url_hint": f"/{store.run_id}/{CHAPTER}/index.html",
        "viewports": viewports or [{"name": "w1280", "width": 1280, "height": 720}],
        "expect": {
            "duration": 32.163411, "width": 1280, "height": 720,
            "steps": [{"id": "s1", "start": 0.0}, {"id": "s2", "start": 8.0},
                      {"id": "s3", "start": 16.0}, {"id": "s4", "start": 24.0}],
        },
    }


def run_driver(node: str, config: dict, *, timeout: int = 300) -> tuple[int, dict]:
    config_path = EVIDENCE / (Path(config["out"]).name + ".config.json")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    result = subprocess.run([node, str(DRIVER), str(config_path)], cwd=ROOT,
                            text=True, capture_output=True, timeout=timeout)
    report_path = Path(config["out"]) / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    report["_driver_exit_code"] = result.returncode
    report["_driver_stdout"] = result.stdout[-4000:]
    report["_driver_stderr"] = result.stderr[-4000:]
    (EVIDENCE / f"driver-{Path(config['out']).name}.log").write_text(
        "$ node tests/browser/animation_player.browser.cjs " + str(config_path) + "\n"
        f"exit={result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n",
        encoding="utf-8")
    return result.returncode, report


# ────────────────────────────────────────── 红：四种坏形态必须被抓住

def _red_run(tmp_path, name, mutate, *, expect_fragment, bad_captions=None):
    node = require_real_browser()
    workspace, store, client, chapter_root, httpd = build_export(
        tmp_path, mutate=mutate, bad_captions=bad_captions)
    try:
        code, report = run_driver(node, driver_config(tmp_path, client, store, name=name))
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert code != 0, f"{name}：坏的播放形态居然通过了（退出码 0）\n" + json.dumps(
        {"failures": report.get("failures"), "viewports": report.get("viewports")},
        ensure_ascii=False)[:2000]
    blob = json.dumps(report, ensure_ascii=False)
    assert expect_fragment in blob, f"{name}：失败原因里没有 {expect_fragment}\n" + blob[:3000]
    return report


def _media_path(root, name):
    payload = json.loads((root / 'data/data.js').read_text().split('window.__DATA__ = ', 1)[1].strip().removesuffix(';'))
    return root / payload['animations'][UNIT][0]['base'] / name


def test_red_truncated_video_must_fail_not_pass(tmp_path):
    """红：MP4 被截断（仍登记、仍 200）→ 浏览器**必须**报解码/取帧失败。"""
    def mutate(chapter_root: Path):
        video = _media_path(chapter_root, "video.mp4")
        blob = video.read_bytes()
        video.write_bytes(blob[:20])
    report = _red_run(tmp_path, "red-truncated-video", mutate,
                      expect_fragment="loadedmetadata")
    assert report["_driver_exit_code"] == 1
    assert any("loadedmetadata" in failure for failure in report["failures"]), report["failures"]


def test_red_wrong_vtt_must_fail_not_pass(tmp_path):
    """红：构建输入的 `.vtt` 不是 WebVTT → 浏览器读不到中文 cue，必须报失败。"""
    report = _red_run(tmp_path, "red-wrong-vtt", None, bad_captions="这不是 WebVTT，只是一段普通文本。\n",
                      expect_fragment="字幕")
    assert any("字幕" in failure for failure in report["failures"]), report["failures"]
    # 坏字幕必须坏在**内容**上：这条资源本身没有被拒（不是 404、不是被安全校验挡掉）
    assert not any("captions.vtt" in str(row.get("url"))
                   for row in report["failed_requests"]), report["failed_requests"]


def test_red_missing_poster_must_fail_not_pass(tmp_path):
    """红：漏了 poster（`data.js` 仍引用）→ 必须报"这条资源取不到"，不能因为"视频能放"就算过。

    Chrome 对 404 的图片可能报 `net::ERR_BLOCKED_BY_ORB` 而不是状态码 404（都取决于
    响应体），所以判据是**那条 poster 请求失败了**，而不是某个具体错误字符串。
    """
    def mutate(chapter_root: Path):
        (_media_path(chapter_root, "poster.png")).unlink()
    report = _red_run(tmp_path, "red-missing-poster", mutate, expect_fragment="poster.png")
    assert any("poster.png" in failure for failure in report["failures"]), report["failures"]
    assert not any(row.get("status") == 200 and "poster.png" in str(row.get("url"))
                   for row in report.get("responses", [])), "缺的 poster 不该有一份 200"


def test_red_section_change_still_playing_must_fail_not_pass(tmp_path):
    """红：换节后旧播放器**还在 DOM 里还在播** → 必须报失败。

    只把 `disposeAnimations()` 去掉是**造不出**这个故障的：`renderUnit` 会把
    `el.main.innerHTML` 整个换掉，视频节点一离开文档，Chrome 自己就把它暂停了
    （实测：去掉 dispose 也仍然 `stillPlaying=0`）。真实故障形态是"播放器挂在正文
    之外、换节时摘不掉"——所以夹具把挂载点挪到 `document.body` 并去掉 dispose。
    """
    def mutate(chapter_root: Path):
        app = chapter_root / "assets" / "app.js"
        text = app.read_text(encoding="utf-8")
        assert "disposeAnimations();" in text
        text = text.replace("disposeAnimations();", "/* 红夹具：换节不销毁播放器 */ 0;")
        old_host = ('var host = root.querySelector("[data-animation-host]") || '
                    'root.querySelector(".unit");')
        assert text.count(old_host) == 1, "真实播放器挂载点变了，红夹具需要同步"
        text = text.replace(old_host, "var host = document.body; /* 红夹具：挂在正文之外 */")
        app.write_text(text, encoding="utf-8")
    report = _red_run(tmp_path, "red-section-change-still-playing", mutate,
                      expect_fragment="换节后上一节还在播")
    assert any("换节后上一节还在播" in failure for failure in report["failures"]), report["failures"]


# ────────────────────────────────────────── 绿：真实产物 + 生产 sandbox，两个视口

@pytest.fixture(scope="module")
def green(tmp_path_factory):
    node = require_real_browser()
    tmp_path = tmp_path_factory.mktemp("t10-green")
    workspace, store, client, chapter_root, httpd = build_export(tmp_path)
    try:
        yield node, client, store, chapter_root
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_green_real_export_plays_pause_seeks_and_stops_on_unit_change(green):
    """V04 主判据：独立导出目录 + 断外网 + 生产 sandbox iframe，全过。"""
    node, client, store, chapter_root = green
    config = driver_config(None, client, store, name="green", viewports=[
        {"name": "w1280", "width": 1280, "height": 720},
        {"name": "w390", "width": 390, "height": 844},
    ])
    code, report = run_driver(node, config, timeout=480)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "browser-report-green.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    assert code == 0, json.dumps(
        {"failures": report.get("failures"), "viewports": report.get("viewports"),
         "js_errors": report.get("js_errors")}, ensure_ascii=False)[:4000]
    assert report["ok"] is True
    assert report["external_request_count"] == 0
    assert not report["js_errors"]
    # 两个视口都跑了，且每个视口都有真帧 + 截图
    assert [row["name"] for row in report["viewports"]] == ["w1280", "w390"]
    for viewport in report["viewports"]:
        assert viewport["failures"] == []
        assert len(viewport["checks"]["frames"]) == 4
        assert all(row["presented"] for row in viewport["checks"]["frames"]), viewport["name"]
        assert len(viewport["shots"]) == 4
        captions = viewport["checks"]["captions"]
        assert captions["ok"] and captions["hasChinese"], captions
        # 不透明 origin 下 `<track src>` 不能指 `.vtt` 文件（Chrome 会以
        # "Unsafe attempt to load URL … Domains must match" 拒绝，见证据
        # `track-blocker-isolation.json`）→ 播放器必须改用手造的 blob URL。
        assert str(captions["before"]["src"]).startswith("blob:"), captions["before"]
        assert captions["off"]["mode"] == "disabled" and captions["on"]["mode"] == "showing", captions
        layout = viewport["checks"]["layout"]
        assert layout["transcript_visible"] and layout["transcript_text_length"] > 40
        assert layout["visible_steps"] == 4, layout
        assert layout["step_buttons"] == 4 and layout["transcript_step_buttons"] == 4, layout
        # 「所选单元恰好 1」：只有 UNIT 挂上了播放器，换节那一步走的是另一个单元
        assert viewport["checks"]["sectionChange"]["away"]["players"] == 0
    # 每个视口的截图都要留在证据里
    for viewport in report["viewports"]:
        for shot in viewport["shots"]:
            assert Path(shot).is_file() and Path(shot).stat().st_size > 1000
    # 播放器载荷里没有任何管理 API／外部地址
    manifest = (chapter_root / "data" / "data.js").read_text(encoding="utf-8")
    assert "http://" not in manifest and "https://" not in manifest
    assert "/api/" not in manifest


def test_captions_text_in_the_payload_equals_the_shipped_vtt(green):
    """字幕保真：浏览器在沙箱里显示的那份文本 == 交付包里的 `captions.vtt`。

    （播放器在不透明 origin 下不用网络取 `.vtt`，而是用载荷里的 `captions_text`
    造 blob URL；这条断的就是"载荷那一份没被改写"。）
    """
    _node, _client, _store, chapter_root = green
    data_js = (chapter_root / "data" / "data.js").read_text(encoding="utf-8")
    payload = json.loads(data_js.split("window.__DATA__ = ", 1)[1].rstrip().rstrip(";"))
    shipped = (_media_path(chapter_root, "captions.vtt")).read_text(encoding="utf-8")
    assert payload["animations"][UNIT][0]["captions_text"] == shipped
    assert shipped.startswith("WEBVTT")
    # 盘上那份真文件仍然在，并且 hash 与清单一致（不是"改了文件哄浏览器"）
    from aiforstudy.core.digest import digest_file
    manifest = json.loads((chapter_root / ".." / ".." / ".." / "products" / "delivery"
                           / "delivery.json").read_text(encoding="utf-8"))
    registered = manifest["payload"]["resources"]["resources"][_media_path(chapter_root, "captions.vtt").relative_to(chapter_root).as_posix()]
    assert registered["sha256"] == digest_file(_media_path(chapter_root, "captions.vtt"))


def test_green_export_has_exactly_one_animated_unit(green):
    """「所选单元恰好 1」：`data.js` 的 `animations` 只有一个键，但章里有两节。"""
    _node, _client, _store, chapter_root = green
    data_js = (chapter_root / "data" / "data.js").read_text(encoding="utf-8")
    payload = json.loads(data_js.split("window.__DATA__ = ", 1)[1].rstrip().rstrip(";"))
    assert list(payload["animations"]) == [UNIT]
    assert payload["scope"] == [UNIT, OTHER_UNIT]
    entry = payload["animations"][UNIT][0]
    assert entry["missing"] == [] and entry["base"].startswith("assets/animations/")
    assert (_media_path(chapter_root, "video.mp4")).stat().st_size == 1391656


def test_green_export_is_served_with_ranges_outside_the_textbook_tree(green):
    """独立目录 + 真实 HTTP 语义：视频按 Range 分块给出去，且不依赖教材源目录。"""
    _node, client, store, chapter_root = green
    import urllib.request
    url = f"{client.base}/artifacts/synth-book/{store.run_id}/{CHAPTER}/{_media_path(chapter_root, "video.mp4").relative_to(chapter_root).as_posix()}"
    request = urllib.request.Request(url, headers={"Range": "bytes=0-1023"})
    with urllib.request.urlopen(request, timeout=30) as response:
        assert response.status == 206
        assert response.headers["Content-Range"] == "bytes 0-1023/1391656"
        assert response.headers["Content-Type"] == "video/mp4"
        assert len(response.read()) == 1024
    # 导出目录必须在工作区临时目录里，而不是教材源目录
    assert "AIforStudy/data" not in str(chapter_root)
    assert not str(chapter_root.resolve()).startswith(
        str((ROOT / "data" / "books").resolve()))
    assert not any(path.is_symlink() for path in chapter_root.rglob("*"))


def test_offline_playback_check_is_green_on_the_real_export_and_red_on_a_broken_one(tmp_path):
    """`checks.offline.check_animation_playback`：好导出 `accepted`，坏视频 `failed`。

    这是"浏览器实际播放检查"进入离线门禁的那一层（V03 的 ffmpeg 解码检查管不了它）：
    一个 HTTP 200、非空、甚至能拿到 poster 的**截断视频**必须被判失败。
    """
    node = require_real_browser()
    from aiforstudy.checks.offline import check_animation_playback

    good_root = tmp_path / "good"
    good_root.mkdir()
    _ws, _store, _client, chapter_root, httpd = build_export(good_root)
    try:
        check = check_animation_playback(chapter_root, node=node, workspace_root=tmp_path)
    finally:
        httpd.shutdown()
        httpd.server_close()
    (EVIDENCE / "offline-playback-good.json").write_text(
        json.dumps({"status": check.status, "detail": check.detail, "evidence": check.evidence},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    assert check.status == "accepted", check
    assert check.evidence["animation"]["play"]["width"] == 1280

    bad_root = tmp_path / "bad"
    bad_root.mkdir()

    def truncate(root: Path):
        video = _media_path(root, "video.mp4")
        video.write_bytes(video.read_bytes()[:20])

    _ws2, _store2, _client2, bad_chapter, httpd2 = build_export(bad_root, mutate=truncate)
    try:
        failed = check_animation_playback(bad_chapter, node=node, workspace_root=tmp_path)
    finally:
        httpd2.shutdown()
        httpd2.server_close()
    (EVIDENCE / "offline-playback-corrupt.json").write_text(
        json.dumps({"status": failed.status, "detail": failed.detail,
                    "evidence": failed.evidence}, ensure_ascii=False, indent=2), encoding="utf-8")
    assert failed.status == "failed", failed
    assert "播放" in failed.detail or "元数据" in failed.detail


def test_missing_browser_reports_blocked_not_a_static_pass(tmp_path, monkeypatch):
    """缺浏览器时必须 `blocked`（不是 accepted、不是 skip）。"""
    from aiforstudy.checks import offline
    monkeypatch.setattr(offline, "_browser_executable", lambda workspace_root=None: None)
    chapter = tmp_path / "page"
    chapter.mkdir()
    (chapter / "index.html").write_text("<!doctype html><title>x</title>", encoding="utf-8")
    check = offline.check_animation_playback(chapter, node=shutil.which("node"),
                                             workspace_root=tmp_path)
    assert check.status == "blocked", check
    assert not check.ok
