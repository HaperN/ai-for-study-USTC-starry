"""本地界面服务：本机 HTTP + 短轮询事件游标 + 幂等提交。

方案 §5 的最小 API：

> `GET /api/books`、`POST /api/books/import`、`POST /api/plans`、`POST /api/runs`、
> `GET /api/runs/{id}`、`GET /api/runs/{id}/events?after=N`、`POST /api/runs/{id}/cancel`、
> `POST /api/runs/{id}/resume`、`GET /api/artifacts/{id}`。章节清单属于书籍详情。

## 五条安全与正确性约束（都做成结构上做不到，而不是"记得别做"）

| 约束 | 做法 |
|---|---|
| 不执行任意 shell | 路由是**固定表**，请求体里没有任何字段能变成命令 |
| 禁止路径穿越 | 产物按**登记 ID** 定位；路径必须落在该 run 的目录内，否则拒绝 |
| 只限本机 | 默认绑 `127.0.0.1`；写请求校验**同源** + **启动令牌** |
| 密钥不回传 | 配置只回角色与密钥**引用**；响应里出现 key/token 字段直接报错 |
| 刷新/关页不取消任务 | 任务跑在**后台线程**里，状态落在 run 目录；浏览器只是观察者 |

## 服务重启后的状态

方案 §5：

> 服务重启后把**无存活 worker** 的 `running` 状态改为 `interrupted`，由恢复操作继续，
> **不能仅靠 UI 内存判断**。

`mark_interrupted()` 结合磁盘状态、当前 worker 和 CLI/UI 共用的 OS lease；
只有书籍没有存活写任务时才标记中断，不凭一个 UI 进程的内存推断其他进程失活。
"""
from __future__ import annotations

import json
import mimetypes
import re
import secrets
import threading
import time
from dataclasses import dataclass, field, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from ..contracts.request import RunRequest
from ..contracts.enums import SUCCESSFUL_EXECUTION_STATES
from ..contracts.scope import ScopePlan
from ..core.digest import digest_file
from ..core.resource_files import INVALID_PATH_SEGMENTS
from ..core.jsonio import read_json_soft, write_json_atomic
from ..core.registry import load_registry
from ..core.workspace import Workspace, open_workspace
from ..delivery.resources import iter_chunks, mime_for, parse_range_header, range_for_response
from ..errors import AifsError, ConfigError, ContractError
from ..pipeline.engine import build_engine, execute
from ..pipeline.graph import build_graph, PREPARATION_STAGES, PARSE_STAGES
from ..pipeline.lease import is_book_active
from ..pipeline.services import CancelToken
from ..pipeline.state import RunStore, book_runs_root, read_state_summary
from ..pipeline.events import KIND_RUN_FINISHED, KIND_RUN_STARTED
from .api import capabilities_payload, normalize_request
from .feedback import build_timeline, collect_failure_cards

ROUTE_RE = re.compile(r"^/api/(?P<name>[a-z_]+)(?:/(?P<arg>[^/?]+))?(?:/(?P<sub>[a-z_]+))?$")

#: 响应里**绝不允许**出现的键（方案 §5：密钥不回传浏览器）
FORBIDDEN_RESPONSE_KEYS = frozenset({"api_key", "apikey", "authorization", "secret",
                                     "password", "token_value"})

#: 允许静态文件的扩展名
STATIC_SUFFIXES = (".html", ".js", ".css", ".svg", ".png", ".ico", ".json")
#: 教材页资源允许的后缀。**必须含媒体**：T09 之前这里没有 `.mp4`／`.vtt`，
#: 于是"文件安全校验"会把合法视频当成不许发的文件（计划 §T09 明确要求不得因新增后缀失效）。
PREVIEW_SUFFIXES = frozenset({".html", ".js", ".css", ".svg", ".png", ".jpg", ".jpeg",
                              ".webp", ".gif", ".woff", ".woff2", ".ttf", ".ico",
                              ".mp4", ".m4v", ".webm", ".vtt", ".txt"})
#: 产物根下允许为**已登记资源**服务的后缀（与 PREVIEW_SUFFIXES 同步扩展）
ARTIFACT_SUFFIXES = PREVIEW_SUFFIXES
#: 只给播放器的媒体来源；**不加** `allow-same-origin`，工作台权限一点没放宽。
#:
#: `blob:`（T10）：不透明 origin 下 `<track src="…captions.vtt">` 会被 Chrome 当成
#: 跨源资源拒绝（「Unsafe attempt to load URL … Domains, protocols and ports must match」），
#: 而页面自己 `URL.createObjectURL` 出来的字幕正是 `blob:`。放行 `blob:` **只**允许播放
#: 页面自己造的对象 URL，不给网络、不给同源、不给管理端；`connect-src 'none'` 不动。
ARTIFACT_CSP = ("sandbox allow-scripts; default-src 'none'; "
                "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; font-src 'self' data:; "
                # ⚠️ 这里**故意没有** `track-src`（T11 清理，2026-10-07）：
                #    `track-src` **不是** CSP 指令。Chrome 131 的原话是
                #    `Unrecognized Content-Security-Policy directive 'track-src'` ——
                #    也就是说它**一条控制都不提供**，真正管 `<track>`（字幕）的是 `media-src`。
                #    留着它的坏处很具体：复核的人会把它当成"字幕来源已被限制"，
                #    于是把一条**不存在**的控制算进安全论证里（本项目最忌的
                #    "看起来有控制、实际没有"）。T10 实测确认后由 T11 删除。
                "media-src 'self' blob:; "
                "connect-src 'none'; frame-src 'none'; frame-ancestors 'self'; "
                "form-action 'none'; base-uri 'none'")
#: 流式发送的块大小（不许整段 read_bytes 吞视频）
SERVE_CHUNK_BYTES = 64 * 1024


class RunConflict(ContractError):
    """请求与已存在或正在进行的运行冲突。"""


@dataclass
class RunningTask:
    run_id: str
    cancel: CancelToken
    thread: threading.Thread
    request: RunRequest
    state: str = "running"          # running | cancelling | cancelled | finished


@dataclass
class ControlState:
    """服务的内存状态。**磁盘上的 state.json 才是权威**，这里只是活 worker 表。"""
    tasks: dict[str, RunningTask] = field(default_factory=dict)
    idempotency: dict[str, str] = field(default_factory=dict)   # key → run_id
    lock: threading.Lock = field(default_factory=threading.Lock)

    def live(self, run_id: str) -> bool:
        task = self.tasks.get(run_id)
        return bool(task and task.thread.is_alive())


class ControlServer:
    """把 Engine 包成一组本机 HTTP 接口。**不含业务逻辑**：它只做校验与转发。"""

    def __init__(self, workspace: Workspace | None = None, *, host: str = "127.0.0.1",
                 port: int = 0, ui_dir: Path | None = None, recover_interrupted: bool = True) -> None:
        self.workspace = workspace or open_workspace()
        self.host = host
        self.port = port
        self.token = secrets.token_urlsafe(24)
        self.state = ControlState()
        self.ui_dir = Path(ui_dir) if ui_dir else (Path(__file__).resolve().parent.parent
                                                   / "resources" / "control-ui")
        self._httpd: ThreadingHTTPServer | None = None
        self._video_service = None
        self._video_lock = threading.Lock()
        if recover_interrupted:
            self.mark_interrupted()

    @property
    def video_service(self):
        with self._video_lock:
            if self._video_service is None:
                from ..core.video_analysis import VideoAnalysisService
                self._video_service = VideoAnalysisService(self.workspace.root)
            return self._video_service

    # ── 生命周期
    @property
    def origin(self) -> str:
        return f"http://{self.host}:{self.port}"

    def make_server(self) -> ThreadingHTTPServer:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "aifs-ui/0.1"

            def log_message(self, fmt: str, *args: Any) -> None:   # 安静点
                return

            def do_GET(self) -> None:                              # noqa: N802
                outer.handle(self, "GET")

            def do_HEAD(self) -> None:                             # noqa: N802
                """HEAD：**头部与 GET 完全一致、正文为 0 字节**。

                播放器与 CDN 用 HEAD 探长度／是否支持 Range；缺了它浏览器只能拿 501，
                于是媒体要么整段拉、要么干脆不放。
                """
                self._head_only = True                             # type: ignore[attr-defined]
                outer.handle(self, "HEAD")

            def do_POST(self) -> None:                             # noqa: N802
                outer.handle(self, "POST")

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = self._httpd.server_address[1]
        return self._httpd

    def serve_forever(self) -> None:                               # pragma: no cover
        httpd = self._httpd or self.make_server()
        httpd.serve_forever()

    # ── 重启后把"没有活 worker 的 running"改成 interrupted
    def mark_interrupted(self) -> list[str]:
        """方案 §5：**不能仅靠 UI 内存判断**。"""
        fixed: list[str] = []
        books_root = self.workspace.root / "data" / "books"
        if not books_root.is_dir():
            return fixed
        for book_dir in sorted(books_root.iterdir()):
            runs_root = book_runs_root(self.workspace.root, book_dir.name)
            if not runs_root.is_dir():
                continue
            for path in sorted(runs_root.iterdir()):
                if not (path / "state.json").is_file():
                    continue
                payload = read_json_soft(path / "state.json") or {}
                from ..pipeline.graph import GRAPH_VERSION
                if payload.get("graph_version") != GRAPH_VERSION:
                    continue  # Historical graphs are read-only, including UI startup.

                if payload.get("status") not in {"queued", "running", "cancelling", "stopping"}:
                    continue
                if self.state.live(path.name) or is_book_active(book_dir):
                    continue
                store = RunStore(path, path.name)
                store.set_status("interrupted",
                                 interrupted_reason="服务重启：没有存活 worker（方案 §5）")
                store.emit(KIND_RUN_FINISHED, message="interrupted",
                           data={"reason": "服务重启，无存活 worker —— 可用 resume 继续"})
                fixed.append(path.name)
        return fixed

    # ── 静态资源
    def _static(self, rel: str) -> tuple[bytes, str] | None:
        if not self.ui_dir.is_dir():
            return None
        candidate = (self.ui_dir / rel.lstrip("/")).resolve()
        try:
            candidate.relative_to(self.ui_dir.resolve())
        except ValueError:
            return None                        # 路径穿越 → 当没有
        if not candidate.is_file() or candidate.suffix not in STATIC_SUFFIXES:
            return None
        ctype = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        return candidate.read_bytes(), ctype

    # ── 路由
    def handle(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        try:
            path = unquote(urlsplit(handler.path).path)
            query = _query(handler.path)
            if path.startswith("/api/videos/") and "/artifacts/" in path:
                parts = path.split("/", 5)
                target = self.video_service.artifact(parts[3], parts[5]) if len(parts) == 6 and parts[4] == "artifacts" else None
                self._serve_file(handler, method, target)
                return
            if path.startswith(("/artifacts/", "/diagnostics/")):
                self._serve_artifact(handler, method, path)
                return
            if not path.startswith("/api/"):
                self._serve_static(handler, path)
                return
            if method == "POST":
                self._check_write_origin(handler)
            if path == "/api/videos/upload" and method == "POST":
                self._respond(handler, HTTPStatus.CREATED, self._video_upload(handler))
                return
            if path.startswith("/api/videos") and method == "POST":
                if int(handler.headers.get("Content-Length") or 0) > 64 * 1024:
                    raise ContractError("视频控制请求过大，请通过上传接口发送文件")
            body = self._read_body(handler) if method == "POST" else {}
            status, payload = self.dispatch(method, path, query, body, handler)
        except AifsError as exc:
            status, payload = _status_for(exc), {"error": str(exc),
                                                 "type": type(exc).__name__}
        except KeyError as exc:
            status, payload = HTTPStatus.NOT_FOUND, {"error": str(exc)}
        except (ValueError, RuntimeError) as exc:
            status, payload = HTTPStatus.BAD_REQUEST, {"error": str(exc)}
        except Exception as exc:                                  # noqa: BLE001
            status = HTTPStatus.INTERNAL_SERVER_ERROR
            payload = {"error": f"{type(exc).__name__}: {exc}"}
        self._respond(handler, status, payload)

    def dispatch(self, method: str, path: str, query: dict, body: dict,
                 handler: Any = None) -> tuple[int, dict]:
        match = ROUTE_RE.match(path)
        if not match:
            return HTTPStatus.NOT_FOUND, {"error": f"没有这个接口：{path}"}
        name, arg, sub = match.group("name"), match.group("arg"), match.group("sub")

        if name == "videos":
            service = self.video_service
            if method == "GET" and not arg:
                return HTTPStatus.OK, {"jobs": service.list_jobs()}
            if method == "GET" and arg == "capabilities":
                return HTTPStatus.OK, {**service.capabilities(), "token": self.token}
            if method == "POST" and arg == "sample":
                return HTTPStatus.OK, service.sample()
            if method == "POST" and not arg:
                if not isinstance(body.get("source_id"), str) or not body["source_id"].strip():
                    raise ContractError("请先上传视频或选择已提供的第一讲")
                if set(body) - {"source_id", "title", "course_id", "force"}:
                    raise ContractError("视频提交只接受已登记 source_id，不接受文件路径")
                return HTTPStatus.ACCEPTED, service.submit(body)
            if method == "GET" and arg and not sub:
                return HTTPStatus.OK, service.get(arg)
            if method == "POST" and arg and sub in {"cancel", "resume", "reconcile"}:
                return HTTPStatus.OK, getattr(service, sub)(arg)

        if method == "GET" and name == "books" and not arg:
            return HTTPStatus.OK, self.api_books()
        if method == "GET" and name == "books" and arg:
            return HTTPStatus.OK, self.api_book_detail(arg)
        if method == "POST" and name == "books" and arg == "import":
            return HTTPStatus.OK, self.api_import(body)
        if method == "GET" and name == "capabilities":
            return HTTPStatus.OK, capabilities_payload(self.workspace)
        if method == "POST" and name == "plans":
            return HTTPStatus.OK, self.api_plan(body)
        if method == "POST" and name == "runs" and not arg:
            return HTTPStatus.OK, self.api_start(body)
        if method == "GET" and name == "runs" and not arg:
            book_id = str(query.get("book_id") or "")
            if book_id:
                load_registry(self.workspace).resolve(book_id)
                return HTTPStatus.OK, {"runs": self._runs_of(book_id)}
            return HTTPStatus.OK, {"runs": sorted(
                [run for book in load_registry(self.workspace).list_books()
                 for run in self._runs_of(book)],
                key=lambda run: run.get("created_at") or 0, reverse=True)}
        if name == "runs" and arg and sub is None and method == "GET":
            return HTTPStatus.OK, self.api_run(arg)
        if name == "runs" and arg and sub == "events" and method == "GET":
            return HTTPStatus.OK, self.api_events(arg, int(query.get("after", 0) or 0))
        if name == "runs" and arg and sub == "cancel" and method == "POST":
            return HTTPStatus.OK, self.api_cancel(arg)
        if name == "runs" and arg and sub == "resume" and method == "POST":
            return HTTPStatus.OK, self.api_resume(arg, body)
        if name == "runs" and arg and sub == "preview" and method == "POST":
            return HTTPStatus.OK, self.api_preview(arg, body)
        if method == "GET" and name == "artifacts" and arg:
            return HTTPStatus.OK, self.api_artifact(arg)
        return HTTPStatus.NOT_FOUND, {"error": f"没有这个接口：{method} {path}"}

    def _video_upload(self, handler) -> dict:
        """Raw request body, bounded and streamed directly into the controlled source store."""
        if handler.headers.get("Transfer-Encoding"):
            raise ContractError("上传需要明确的 Content-Length")
        length = int(handler.headers.get("Content-Length") or 0)
        limit = int(self.video_service.capabilities().get("max_upload_bytes") or 2 * 1024 ** 3)
        if not 0 < length <= limit:
            raise ContractError(f"视频不能为空，且不得超过 {limit} 字节")
        filename = unquote(handler.headers.get("X-Video-Filename", ""))
        if (not filename or len(filename) > 240 or filename.startswith(".") or ".." in filename
                or any(c in filename for c in "/\\\x00\r\n")
                or Path(filename).suffix.lower() not in {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi"}):
            raise ContractError("视频文件名不合法；请使用不带目录的视频文件名")
        return self.video_service.register_upload(handler.rfile, length, filename)

    # ── 各接口
    def api_books(self) -> dict:
        registry = load_registry(self.workspace)
        books = []
        for book_id in registry.list_books():
            ctx = registry.resolve(book_id)
            books.append({"book_id": ctx.book_id, "title": ctx.title,
                          "revision": ctx.revision,
                          "catalog_present": (ctx.parse / "chapters.json").is_file(),
                          "concepts_present": (ctx.parse / "concepts.json").is_file()})
        return {"books": books, "workspace": str(self.workspace.root),
                "origin": self.origin, "token": self.token}

    def api_import(self, body: dict) -> dict:
        """导入一份教材文件：**只复制到本机工作区并记下来源哈希**（方案 §5）。

        方案 §5：*导入操作只将选择的文件复制到本机工作区，保存来源哈希；
        **云端识别发生在实际运行的 MinerU 阶段***。

        所以这里**不解析、不上传、不调模型** —— 它只做一次可核对的文件复制。
        文件名要过一道"不许带目录"的检查：带 `/` 或 `..` 的名字会被拒绝
        （否则"导入"就成了任意路径写入）。
        """
        import base64
        import hashlib
        name = str(body.get("name") or "").strip()
        if not name or "/" in name or "\\" in name or name.startswith(".") or ".." in name:
            raise ContractError(
                f"导入文件名不合法：{name!r}。方案 §5：导入只把文件复制进工作区，"
                f"文件名不许带目录（否则'导入'会变成任意路径写入）。")
        raw_dir = self.workspace.root / "data" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        target = raw_dir / name
        if body.get("content_base64"):
            blob = base64.b64decode(str(body["content_base64"]))
        elif body.get("source_path"):
            source = Path(str(body["source_path"]))
            if not source.is_file():
                raise ConfigError(f"源文件不在位：{source}")
            blob = source.read_bytes()
        else:
            raise ContractError("导入需要 content_base64（浏览器上传）或 source_path（本机路径）")
        if target.exists():
            raise ContractError(
                f"{target} 已存在。导入**不覆盖**已有原始材料（硬约束：不删原始材料）——"
                f"请换一个文件名，或先确认那份是不是同一份。")
        target.write_bytes(blob)
        sha = digest_file(target)
        record_path = self.workspace.root / "data" / "_migration" / "imports.json"
        previous = read_json_soft(record_path) or {"_meta": {"file_type": "imports",
                                                             "schema_version": "1.0"}, "imports": []}
        previous["imports"].append({"name": name, "path": str(target.relative_to(self.workspace.root)),
                                    "sha256": sha, "bytes": len(blob),
                                    "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                    "note": "只复制+记指纹；识别发生在运行时的 MinerU 阶段"})
        write_json_atomic(record_path, previous)
        return {"imported": True, "name": name, "sha256": sha, "bytes": len(blob),
                "path": str(target.relative_to(self.workspace.root)),
                "note": ("已复制到工作区并记下来源哈希。**本次没有解析、没有上传、"
                         "没有调用模型** —— 识别发生在实际运行时的 MinerU 阶段（方案 §5）。")}

    def api_book_detail(self, book_id: str) -> dict:
        registry = load_registry(self.workspace)
        ctx = registry.resolve(book_id)
        units: list[dict] = []
        chapters: list[dict] = []
        path = ctx.parse / "chapters.json"
        if path.is_file():
            payload = read_json_soft(path) or {}
            chapters = [{"chapter_id": row.get("id"), "title": row.get("title") or row.get("id")}
                        for row in payload.get("chapters", []) if row.get("id")]
            for row in payload.get("lesson_units") or []:
                units.append({"unit_key": row.get("unit_id"), "title": row.get("title"),
                                 "chapter": row.get("chapter"), "level": row.get("level")})
        return {"book_id": ctx.book_id, "title": ctx.title,
                "catalog_present": bool(units), "units": units, "chapters": chapters,
                "active": self._same_book_running(ctx.book_id),
                "runs": self._runs_of(ctx.book_id),
                "note": ("目录还没形成时只能选整本或输入章号；"
                         "执行会先完成全书基础工作，再校验并生成所选范围。"
                         "各对象按有限尝试执行，失败不阻断其他独立对象。")}

    def _runs_of(self, book_id: str) -> list[dict]:
        root = book_runs_root(self.workspace.root, book_id)
        out = []
        if root.is_dir():
            for path in sorted(root.iterdir()):
                if (path / "state.json").is_file():
                    summary = read_state_summary(self.workspace.root, book_id, path.name)
                    state = read_json_soft(path / "state.json") or {}
                    request = read_json_soft(path / "request.json") or {}
                    summary.update({"selectors": request.get("selectors"),
                                    "release_ready": bool(state.get("release_ready")),
                                    "quality_status": self._quality_status(state)})
                    out.append(summary)
        return sorted(out, key=lambda item: item.get("created_at") or 0, reverse=True)

    def api_plan(self, body: dict) -> dict:
        request = normalize_request(body, self.workspace)
        engine = build_engine(request, self.workspace)
        payload = engine.plan_payload()
        # 界面要能把"哪些按钮可用"从服务端拿到，而不是前端自己判断（§5）
        payload["capabilities"] = capabilities_payload(self.workspace)
        payload["request_digest"] = request.digest()
        return payload

    def api_start(self, body: dict) -> dict:
        request = normalize_request(body, self.workspace)
        key = request.idempotency_key
        with self.state.lock:
            previous = self._idempotent_run(request) if key else None
            if previous:
                return {"run_id": previous.run_id, "created": False,
                        "note": "同一个 idempotency_key 只创建一个任务（双击不会建两个）"}
            if self._same_book_running(request.book_id):
                raise RunConflict("同书已有活动写任务，本次未提交；待当前任务结束后重试。")
            engine = build_engine(request, self.workspace)
            store = RunStore.create(book_runs_root(self.workspace.root, request.book_id),
                                    request, engine.plan, control=engine.services.control)
            cancel = CancelToken()
            engine.services.cancel = cancel
            task = RunningTask(run_id=store.run_id, cancel=cancel,
                               thread=threading.Thread(target=lambda: None),
                               request=request)
            self.state.tasks[store.run_id] = task
            if key:
                self.state.idempotency[key] = store.run_id
            store.emit(KIND_RUN_STARTED, message="由本地界面提交")
            thread = threading.Thread(target=self._run_in_background,
                                      args=(engine, store, task), daemon=True)
            task.thread = thread
            thread.start()
        return {"run_id": store.run_id, "created": True, "status": "running"}

    def _idempotent_run(self, request: RunRequest) -> RunStore | None:
        """request.json 是幂等记录；服务重启不丢失，键不能换意图。"""
        books_root = self.workspace.root / "data" / "books"
        if not books_root.is_dir():
            return None
        for path in books_root.glob("*/runs/*/request.json"):
            previous = read_json_soft(path) or {}
            if previous.get("idempotency_key") != request.idempotency_key:
                continue
            if RunRequest.from_dict(previous).digest() != request.digest():
                raise RunConflict("idempotency_key 已用于不同请求；修改范围或设置后请创建新提交。")
            if (path.parent / "state.json").is_file():
                return RunStore(path.parent, path.parent.name)
        return None

    def _run_in_background(self, engine, store: RunStore, task: RunningTask) -> None:
        try:
            result, _store = execute(engine, store=store)
            task.state = "finished"
            store.set_status(result.status, exit_code=result.exit_code,
                             release_ready=result.release_ready)
        except Exception as exc:                                  # noqa: BLE001
            from ..pipeline.lease import BookBusyError
            task.state = "finished"
            status = "blocked" if isinstance(exc, BookBusyError) else "failed"
            store.set_status(status, error=f"{type(exc).__name__}: {exc}")
            store.emit(KIND_RUN_FINISHED, message=status, data={"error": str(exc)})

    def _same_book_running(self, book_id: str) -> bool:
        if is_book_active(book_runs_root(self.workspace.root, book_id).parent):
            return True
        for run_id, task in self.state.tasks.items():
            if task.request.book_id == book_id and task.thread.is_alive():
                return True
        return False

    def api_run(self, run_id: str) -> dict:
        store = self._open_run(run_id)
        payload = store.load_state()
        payload["run_id"] = run_id
        payload["live_worker"] = self.state.live(run_id)
        payload["last_event_seq"] = store.events.last_seq
        payload["request"] = read_json_soft(store.request_path) or {}
        payload["plan"] = read_json_soft(store.plan_path) or {}
        payload["progress"] = self._progress(store, payload)
        payload["quality_status"] = self._quality_status(payload)
        payload["execution_complete"] = payload.get("status") in {"succeeded", "needs_review"} and bool(payload.get("nodes")) and all(
            node.get("execution_state") in SUCCESSFUL_EXECUTION_STATES
            for node in payload["nodes"].values())
        payload["previews"] = self._previews(store, payload) + self._diagnostic_previews(store, payload)
        payload["book_active"] = self._same_book_running(str(payload.get("book_id") or ""))
        # ── T10 工作台反馈 ────────────────────────────────────────────────
        # `failure_cards`：每张失败卡回答八件事（卡点／方法／标准／责任方／当前版本／
        # 前次失败／已耗轮次／停下原因），数据来自**已落盘的**工单与返修账。
        # `media_timeline`：text_audit 与媒体规划／draft／检查／final 的事件（带中文标签）。
        limits = self._attempt_limits()
        payload["attempt_limits"] = limits
        payload["failure_cards"] = collect_failure_cards(store_root=store.root, state=payload)
        payload["media_timeline"] = build_timeline(store.events_after())
        payload.update(self._v4_products(store, payload))
        return payload

    @staticmethod
    def _v4_products(store, state) -> dict:
        from ..contracts.artifact import ArtifactRef
        result = {"content_versions": [], "media_requirements": []}
        kinds = {"lesson.unit": "content_versions", "media.requirements": "media_requirements"}
        for node in (state.get("nodes") or {}).values():
            for raw in node.get("outputs") or ():
                group = kinds.get(raw.get("kind"))
                if not group:
                    continue
                row = {"unit_id": node.get("scope_key"), "quality_state": node.get("quality_state"), "valid": False}
                try:
                    ref = ArtifactRef.from_dict(raw).verify()
                    if not ref.path.resolve().is_relative_to(Path(store.work).resolve()):
                        raise ContractError("当前产物不在本次运行工作目录")
                    payload = (read_json_soft(ref.path) or {}).get("payload") or {}
                    fields = ("content_revision", "content_hash", "policy_digest") if group == "content_versions" else ("revision", "content_hash", "decisions", "requests")
                    row.update({field: payload.get(field) for field in fields})
                    row["valid"] = True
                except (ContractError, OSError, ValueError, TypeError) as exc:
                    row["error"] = str(exc)
                result[group].append(row)
        return result

    def _attempt_limits(self) -> dict:
        config=read_json_soft(self.workspace.root / "config" / "execution.json") or {}
        return {"transport":(config.get("attempt_limits") or {}).get("transport",2),
                "content":config.get("content_repair_rounds_per_object",2),
                "evidence":config.get("evidence_repairs_per_verdict",1)}

    @staticmethod
    def _quality_status(state: dict) -> str:
        if state.get("scope_accepted") is True:
            return "accepted"
        if state.get("status") == "needs_review" or any(
                node.get("quality_state") in {"needs_review", "rejected"}
                for node in (state.get("nodes") or {}).values()):
            return "needs_review"
        return "not_checked"

    @staticmethod
    def _progress(store: RunStore, state: dict) -> dict:
        groups = {name: {"nodes": [], "completed": 0, "total": 0} for name in ("preparation", "selected")}
        recorded = state.get("nodes") or {}
        events = store.events_after()
        latest = {}
        until = None
        for event in events:
            if event.kind == "run.started":
                details = event.data.get("data", event.data)
                until = details.get("truncated_at")
            if event.node_id and event.kind in {
                    "stage.started", "stage.finished", "stage.cached", "stage.failed",
                    "stage.blocked", "stage.skipped"}:
                latest[event.node_id] = event

        # Rebuild only the pure graph from the saved scope, never re-plan against a
        # changing catalog or initialize providers while observing an active CLI.
        unresolved = False
        try:
            plan = ScopePlan.from_dict(state.get("plan") or {})
            unresolved = plan.resolved_scope is None
            if unresolved:
                plan = replace(plan, resolved_scope=())
                until = until if until in PARSE_STAGES else "body"
            from ..pipeline.graph import GRAPH_VERSION
            saved_graph = read_json_soft(store.root / "graph.json") or {}
            if state.get("graph_version") != GRAPH_VERSION:
                nodes = {n["node_id"]: n for n in saved_graph.get("nodes", ())}
                if not nodes:
                    nodes = {key: {"stage": row.get("stage")} for key, row in recorded.items()}
            else:
                expected = build_graph(plan, until=until).by_id()
                nodes = {node_id: node.as_dict() for node_id, node in expected.items()}
        except (ContractError, TypeError, ValueError):
            # Historical/incomplete records remain readable; do not invent a plan.
            nodes = {node_id: {"stage": node.get("stage")} for node_id, node in recorded.items()}
            unresolved = True

        active = state.get("status") in {"queued", "running", "cancelling", "stopping"}
        for node_id, spec in nodes.items():
            node = {**spec, "execution_state": "pending", **recorded.get(node_id, {}),
                    "node_id": node_id}
            event = latest.get(node_id)
            if (active and event and event.kind == "stage.started"
                    and (not node.get("finished_at") or node["finished_at"] < event.at)):
                node["execution_state"] = "running"
                node["started_at"] = event.at
            bucket = groups["preparation" if node.get("stage") in PREPARATION_STAGES else "selected"]
            bucket["nodes"].append(node)
            bucket["total"] += 1
            if node.get("execution_state") in SUCCESSFUL_EXECUTION_STATES:
                bucket["completed"] += 1
        if unresolved:
            groups["selected"]["total"] = None
        return groups

    def api_events(self, run_id: str, after: int) -> dict:
        store = self._open_run(run_id)
        events = [e.as_dict() for e in store.events_after(after=after, limit=200)]
        return {"events": events, "last_seq": store.events.last_seq, "after": after}

    def api_cancel(self, run_id: str) -> dict:
        """§5：取消明确区分"正在取消"与"已取消"。"""
        store = self._open_run(run_id)
        task = self.state.tasks.get(run_id)
        if task is None or not task.thread.is_alive():
            if is_book_active(store.root.parent.parent):
                raise RunConflict("此书正在其他进程运行；请在启动它的终端或工作台取消。")
            return {"state": "already-finished", "run_id": run_id,
                    "note": "任务已结束；取消不改变结果"}
        task.cancel.cancel("界面取消")
        task.state = "cancelling"
        store.set_status("cancelling", note="**正在取消**：已收到信号，正在收尾记账")
        store.emit("run.cancelling", message="界面请求取消")
        return {"state": "cancelling", "run_id": run_id,
                "note": "正在取消；已派发尝试次数保留，完成后状态变为 cancelled"}

    def api_resume(self, run_id: str, body: dict) -> dict:
        from ..pipeline.engine import resume_request
        store = self._open_run(run_id)
        from ..pipeline.resource_binding import assert_resumable
        assert_resumable(state=store.load_state(),
                         plan=read_json_soft(store.root / "plan.json") or {},
                         run_id=run_id)
        request = RunRequest.from_dict(
            json.loads((store.root / "request.json").read_text(encoding="utf-8")))
        if body:
            raise ContractError("恢复不接受参数覆盖；执行尝试次数保持原样")
        with self.state.lock:
            if self.state.live(run_id):
                return {"run_id": run_id, "resumed": False,
                        "note": "这次运行还活着，不需要恢复"}
            if self._same_book_running(request.book_id):
                raise RunConflict("同书已有活动写任务，本次未恢复；待当前任务结束后重试。")
            cancel = CancelToken()
            task = RunningTask(run_id=run_id, cancel=cancel,
                               thread=threading.Thread(target=lambda: None), request=request)
            self.state.tasks[run_id] = task

            def work() -> None:
                try:
                    resume_request(request, run_id, self.workspace, cancel=cancel)
                except Exception as exc:                          # noqa: BLE001
                    # lease 冲突可能发生于预检后；不能改写另一个进程正在写的状态。
                    from ..pipeline.lease import BookBusyError
                    if not isinstance(exc, BookBusyError):
                        store.set_status("failed", error=f"{type(exc).__name__}: {exc}")
                finally:
                    task.state = "finished"

            thread = threading.Thread(target=work, daemon=True)
            task.thread = thread
            thread.start()
        return {"run_id": run_id, "resumed": True, "status": "running"}

    def api_artifact(self, artifact_id: str) -> dict:
        """仅返回登记过的 HTML 预览；不枚举运行目录或返回任意本机路径。"""
        run_id, _, rel = artifact_id.partition(":")
        store = self._open_run(run_id)
        previews = self._previews(store, store.load_state())
        if not rel:
            return {"artifact_id": artifact_id, "previews": previews}
        for item in previews:
            if rel == f"work/legacy/render/{item['chapter']}/index.html":
                return {"artifact_id": artifact_id, **item}
        raise ContractError("产物未登记或路径越界；只能访问登记的章节 HTML。")

    def _registered_outputs(self, store: RunStore, state: dict, kind: str):
        for node in (state.get("nodes") or {}).values():
            for ref in node.get("outputs") or []:
                if ref.get("kind") != kind:
                    continue
                path = Path(str(ref.get("path") or ""))
                if not path.is_absolute():
                    path = store.root / path
                path = path.resolve()
                if not path.is_relative_to(store.root.resolve()) or not path.is_file():
                    continue
                if ref.get("content_digest") and digest_file(path) != ref["content_digest"]:
                    continue
                yield ref, path

    def _preview_entries(self, store: RunStore, state: dict) -> dict:
        root = (store.work / "legacy" / "render").resolve()
        if not root.is_relative_to(store.root.resolve()):
            return {}
        entries = {}
        for ref, path in self._registered_outputs(store, state, "render.html"):
            if path.name != "index.html" or path.parent.parent != root:
                continue
            chapter = path.parent.name
            if re.fullmatch(r"CH\d{1,3}", chapter):
                entries[chapter] = {"root": path.parent, "resources": (ref.get("meta") or {}).get("resources", [])}
        # 旧运行登记 render.chapter 元数据，仍可预览；不能信任任意 out_dir。
        for _ref, path in self._registered_outputs(store, state, "render.chapter"):
            payload = (read_json_soft(path) or {}).get("payload") or {}
            chapter = str(payload.get("chapter") or "")
            if not re.fullmatch(r"CH\d{1,3}", chapter):
                continue
            directory = root / chapter
            if not directory.resolve().is_relative_to(root):
                continue
            if "index.html" in payload.get("pages", []) and (directory / "index.html").is_file():
                entries.setdefault(chapter, {"root": directory, "resources": []})
        return entries

    def _previews(self, store: RunStore, state: dict) -> list[dict]:
        return [{"chapter": chapter, "title": chapter + " 章节 HTML",
                 "quality_status": self._quality_status(state),
                 "url": "/artifacts/" + "/".join(quote(str(part), safe="") for part in
                       (state.get("book_id"), store.run_id, chapter, "index.html"))}
                for chapter in sorted(self._preview_entries(store, state))]

    def api_preview(self, run_id: str, body: dict) -> dict:
        from ..blocks.render.diagnostics import build_diagnostic_preview
        store = self._open_run(run_id)
        if self.state.live(run_id):
            raise ContractError('请在当前运行停止后生成诊断快照，避免读取写入中的产物。')
        units = body.get('units')
        if units is None:
            units = sorted(p.stem for p in (store.work/'products/lesson').glob('*.json'))
        if not isinstance(units,list) or not all(isinstance(u,str) for u in units):
            raise ContractError('units 必须为教学单元列表')
        report = build_diagnostic_preview(store.root, unit_ids=units, out_dir=store.root/'diagnostics')
        report['url'] = self._diagnostic_url(store, store.load_state(), Path(report['output_dir']).name)
        return report

    @staticmethod
    def _diagnostic_url(store: RunStore, state: dict, snapshot: str) -> str:
        return '/diagnostics/' + '/'.join(quote(str(part),safe='') for part in
                                          (state.get('book_id'),store.run_id,snapshot,'index.html'))

    def _diagnostic_previews(self, store: RunStore, state: dict) -> list[dict]:
        result=[]
        for manifest in sorted((store.root/'diagnostics').glob('*/diagnostic-preview.json')):
            snapshot=manifest.parent.name
            if self._diagnostic_file(str(state.get('book_id')),store.run_id,snapshot,'index.html'):
                report=read_json_soft(manifest) or {}
                result.append({'chapter':', '.join(report.get('units') or []),'title':'诊断预览（未验收）',
                               'quality_status':'needs_review','preview_only':True,
                               'url':self._diagnostic_url(store,state,snapshot)})
        return result

    def _diagnostic_file(self, book_id: str, run_id: str, snapshot: str, rel: str) -> Path | None:
        if not all(re.fullmatch(r'[A-Za-z0-9_-]+',value) for value in (book_id,run_id,snapshot)):
            return None
        if any(p in INVALID_PATH_SEGMENTS or p.startswith('.') or '\\' in p for p in rel.split('/')):
            return None
        run=book_runs_root(self.workspace.root,book_id)/run_id
        state=read_json_soft(run/'state.json') or {}
        root=run/'diagnostics'/snapshot
        if state.get('book_id')!=book_id or not root.resolve().is_relative_to(run.resolve()): return None
        manifest=read_json_soft(root/'diagnostic-preview.json') or {}
        if manifest.get('schema_version')!='DiagnosticPreviewV1' or manifest.get('preview_only') is not True or manifest.get('production_accepted') is not False:
            return None
        target=root/rel
        if (not target.is_file() or not target.resolve().is_relative_to(root.resolve())
                or target.suffix.lower() not in PREVIEW_SUFFIXES
                or any(p.is_symlink() for p in [target,*target.parents] if p.is_relative_to(run))): return None
        resource=next((r for r in manifest.get('resources') or [] if r.get('path')==rel),None)
        return target if resource and resource.get('sha256')==digest_file(target) else None

    def _artifact_file(self, book_id: str, run_id: str, chapter: str, rel: str) -> Path | None:
        if not all(re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in (book_id, run_id, chapter)):
            return None
        parts = rel.split("/")
        if any(part in INVALID_PATH_SEGMENTS or part.startswith(".") or "\\" in part
               or re.search(r"secret|password|credential|api[_-]?key|token", part, re.I)
               for part in parts):
            return None
        store_root = book_runs_root(self.workspace.root, book_id) / run_id
        if not (store_root / "state.json").is_file():
            return None
        store = RunStore(store_root, run_id)
        state = store.load_state()
        if state.get("book_id") != book_id:
            return None
        entry = self._preview_entries(store, state).get(chapter)
        if not entry:
            return None
        chapter_root = entry["root"].resolve()
        target = (chapter_root / rel).resolve()
        if (not target.is_relative_to(chapter_root) or not target.is_file()
                or target.suffix.lower() not in PREVIEW_SUFFIXES):
            return None
        allowed = {chapter_root / "index.html": None}
        manifests = list(self._registered_outputs(store, state, "delivery.scope"))
        if manifests:
            for _ref, path in manifests:
                manifest = ((read_json_soft(path) or {}).get("payload") or {}).get("resources") or {}
                for resource in (manifest.get("resources") or {}).values():
                    source = Path(str(resource.get("source") or "")).resolve()
                    if source.is_relative_to(chapter_root):
                        allowed[source] = resource.get("sha256")
        else:
            for resource in entry["resources"]:
                item = resource.get("path") if isinstance(resource, dict) else resource
                source = (chapter_root / str(item)).resolve()
                if source.is_relative_to(chapter_root):
                    allowed[source] = resource.get("sha256") if isinstance(resource, dict) else None
        if target not in allowed:
            return None
        if allowed[target] and digest_file(target) != allowed[target]:
            return None
        return target

    def _serve_artifact(self, handler, method: str, path: str) -> None:
        """受限产物服务：**只给已登记的产物**，按 Range 分块，HEAD 无 body。

        与 T09 之前的行为差别（都是媒体必需、而旧实现做不到的）：
          1. 支持 `HEAD`（旧实现只有 `do_GET` → 501）；
          2. 支持**单** Range（206 + `Content-Range`／`Content-Length`，越界 416）；
          3. **不实现 multipart Range**：收到多范围按已声明策略忽略 Range 返回 200，
             绝不伪造一个只有第一段的 206；
          4. 分块读取，不再 `read_bytes()` 整段吞视频。
        """
        if method not in ("GET", "HEAD"):
            self._respond(handler, HTTPStatus.METHOD_NOT_ALLOWED, {"error": "产物仅支持只读 GET/HEAD"})
            return
        parts = path.split("/", 5)
        lookup = self._diagnostic_file if path.startswith('/diagnostics/') else self._artifact_file
        target = lookup(*parts[2:]) if len(parts) == 6 else None
        self._serve_file(handler, method, target)

    def _serve_file(self, handler, method: str, target: Path | None) -> None:
        if method not in ("GET", "HEAD"):
            self._respond(handler, HTTPStatus.METHOD_NOT_ALLOWED, {"error": "产物仅支持只读 GET/HEAD"})
            return
        if target is None:
            self._respond(handler, HTTPStatus.NOT_FOUND, {"error": "产物不存在或未允许访问"})
            return

        total = target.stat().st_size
        header = handler.headers.get("Range") if handler.headers else None
        status = HTTPStatus.OK
        start, length = 0, total
        content_range = None
        if header is not None and str(header).strip():
            span = parse_range_header(str(header))
            if span is None:
                # 多范围／不支持的语法 → **按已声明策略忽略 Range**，返回 200 全量
                status = HTTPStatus.OK
            else:
                resolved = range_for_response(span, total)
                if resolved is None:
                    handler.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    handler.send_header("Content-Range", f"bytes */{total}")
                    handler.send_header("Content-Length", "0")
                    handler.send_header("Accept-Ranges", "bytes")
                    handler.send_header("Cache-Control", "no-store")
                    handler.send_header("X-Content-Type-Options", "nosniff")
                    handler.end_headers()
                    return
                status = HTTPStatus.PARTIAL_CONTENT
                start, last = resolved
                length = last - start + 1
                content_range = f"bytes {start}-{last}/{total}"

        handler.send_response(status)
        handler.send_header("Content-Type", mime_for(target.name))
        handler.send_header("Content-Length", str(length))
        handler.send_header("Accept-Ranges", "bytes")
        if content_range:
            handler.send_header("Content-Range", content_range)
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Referrer-Policy", "no-referrer")
        # sandbox 的不透明 origin（null）读字体／字幕需要这一条；它**不给**工作台权限，
        # 只是允许读这份已登记产物。要不要收紧到具体 origin 见 T09 证据。
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.send_header("Vary", "Origin")
        handler.send_header("Content-Security-Policy", ARTIFACT_CSP)
        handler.end_headers()
        if method == "HEAD" or length == 0:
            return
        try:
            for block in iter_chunks(target, chunk_bytes=SERVE_CHUNK_BYTES,
                                     start=start, length=length):
                handler.wfile.write(block)
        except (BrokenPipeError, ConnectionResetError):         # 客户端中途关掉（正常）
            return

    # ── 内部
    def _open_run(self, run_id: str) -> RunStore:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
            raise ConfigError("运行编号格式不合法")
        books_root = self.workspace.root / "data" / "books"
        if books_root.is_dir():
            for book_dir in sorted(books_root.iterdir()):
                root = book_runs_root(self.workspace.root, book_dir.name)
                if (root / run_id / "state.json").is_file():
                    return RunStore.open(root, run_id)
        raise ConfigError(f"找不到运行 {run_id!r}")

    def _check_write_origin(self, handler) -> None:
        """写请求：**同源 + 启动令牌**（方案 §5：限制为本机操作）。"""
        token = handler.headers.get("X-Aifs-Token", "")
        if not secrets.compare_digest(token, self.token):
            raise ContractError(
                "写请求缺少或带错启动令牌（X-Aifs-Token）。方案 §5：写请求验证同源与"
                "服务启动令牌，限制为本机操作。")
        origin = handler.headers.get("Origin")
        referer = handler.headers.get("Referer")
        if origin is None and referer:
            parsed = urlsplit(referer)
            origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin is not None and origin != self.origin:
            raise ContractError(f"写请求来自非同源：{origin!r}")

    def _read_body(self, handler) -> dict:
        length = int(handler.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = handler.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ContractError(f"请求体不是合法 JSON：{exc}") from exc
        if not isinstance(payload, dict):
            raise ContractError("请求体必须是对象")
        return payload

    def _serve_static(self, handler, path: str) -> None:
        rel = "index.html" if path in ("/", "") else "video.html" if path == "/video" else path
        found = self._video_static(path) or self._static(rel)
        if found is None:
            self._respond(handler, HTTPStatus.NOT_FOUND, {"error": f"没有这个文件：{path}"})
            return
        blob, ctype = found
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", ctype)
        handler.send_header("Content-Length", str(len(blob)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()
        self._write_body(handler, blob)

    def _video_static(self, path: str) -> tuple[bytes, str] | None:
        package = Path(__file__).resolve().parent.parent
        target = None
        if path == "/video-result.html":
            target = package / "resources/video-analysis/index.html"
        elif path in {"/video-analysis/result.js", "/video-analysis/result.css"}:
            target = package / "resources" / path.lstrip("/")
        elif path.startswith("/video-assets/"):
            rel = path.removeprefix("/video-assets/")
            if rel in {"audible-audio.js", "study-math.js"}:
                asset = "assets/" + rel
            elif rel in {"katex.min.js", "katex.min.css"} or re.fullmatch(r"fonts/[A-Za-z0-9_.-]+\.(?:woff2?|ttf)", rel):
                asset = "vendor/" + rel
            else:
                return None
            # BuildWithAdapters installs the renderer under _legacy_tree; an
            # installed app must work with a workspace containing only inputs.
            for root in (package / "_legacy_tree/blocks/render", package / "blocks/render",
                         package.parents[1] / "blocks/render", self.workspace.root / "blocks/render"):
                candidate = root / asset
                if candidate.is_file():
                    target = candidate
                    break
        if target and target.is_file():
            return target.read_bytes(), mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return None

    def _respond(self, handler, status: int, payload: dict) -> None:
        _assert_no_secrets(payload)
        blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Content-Length", str(len(blob)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        self._write_body(handler, blob)

    @staticmethod
    def _write_body(handler, blob: bytes) -> None:
        """写正文；`HEAD` 请求**一个字节都不写**（头部照发）。"""
        if getattr(handler, "_head_only", False):
            return
        handler.wfile.write(blob)


def _assert_no_secrets(payload: dict) -> None:
    """方案 §5：密钥不回传浏览器。**发现就报错**，而不是静默删掉。"""
    def walk(node, trail=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).lower() in FORBIDDEN_RESPONSE_KEYS and value:
                    raise ContractError(
                        f"响应里出现了不该回传浏览器的字段 {trail}.{key}（方案 §5："
                        f"密钥不回传浏览器、不写日志，配置只保留角色与密钥引用）")
                walk(value, f"{trail}.{key}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{trail}[{i}]")
    walk(payload)


def _query(path: str) -> dict:
    return {key: values[-1] for key, values in parse_qs(urlsplit(path).query).items()}


def _status_for(exc: AifsError) -> int:
    from ..errors import BudgetExceeded, Cancelled, CapabilityError, InputError
    from ..pipeline.lease import BookBusyError
    if isinstance(exc, (RunConflict, BookBusyError)):
        return HTTPStatus.CONFLICT
    if isinstance(exc, (ContractError, InputError, ConfigError, CapabilityError)):
        return HTTPStatus.BAD_REQUEST
    if isinstance(exc, BudgetExceeded):
        return HTTPStatus.TOO_MANY_REQUESTS
    if isinstance(exc, Cancelled):
        return HTTPStatus.CONFLICT
    return HTTPStatus.INTERNAL_SERVER_ERROR


__all__ = ["ROUTE_RE", "FORBIDDEN_RESPONSE_KEYS", "STATIC_SUFFIXES", "RunningTask",
           "ControlState", "ControlServer"]
