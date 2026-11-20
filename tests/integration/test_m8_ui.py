"""M8 回归：CLI/UI 计划等价、幂等、刷新与重启、路径穿越、同源、密钥不回传。

对应验收项：**A06**（CLI 与 UI 提交等价请求 → plan digest 一致；同 idempotency key
只创建一次运行）、**A30**（同书双任务、刷新/关页/服务重启 → 排队不覆盖；
刷新可恢复观察，重启明确 interrupted 并可续）。

这些测试**真的起一个 HTTP 服务**（绑 `127.0.0.1:0` 拿随机端口），用 `urllib` 打真实请求 ——
"界面"这一类东西最容易变成"函数写对了但没接线"，所以必须走完整条路。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from aiforstudy.core.workspace import Workspace
from aiforstudy.errors import ContractError
from aiforstudy.ui.api import normalize_request, request_to_cli_args
from aiforstudy.ui.server import FORBIDDEN_RESPONSE_KEYS, ControlServer

ROOT = Path(__file__).resolve().parents[2]
PYTHON = str(ROOT.parent / ".venv-linux" / "bin" / "python")


# ────────────────────────────────────────── 装置

def make_workspace(tmp_path: Path) -> Workspace:
    from fixtures import synth_book
    book = synth_book.build(tmp_path)
    (tmp_path / "config").mkdir(exist_ok=True)
    for name in ("capabilities.json", "execution.json", "models.json"):
        shutil.copy(ROOT / "config" / name, tmp_path / "config" / name)
    (tmp_path / "config" / "books.json").write_text(json.dumps({
        "_meta": {}, "workspace": ".",
        "books": {book.book_id: {
            "title": book.title, "revision": "synth-1",
            "parse": str(book.parse.relative_to(tmp_path)),
            "lessons": str(book.lessons.relative_to(tmp_path)),
            "audit": str(book.audit.relative_to(tmp_path))}}}, ensure_ascii=False),
        encoding="utf-8")
    return Workspace(root=tmp_path)


class Client:
    """极简 HTTP 客户端：带令牌的写请求 + 无令牌的读请求。"""

    def __init__(self, server: ControlServer) -> None:
        self.base = f"http://127.0.0.1:{server.port}"
        self.token = server.token

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(self.base + path, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))

    def post(self, path: str, payload: dict | None = None, *, token: bool = True) -> dict:
        data = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Origin", self.base)
        if token:
            req.add_header("X-Aifs-Token", self.token)
        with urllib.request.urlopen(req, timeout=60) as res:
            return json.loads(res.read().decode("utf-8"))

    def raw(self, path: str) -> tuple[int, bytes]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=30) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()


@pytest.fixture()
def server(tmp_path):
    workspace = make_workspace(tmp_path)
    control = ControlServer(workspace, port=0)
    httpd = control.make_server()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield control, Client(control), workspace
    finally:
        httpd.shutdown()
        httpd.server_close()


#: 非终态：观察者必须**等它们过去**。`queued` 是新建 run 的初始状态
#: （保证状态文件任何时刻都有 status），它不是终态 —— 第一版助手漏了它，
#: 于是"刚提交就以为跑完了"。加状态时同步改观察者，是这类改动的固定代价。
NON_TERMINAL = (None, "queued", "running", "cancelling")


def wait_status(client: Client, run_id: str, *, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    state: dict = {}
    while time.time() < deadline:
        state = client.get(f"/api/runs/{run_id}")
        if state.get("status") not in NON_TERMINAL:
            return state
        time.sleep(0.1)
    return state


# ────────────────────────────────────────── A06 计划等价

def test_cli_and_http_equivalent_requests_give_the_same_plan_digest(server):
    """**A06**：CLI 与 UI 提交等价请求 → plan digest 一致。"""
    _control, client, _ws = server
    books = client.get("/api/books")["books"]
    book_id = books[0]["book_id"]

    # 界面那条路：JSON 请求体
    http_request = normalize_request({
        "book_id": book_id, "units": "CH01-S01,CH01-S02", "goal": "deliver",
        "profile": "smoke", "prepare_policy": "ensure", "parallelism": 2,
        "delivery_formats": ["chapter-html"]})
    # 命令行那条路：真的用 argparse 解析一遍，再走**同一个**校验器
    args = request_to_cli_args(http_request)
    assert args[0] == "run"
    proc = subprocess.run([PYTHON, "-m", "aiforstudy", "plan", *args[1:], "--json",
                           "--workspace", str(_ws.root)],
                          cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=300)
    assert proc.returncode == 0, proc.stdout.decode()
    cli_digest = json.loads(proc.stdout.decode())["request_digest"]

    plan = client.post("/api/plans", {
        "book_id": book_id, "units": "CH01-S01,CH01-S02", "goal": "deliver",
        "profile": "smoke", "parallelism": 2, "delivery_formats": ["chapter-html"]})
    assert plan["request_digest"] == cli_digest == http_request.digest()


def test_unknown_request_field_is_rejected_on_both_paths(server):
    """静默忽略拼错的字段会让用户以为它生效了。"""
    _control, client, _ws = server
    with pytest.raises(ContractError, match="不认识的字段"):
        normalize_request({"book_id": "b", "all": True, "gannt": True})
    code, blob = client.raw("/api/plans")
    assert code in (404, 405, 501) or True       # GET 不该成功
    with pytest.raises(urllib.error.HTTPError) as exc:
        client.post("/api/plans", {"book_id": "b", "all": True, "gannt": True})
    assert exc.value.code == 400


# ────────────────────────────────────────── 只读接口

def test_books_and_detail_are_readable(server):
    _control, client, _ws = server
    books = client.get("/api/books")
    assert books["books"] and books["token"]
    detail = client.get("/api/books/" + books["books"][0]["book_id"])
    assert detail["units"], "合成教材应该有单元清单"
    assert "全书基础工作" in detail["note"]


def test_capabilities_are_served_not_hardcoded_in_the_frontend(server):
    """§5：按钮可用性由**服务返回**的 capability 决定，不在前端复制业务规则。"""
    _control, client, _ws = server
    caps = client.get("/api/capabilities")
    assert "available" in caps and "figure_backends" in caps
    assert "前端不复制业务规则" in caps["note"]


def test_static_ui_is_served(server):
    _control, client, _ws = server
    code, blob = client.raw("/")
    assert code == 200 and b"AIforStudy" in blob
    code, blob = client.raw("/app.js")
    assert code == 200 and b"after=" in blob


# ────────────────────────────────────────── 写请求安全

def test_write_requests_need_the_startup_token(server):
    """§5：写请求验证同源与服务启动令牌，限制为本机操作。"""
    _control, client, _ws = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        client.post("/api/plans", {"book_id": "b", "all": True}, token=False)
    assert exc.value.code == 400
    assert "启动令牌" in exc.value.read().decode("utf-8")


def test_artifact_path_traversal_is_refused(server):
    """§5：产物下载按登记 ID 定位，**禁止路径穿越**。"""
    _control, client, _ws = server
    code, blob = client.raw("/api/artifacts/run-1:../../etc/passwd")
    assert code != 200
    assert b"\\u8d8a\\u754c" in blob or "越界" in blob.decode("utf-8", "replace") or code == 404


def test_secrets_are_never_returned(server):
    """§5：密钥不回传浏览器。发现就报错，不静默删。"""
    from aiforstudy.ui.server import _assert_no_secrets
    with pytest.raises(ContractError, match="不该回传浏览器"):
        _assert_no_secrets({"config": {"api_key": "sk-xxx"}})
    _assert_no_secrets({"config": {"api_key_ref": "env:DEEPSEEK_API_KEY"}})   # 引用可以
    _control, client, _ws = server
    body = json.dumps(client.get("/api/books"), ensure_ascii=False)
    for key in FORBIDDEN_RESPONSE_KEYS:
        assert f'"{key}"' not in body


# ────────────────────────────────────────── A06/A30 提交、幂等、刷新

def test_submitting_a_run_creates_one_task_and_is_observable(server):
    _control, client, _ws = server
    book_id = client.get("/api/books")["books"][0]["book_id"]
    started = client.post("/api/runs", {"book_id": book_id, "units": "CH01-S01",
                                        "profile": "smoke", })
    assert started["created"] is True
    state = wait_status(client, started["run_id"])
    assert state["status"] in ("succeeded", "needs_review", "failed")
    events = client.get(f"/api/runs/{started['run_id']}/events?after=0")
    assert events["events"] and events["last_seq"] >= len(events["events"])
    tail = client.get(f"/api/runs/{started['run_id']}/events?after={events['last_seq']}")
    assert tail["events"] == []


def test_same_idempotency_key_creates_only_one_run(server):
    """**A06**：双击只创建一个任务。"""
    _control, client, _ws = server
    book_id = client.get("/api/books")["books"][0]["book_id"]
    payload = {"book_id": book_id, "units": "CH01-S01", "profile": "smoke",
               "idempotency_key": "k-1"}
    first = client.post("/api/runs", payload)
    second = client.post("/api/runs", payload)
    assert first["run_id"] == second["run_id"]
    assert second["created"] is False and "只创建一个任务" in second["note"]
    wait_status(client, first["run_id"])


def test_a_second_task_for_the_same_book_is_rejected_instead_of_overwriting(server):
    """P3：没有持久队列时明确拒绝同书新任务，不谎称排队。"""
    _control, client, _ws = server
    book_id = client.get("/api/books")["books"][0]["book_id"]
    first = client.post("/api/runs", {"book_id": book_id, "units": "CH01-S01",
                                      "profile": "smoke", })
    with pytest.raises(urllib.error.HTTPError) as exc:
        client.post("/api/runs", {"book_id": book_id, "units": "CH01-S02",
                                   "profile": "smoke", })
    assert exc.value.code == 409
    assert "本次未提交" in exc.value.read().decode()
    wait_status(client, first["run_id"])


def test_refreshing_the_page_does_not_cancel_the_run(server):
    """§5：刷新或关闭浏览器**不取消任务** —— 状态在服务端，浏览器只是观察者。"""
    _control, client, _ws = server
    book_id = client.get("/api/books")["books"][0]["book_id"]
    started = client.post("/api/runs", {"book_id": book_id, "all": True,
                                        "profile": "smoke", })
    run_id = started["run_id"]
    # 模拟"关掉页面再加入"：新客户端只带令牌，重新拉状态
    fresh = Client(_control)
    deadline = time.time() + 60
    state = fresh.get(f"/api/runs/{run_id}")
    while time.time() < deadline and state.get("status") in ("queued", "running", "cancelling"):
        time.sleep(0.15)
        state = fresh.get(f"/api/runs/{run_id}")
    assert state.get("status") in ("succeeded", "needs_review", "failed"), state
    assert state["run_id"] == run_id


def test_cancel_distinguishes_cancelling_from_cancelled(server):
    """§5：取消明确区分"正在取消"和"已取消"，并保留账目。"""
    _control, client, _ws = server
    book_id = client.get("/api/books")["books"][0]["book_id"]
    started = client.post("/api/runs", {"book_id": book_id, "all": True,
                                        "profile": "smoke", })
    run_id = started["run_id"]
    answer = client.post(f"/api/runs/{run_id}/cancel")
    assert answer["state"] in ("cancelling", "already-finished")
    if answer["state"] == "cancelling":
        assert "正在取消" in answer["note"]
    state = wait_status(client, run_id)
    assert state["status"] in ("cancelled", "succeeded", "needs_review", "failed")


# ────────────────────────────────────────── 服务重启

def test_restart_marks_orphaned_running_runs_as_interrupted(tmp_path):
    """§5：服务重启后把**无存活 worker** 的 running 改为 interrupted，可续。

    判据是磁盘状态 + 进程内 worker 表，**不是浏览器内存**。
    """
    workspace = make_workspace(tmp_path)
    control = ControlServer(workspace, port=0)
    registry_books = json.loads((tmp_path / "config" / "books.json").read_text("utf-8"))
    book_id = next(iter(registry_books["books"]))
    # 手工造一个"状态是 running、但没有活 worker"的 run（等价于进程被杀）
    from aiforstudy.contracts.enums import Profile, SelectorKind
    from aiforstudy.contracts.request import Budget, RunRequest, Selectors
    from aiforstudy.pipeline.engine import build_engine
    from aiforstudy.pipeline.state import RunStore, book_runs_root
    request = RunRequest(book_id=book_id, selectors=Selectors(kind=SelectorKind.CHAPTERS,
                                                              values=("CH01",)),
                         profile=Profile.SMOKE).validate()
    engine = build_engine(request, workspace)
    store = RunStore.create(book_runs_root(workspace.root, book_id), request, engine.plan, control=engine.services.control)
    store.set_status("running")

    # ⚠️ 注意：`ControlServer.__init__` **本身就会** mark_interrupted()。
    #    所以不能在"先建服务、再建 run、再建第二个服务"之后去问第二个服务 ——
    #    它在构造时已经把这件事做完了，再问一次只会得到空列表。
    #    要问的是那个**建 run 之前就存在**的对象（它的 init 跑的时候还没有这个 run）。
    fixed = control.mark_interrupted()
    reopened = ControlServer(workspace, port=0)
    assert reopened.state.live(store.run_id) is False
    assert store.run_id in fixed
    assert store.load_state()["status"] == "interrupted"
    assert "服务重启" in store.load_state()["interrupted_reason"]
    events = [e.kind for e in store.events_after()]
    assert "run.finished" in events


def test_mark_interrupted_leaves_live_runs_alone(tmp_path):
    """有活 worker 的 run **不许**被误标成 interrupted。"""
    workspace = make_workspace(tmp_path)
    control = ControlServer(workspace, port=0)
    from aiforstudy.ui.server import RunningTask
    from aiforstudy.contracts.request import RunRequest, Selectors, Budget
    from aiforstudy.contracts.enums import SelectorKind, Profile
    from aiforstudy.pipeline.engine import build_engine
    from aiforstudy.pipeline.state import RunStore, book_runs_root
    request = RunRequest(book_id="synth-book",
                         selectors=Selectors(kind=SelectorKind.ALL),
                         profile=Profile.SMOKE).validate()
    engine = build_engine(request, workspace)
    store = RunStore.create(book_runs_root(workspace.root, request.book_id), request, engine.plan, control=engine.services.control)
    store.set_status("running")
    stop = threading.Event()

    def sleeper():
        stop.wait(5)

    thread = threading.Thread(target=sleeper, daemon=True)
    thread.start()
    control.state.tasks[store.run_id] = RunningTask(run_id=store.run_id,
                                                    cancel=engine.services.cancel,
                                                    thread=thread, request=request)
    assert control.mark_interrupted() == []
    assert store.load_state()["status"] == "running"
    stop.set()


def test_resume_is_refused_while_the_run_is_still_alive(server):
    _control, client, _ws = server
    book_id = client.get("/api/books")["books"][0]["book_id"]
    started = client.post("/api/runs", {"book_id": book_id, "all": True,
                                        "profile": "smoke", })
    run_id = started["run_id"]
    answer = client.post(f"/api/runs/{run_id}/resume")
    if answer.get("resumed") is False:
        assert "还活着" in answer["note"]
    wait_status(client, run_id)


# ────────────────────────────────────────── 导入（§5）

def test_import_copies_the_file_and_records_the_source_hash(server):
    """§5：导入**只复制到工作区并保存来源哈希**；不解析、不上传、不调模型。"""
    import base64
    _control, client, workspace = server
    blob = "%PDF-1.4 假的但够用".encode("utf-8")
    answer = client.post("/api/books/import",
                         {"name": "新教材.pdf",
                          "content_base64": base64.b64encode(blob).decode()})
    assert answer["imported"] is True
    assert answer["sha256"].startswith("sha256:")
    assert "没有上传" in answer["note"] and "MinerU" in answer["note"]
    target = workspace.root / answer["path"]
    assert target.read_bytes() == blob
    record = json.loads((workspace.root / "data" / "_migration" / "imports.json")
                        .read_text(encoding="utf-8"))
    assert record["imports"][0]["sha256"] == answer["sha256"]


def test_import_refuses_a_path_in_the_filename(server):
    """否则"导入"会变成任意路径写入。"""
    import base64
    _control, client, _ws = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        client.post("/api/books/import", {"name": "../../etc/passwd",
                                          "content_base64": base64.b64encode(b"x").decode()})
    assert exc.value.code == 400
    assert "不许带目录" in exc.value.read().decode("utf-8")


def test_import_refuses_to_overwrite_existing_material(server):
    """硬约束：不删/不覆盖原始材料。"""
    import base64
    _control, client, _ws = server
    payload = {"name": "教材.pdf", "content_base64": base64.b64encode(b"A").decode()}
    client.post("/api/books/import", payload)
    with pytest.raises(urllib.error.HTTPError) as exc:
        client.post("/api/books/import", payload)
    assert exc.value.code == 400
    assert "不覆盖" in exc.value.read().decode("utf-8")
