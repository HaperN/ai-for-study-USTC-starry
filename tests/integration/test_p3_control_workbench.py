"""P3 HTTP contracts: durable requests, isolated previews, and honest run state."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from test_m8_ui import Client, make_workspace, server, wait_status
from aiforstudy.core.digest import digest_file
from aiforstudy.core.jsonio import write_json_atomic
from aiforstudy.pipeline.engine import build_engine
from aiforstudy.pipeline.state import RunStore, book_runs_root
from aiforstudy.ui.api import normalize_request
from aiforstudy.ui.server import ControlServer, RunningTask


def request_body(**changes):
    return {"book_id": "synth-book", "chapters": ["CH01"], "profile": "smoke",
            **changes}


def saved_run(workspace, **changes):
    # 修改计划 E7：与服务端一致，未填并发时按 profile 配置。
    request = normalize_request(request_body(**changes), workspace)
    engine = build_engine(request, workspace)
    store = RunStore.create(book_runs_root(workspace.root, request.book_id), request, engine.plan, control=engine.services.control)
    store.set_status("needs_review", exit_code=1, scope_accepted=False, release_ready=False)
    return store, engine


@pytest.mark.parametrize("origin", ["http://localhost.evil.invalid", "http://127.0.0.1.evil.invalid",
                                   "http://evil.invalid/127.0.0.1", "null"])
def test_localhost_substrings_and_opaque_origins_are_rejected(server, origin):
    control, client, _ws = server
    request = urllib.request.Request(client.base + "/api/plans", data=json.dumps(request_body()).encode(),
                                     method="POST", headers={"Origin": origin,
                                                               "X-Aifs-Token": control.token})
    with pytest.raises(urllib.error.HTTPError) as failure:
        urllib.request.urlopen(request)
    assert failure.value.code == 400


def test_idempotency_survives_service_restart(server):
    control, client, workspace = server
    store, _engine = saved_run(workspace, idempotency_key="durable-retry")
    reopened = ControlServer(workspace)
    response = reopened.api_start(request_body(idempotency_key="durable-retry"))
    assert response["run_id"] == store.run_id
    assert response["created"] is False
    assert len(list(store.root.parent.iterdir())) == 1


def test_reusing_idempotency_key_for_other_scope_is_conflict(server):
    _control, client, workspace = server
    saved_run(workspace, idempotency_key="scope-bound")
    with pytest.raises(urllib.error.HTTPError) as failure:
        client.post("/api/runs", request_body(chapters=["CH02"], idempotency_key="scope-bound"))
    assert failure.value.code == 409


def test_same_book_is_rejected_without_pretending_to_queue(server):
    control, client, workspace = server
    store, engine = saved_run(workspace)
    stop = threading.Event()
    thread = threading.Thread(target=lambda: stop.wait(10), daemon=True)
    control.state.tasks[store.run_id] = RunningTask(store.run_id, engine.services.cancel, thread,
                                                   engine.request)
    thread.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as failure:
            client.post("/api/runs", request_body(chapters=["CH02"]))
        assert failure.value.code == 409
        assert "已有活动" in failure.value.read().decode()
        assert len(list(store.root.parent.iterdir())) == 1
    finally:
        stop.set()
        thread.join()


def test_book_history_restores_scope_quality_and_separate_usage(server):
    _control, client, workspace = server
    store, _engine = saved_run(workspace)
    store.usage_path.write_text('\n'.join(json.dumps(row) for row in [
        {"stage": "toc", "bucket": "prepare", "tokens": 120, "kind": "call", "unknown": False},
        {"stage": "concepts", "bucket": "concepts", "tokens": 80, "kind": "call", "unknown": False},
        {"stage": "lesson", "bucket": "chapter", "tokens": 300, "kind": "call", "unknown": False},
    ]), encoding="utf-8")
    detail = client.get("/api/books/synth-book")
    assert detail["chapters"][0]["chapter_id"] == "CH01"
    assert detail["runs"][0]["run_id"] == store.run_id
    state = client.get("/api/runs/" + store.run_id)
    assert state["request"]["selectors"] == {"kind": "chapters", "values": ["CH01"]}
    assert "tokens" not in state["progress"]["preparation"]
    assert "tokens" not in state["progress"]["selected"]
    assert state["quality_status"] == "needs_review"
    assert state["release_ready"] is False
    assert client.get("/api/runs?book_id=synth-book")["runs"][0]["run_id"] == store.run_id


def test_progress_keeps_frozen_chapter_graph_and_shows_started_node(server):
    from dataclasses import replace
    _control, client, workspace = server
    store, engine = saved_run(workspace)
    plan = replace(engine.plan, resolved_scope=("CH04", "CH04-S01", "CH04-S02"))
    write_json_atomic(store.plan_path, plan.as_dict())
    state = store.load_state()
    state.update(status="running", nodes={stage: {
        "stage": stage, "execution_state": "succeeded", "quality_state": "accepted",
    } for stage in ("mineru", "toc", "structure", "export", "body", "concepts")})
    store.save_state(state)
    store.emit("stage.started", stage="lesson", node_id="lesson:CH04", scope_key="CH04")

    progress = client.get("/api/runs/" + store.run_id)["progress"]
    assert (progress["preparation"]["completed"], progress["preparation"]["total"]) == (6, 6)
    assert (progress["selected"]["completed"], progress["selected"]["total"]) == (0, 20)
    rows = {node["node_id"]: node for node in progress["selected"]["nodes"]}
    assert all("media_plan:" + unit in rows for unit in plan.resolved_scope)
    assert rows["lesson:CH04"]["execution_state"] == "running"
    assert rows["delivery"]["execution_state"] == "pending"

    state["nodes"]["lesson:CH04"] = {"stage": "lesson", "execution_state": "succeeded"}
    store.save_state(state)
    store.emit("stage.finished", stage="lesson", node_id="lesson:CH04")
    store.emit("stage.started", stage="figures", node_id="figures:CH04")
    progress = client.get("/api/runs/" + store.run_id)["progress"]["selected"]
    assert (progress["completed"], progress["total"]) == (1, 20)
    assert [row["node_id"] for row in progress["nodes"] if row["execution_state"] == "running"] == ["figures:CH04"]
    store.set_status("interrupted")
    progress = client.get("/api/runs/" + store.run_id)["progress"]["selected"]
    assert not any(row["execution_state"] == "running" for row in progress["nodes"])


def test_progress_does_not_invent_selected_total_before_cold_scope_is_fixed(server):
    from dataclasses import replace
    _control, client, workspace = server
    store, engine = saved_run(workspace)
    plan = replace(engine.plan, resolved_scope=None, requires_preparation=True)
    write_json_atomic(store.plan_path, plan.as_dict())
    progress = client.get("/api/runs/" + store.run_id)["progress"]
    assert progress["preparation"]["total"] == 5
    assert progress["selected"]["total"] is None
    assert progress["selected"]["completed"] == 0


def register_preview(workspace):
    store, _engine = saved_run(workspace)
    root = store.work / "legacy" / "render" / "CH01"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text('<h1>章节预览</h1><script src="assets/app.js"></script>', encoding="utf-8")
    (root / "assets" / "app.js").write_text('document.title = "preview";', encoding="utf-8")
    (root / "assets" / "secret.js").write_text('const secret = "do-not-serve";', encoding="utf-8")
    outside = store.root / "private.js"
    outside.write_text('const privateKey = "hidden";', encoding="utf-8")
    (root / "assets" / "escape.js").symlink_to(outside)
    manifest = store.work / "products" / "delivery" / "delivery.json"
    files = [root / "index.html", root / "assets" / "app.js", root / "assets" / "escape.js"]
    write_json_atomic(manifest, {"payload": {"resources": {"resources": {
        str(p.relative_to(root.parent)): {"source": str(p), "sha256": digest_file(p)} for p in files}}}})
    store.save_state({**store.load_state(), "nodes": {
        "render:CH01": {"stage": "render", "scope_key": "CH01", "execution_state": "succeeded",
                         "quality_state": "needs_review", "outputs": [
            {"kind": "render.html", "path": str(root / "index.html"), "content_digest": digest_file(root / "index.html")}]},
        "delivery": {"stage": "delivery", "scope_key": "scope", "execution_state": "succeeded",
                     "quality_state": "accepted", "outputs": [
            {"kind": "delivery.scope", "path": str(manifest), "content_digest": digest_file(manifest)}]},
    }})
    return store


def test_preview_is_http_readable_while_quality_needs_review_and_is_sandboxed(server):
    _control, client, workspace = server
    store = register_preview(workspace)
    state = client.get("/api/runs/" + store.run_id)
    assert state["quality_status"] == "needs_review"
    assert len(state["previews"]) == 1
    url = state["previews"][0]["url"]
    assert url == f"/artifacts/synth-book/{store.run_id}/CH01/index.html"
    with urllib.request.urlopen(client.base + url) as response:
        assert response.status == 200
        assert "章节预览" in response.read().decode()
        policy = response.headers["Content-Security-Policy"]
        assert "sandbox allow-scripts" in policy and "allow-same-origin" not in policy
        assert "connect-src 'none'" in policy
        assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert client.raw(url.replace("index.html", "assets/app.js"))[0] == 200


@pytest.mark.parametrize("relative", ["../../request.json", "%2e%2e/%2e%2e/request.json",
                                     "assets/secret.js", "assets/escape.js", ".env", "render.index.json"])
def test_preview_cannot_read_unlisted_secrets_or_leave_render_tree(server, relative):
    _control, client, workspace = server
    store = register_preview(workspace)
    url = f"/artifacts/synth-book/{store.run_id}/CH01/{relative}"
    status, response = client.raw(url)
    assert status == 404
    assert b"do-not-serve" not in response and b"privateKey" not in response


def test_preview_book_id_cannot_be_substituted_and_post_is_refused(server):
    _control, client, workspace = server
    store = register_preview(workspace)
    assert client.raw(f"/artifacts/another-book/{store.run_id}/CH01/index.html")[0] == 404
    with pytest.raises(urllib.error.HTTPError) as failure:
        client.post(f"/artifacts/synth-book/{store.run_id}/CH01/index.html")
    assert failure.value.code == 405


def test_service_restart_does_not_interrupt_active_cli_lease(server):
    from aiforstudy.pipeline.lease import BookLease
    control, client, workspace = server
    store, _engine = saved_run(workspace)
    store.set_status("running")
    with BookLease(store.root.parent.parent):
        restarted = ControlServer(workspace)
        assert store.load_state()["status"] == "running"
        assert restarted.mark_interrupted() == []
        for path in ("/api/runs", f"/api/runs/{store.run_id}/resume", f"/api/runs/{store.run_id}/cancel"):
            with pytest.raises(urllib.error.HTTPError) as failure:
                client.post(path, request_body() if path=="/api/runs" else {})
            assert failure.value.code == 409
        assert store.load_state()["status"] == "running"
    assert control.mark_interrupted() == [store.run_id]


def test_control_ui_runs_in_dom_with_refresh_and_retry_contracts():
    import shutil
    import subprocess
    root = Path(__file__).resolve().parents[2]
    node = shutil.which("node")
    if not node or not (root / "node_modules" / "jsdom").is_dir():
        pytest.skip("UI DOM acceptance needs the declared Node/jsdom test dependencies")
    result = subprocess.run([node, str(root / "tests/ui/control_workbench.test.cjs")],
                            cwd=root, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_browser_preview_loads_and_cannot_access_workbench(server, tmp_path):
    import shutil
    import subprocess
    _control, client, workspace = server
    store = register_preview(workspace)
    root = Path(__file__).resolve().parents[2]
    node = shutil.which("node")
    if not node or not (root / ".devtools/chrome/chrome-linux64/chrome").is_file():
        pytest.skip("real browser acceptance blocked: Chromium/Node is unavailable")
    preview = f"/artifacts/synth-book/{store.run_id}/CH01/index.html"
    result = subprocess.run([node, str(root / "tests/ui/control_preview.browser.cjs"), client.base,
                             preview, str(tmp_path / "control-preview.png")],
                            cwd=root, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "control-preview.png").stat().st_size > 1000


@pytest.mark.parametrize("change", [{"budget_tokens": 0}, {"budget_minutes": 0}, {"parallelism": 0},
                                     {"budget_tokens": "invalid"}, {"parallelism": "invalid"},
                                     {"selectors": {"kind": "all", "values": []}},
                                     {"delivery_formats": ["not-a-format"]}])
def test_invalid_budget_and_conflicting_scope_are_not_silently_replaced(server, change):
    _control, client, _workspace = server
    with pytest.raises(urllib.error.HTTPError) as failure:
        client.post("/api/plans", request_body(**change))
    assert failure.value.code == 400


def test_cached_usage_is_not_reported_as_new_tokens(server):
    _control, client, workspace = server
    store, _engine = saved_run(workspace)
    store.usage_path.write_text(json.dumps({"stage": "lesson", "tokens": 1200, "kind": "cache"}), encoding="utf-8")
    state = client.get("/api/runs/" + store.run_id)
    assert "tokens" not in state["progress"]["selected"]


def test_busy_race_marks_only_new_submission_blocked(server):
    from aiforstudy.pipeline.lease import BookLease
    control, _client, workspace = server
    store, engine = saved_run(workspace)
    store.set_status("queued")
    task = RunningTask(store.run_id, engine.services.cancel, threading.Thread(), engine.request)
    with BookLease(store.root.parent.parent):
        control._run_in_background(engine, store, task)
    assert store.load_state()["status"] == "blocked"
    assert "正在运行" in store.load_state()["error"]


def test_usage_summary_does_not_double_count_provider_calls(server):
    _control, client, workspace = server
    store, _engine = saved_run(workspace)
    store.usage_path.write_text('\n'.join(json.dumps(row) for row in [
        {"stage": "toc", "bucket": "prepare", "tokens": 100, "kind": "call"},
        {"stage": "lesson", "bucket": "chapter", "tokens": 200, "kind": "call"},
        {"stage": "__run__", "bucket": "other", "tokens": 300, "kind": "call", "extra": {"by_bucket": {"prepare": 100, "chapter": 200}}},
    ]), encoding="utf-8")
    state = client.get("/api/runs/" + store.run_id)
    assert "tokens" not in state["progress"]["preparation"]
    assert "tokens" not in state["progress"]["selected"]


def test_orphaned_budget_draining_run_is_recoverable_on_restart(server):
    _control, _client, workspace = server
    store, _engine = saved_run(workspace)
    store.set_status("stopping")
    ControlServer(workspace)
    assert store.load_state()["status"] == "interrupted"


def test_scope_failure_is_not_reported_as_execution_complete(server):
    _control, client, workspace = server
    store = register_preview(workspace)
    store.set_status("failed", failure_stage="scope")
    assert client.get("/api/runs/" + store.run_id)["execution_complete"] is False


@pytest.mark.parametrize("budget", [{"budget_tokens": 0}, {"budget_tokens": 500}, {"budget_tokens": "bad"}, {"budget_minutes": 0}])
def test_invalid_resume_budget_is_rejected_without_changing_old_result(server, budget):
    _control, client, workspace = server
    store, _engine = saved_run(workspace)
    with pytest.raises(urllib.error.HTTPError) as failure:
        client.post(f"/api/runs/{store.run_id}/resume", budget)
    assert failure.value.code == 400
    assert store.load_state()["status"] == "needs_review"


def test_http_resume_rejects_budget_in_existing_run(server):
    import time
    _control, client, workspace = server
    store, _engine = saved_run(workspace)
    before=store.request_path.read_bytes()
    with pytest.raises(urllib.error.HTTPError):
        client.post(f"/api/runs/{store.run_id}/resume", {"budget_tokens":2000})
    assert store.request_path.read_bytes()==before


def _register_media(workspace, *, chapter: str = "CH01"):
    """在已登记章节预览里放一个真实 MP4，并登记进交付清单（T09 装置）。"""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from test_video_http import VIDEO_BYTES, VTT_TEXT

    store = register_preview(workspace)
    root = store.work / "legacy" / "render" / chapter
    media = root / "assets" / "animations" / "media"
    media.mkdir(parents=True, exist_ok=True)
    (media / "scene.mp4").write_bytes(VIDEO_BYTES)
    (media / "captions.vtt").write_text(VTT_TEXT, encoding="utf-8")
    manifest = store.work / "products" / "delivery" / "delivery.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    registered = payload["payload"]["resources"]["resources"]
    for path in (media / "scene.mp4", media / "captions.vtt"):
        registered[str(path.relative_to(root))] = {"source": str(path),
                                                   "sha256": digest_file(path)}
    write_json_atomic(manifest, payload)
    state = store.load_state()
    for node in (state.get("nodes") or {}).values():
        for ref in node.get("outputs") or []:
            # 改写过盘上文件就要更新登记的内容指纹：产物引用自带"内容与登记一致"这道门
            # （§11），digest 过期时服务端会**正确地**拒绝它（第一版就踩了这个 404）。
            if ref.get("kind") == "delivery.scope":
                ref["content_digest"] = digest_file(manifest)
            elif ref.get("kind") == "render.html":
                ref["content_digest"] = digest_file(root / "index.html")
    store.save_state(state)
    return store


def _raw(client, url, *, method="GET", headers=None):
    request = urllib.request.Request(client.base + url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def test_registered_media_is_served_with_range_and_head_but_unregistered_is_not(server):
    """**T09**：媒体只从已登记产物里按 Range 分块给，未登记的一律 404。"""
    from test_video_http import VIDEO_BYTES

    _control, client, workspace = server
    store = _register_media(workspace)
    base = f"/artifacts/synth-book/{store.run_id}/CH01/assets/animations/media"
    video = f"{base}/scene.mp4"

    status, headers, body = _raw(client, video)
    assert (status, headers["Content-Type"], headers["Accept-Ranges"]) == (
        200, "video/mp4", "bytes")
    assert body == VIDEO_BYTES

    status, headers, body = _raw(client, video, headers={"Range": "bytes=0-9"})
    assert status == 206
    assert headers["Content-Range"] == f"bytes 0-9/{len(VIDEO_BYTES)}"
    assert headers["Content-Length"] == "10"
    assert body == VIDEO_BYTES[:10]

    status, headers, body = _raw(client, video, method="HEAD")
    assert (status, len(body)) == (200, 0)
    assert headers["Content-Length"] == str(len(VIDEO_BYTES))

    status, headers, body = _raw(client, video, headers={"Range": "bytes=0-9,20-29"})
    assert status == 200, "多范围按已声明策略忽略 Range（不实现 multipart，不伪造 206）"
    assert "Content-Range" not in headers

    # CSP：只增 media-src，sandbox 与管理连接约束一条都不许松
    policy = headers["Content-Security-Policy"]
    assert "media-src 'self'" in policy and "sandbox allow-scripts" in policy
    assert "allow-same-origin" not in policy and "connect-src 'none'" in policy
    assert headers["Access-Control-Allow-Origin"] == "*"

    # 盘上有、清单里没有 → 不给
    (store.work / "legacy" / "render" / "CH01" / "assets" / "animations" / "media"
     / "extra.mp4").write_bytes(b"unregistered")
    assert _raw(client, f"{base}/extra.mp4")[0] == 404
    # 篡改已登记内容 → 不给
    (store.work / "legacy" / "render" / "CH01" / "assets" / "animations" / "media"
     / "scene.mp4").write_bytes(b"x" * len(VIDEO_BYTES))
    assert _raw(client, video)[0] == 404


def test_preview_selects_exactly_one_unit_and_serves_only_that_chapter(server):
    """**恰好一个所选单元**：多一章产物也不许顺着路径读到别的章。"""
    _control, client, workspace = server
    store = _register_media(workspace)
    other = store.work / "legacy" / "render" / "CH02"
    (other / "assets").mkdir(parents=True, exist_ok=True)
    (other / "index.html").write_text("<h1>第二章</h1>", encoding="utf-8")
    state = store.load_state()
    state["nodes"]["render:CH02"] = {"stage": "render", "scope_key": "CH02",
                                     "execution_state": "succeeded",
                                     "quality_state": "accepted", "outputs": [
        {"kind": "render.html", "path": str(other / "index.html"),
         "content_digest": digest_file(other / "index.html")}]}
    store.save_state(state)

    answer = client.get("/api/runs/" + store.run_id)
    assert len(answer["previews"]) == 2, "两章都有产物时预览列两章"
    # CH01 的服务不能读到 CH02 的文件（每章一个 root，不是整个 render 目录）
    assert _raw(client, f"/artifacts/synth-book/{store.run_id}/CH01/../../../CH02/index.html")[0] == 404
    assert _raw(client, f"/artifacts/synth-book/{store.run_id}/CH01/assets/../../../CH02/index.html")[0] == 404
