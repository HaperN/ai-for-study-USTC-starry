"""统一命令行：`aifs`（等价模块入口 `python -m aiforstudy`）。

方案 §4：

> 安装后提供 `aifs`，等价模块入口为 `python -m aiforstudy`。

两个贯穿全部子命令的规则：

1. **退出码不许塌陷**（方案 §9）。每个子命令只负责把异常交给 `main()`，
   由错误类型映射成 0/1/2/3/130；**崩溃与"发现违规"必须是不同的码**。
2. **`plan` 零副作用**。方案 §11 逐条点名：不创建运行目录、不写 `pdf.path`、
   不改 scope、不修 doctor 记录、不追加 usage。所以 `plan` 分支里没有一次写操作。

M1 落地 `books` / `plan` / `capabilities` / `sources`。
`run` / `prepare` / `resume` / `check` / `repair` / `ui` / `test` 随 M2–M8 加入；
在那之前对它们不设"看起来能跑"的占位实现。
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from pathlib import Path
import json
import time
import sys

from . import __version__
from .contracts.enums import DeliveryFormat, Goal, PreparePolicy, Profile, SelectorKind
from .contracts.request import RunRequest, Selectors
from .pipeline.graph import STAGE_BY_NAME
from .core.registry import load_registry
from .core.workspace import ENV_WORKSPACE, open_workspace
from .errors import AifsError, EXIT_OK, EXIT_USAGE

PROG = "aifs"


@contextmanager
def _run_cancellation():
    """Route SIGINT to workers before the runner waits for their final accounting."""
    import signal
    import threading
    from .pipeline.services import CancelToken

    token = CancelToken()
    main_thread = threading.current_thread() is threading.main_thread()
    previous = signal.getsignal(signal.SIGINT) if main_thread else None

    def interrupt(_signum, _frame):
        if not token.cancelled:
            token.cancel("用户取消（SIGINT）")

    if main_thread:
        signal.signal(signal.SIGINT, interrupt)
    try:
        yield token
    finally:
        if main_thread:
            signal.signal(signal.SIGINT, previous)


# ────────────────────────────────────────── 输出小工具（只影响人读的那一份）

def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    _pretty(payload)


def _pretty(payload: dict) -> None:
    """人读输出。刻意写得朴素——界面层（M8）才是给人看的地方。"""
    kind = payload.get("_meta", {}).get("file_type", "")
    if kind == "books_view":
        print(f"工作区：{payload['workspace']}")
        print(f"注册表：{payload['registry']}（修订 {payload['registry_revision']}）")
        print(f"共 {len(payload['books'])} 本：")
        for book in payload["books"]:
            print(f"  {book['book_id']:<24} {book['title']}")
            print(f"      解析 {book['parse']}")
            print(f"      教案 {book['lessons']}")
            if book.get("parse_present"):
                pass
            print(f"      catalog：{'有' if book['catalog_present'] else '缺'}"
                  f"｜概念索引：{'有' if book['concepts_present'] else '缺'}")
        return
    if kind == "plan_view":
        print(f"书：{payload['book_id']}（{payload['title']}）")
        print(f"请求指纹：{payload['request_digest']}")
        print(f"目标 {payload['goal']}｜档位 {payload['profile']}｜准备策略 {payload['prepare_policy']}")
        print(f"选择：{payload['selectors']}")
        if payload["resolved_scope"] is None:
            print("范围：**尚未解析**（冷启动）——计划不虚构单元数")
        else:
            print(f"范围：{len(payload['resolved_scope'])} 个教学单元")
            for unit in payload["resolved_scope"][:8]:
                print(f"    {unit}")
            if len(payload["resolved_scope"]) > 8:
                print(f"    … 另 {len(payload['resolved_scope']) - 8} 个")
        if payload["preparation"]:
            print("必须先做的基础准备：")
            for task in payload["preparation"]:
                print(f"    [{task['stage']}] {task['reason']}")
        for note in payload["notes"]:
            print(f"  注：{note}")
        return
    if kind == "capabilities_view":
        print(f"可用能力（{len(payload['available'])} 项）：")
        for item in payload["items"]:
            mark = "✓" if item["available"] else "×"
            print(f"  {mark} {item['name']:<26} {item['detail']}")
        print(f"可用图后端：{payload['figure_backends']}")
        return
    if kind == "sources_view":
        for art in payload["artifacts"]:
            print(f"{art['name']} {art.get('version') or ''}")
            print(f"    payload（生产真正跑的）：{art['payload']}（{art['payload_files']} 个文件）")
            if art.get("source_hash"):
                print(f"    来源制品哈希：{art['source_hash']}")
            if art.get("recorded_source_hash"):
                print(f"    安装时记录的来源哈希：{art['recorded_source_hash']}")
            if art.get("drift"):
                print("    ⚠️ **漂移**：同一版本号、不同字节（见 accepted_drift 裁决）")
            verdict = art.get("source_verdict") or {}
            if verdict.get("verdict"):
                mark = {"verified-identical": "✓", "verified-same-file-set": "✓"}.get(
                    verdict["verdict"], "⚠️")
                print(f"    {mark} 来源判定：{verdict['verdict']}")
                if verdict.get("note"):
                    print(f"        {verdict['note']}")
            wheel_src = (art.get("source_wheel") or {})
            if wheel_src.get("verdict"):
                mark = "✓" if wheel_src["verdict"] == "verified-identical" else "⚠️"
                print(f"    {mark} 来源 wheel：{wheel_src['verdict']}"
                      f"（盘 {wheel_src.get('files_dir')} 文件 / wheel {wheel_src.get('files_wheel')} 文件）")
                if wheel_src.get("sha256"):
                    print(f"        {wheel_src['sha256']}")
                if wheel_src.get("note"):
                    print(f"        {wheel_src['note']}")
            for note in art.get("notes") or []:
                if note:
                    print(f"    注：{note}")
        if payload["problems"]:
            print("\n问题：")
            for problem in payload["problems"]:
                print(f"  ✗ {problem}")
        else:
            print("\n✓ 依赖来源与锁一致")
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# ────────────────────────────────────────── 子命令

def cmd_books(args) -> int:
    workspace = open_workspace(args.workspace)
    registry = load_registry(workspace)
    books = []
    for book_id in registry.list_books():
        ctx = registry.resolve(book_id)
        books.append({
            "book_id": ctx.book_id, "title": ctx.title, "revision": ctx.revision,
            "parse": str(ctx.parse), "lessons": str(ctx.lessons),
            "catalog_present": (ctx.parse / "chapters.json").is_file(),
            "concepts_present": (ctx.parse / "concepts.json").is_file(),
            "sources": [s.as_dict() for s in ctx.sources],
        })
    _emit({"_meta": {"file_type": "books_view", "schema_version": "1.0"},
           "workspace": str(workspace.root), "registry": str(registry.path),
           "registry_revision": registry.revision, "books": books}, args.json)
    return EXIT_OK


def _selectors_from_args(args) -> Selectors:
    """把三个互斥开关收成一个选择器。

    ⚠️ argparse 的 `add_mutually_exclusive_group(required=True)` 已经保证了"选了一项且
    只选一项"，这里只做形状转换。**不再自己抛 SystemExit**：那会绕过统一退出码映射
    （`SystemExit` 的码是 argparse 的 2，看起来对，但它跳过了 `AifsError` 的分类）。
    """
    if getattr(args, "all", False):
        return Selectors(kind=SelectorKind.ALL)
    if getattr(args, "chapters", None):
        return Selectors(kind=SelectorKind.CHAPTERS,
                         values=tuple(v.strip() for v in args.chapters.split(",") if v.strip()))
    return Selectors(kind=SelectorKind.UNITS,
                     values=tuple(v.strip() for v in (args.units or "").split(",") if v.strip()))


def cmd_plan(args) -> int:
    from .pipeline.planner import plan as build_plan

    workspace = open_workspace(args.workspace)
    registry = load_registry(workspace)
    selectors = _selectors_from_args(args)
    request = RunRequest(
        book_id=args.book or "",
        selectors=selectors,
        goal=Goal(args.goal),
        profile=Profile(args.profile),
        prepare_policy=PreparePolicy(args.prepare),
        parallelism=_resolve_parallel(args, workspace),
        delivery_formats=tuple(DeliveryFormat(f) for f in args.delivery),
    ).validate()

    scope_plan = build_plan(request, registry)
    ctx = registry.resolve(request.book_id)
    payload = dict(scope_plan.as_dict())
    payload["_meta"] = {"file_type": "plan_view", "schema_version": "1.0"}
    payload["title"] = ctx.title
    payload["prepare_policy"] = request.prepare_policy.value
    payload["requests"] = request.as_dict()
    # 计划页要能把"为什么重跑/为什么复用"讲清楚：这里给出依据而不是结论
    payload["reuse_basis"] = {
        "catalog": payload.get("context_ref"),
        "note": ("复用判定要按 stage version + 实际输入哈希 + 提示词/模型参数算 digest；"
                 "文件存在不是缓存有效证据（方案 §11）。M6 接入缓存账本后给出逐项结论。"),
    }
    _emit(payload, args.json)
    return EXIT_OK


def cmd_capabilities(args) -> int:
    from .providers.capabilities import probe

    workspace = open_workspace(args.workspace)
    caps = probe(workspace)
    payload = dict(caps.as_dict())
    payload["_meta"] = {"file_type": "capabilities_view", "schema_version": "1.0"}
    _emit(payload, args.json)
    return EXIT_OK


def cmd_sources(args) -> int:
    from .providers import auditor, normalizer
    from .providers.artifacts import SourceLock

    workspace = open_workspace(args.workspace)
    lock = SourceLock.load(workspace.root / "vendor.lock.json")
    problems: list[str] = []
    artifacts = []

    source_wheel = None
    expected = lock.expected("normalizer") or {}
    declared = (expected.get("declared_source_wheel") or {}).get("declared")
    if declared:
        source_wheel = Path(declared)
    art, probs = normalizer.verify(workspace, lock, source_wheel=source_wheel)
    # 把锁里记的**来源判定**带上：它是"这份东西是哪来的"的结论，
    # 只靠现场探测拿不到（探测只能看到装好之后的样子）
    artifacts.append({**expected, **art.as_dict()})
    problems.extend(probs)

    art, probs = auditor.verify(workspace, lock)
    artifacts.append({**(lock.expected("auditor_vendor") or {}), **art.as_dict()})
    problems.extend(probs)

    payload = {"_meta": {"file_type": "sources_view", "schema_version": "1.0"},
               "lock": str(lock.path),
               "accepted_drift": dict(lock.accepted_drift),
               "artifacts": artifacts, "problems": problems,
               "问题": bool(problems)}
    _emit(payload, args.json)
    return EXIT_USAGE if problems else EXIT_OK


# ────────────────────────────────────────── 运行（M2 起）

def _print_run_result(result, store, as_json: bool) -> None:
    payload = dict(result.as_dict())
    payload["_meta"] = {"file_type": "run_view", "schema_version": "1.0"}
    payload["run_dir"] = str(store.root)
    payload["last_event_seq"] = store.events.last_seq
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(f"运行 {result.run_id}：{result.status}（退出码 {result.exit_code}）")
    print(f"  目标 {result.goal}｜档位 {result.profile}｜耗时 {result.elapsed_seconds:.1f}s")
    print(f"  执行状态：{result.execution_counts}")
    print(f"  质量状态：{result.quality_counts}")
    print(f"  scope_accepted={result.scope_accepted}｜release_ready={result.release_ready}")
    if result.truncated_at:
        print(f"  ⚠️ 因 --until {result.truncated_at} 早停：**未交付**，不获得 release_ready")
    if result.failure_stage:
        print(f"  失败阶段：{result.failure_stage}")
    if result.failed_nodes:
        print(f"  失败节点：{result.failed_nodes[:5]}")
    if result.blocked_nodes:
        items = list(result.blocked_nodes.items())[:5]
        print(f"  被阻断节点（未拿旧输出接着跑）：{items}")
    if result.recovery_hint:
        print(f"  恢复建议：{result.recovery_hint}")
    print(f"  运行目录：{store.root}")


def cmd_import_candidate(args) -> int:
    from .pipeline.engine import build_engine
    from .pipeline.state import RunStore, book_runs_root
    from .pipeline.candidate_import import import_legacy_candidate
    from .core.jsonio import write_json_atomic
    workspace = open_workspace(args.workspace)
    request = RunRequest(book_id=args.book, selectors=Selectors(SelectorKind.UNITS, (args.unit,)))
    engine = build_engine(request, workspace)
    fields=json.loads(Path(args.v4_fields).read_text(encoding="utf-8"))
    import_legacy_candidate(Path(args.source),book_runs_root(workspace.root,args.book)/"candidate-preflight",book_id=args.book,
                            source_run_id=args.source_run or "",v4_fields=fields,expected_unit_id=args.unit,dry_run=True)
    store = RunStore.create(book_runs_root(workspace.root, args.book), request, engine.plan,
                            control=engine.services.control)
    try:
        report = import_legacy_candidate(Path(args.source), store.work, book_id=args.book,
                                         source_run_id=args.source_run or "",
                                         v4_fields=fields,
                                         expected_unit_id=args.unit)
        if report["unit_id"] != args.unit:
            raise ValueError("导入单元与 --unit 不一致")
        store.set_status("queued", migration=report)
        report["run_id"] = store.run_id
        write_json_atomic(store.root / "migration-report.json", report)
        _emit(report, args.json)
        return EXIT_OK
    except Exception as exc:
        store.set_status("failed", error=str(exc))
        raise


def cmd_run(args) -> int:
    from .contracts.enums import DeliveryFormat, Goal, PreparePolicy, Profile
    from .contracts.request import RunRequest
    from .pipeline.engine import run_request

    workspace = open_workspace(args.workspace)
    request = RunRequest(
        book_id=args.book or "",
        selectors=_selectors_from_args(args),
        goal=Goal(args.goal),
        profile=Profile(args.profile),
        prepare_policy=PreparePolicy(args.prepare),
        parallelism=_resolve_parallel(args, workspace),
        delivery_formats=tuple(DeliveryFormat(f) for f in (args.delivery
                                                           or [DeliveryFormat.CHAPTER_HTML.value])),
    ).validate()
    with _run_cancellation() as cancel:
        result, store, _engine = run_request(request, workspace, until=args.until, cancel=cancel)
    _print_run_result(result, store, args.json)
    return result.exit_code


def cmd_prepare(args) -> int:
    """`aifs prepare`：只做**全书基础准备**（方案 §4）。

    §4 原话：「`prepare` 命令的目标天然为全书基础准备，**不要求用户另选章节**。」
    所以这里不接受 --chapters/--units：它做的事本来就是整本的（解析 + 完整概念索引），
    让它"看起来只解析了所选章"是一种误导。
    """
    from .contracts.enums import DeliveryFormat, Goal, PreparePolicy, Profile
    from .contracts.request import RunRequest, Selectors
    from .contracts.enums import SelectorKind
    from .pipeline.engine import run_request

    workspace = open_workspace(args.workspace)
    request = RunRequest(
        book_id=args.book or "",
        selectors=Selectors(kind=SelectorKind.ALL),
        goal=Goal.PREPARE,
        profile=Profile(args.profile),
        prepare_policy=PreparePolicy(args.prepare),
        parallelism=_resolve_parallel(args, workspace),
        delivery_formats=(DeliveryFormat.CHAPTER_HTML,),
    ).validate()
    with _run_cancellation() as cancel:
        result, store, _engine = run_request(request, workspace, until=args.until, cancel=cancel)
    _print_run_result(result, store, args.json)
    return result.exit_code


def _find_run(workspace, run_id: str):
    from .pipeline.state import book_runs_root
    root = workspace.root / "data" / "books"
    if root.is_dir():
        for book_dir in sorted(root.iterdir()):
            candidate = book_runs_root(workspace.root, book_dir.name) / run_id
            if (candidate / "state.json").is_file():
                return book_dir.name, candidate
    from .errors import ContractError
    raise ContractError(
        f"找不到运行 {run_id!r}。运行记录在 data/books/<book_id>/runs/<run_id>/；"
        f"可用 `aifs runs --book <book_id>` 列出现有的。")


def _resolve_parallel(args, workspace) -> int:
    """修改计划 E7：未指定 --parallel 时按 profile 配置（release 为 18/6/12），不再压成 2。"""
    if getattr(args, "parallel", None) is not None:
        return args.parallel
    from .pipeline.execution_control import load_policy
    goal = getattr(args, "goal", None) or "deliver"
    if args.command == "prepare":
        goal = "prepare"
    return load_policy(workspace, args.profile, goal).parallelism


def cmd_preview(args) -> int:
    """Render an explicitly labelled diagnostic snapshot without running models."""
    from .blocks.render.diagnostics import build_diagnostic_preview
    workspace = open_workspace(args.workspace)
    _book_id, run_dir = _find_run(workspace, args.run)
    result = build_diagnostic_preview(run_dir, unit_ids=[s.strip() for s in args.units.split(',') if s.strip()],
                                      out_dir=run_dir / 'diagnostics')
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print('诊断预览，尚非验收交付：' + str(Path(result['output_dir']) / 'index.html'))
        print('媒体位置：' + str(Path(result['output_dir']) / 'media-placement-plan.json'))
    return EXIT_OK


def cmd_resume(args) -> int:
    """续跑：**先核对请求指纹**，输入变了就要求重新发起（方案 §11）。"""
    from .contracts.request import RunRequest
    from .pipeline.engine import resume_request

    workspace = open_workspace(args.workspace)
    book_id, run_dir = _find_run(workspace, args.run)
    request = RunRequest.from_dict(
        json.loads((run_dir / "request.json").read_text(encoding="utf-8")))
    with _run_cancellation() as cancel:
        result, store, _engine = resume_request(request, args.run, workspace, until=args.until, cancel=cancel,
                                                parallelism=args.parallel)
    _print_run_result(result, store, args.json)
    return result.exit_code


def cmd_runs(args) -> int:
    """列出某本书的运行记录（只读）。"""
    from .pipeline.state import book_runs_root, read_state_summary

    workspace = open_workspace(args.workspace)
    registry = load_registry(workspace)
    book_id = registry.require(args.book).book_id
    root = book_runs_root(workspace.root, book_id)
    runs = []
    if root.is_dir():
        for path in sorted(root.iterdir()):
            if not (path / "state.json").is_file():
                continue
            runs.append(read_state_summary(workspace.root, book_id, path.name))
    payload = {"_meta": {"file_type": "runs_view", "schema_version": "1.0"},
               "book_id": book_id, "runs_root": str(root), "runs": runs}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"书 {book_id}｜运行目录 {root}")
        if not runs:
            print("  （还没有运行记录）")
        for item in runs:
            print(f"  {item['run_id']}  {item['status'] or '-'}  "
                  f"{item['goal']}/{item['profile']}  节点 {item['node_counts']}")
    return EXIT_OK


def cmd_events(args) -> int:
    """读事件流（只读）：实现方案 §5 的"短轮询事件游标"（`?after=N`）。"""
    workspace = open_workspace(args.workspace)
    book_id, run_dir = _find_run(workspace, args.run)
    from .pipeline.state import RunStore
    store = RunStore(run_dir, args.run)
    events = [e.as_dict() for e in store.events_after(after=args.after, limit=args.limit)]
    payload = {"_meta": {"file_type": "events_view", "schema_version": "1.0"},
               "run_id": args.run, "book_id": book_id,
               "after": args.after, "last_seq": store.events.last_seq, "events": events}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for event in events:
            print(f"  [{event['seq']:>4}] {event['kind']:<18} {event['stage']:<12} "
                  f"{event['message'][:80]}")
        print(f"（last_seq={payload['last_seq']}；下次用 --after {payload['last_seq']} 续读）")
    return EXIT_OK


def cmd_check_render(args) -> int:
    """修改计划 R4：最终交付产物渲染检验（只读，只出一份定位报告）。"""
    from .checks.render_audit import KIND_LABELS, audit_delivery
    workspace = open_workspace(args.workspace)
    delivery = Path(args.delivery_dir).resolve()
    out_dir = Path(args.out) if args.out else delivery.with_name(delivery.name + '-render-check')
    report = audit_delivery(args.delivery_dir, out_dir=out_dir, workspace_root=workspace.root,
                            ready_timeout=args.ready_timeout, unit_timeout=args.unit_timeout)
    summary = report['summary']
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"入口 {summary['entries']} 个；单元检查 {summary['units_checked']} 次，"
              f"{summary['units_with_issues']} 次有问题")
        for name in summary['entries_not_ready']:
            print(f"  未就绪入口：{name}")
        for kind, count in summary['issue_counts'].items():
            print(f"  {KIND_LABELS.get(kind, kind)}：{count}")
        print('报告：' + str(out_dir / 'render-check.md'))
    return EXIT_OK



def cmd_shelf(args) -> int:
    """修改计划 S：本机书架（只监听 127.0.0.1；教材未获授权，不对外发布）。"""
    from .shelf.library import add_book, library_index
    from .shelf.server import make_server
    workspace = open_workspace(args.workspace)
    library = Path(args.library) if args.library else workspace.root / 'data' / 'shelf'
    library.mkdir(parents=True, exist_ok=True)
    if args.add:
        delivery = Path(args.add)
        manifest = add_book(delivery, library, book_id=args.book_id or delivery.name,
                            version=args.version or time.strftime('%Y%m%d-%H%M%S'),
                            title=args.title or delivery.name, intro=args.intro or '')
        print(f"已加入书库：{manifest['title']}（版本 {manifest['versions'][-1]['id']}）→ {library}")
        if args.no_serve:
            return EXIT_OK
    books = library_index(library)['books']
    httpd = make_server(library, host='127.0.0.1', port=args.port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    if args.json:
        print(json.dumps({'url': url, 'library': str(library), 'books': len(books)}, ensure_ascii=False))
    else:
        print(f"本机书架：{url}（书 {len(books)} 本；书库 {library}）")
        print("只监听本机。Ctrl-C 退出。")
    if args.once:
        httpd.server_close()
        return EXIT_OK
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止书架服务")
    finally:
        httpd.server_close()
    return EXIT_OK


# ────────────────────────────────────────── 本地界面（M8）

def cmd_ui(args) -> int:
    """`aifs ui`：起本机 HTTP 服务（默认只绑 127.0.0.1）。

    方案 §5：服务只负责输入、状态与结果展示；规划/执行/取消/恢复都调同一个引擎。
    """
    from .ui import ControlServer

    workspace = open_workspace(args.workspace)
    server = ControlServer(workspace, host=args.host, port=args.port)
    httpd = server.make_server()
    url = f"http://{server.host}:{server.port}/"
    if args.json:
        print(json.dumps({"url": url, "token": server.token,
                          "workspace": str(workspace.root)}, ensure_ascii=False))
    else:
        print(f"本地控制台：{url}")
        print(f"启动令牌（写请求需要，浏览器自动带上）：{server.token}")
        print("Ctrl-C 退出。刷新或关闭页面**不会**取消任务。")
    if args.open:
        import webbrowser
        try:
            webbrowser.open(url)
        except Exception:                                        # noqa: BLE001
            pass
    if args.once:
        return EXIT_OK
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止服务（正在跑的任务会落到 run 目录，可在下次启动后 resume）")
    finally:
        httpd.server_close()
    return EXIT_OK


# ────────────────────────────────────────── 解析与入口

def cmd_community(args) -> int:
    """Run a local shared reader without invoking any generation services."""
    from .community.server import make_server
    try:
        recognizer = None
        if bool(args.ocr_python) != bool(args.ocr_model):
            raise ValueError('识别需要同时指定 --ocr-python 和 --ocr-model。')
        if args.ocr_python:
            from .community.recognition import LocalRecognizer
            recognizer = LocalRecognizer(Path(args.ocr_python), Path(args.ocr_model))
        server = make_server(Path(args.reader), Path(args.database), port=args.port, recognizer=recognizer)
    except (ValueError, OSError) as exc:
        print(f"无法启动共享批注：{exc}", file=sys.stderr)
        return EXIT_USAGE
    import signal
    import threading
    previous_term = None
    def stop_community(signum, frame):
        raise KeyboardInterrupt
    if threading.current_thread() is threading.main_thread():
        previous_term = signal.signal(signal.SIGTERM, stop_community)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(json.dumps({"url": url, "book": server.context["book"],
                      "revision": server.context["revision"]}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if previous_term is not None:
            signal.signal(signal.SIGTERM, previous_term)
    return EXIT_OK


def cmd_integrated(args) -> int:
    import signal
    import threading
    from .integrated.config import load_config, check
    from .integrated.server import running
    try:
        config = load_config(args.config)
        if args.check:
            print(json.dumps(check(config), ensure_ascii=False))
            return EXIT_OK
        stopped = threading.Event()
        with running(config) as server:
            previous = None
            if threading.current_thread() is threading.main_thread():
                previous = signal.signal(signal.SIGTERM, lambda *_: stopped.set())
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/start"
                print(json.dumps({"url":url, "workbench_url":server.workbench_url}, ensure_ascii=False), flush=True)
                stopped.wait()
            except KeyboardInterrupt:
                pass
            finally:
                if previous is not None:
                    signal.signal(signal.SIGTERM, previous)
        return EXIT_OK
    except (ValueError, OSError) as exc:
        print(f"无法打开学习服务：{exc}", file=sys.stderr)
        return EXIT_USAGE


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog=PROG,
        description="AIforStudy 统一引擎（教材 PDF → 解析 → 教案 → 审核 → 交互 → 离线 HTML）")
    ap.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    ap.add_argument("--workspace", default=None,
                    help=f"工作区根（含 config/books.json）。"
                         f"也可用环境变量 {ENV_WORKSPACE}；都不给则从当前目录向上找")
    ap.add_argument("--json", action="store_true", help="输出 JSON（给机器/agent 读）")
    sub = ap.add_subparsers(dest="command", metavar="命令")

    def _sub(name: str, **kw):
        """建子命令，并把全局开关也挂上去。

        ⚠️ 为什么每个子命令都再挂一次 `--json` / `--workspace`：argparse 的全局选项必须写在
        子命令**之前**（`aifs --json plan ...`）。实测用户/脚本很自然会写成
        `aifs plan ... --json`，那会得到 "unrecognized arguments" —— 一个纯粹由参数位置
        造成的失败。用 `default=SUPPRESS` 让"没给"时不覆盖全局值，两种写法都成立。
        """
        parser = sub.add_parser(name, **kw)
        parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                            help=argparse.SUPPRESS)
        parser.add_argument("--workspace", default=argparse.SUPPRESS,
                            help=argparse.SUPPRESS)
        return parser

    p = _sub("books", help="列出注册表里的书与它们的基础产物状态（只读）")
    p.set_defaults(func=cmd_books)

    p = _sub("plan", help="查看本次范围的计划（**只读**：不建目录、不写产物、不花钱）")
    p.add_argument("--book", required=True, help="书 id（**必填**；方案 §4：不退回历史默认书）")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--chapters", help="章选择，逗号分隔；支持区间，例 CH02-CH04")
    group.add_argument("--units", help="教学单元选择，逗号分隔，例 CH03-S01")
    group.add_argument("--all", action="store_true", help="整本")
    p.add_argument("--goal", choices=[g.value for g in Goal], default=Goal.DELIVER.value)
    p.add_argument("--profile", choices=[pr.value for pr in Profile], default=Profile.RELEASE.value)
    p.add_argument("--prepare", choices=[pp.value for pp in PreparePolicy],
                   default=PreparePolicy.ENSURE.value)
    p.add_argument("--parallel", type=int, default=None, help="并行度（1–32）；不写则按 profile 配置")
    # Keep the sole supported choice visible in CLI help; unsupported formats fail early.
    p.add_argument("--delivery", action="append",
                   choices=[f.value for f in DeliveryFormat],
                   default=None, help="交付形式，可重复；默认 chapter-html")
    p.set_defaults(func=cmd_plan)

    p = _sub("capabilities", help="预检本机能力（后端/浏览器/Node…）")
    p.set_defaults(func=cmd_capabilities)

    p = _sub("sources", help="核对依赖来源与 vendor.lock.json 是否一致（只读）")
    p.set_defaults(func=cmd_sources)

    p = _sub("import-candidate", help="将旧内容导入一个新 run 候选；不继承通过状态")
    p.add_argument("--book", required=True)
    p.add_argument("--unit", required=True)
    p.add_argument("--source", required=True, help="明确的旧 lesson JSON 路径")
    p.add_argument("--source-run", default="")
    p.add_argument("--v4-fields", required=True, help="显式 schema 4 教学/读者字段 JSON；不推断缺失要求")
    p.set_defaults(func=cmd_import_candidate)

    p = _sub("run", help="执行所选范围（单章 / 多章 / 整本走**同一张图**）")
    p.add_argument("--book", required=True, help="书 id（必填；不退回历史默认书）")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--chapters", help="章选择，逗号分隔；支持区间，例 CH02-CH04")
    group.add_argument("--units", help="教学单元选择，逗号分隔")
    group.add_argument("--all", action="store_true", help="整本")
    p.add_argument("--goal", choices=[g.value for g in Goal], default=Goal.DELIVER.value)
    p.add_argument("--profile", choices=[pr.value for pr in Profile], default=Profile.RELEASE.value)
    p.add_argument("--prepare", choices=[pp.value for pp in PreparePolicy],
                   default=PreparePolicy.ENSURE.value)
    p.add_argument("--parallel", type=int, default=None, help="并行度；不写则按 profile 配置")
    # Keep the sole supported choice visible in CLI help; unsupported formats fail early.
    p.add_argument("--delivery", action="append", choices=[f.value for f in DeliveryFormat])
    p.add_argument("--until", choices=tuple(STAGE_BY_NAME), help="开发用早停：只跑到这个阶段。结果标为阶段完成、**未交付**")
    p.set_defaults(func=cmd_run)

    p = _sub("prepare", help="只做全书基础准备（解析 + 完整概念索引）；不要求选章节")
    p.add_argument("--book", required=True)
    p.add_argument("--profile", choices=[pr.value for pr in Profile], default=Profile.RELEASE.value)
    p.add_argument("--prepare", choices=[pp.value for pp in PreparePolicy],
                   default=PreparePolicy.ENSURE.value)
    p.add_argument("--parallel", type=int, default=None, help="并行度；不写则按 profile 配置")
    p.add_argument("--until", choices=tuple(STAGE_BY_NAME))
    p.set_defaults(func=cmd_prepare)

    p = _sub("preview", help="从已有产物生成诊断阅读页；不调用模型、不授予通过状态")
    p.add_argument("--run", required=True)
    p.add_argument("--units", required=True, help="教学单元，逗号分隔")
    p.set_defaults(func=cmd_preview)

    p = _sub("resume", help="续跑一次既有运行（先核对请求指纹；输入变了会拒绝）")
    p.add_argument("--run", required=True, help="运行 id（见 `aifs runs`）")
    p.add_argument("--until", choices=tuple(STAGE_BY_NAME))
    p.add_argument("--parallel", type=int, default=None,
                   help="停止后续跑时改并发（不超过 profile 配置）；会写交接记录，尝试账与返修账全部保留")
    p.set_defaults(func=cmd_resume)

    p = _sub("runs", help="列出某本书的运行记录（只读）")
    p.add_argument("--book", required=True)
    p.set_defaults(func=cmd_runs)

    p = _sub("ui", help="启动本机控制台（默认绑 127.0.0.1；刷新页面不取消任务）")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8788)
    p.add_argument("--open", action="store_true", help="启动后打开浏览器")
    p.add_argument("--once", action="store_true", help="只打印地址与令牌就退出（供脚本/测试用）")
    p.set_defaults(func=cmd_ui)

    p = _sub("events", help="读事件流（只读；短轮询游标 --after N）")
    p.add_argument("--run", required=True)
    p.add_argument("--after", type=int, default=0)
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_events)

    p = _sub("integrated", help="本机教材、练习与共享感悟")
    p.add_argument("--config", required=True, help="学习服务配置文件")
    p.add_argument("--check", action="store_true", help="核对配置与教材版本")
    p.set_defaults(func=cmd_integrated)

    p = _sub("community", help="本地昵称共享感悟；教材只读，不调用模型")
    p.add_argument("--reader", required=True, help="已有教材的 book 目录")
    p.add_argument("--database", required=True, help="教材目录以外的 SQLite 数据文件")
    p.add_argument("--ocr-python", help="本地图片识别的独立 Python（可选）")
    p.add_argument("--ocr-model", help="已下载的 PaddleOCR-VL 模型目录（可选）")
    p.add_argument("--port", type=int, default=8791)
    p.set_defaults(func=cmd_community)

    p = _sub("shelf", help="本机书架与阅读批注（只监听 127.0.0.1，只做本地效果）")
    p.add_argument("--library", default=None, help="书库目录；默认 <工作区>/data/shelf")
    p.add_argument("--add", default=None, help="把一个交付目录（解压后的 HTML 包）复制进书库，作为新版本")
    p.add_argument("--book-id", default=None)
    p.add_argument("--version", default=None, help="版本号；默认用当前时间")
    p.add_argument("--title", default=None)
    p.add_argument("--intro", default=None)
    p.add_argument("--port", type=int, default=8790)
    p.add_argument("--no-serve", action="store_true", help="只加书，不启动服务")
    p.add_argument("--once", action="store_true", help="只打印地址就退出（供脚本/测试用）")
    p.set_defaults(func=cmd_shelf)

    p = _sub("check-render", help="检验最终交付 HTML 的渲染（只读，只出定位报告，不改交付物）")
    p.add_argument("delivery_dir", help="交付目录（解压后的 HTML 包）")
    p.add_argument("--out", default=None, help="报告目录；默认与交付目录同级的 <交付目录名>-render-check（不写进交付目录）")
    p.add_argument("--ready-timeout", type=float, default=180.0, help="每个入口等就绪信号的秒数")
    p.add_argument("--unit-timeout", type=float, default=30.0, help="每个单元等渲染的秒数")
    p.set_defaults(func=cmd_check_render)
    return ap


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_USAGE
    if getattr(args, "delivery", None) is None and args.command in ("plan", "run"):
        args.delivery = [DeliveryFormat.CHAPTER_HTML.value]
    if not hasattr(args, "until"):
        args.until = None
    try:
        return int(args.func(args))
    except AifsError as exc:
        # 退出码按错误类型走（方案 §9）；不是一律 1
        print(f"✗ {type(exc).__name__}: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        from .errors import EXIT_CANCELLED
        print("已取消", file=sys.stderr)
        return EXIT_CANCELLED


__all__ = ["PROG", "build_parser", "main", "cmd_books", "cmd_plan", "cmd_capabilities",
           "cmd_sources", "cmd_run", "cmd_prepare", "cmd_resume", "cmd_runs", "cmd_events",
           "cmd_ui", "cmd_preview", "cmd_check_render", "cmd_shelf"]
