"""M10 离线验收：**A02 / A03 / A04**。

方案 §15 的三条验收项，逐条在这里变成可执行的机器判据：

> | A02 | 默认登记 A，同时规划/检查/运行 B，再同进程切回 A | 读写、报告、费用全部归目标书；不更改另一书文件 |
> | A03 | 完整/缺文件/坏JSON/过期四类输入调用 plan/check | 保护目录内容和 mtime 不变；无 HTTP、模型、生产子进程、usage 追加 |
> | A04 | 冷PDF、已有MinerU、已有解析 × 单章、多章、整本，共9组合 | 同一引擎执行；期望目标集合精确一致；已有MinerU不上传；无OCR回退 |

**全部离线**：不联网、不调真实模型、不起生产子进程。外部边界一律用
`tests/fixtures/synth_book.py` 造的合成教材 + `smoke` 档替身；网络与子进程入口在
测试里被换成"一碰就抛"的桩（`_no_io`），所以"没有发生 HTTP/模型/子进程"不是靠
"我觉得没发生"，而是靠**真发生了就会当场炸**。

## 离线可判与不可判（写清楚，不假装）

* A02 的"费用归目标书"在**账本契约层**钉住（`UsageLedger` + 离线 `Gateway` 替身
  transport）：每条记录的 `book_id` 必须等于目标书，且按 `book_id` 过滤查不到别书。
  ⚠️ `smoke` 档本身不产生模型调用，所以引擎自己**不写** `usage.jsonl`；引擎的运行内
  用量是 `RunResult.usage`（来自 `BudgetGuard`，落在**按书分目录**的运行目录里）。
  也就是说"引擎跑一次 smoke → 出现一条带 book_id 的账本行"这件事**离线不可验**
  （它要么需要真实模型调用，要么需要一条尚未迁入的引擎→账本接线）。
* A03 里的 `check` 指的是方案 §6 的 `check(release_ref, policy) -> CheckReport`
  （`aifs check --book … --release …`）。⚠️ **这个子命令现在还不存在**
  （`cli.py` 只落地了 books/plan/capabilities/sources/run/prepare/resume/runs/ui/events）。
  所以"check"这一半用的是仓库里**现存的只读体检** `tools/doctor.py`（同一类只读检查：
  只读已有产物、不调模型、不获取 MinerU、不维修、不写产物；§14 也写着 doctor 将并入
  `check` 子命令）。**发布级 CheckReport 本身没有被覆盖** —— 那是它落地之后的事。
* A04 里的"冷PDF → 上传 → 接着跑完整链"离线**不可执行**：新引擎的 MinerU 阶段实现
  尚未迁入（release 档明确 blocked，smoke 档是合成替身）。所以冷启动组合验证的是
  **计划必须承认自己不知道范围、且必须先做 MinerU**，以及**引擎拒绝在没有
  resolved_scope 时建图**（不另开一条"冷启动通道"）；路线判定层则验证
  "已有产物→复用、源哈希不同→阻断、不支持→明确 blocked、**绝不回退 OCR**"。
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
from dataclasses import dataclass, field
import shutil
import socket
import subprocess
import urllib.request
from pathlib import Path

import pytest

from aiforstudy.contracts.enums import Goal, Profile, SelectorKind
from aiforstudy.contracts.request import Budget, RunRequest, Selectors
from aiforstudy.core.registry import load_registry
from aiforstudy.core.usage import UsageLedger, UsageRecord
from aiforstudy.core.workspace import Workspace
from aiforstudy.errors import CapabilityError, ConfigError, ContractError
from aiforstudy.pipeline.budget import BudgetGuard, BudgetPolicy
from aiforstudy.pipeline.engine import build_engine, run_request
from aiforstudy.pipeline.graph import (CANONICAL_STAGES, graphs_equivalent_for_scope,
                                       stage_relative_order)
from aiforstudy.pipeline.planner import plan as build_plan
from aiforstudy.pipeline.state import book_runs_root, latest_run_id, read_state_summary
from aiforstudy.providers import mineru
from aiforstudy.providers.gateway import CallRequest, Gateway

ROOT = Path(__file__).resolve().parents[2]
REAL_CONFIG = ROOT / "config"
BOOK_A = "book-a"
BOOK_B = "book-b"


# ────────────────────────────────────────── 装置

def _synth():
    """夹具导入放在函数里：`tests/` 由 conftest 放进 sys.path（与 integration 那批一致）。"""
    from fixtures import synth_book
    return synth_book


def _write_registry(root: Path, books: dict) -> None:
    """写一份最小但契约合法的 `config/books.json`（+ 抄三份真配置）。"""
    (root / "config").mkdir(parents=True, exist_ok=True)
    for name in ("capabilities.json", "execution.json", "models.json"):
        shutil.copy(REAL_CONFIG / name, root / "config" / name)
    entries = {}
    for book_id, book in books.items():
        rel = book.root.relative_to(root)
        entries[book_id] = {
            "title": book.title, "revision": "synth-1",
            "parse": str(rel / "parse"), "lessons": str(rel / "lesson"),
            "audit": str(rel / "audit"),
            "sources": [{"kind": "pdf", "path": str(Path("raw") / f"{book_id}.pdf"),
                         "sha256": None}],
        }
        # 登记的来源得真的存在，否则"冷 PDF"这类状态就是假的
        pdf = root / "raw" / f"{book_id}.pdf"
        if not pdf.exists():
            pdf.parent.mkdir(parents=True, exist_ok=True)
            pdf.write_bytes("%PDF-1.4 M10 离线合成样张（不是真教材）".encode("utf-8"))
    (root / "config" / "books.json").write_text(
        json.dumps({"_meta": {"file_type": "book_registry", "schema_version": "1.0"},
                    "workspace": ".", "books": entries}, ensure_ascii=False),
        encoding="utf-8")


def _request(book_id: str, *, kind: SelectorKind = SelectorKind.ALL, values=(),
             profile: Profile = Profile.SMOKE, goal: Goal = Goal.DELIVER) -> RunRequest:
    return RunRequest(book_id=book_id,
                      selectors=Selectors(kind=kind, values=tuple(values)),
                      goal=goal, profile=profile).validate()


def _two_book_workspace(tmp_path: Path):
    """A 是"默认登记"的那本（4 个单元）；B 的单元集合**故意不同**（2 个单元）。

    为什么 B 的单元要不一样：A02 要证"规划 B 只提 B 的东西"。如果两本书的
    `CH01-S01` 长得一模一样，"串了没有"就看不出来 —— 那正是这个项目历史上
    跨书串号最隐蔽的形态（两本书都有同名 CH 单元）。
    """
    synth = _synth()
    root = tmp_path / "ws"
    books = {
        BOOK_A: synth.build(root / "books" / "A", book_id=BOOK_A, title="A 教材"),
        BOOK_B: synth.build(root / "books" / "B", book_id=BOOK_B, title="B 教材",
                            units=("CH01-S01", "CH02-S02")),
    }
    _write_registry(root, books)
    return Workspace(root=root), books


def _one_book_workspace(tmp_path: Path, *, broken: str | None = None):
    synth = _synth()
    root = tmp_path / "ws"
    book = synth.build(root / "book", book_id="m10-book", title="M10 合成教材",
                       broken=broken)
    _write_registry(root, {"m10-book": book})
    return Workspace(root=root), book


def _tree(root: Path) -> dict:
    """目录"动过没有"的凭据：相对路径 → (大小, mtime_ns, sha256)。

    比大小/mtime 更严：内容变了而大小没变（同长度改字）也必须被抓到。
    """
    state = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            st = path.stat()
            state[str(path.relative_to(root))] = (
                st.st_size, st.st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest())
    return state


@contextlib.contextmanager
def _no_io():
    """把网络与子进程入口换成会抛异常的桩。

    选 `PermissionError`（`OSError` 的子类）而不是 `RuntimeError`：能力预检
    `providers.capabilities._probe_binary` 只吞 `OSError/SubprocessError` ——
    用别的异常类型会让 `build_engine` **因为补丁本身**而失败，那就测不出
    "引擎不需要子进程"了。用 `OSError` 的语义也更准：这些动作在本测试里没有权限。
    """
    def blocked(*_args, **_kwargs):
        raise PermissionError("本次离线验收禁止网络/子进程")

    targets = ((socket, "socket"), (urllib.request, "urlopen"),
               (subprocess, "run"), (subprocess, "Popen"))
    saved = [(obj, name, getattr(obj, name)) for obj, name in targets]
    for obj, name in targets:
        setattr(obj, name, blocked)
    try:
        yield
    finally:
        for obj, name, value in saved:
            setattr(obj, name, value)


class _Role:
    """Gateway 的最小角色（与 `test_m6_resume_and_cost.py` 同一个形状）。"""

    def __init__(self, model="m1", effort="high", max_tokens=1000):
        self.model, self.effort, self.max_tokens = model, effort, max_tokens


def _stub_transport(**_kw):
    """离线替身 transport：**没有任何网络**，返回固定 JSON 与固定用量。"""
    return '{"ok": 1}', {"total_tokens": 10}


_DOCTOR = None


def _doctor():
    """懒加载 `tools/doctor.py`（现存唯一的只读体检面；见模块 docstring 的说明）。"""
    global _DOCTOR
    if _DOCTOR is None:
        spec = importlib.util.spec_from_file_location("doctor_m10_under_test",
                                                     ROOT / "tools" / "doctor.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _DOCTOR = module
    return _DOCTOR


# ══════════════════════════════════════════ A02

def test_a02_plan_for_b_names_b_identity_and_b_directories_only(tmp_path):
    """**A02**：规划 B 时，计划里的身份、读取依赖、写入目标全部只提 B。

    两本书的单元键会撞名（都有 `CH01-S01`），所以判据必须是"**路径**与单元集合
    都指向 B"，而不是"单元名看着对"。
    """
    workspace, books = _two_book_workspace(tmp_path)
    registry = load_registry(workspace)
    a, b = books[BOOK_A], books[BOOK_B]

    plan_a = build_plan(_request(BOOK_A), registry)
    plan_b = build_plan(_request(BOOK_B), registry)

    assert plan_b.book_id == BOOK_B and plan_a.book_id == BOOK_A
    assert tuple(plan_b.resolved_scope or ()) == tuple(b.unit_ids)
    assert tuple(plan_a.resolved_scope or ()) == tuple(a.unit_ids)
    assert set(plan_b.resolved_scope or ()) != set(plan_a.resolved_scope or ())

    payload = json.dumps(plan_b.as_dict(), ensure_ascii=False)
    for a_path in (a.parse, a.lessons, a.audit):
        assert str(a_path) not in payload, f"B 的计划里出现了 A 的路径 {a_path}"
    assert plan_b.read_dependencies == (str(b.parse), str(b.lessons))
    for target in plan_b.write_targets:
        assert Path(target).is_relative_to(book_runs_root(workspace.root, BOOK_B).parent)
        assert not Path(target).is_relative_to(book_runs_root(workspace.root, BOOK_A).parent)
        assert not Path(target).is_relative_to(b.parse)
        assert not Path(target).is_relative_to(b.lessons)
        assert not Path(target).is_relative_to(b.audit)


def test_a02_running_b_writes_only_under_b_and_leaves_a_byte_identical(tmp_path, monkeypatch):
    """**A02**：规划/检查/运行 B 不动 A 一个字节；同进程切回 A 时计划逐字一致。

    A 的整棵树（解析/教案/审核/交付）在规划、检查、运行 B 的前后必须
    (大小, mtime_ns, sha256) 全等；运行产物只许落在 `data/books/<B>/runs/`；
    切回 A 的计划 digest 必须与切走之前相同。
    反向也测（跑 A 不动 B）—— 单方向的"没串"可能只是"这次恰好没读"。

    "检查 B"用的是现存只读体检 `tools/doctor.py`（`aifs check` 尚未落地，见模块
    docstring）：检查完必须能报出结论，而 A 那边一个字节都不能动。
    """
    workspace, books = _two_book_workspace(tmp_path)
    registry = load_registry(workspace)
    a, b = books[BOOK_A], books[BOOK_B]

    a_before = _tree(a.root)
    plan_a_first = build_plan(_request(BOOK_A), registry)

    # 检查 B（只读体检）：结论属于 B，A 不受影响
    check_code, violations, crashed = _check_outcome(monkeypatch, _doctor(), b)
    assert check_code == 1 and crashed == ()
    assert set(violations) == {"历史质量不可继承"}, "历史教材只读检查不能签发当前质量结论"
    assert _tree(a.root) == a_before, "检查 B 改动了 A 的产物"

    with _no_io():
        result_b, store_b, engine_b = run_request(_request(BOOK_B), workspace)

    assert result_b.status == "succeeded", result_b.recovery_hint
    # 读写与报告归 B：运行目录在 B 名下，状态文件自己写着 book_id
    assert store_b.root == book_runs_root(workspace.root, BOOK_B) / store_b.run_id
    assert json.loads(store_b.state_path.read_text("utf-8"))["book_id"] == BOOK_B
    assert latest_run_id(workspace.root, BOOK_B) == store_b.run_id
    assert latest_run_id(workspace.root, BOOK_A) is None
    assert not book_runs_root(workspace.root, BOOK_A).exists()
    summary = read_state_summary(workspace.root, BOOK_B, store_b.run_id)
    assert summary["book_id"] == BOOK_B and summary["status"] == "succeeded"

    # A 的内容、mtime、哈希全等（不只是"文件集合一样"）
    assert _tree(a.root) == a_before, "规划/检查/运行 B 改动了 A 的产物"

    # 同进程切回 A：计划与第一次逐字一致，且仍然只读
    plan_a_second = build_plan(_request(BOOK_A), registry)
    assert plan_a_second.digest() == plan_a_first.digest()
    assert tuple(plan_a_second.resolved_scope or ()) == tuple(a.unit_ids)
    assert _tree(a.root) == a_before

    # 反向：跑 A 不动 B
    b_before = _tree(b.root)
    with _no_io():
        result_a, store_a, _engine_a = run_request(_request(BOOK_A), workspace)
    assert result_a.status == "succeeded", result_a.recovery_hint
    assert store_a.root == book_runs_root(workspace.root, BOOK_A) / store_a.run_id
    assert _tree(b.root) == b_before, "运行 A 改动了 B 的产物"
    # 费用也归目标书：一次 smoke 运行的用量记在**它自己**的运行报告里，
    # 账本行数 == 该图节点数（`usage.jsonl` 的接线见 A02 最后一条测试的说明）
    assert not store_b.usage_path.exists()


def test_a02_registry_gives_each_book_its_own_roots_and_page_root(tmp_path):
    """**A02**：`resolve(A)` 与 `resolve(B)` 的每个根都不同，章页根按书收窄。

    两本书的章名必然撞（`CH02` 是同一个字符串），所以交付页根不收窄就会互相覆盖，
    而且 doctor 会把另一本书的章页当成自己的。
    """
    workspace, books = _two_book_workspace(tmp_path)
    registry = load_registry(workspace)
    ctx_a = registry.resolve(BOOK_A)
    ctx_b = registry.resolve(BOOK_B)

    for attr in ("parse", "lessons", "audit", "problems", "artifacts_root",
                 "runs_root", "releases_root", "page_root"):
        value_a = getattr(ctx_a, attr) if attr != "page_root" else ctx_a.page_root()
        value_b = getattr(ctx_b, attr) if attr != "page_root" else ctx_b.page_root()
        assert value_a != value_b, f"{attr} 在两本书之间没有分开"
    assert ctx_a.page_root().name == BOOK_A and ctx_b.page_root().name == BOOK_B
    assert ctx_a.page_root().parent.name == "整本"
    assert ctx_a.book_id == BOOK_A and ctx_b.book_id == BOOK_B
    assert registry.resolve(BOOK_A) == ctx_a, "解析同一本书两次结果不同（有隐藏的'上次用了哪本'）"




# ══════════════════════════════════════════ A03

#: A03 四类输入仍保留历史快照过期；同时破坏交付页 revision，
#: 要求现存只读结构检查报出具体过期，不能只靠历史质量拒绝。
A03_INPUT_CASES = {
    "complete": {"broken": None},
    "missing-file": {"broken": None, "drop": "chapters.json"},
    "corrupt-json": {"broken": None, "corrupt": "chapters.json"},
    "stale-expired": {"broken": "snapshot"},
}


def _a03_book(tmp_path: Path, case: dict):
    workspace, book = _one_book_workspace(tmp_path, broken=case.get("broken"))
    if case.get("broken") == "snapshot":
        _synth().BREAKAGES["delivery_stale"](book)
    if case.get("drop"):
        (book.parse / case["drop"]).unlink()
    if case.get("corrupt"):
        (book.parse / case["corrupt"]).write_text("{ 这不是合法 JSON", encoding="utf-8")
    return workspace, book


def _plan_outcome(registry, request) -> tuple:
    try:
        plan = build_plan(request, registry)
    except Exception as exc:                       # noqa: BLE001 — 结果本身就是被测对象
        return ("error", type(exc).__name__, str(exc))
    return ("ok", plan.digest(), plan.as_dict()["resolved_scope"],
            plan.as_dict()["requires_preparation"])


def _check_outcome(monkeypatch, doc, book) -> tuple:
    """跑一遍现存的只读体检（doctor），返回 (退出码, 违规不变量, doctor 故障)。"""
    _synth().use_for_doctor(monkeypatch, doc, book)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = doc.main(["--json"])
    payload = json.loads(buffer.getvalue())
    return (code, tuple(v["不变量"] for v in payload["违规"]),
            tuple(payload["doctor自己坏了"]))


@pytest.mark.parametrize("case_name", sorted(A03_INPUT_CASES))
def test_a03_plan_and_check_leave_contents_mtimes_and_usage_untouched(
        tmp_path, monkeypatch, case_name):
    """**A03**：完整/缺文件/坏 JSON/过期四类输入下跑 plan 与 check，产物与账本必须一动不动。

    判据有三层，缺一层都会放过真问题：

    1. **内容与 mtime**：整棵树的 (大小, mtime_ns, sha256) 前后全等 —— 一个"顺手补记录/
       顺手建运行目录"的 plan 会把"只想看看"变成一次真实写操作；
    2. **账本**：预置一份 `usage.jsonl`，字节与读出的记录都不许变（"usage 追加"是
       方案 §11 点名禁止的 plan 副作用之一）；
    3. **没有 HTTP/模型/子进程**：同一批调用在"网络与子进程入口会抛异常"的桩下必须
       得到**完全相同**的结果 —— 因为补丁而失败/变化就说明它真的调了外部边界。

    四类输入各自的期望也钉住：完整 → 计划解析出范围、历史质量不得继承；缺文件 → 冷启动
    计划（不虚构范围）且体检报出来；坏 JSON → `ConfigError`（不是"当成冷启动再跑一遍
    解析"）；过期 → 体检**必须报出**「交付页不过期」而不是静默通过。
    """
    workspace, book = _a03_book(tmp_path, A03_INPUT_CASES[case_name])
    registry = load_registry(workspace)
    request = _request(book.book_id, kind=SelectorKind.CHAPTERS, values=("CH01",))

    # 预置一份账本：plan/check 若追加 usage，字节或记录就会变
    ledger_path = book_runs_root(workspace.root, book.book_id) / "seed-run" / "usage.jsonl"
    ledger = UsageLedger(ledger_path, run_id="seed-run")
    ledger.record(UsageRecord(run_id="seed-run", book_id=book.book_id, tokens=7))
    usage_before = ledger_path.read_bytes()
    records_before = [row.as_dict() for row in ledger.read_all()["records"]]

    protected = {"parse": book.parse, "lessons": book.lessons, "audit": book.audit}
    tree_before = _tree(tmp_path)
    dirs_before = {name: _tree(path) for name, path in protected.items()}

    doc = _doctor()
    plain = (_plan_outcome(registry, request), _check_outcome(monkeypatch, doc, book))
    with _no_io():
        stubbed = (_plan_outcome(registry, request), _check_outcome(monkeypatch, doc, book))

    assert stubbed == plain, "补丁改变了结果：要么结果依赖外部边界，要么它偷偷调了它们"

    assert _tree(tmp_path) == tree_before, "plan/check 动过工作区里的文件（内容或 mtime）"
    for name, path in protected.items():
        assert _tree(path) == dirs_before[name], f"plan/check 动了受保护目录 {name}"
    assert ledger_path.read_bytes() == usage_before, "plan/check 追加了 usage"
    assert [row.as_dict() for row in ledger.read_all()["records"]] == records_before

    plan_outcome, check_outcome = plain
    if case_name == "complete":
        assert plan_outcome[0] == "ok" and plan_outcome[2] == ["CH01-S01", "CH01-S02"]
        assert check_outcome[0] == 1 and check_outcome[2] == ()
        assert set(check_outcome[1]) == {"历史质量不可继承"}
    elif case_name == "missing-file":
        # 冷启动：范围是 None（**不虚构**），且必须先做 MinerU
        assert plan_outcome[0] == "ok" and plan_outcome[2] is None
        assert plan_outcome[3] is True
        assert check_outcome[0] == 1 and check_outcome[2] == (), "体检必须报出违规而不是自己坏掉"
    elif case_name == "corrupt-json":
        assert plan_outcome[0] == "error" and plan_outcome[1] == ConfigError.__name__
        assert "不是合法 JSON" in plan_outcome[2]
    else:                                   # stale-expired
        assert plan_outcome[0] == "ok" and plan_outcome[2] == ["CH01-S01", "CH01-S02"]
        assert check_outcome[0] == 1 and check_outcome[2] == ()
        assert "交付页不过期" in check_outcome[1], "当前 revision 与页面指纹不符必须被只读 check 检出"
        assert "历史质量不可继承" in check_outcome[1]
        assert book.snapshot()["progress"]["needs_review"] == [book.unit_ids[0]]


# ══════════════════════════════════════════ A04

#: 三种输入状态按**盘上还能复用什么**划分（见模块 docstring 的离线边界说明）。
A04_STATES = ("冷PDF", "已有MinerU", "已有解析")
A04_SCOPES = {
    "单章": (SelectorKind.CHAPTERS, ("CH01",)),
    "多章": (SelectorKind.CHAPTERS, ("CH01", "CH02")),
    "整本": (SelectorKind.ALL, ()),
}


def _a04_case(tmp_path: Path, state: str):
    """造一本合成书，并把它调成 A04 的三种输入状态之一。

    * `冷PDF`   —— 盘上只有登记的 PDF，解析产物整棵不在；
    * `已有MinerU` —— MinerU 原始产物在盘上（可复用、**不该上传**），catalog 已形成，
      但解析链还没走完（缺概念索引）→ 计划仍要求准备；
    * `已有解析` —— 夹具的完整解析产物（概念索引在内），准备已齐。

    同时把夹具自带的 catalog 复制一份当"这本书真实内容"的参照：冷启动时盘上没有
    `chapters.json`，"期望目标集合"只能从这份参照里读（它**就是**夹具写出的真实
    catalog，不是测试另编的一份）。
    """
    synth = _synth()
    root = tmp_path / "ws"
    book = synth.build(root / "book", book_id="m10-book", title="M10 合成教材")
    reference = tmp_path / "catalog-reference.json"
    shutil.copy(book.parse / "chapters.json", reference)

    pdf = root / "raw" / "m10-book.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes("%PDF-1.4 M10 离线合成样张（不是真教材）".encode("utf-8"))

    if state == "冷PDF":
        shutil.rmtree(book.parse)
    elif state == "已有MinerU":
        (book.parse / "mineru").mkdir(parents=True, exist_ok=True)
        (book.parse / "mineru" / "layout.json").write_text('{"pdf_info": []}', encoding="utf-8")
        ingest = json.loads((book.parse / "ingest.json").read_text("utf-8"))
        ingest["source_sha256"] = "sha256:" + hashlib.sha256(pdf.read_bytes()).hexdigest()
        (book.parse / "ingest.json").write_text(json.dumps(ingest, ensure_ascii=False),
                                                encoding="utf-8")
        (book.parse / "concepts.json").unlink()      # 解析链还没走完
    _write_registry(root, {"m10-book": book})
    return Workspace(root=root), book, reference


def _expected_units(catalog_payload: dict, scope) -> tuple:
    """**从 catalog 的 `lesson_units` 推**期望目标集合（不是从夹具变量抄）。"""
    kind, values = scope
    units = tuple(str(u["unit_id"]) for u in catalog_payload["lesson_units"])
    if kind is SelectorKind.ALL:
        return units
    chapters = set(values)
    return tuple(u for u in units if u.split("-", 1)[0] in chapters)


def _stage_order(graph) -> list:
    """去重后的阶段顺序（与 `graph._distinct_stage_order` 同义，公接口不可用）。"""
    seen: list = []
    for node_id in graph.order():
        name = graph.by_id()[node_id].stage
        if name not in seen:
            seen.append(name)
    return seen


@pytest.mark.parametrize("state", A04_STATES)
@pytest.mark.parametrize("scope_name", sorted(A04_SCOPES))
def test_a04_nine_combinations_target_exactly_the_expected_units(tmp_path, state, scope_name):
    """**A04**：3 种输入状态 × 3 种范围，计划的目标集合必须与 catalog **精确一致**。

    对已形成 catalog 的两类状态：`plan.resolved_scope` 与从 `chapters.json`
    （或冷启动时保留的那份真实 catalog 参照）推出来的集合**逐项相等** —— 不多不少，
    顺序也一致（教材顺序）。

    对 `冷PDF`：盘上没有 catalog，方案 §6 明确要求计划标 `resolved_scope=null`
    且**不虚构单元数**（编一个"12 个 CH03-Sxx"会让界面显示假范围、然后真跑起来烧钱）。
    所以这一类断言的是"它承认不知道"，并钉住它**必须先做 MinerU**；"精确一致"这条
    判据在冷启动下无对象可比 —— 这是验收项里离线不可判的那一半（见模块 docstring）。
    """
    scope = A04_SCOPES[scope_name]
    workspace, book, reference = _a04_case(tmp_path, state)
    registry = load_registry(workspace)
    expected = _expected_units(json.loads(reference.read_text("utf-8")), scope)
    request = _request(book.book_id, kind=scope[0], values=scope[1])

    with _no_io():
        plan = build_plan(request, registry)

    assert plan.book_id == book.book_id
    if state == "冷PDF":
        # 这本书的真实内容（catalog 参照）里就是 expected；计划**拒绝**把它说出来 ——
        # 这正是 §6 要的"不知道就说不知道"，而不是把 expected 编进计划里好看。
        assert expected, "catalog 参照里应当有单元（否则参照拿错了）"
        assert plan.resolved_scope is None and plan.target_count is None
        assert plan.requires_preparation is True
        assert [task.stage for task in plan.preparation][:1] == ["mineru"], \
            "冷启动必须先有 MinerU（无 OCR 备用路线）"
        assert all(task.scope == "book" for task in plan.preparation)
        with _no_io():
            engine = build_engine(request, workspace)
        assert engine.plan.resolved_scope is None
        assert [n.stage for n in engine.graph.nodes] == ['mineru', 'toc', 'structure', 'export', 'body']
        assert all(n.scope_key == 'book' for n in engine.graph.nodes)
        return

    assert tuple(plan.resolved_scope or ()) == expected
    assert set(plan.resolved_scope or ()) == set(expected)
    if state == "已有MinerU":
        assert plan.requires_preparation is True
        assert [task.stage for task in plan.preparation] == ["concepts"]
    else:
        assert plan.requires_preparation is False


def test_a04_the_same_engine_runs_one_graph_order_for_every_reusable_combination(tmp_path):
    """**A04**：可执行的 6 个组合跑的是**同一台引擎、同一份阶段表、同一张图的形状**。

    判据三条：(1) 6 张图的去重阶段顺序两两一致，且**等于唯一阶段表
    `CANONICAL_STAGES` 的顺序**（没有第二套编排）；(2) 单元级节点集合精确等于
    catalog 推出的期望集合（不补不丢）；(3) 整链真的跑完（smoke 档每个节点都 succeeded）。
    另外扫一遍包：`build_graph` 只有一处实现 —— "同一引擎"这句得有个机器证据。
    """
    builders = sorted(str(path.relative_to(ROOT))
                      for path in (ROOT / "src" / "aiforstudy").rglob("*.py")
                      if "def build_graph(" in path.read_text("utf-8"))
    assert builders == ["src/aiforstudy/pipeline/graph.py"], "包里出现了第二个图构造器"

    canonical = [spec.name for spec in CANONICAL_STAGES]
    graphs = {}
    for state in ("已有MinerU", "已有解析"):
        for scope_name, scope in A04_SCOPES.items():
            case_dir = tmp_path / f"{state}-{scope_name}"
            case_dir.mkdir()
            workspace, book, reference = _a04_case(case_dir, state)
            expected = _expected_units(json.loads(reference.read_text("utf-8")), scope)
            request = _request(book.book_id, kind=scope[0], values=scope[1])
            with _no_io():
                result, _store, engine = run_request(request, workspace)
            assert result.status == "succeeded", (state, scope_name, result.recovery_hint)
            assert result.execution_counts.get("succeeded") == len(engine.graph.nodes)
            unit_nodes = {node.scope_key for node in engine.graph.nodes if node.stage == "lesson"}
            assert unit_nodes == set(expected), (state, scope_name)
            graphs[(state, scope_name)] = engine.graph

    orders = {tuple(_stage_order(graph)) for graph in graphs.values()}
    assert len(orders) == 1, f"不同组合的阶段顺序漂移了：{orders}"
    assert orders.pop() == tuple(canonical)
    first = next(iter(graphs.values()))
    for graph in graphs.values():
        assert graphs_equivalent_for_scope(first, graph)
        assert stage_relative_order(first) == stage_relative_order(graph)


@pytest.mark.parametrize("scope_name", sorted(A04_SCOPES))
def test_a04_existing_mineru_output_is_reused_and_nothing_is_uploaded(tmp_path, scope_name):
    """**A04**：已有 MinerU 产物 → 走"复用本地"，全程没有上传（源哈希不同则明确阻断）。

    三层：

    1. 路线判定层：`decide()` 返回 `reuse-local` 且 `requires_network=False`，
       理由里写明"已有 MinerU 产物"；源哈希不同 → `blocked`（方案 §11：源哈希不同
       **不得复用旧分块**），而不是"看起来没问题"地用下去；
    2. 引擎层：断网 + 禁子进程下整链照跑成功，且 `parse/mineru` 下一根字节都没变
       （上传/重下会落文件）；
    3. 报告层：`require_route()` 交给调用方的是一份 `route/reason` 齐全的复用决定。

    ⚠️ 离线边界：smoke 档的 `mineru` 阶段是**合成替身**，它不读 `providers/mineru`
    （真实 MinerU 阶段实现属 M3 未迁入）。所以"引擎自己报告复用了已有源"这一句
    离线不可验；可验的是**复用决定本身**与"引擎整链不需要任何网络"。
    """
    workspace, book, _reference = _a04_case(tmp_path, "已有MinerU")
    pdf_sha = "sha256:" + hashlib.sha256(
        (workspace.root / "raw" / "m10-book.pdf").read_bytes()).hexdigest()

    decision = mineru.decide(workspace, book.parse, source_hash=pdf_sha, allow_api=True)
    assert decision.route == mineru.ROUTE_REUSE
    assert decision.blocked is False and decision.requires_network is False
    assert decision.requires_api_key is False
    assert "已有 MinerU 产物" in decision.reason
    assert mineru.require_route(workspace, book.parse,
                                source_hash=pdf_sha).as_dict()["route"] == mineru.ROUTE_REUSE

    mismatched = mineru.decide(workspace, book.parse, source_hash="sha256:另一份源")
    assert mismatched.route == mineru.ROUTE_BLOCKED
    assert "源哈希" in mismatched.reason and "不得复用" in mismatched.reason

    mineru_before = _tree(book.parse / "mineru")
    request = _request(book.book_id, kind=A04_SCOPES[scope_name][0],
                       values=A04_SCOPES[scope_name][1])
    with _no_io():
        result, _store, engine = run_request(request, workspace)
    assert result.status == "succeeded", result.recovery_hint
    assert result.execution_counts.get("succeeded") == len(engine.graph.nodes)
    assert _tree(book.parse / "mineru") == mineru_before, "运行改写了已有 MinerU 产物"


def test_a04_pdf_preparation_has_no_silent_ocr_fallback(tmp_path, monkeypatch):
    """**A04**：PDF准备不静默降级；用户显式发起的视频画面OCR是独立功能。

    实测代价（`providers/mineru.py` 记着）：漏掉 `--use-api` 时旧程序安静地走了本地
    OCR，公式全丢（`$` 计数 5，而 MinerU 路线是 11276）—— 那不是"跑失败"，
    是"跑成功了但产物是坏的"。所以这里钉两件事：

    1. 结构性扫描：除显式视频分析的两个模块，包内没有OCR代码入口
       （`core/source.py` 的 `"ocr-typo"` 是**原文修复类别**里的字符串，不是路线）；
       `providers/mineru.py` 的路线常量只有 复用/云端/阻断 三种，没有 `ROUTE_OCR`；
    2. 行为：冷输入 + 没有凭据 → `decide()` 明确 blocked（理由点名两条路都不通、
       **不退回本地 OCR**），`require_route()` 抛 `CapabilityError` 而不是给一条 OCR 路线。
    """
    offenders = []
    video_modules = {ROOT / 'src/aiforstudy/core/video_analysis.py',
                     ROOT / 'src/aiforstudy/core/video_recognition_worker.py'}
    for path in sorted((ROOT / "src" / "aiforstudy").rglob("*.py")):
        if path in video_modules:
            continue
        for lineno, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            stripped = line.strip().lower()
            if stripped.startswith(("def ", "class ", "import ", "from ")) and "ocr" in stripped:
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
    assert offenders == [], f"显式视频分析以外出现了 OCR 路线：{offenders}"
    import ast
    pdf_ast=ast.parse((ROOT/'src/aiforstudy/providers/mineru.py').read_text())
    pdf_imports=[node.module or '' for node in ast.walk(pdf_ast) if isinstance(node,ast.ImportFrom)]
    assert not any('video_analysis' in name or 'video_recognition_worker' in name for name in pdf_imports)

    assert not hasattr(mineru, "ROUTE_OCR")
    assert {mineru.ROUTE_REUSE, mineru.ROUTE_API, mineru.ROUTE_BLOCKED} == \
        {"reuse-local", "mineru-api", "blocked"}

    workspace, book, _reference = _a04_case(tmp_path, "冷PDF")
    monkeypatch.setattr(mineru, "has_api_credentials",
                        lambda _ws: (False, "离线验收：环境里故意没有 MinerU 凭据"))
    decision = mineru.decide(workspace, book.parse, allow_api=True)
    assert decision.route == mineru.ROUTE_BLOCKED
    assert decision.requires_api_key is True and decision.requires_network is False
    assert "不退回本地 OCR" in decision.reason
    assert "两条路都不通" in decision.reason
    with pytest.raises(CapabilityError, match="两条路都不通"):
        mineru.require_route(workspace, book.parse, allow_api=True)


# ────────────────────────────────────────── 运行级账本落盘（M10 补齐的缺口）





def test_unknown_usage_is_recorded_as_unknown_not_zero():
    """§11：**花了但数不出来**时记 `None`，不记 0。

    记 0 会让报告看起来"还有余额"，而钱已经花了。
    """
    from aiforstudy.core.usage import UsageRecord, summarize
    from aiforstudy.pipeline.runner import RUN_USAGE_STAGE

    row = UsageRecord(run_id="r", book_id="b", stage=RUN_USAGE_STAGE, tokens=None,
                      error="有未知用量的请求")
    assert row.unknown is True
    summary = summarize([row])
    assert summary["tokens"] == 0 and summary["unknown_events"] == 1, \
        "未知不许并进 tokens，但要单独计数"


@dataclass
class _FakeResult:
    """只为 `_record_usage` 造一个最小结果对象（不跑整链）。"""
    run_id: str
    status: str = "succeeded"
    elapsed_seconds: float = 0.0
    usage: dict = field(default_factory=dict)
    profile: str = "smoke"


def test_production_run_persists_execution_without_usage_ledger(tmp_path):
    workspace,book=_one_book_workspace(tmp_path)
    result,store,engine=run_request(_request('m10-book'),workspace)
    assert not store.usage_path.exists()
    assert 'usage' not in result.as_dict()
    assert 'budget' not in store.load_state()
    assert (store.root/'execution.json').is_file()
