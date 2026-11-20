"""Current release preparation, migrated from the historical M10 prefix checks.

Real source trees stay read-only. Synthetic complete preparation uses schema 3;
missing transport remains explicit, and zero dispatch is proven by execution.json.
The registered stages and all publication/model boundaries use graph 6.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import socket
import subprocess
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from aiforstudy.contracts.artifact import ArtifactRef
from aiforstudy.contracts.enums import Goal, Profile, SelectorKind
from aiforstudy.contracts.request import RunRequest, Selectors
from aiforstudy.core.digest import digest_file
from aiforstudy.core.workspace import Workspace
from aiforstudy.pipeline.engine import default_stage_registry, run_request
from aiforstudy.pipeline.services import Providers, not_implemented
from aiforstudy.pipeline.preparation_checks import DETERMINISTIC_STAGES, KIND_CONTEXT
from aiforstudy.pipeline.registry import release_registry

ROOT = Path(__file__).resolve().parents[2]
REAL_CONFIG = ROOT / "config"
REAL_BOOK = "complex-analysis-v2"
REAL_PARSE = ROOT / "data" / "parse" / REAL_BOOK
REAL_LESSONS = ROOT / "data" / "lesson" / REAL_BOOK
REAL_RUNS = ROOT / "data" / "books" / REAL_BOOK / "runs"

#: 本任务**已接线**的模型阶段（单元粒度）：注册的是**真跑的壳**，模型访问全部走注入的
#: Gateway 传输；没注入传输时它们明确 `blocked`（见 `pipeline/transitional._MISSING_TRANSPORT`）。
WIRED_MODEL_STAGES = ("lesson", "audit", "interaction")

#: 已接线的**本地**阶段（不调模型：渲染是模板拼装，交付是打包与离线验收）。
WIRED_LOCAL_STAGES = ("render", "delivery")

#: ⚠️ 2026-10-06 **前提更新（第三次，也是最后一次）**：原先这里列四个
#: （figures/interaction/render/delivery），后来 render/delivery 接上、再是 interaction，
#: 现在 `figures` 也接了 —— **12 个阶段全部有实现**。
#:
#: 判据没有放宽，也没有变成空循环：`UNWIRED_STAGES` 仍在这里，只是现在是空的；
#: 一旦有人新加阶段却没接线，把它填进来就会立刻红。而 `not_implemented` 那条兜底
#: 改用**一个永远不会被注册的名字**来验，于是"缺的实现不许冒充通过"这条判据仍然活着。
UNWIRED_STAGES = ()

#: 兼容旧名（文件里多处引用）；语义是"仍未接线的阶段"。
MODEL_STAGES = UNWIRED_STAGES


# ────────────────────────────────────────── 装置

def _copy_config(root: Path) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    for name in ("capabilities.json", "execution.json", "models.json"):
        shutil.copy(REAL_CONFIG / name, root / "config" / name)


def _write_registry(root: Path, books: dict) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "books.json").write_text(
        json.dumps({"_meta": {"file_type": "book_registry", "schema_version": "1.0"},
                    "workspace": ".", "books": books}, ensure_ascii=False), encoding="utf-8")


def _request(book_id: str, *, profile: Profile = Profile.RELEASE,
             goal: Goal = Goal.DELIVER, kind: SelectorKind = SelectorKind.ALL,
             values=()) -> RunRequest:
    return RunRequest(book_id=book_id, selectors=Selectors(kind=kind, values=tuple(values)),
                      goal=goal, profile=profile).validate()


def _tree(root: Path) -> dict[str, str]:
    """目录的内容凭据：相对路径 → 内容 sha256（只比内容，不比 mtime）。"""
    out: dict[str, str] = {}
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                digest.update(block)
        out[str(path.relative_to(root))] = digest.hexdigest()
    return out


def _real_book_workspace(tmp_path: Path) -> Workspace:
    """真实书 + **临时工作区**：`data/` 只读，`runs`/`artifacts` 落 tmp。

    为什么必须这样接：`book_runs_root()` 是按 **workspace 根**算的，所以只要工作区在 tmp，
    release 运行连"往 data/books/<book>/runs 新建一个目录"都做不到 —— 这是可证明的，
    比"我记得没有写"强。
    """
    root = tmp_path / "ws"
    _copy_config(root)
    _write_registry(root, {REAL_BOOK: {
        "title": "复变函数（洁净结构 v2）", "revision": "parse-v2",
        "sources": [{"kind": "pdf",
                     "path": str(ROOT / "data" / "raw" / "复变函数.pdf"), "sha256": None}],
        "parse": str(REAL_PARSE), "lessons": str(REAL_LESSONS),
        "audit": str(ROOT / "data" / "audit" / REAL_BOOK),
        "problems": str(ROOT / "data" / "problems" / f"{REAL_BOOK}.json"),
        "artifacts_root": str(root / "artifacts"), "runs_root": str(root / "runs"),
        "releases_root": str(root / "releases")}})
    return Workspace(root=root)


# Keep this historical import location, with one current synthetic fixture.
from support.preparation_book import _mini_book


def _no_io(monkeypatch) -> None:
    """网络与子进程入口换成"一碰就抛"。用 `PermissionError`（`OSError` 子类），
    好让能力预检 `_probe_binary` 的正常捕获路径生效 —— 否则失败会是补丁本身造成的。"""
    def blocked(*_args, **_kwargs):
        raise PermissionError("本次离线验收禁止网络/子进程")

    for obj, name in ((socket, "socket"), (urllib.request, "urlopen"),
                      (subprocess, "run"), (subprocess, "Popen")):
        monkeypatch.setattr(obj, name, blocked)


def _gateway_tripwire(monkeypatch) -> None:
    """模型网关的绊线：真发请求就会当场炸。"""
    from aiforstudy.providers.gateway import Gateway

    def refuse(*_args, **_kwargs):
        raise AssertionError("确定性前缀不许调用模型网关（本次范围零模型调用）")

    monkeypatch.setattr(Gateway, "call", refuse)


def _assert_no_model_requests(store) -> None:
    """No transport dispatch: a durable, empty execution journal is the proof."""
    execution = json.loads((store.root/'execution.json').read_text())
    assert execution['run_id'] == store.run_id
    assert execution['attempts'] == []
    assert not (store.root/'usage.jsonl').exists()


def _assert_products_have_fingerprints(nodes, stages) -> None:
    """每个阶段的每份产物都要带 `_meta` 与内容指纹（方案 §6：不能只给一个路径）。"""
    for stage in stages:
        state = nodes[stage]
        assert state.outputs, f"{stage} 没写出产物"
        for raw in state.outputs:
            ref = ArtifactRef.from_dict(raw).verify()
            assert ref.content_digest and ref.content_digest.startswith("sha256:")
            assert ref.producer_stage == stage and ref.producer_version
            payload = json.loads(Path(ref.path).read_text(encoding="utf-8"))
            assert payload["_meta"]["inputs_fingerprint"].startswith("sha256:")
            assert payload["_meta"]["stage"] == stage
            assert payload["_meta"]["book_id"] == ref.book_id


# ────────────────────────────────────────── 1) 注册表

def test_release_registry_wires_prefix_plus_lesson_audit_only():
    """注册表 = 确定性前缀 + `lesson`/`audit`；其余模型阶段**一个都不接**。

    ⚠️ 这条判据在 M10 后半段被**有意加强**：原来它断言"模型阶段一个都不在注册表里"，
    因为那时确实一个都没接。现在 `lesson`/`audit` 接了（且必须真跑、模型访问全走
    Gateway），所以断言改成两边都钉死：接上的必须在，没接的必须不在 ——
    比"全都不在"更强，因为它同时防"漏接"和"多接"。
    """
    registry = release_registry()
    assert set(DETERMINISTIC_STAGES) <= set(registry.names()), registry.names()
    assert set(WIRED_MODEL_STAGES) <= set(registry.names()), registry.names()
    assert set(WIRED_LOCAL_STAGES) <= set(registry.names()), registry.names()
    for name in MODEL_STAGES:
        assert name not in registry, \
            f"{name!r} 仍未接线，不许出现在注册表里 —— 出现了就等于用空壳冒充通过"


def test_default_stage_registry_dispatches_release_to_the_shell(tmp_path):
    """`default_stage_registry` 是"哪些阶段真能做"的唯一判断处，release 不许再是空表。"""
    workspace = _mini_book(tmp_path)
    registry = default_stage_registry(_request("mini-book"), workspace)
    assert set(DETERMINISTIC_STAGES) <= set(registry.names())
    assert set(WIRED_MODEL_STAGES) <= set(registry.names())
    assert set(WIRED_LOCAL_STAGES) <= set(registry.names())
    for name in MODEL_STAGES:
        assert name not in registry


def test_an_unregistered_stage_is_still_honestly_not_implemented():
    """没注册 → `not_implemented`：**明确 blocked 且写明"尚未迁入"**，不是 succeeded。

    ⚠️ 12 个阶段现在全部有实现，所以这条判据**不能**再靠"遍历未接阶段"来验 ——
    那会变成空循环、永远绿。改用一个**永远不会被注册的名字**，判据本身一字未改：
    兜底必须明确 blocked，而且理由要写明"尚未迁入"。
    """
    name = "__definitely_not_a_registered_stage__"
    assert name not in release_registry(), "这个名字不该被注册，否则这条判据就失效了"
    result = not_implemented(name)(SimpleNamespace(node=SimpleNamespace(stage=name)))
    assert result.execution_state.value == "blocked"
    assert "尚未迁入" in (result.error or {}).get("reason", "")


def test_every_canonical_stage_has_an_implementation():
    """**新增判据**：12 个规范阶段**一个都不许落在 not_implemented**。

    这是"全接完了"这件事的机器可读形式 —— 原先几条测试是反着写的（列"未接的"），
    那种写法在全部接完之后会变成空集、失去意义。这条从正面钉死。
    """
    from aiforstudy.pipeline.graph import CANONICAL_STAGES
    names = set(release_registry().names())
    missing = sorted(spec.name for spec in CANONICAL_STAGES if spec.name not in names)
    assert missing == [], f"这些规范阶段还没有实现：{missing}"
    assert not UNWIRED_STAGES, "UNWIRED_STAGES 应当是空的；有人新加阶段却没接线时把它填上"


# ────────────────────────────────────────── 2) 真实书

@pytest.mark.skipif(not (REAL_PARSE / "chapters.json").is_file(),
                    reason="真实解析产物不在位（已归档）")
def test_real_pdf_without_dual_directory_stops_after_verified_mineru(tmp_path, monkeypatch):
    """旧真实 PDF 目录不冒充新双源缓存；无模型时阻断，源数据不变。"""
    workspace = _real_book_workspace(tmp_path)
    parse_before = _tree(REAL_PARSE)
    lessons_before = _tree(REAL_LESSONS)
    real_runs_before = sorted(p.name for p in REAL_RUNS.iterdir()) if REAL_RUNS.is_dir() else []
    _gateway_tripwire(monkeypatch)

    result, store, engine = run_request(_request(REAL_BOOK), workspace, providers=Providers())
    nodes = store.node_states()

    assert nodes['toc'].execution_state.value == 'blocked'
    assert '目录' in str(nodes['toc'].error)
    # 新目录批准前尚不能解析单元范围，只建立到 body 的准备图。
    # 明确核对非空节点全集，不能把缺失下游节点变成空循环通过。
    assert set(nodes) == {'mineru', 'toc', 'structure', 'export', 'body'}
    plan = json.loads((store.root / 'plan.json').read_text('utf-8'))
    assert plan['requires_preparation'] is True and plan['resolved_scope'] is None
    for stage in ('toc', 'structure', 'export', 'body'):
        assert nodes[stage].execution_state.value == 'blocked', nodes[stage].error
        assert nodes[stage].outputs == [], f'{stage} 不应把旧结构当新双源解析产物'
        assert nodes[stage].error.get('reason')
    _assert_products_have_fingerprints(nodes, ('mineru',))

    # mineru 走复用、不上传
    assert nodes["mineru"].execution_state.value == "succeeded"
    mineru_receipt = json.loads(Path(nodes["mineru"].outputs[0]["path"]).read_text("utf-8"))
    assert mineru_receipt["payload"]["route"] == "reuse-local"
    assert mineru_receipt["payload"]["requires_network"] is False
    assert mineru_receipt["payload"]["requires_api_key"] is False

    # 未解析范围时不得提前建立或执行单元模型阶段。
    model_grain = WIRED_MODEL_STAGES + MODEL_STAGES
    model_nodes = [n for n in nodes.values() if n.stage in model_grain]
    assert model_nodes == []
    assert result.failure_stage == 'toc'
    assert result.release_ready is False and result.scope_accepted is False

    # `data/` 一个字节都没动，真实 runs 目录也没多出一次运行
    assert _tree(REAL_PARSE) == parse_before
    assert _tree(REAL_LESSONS) == lessons_before
    assert (sorted(p.name for p in REAL_RUNS.iterdir()) if REAL_RUNS.is_dir() else []) \
        == real_runs_before
    # 运行目录确实落在**临时工作区**里
    assert store.root.is_relative_to(workspace.root)

    # 新模型请求 = 0
    _assert_no_model_requests(store)


# ────────────────────────────────────────── 3) 断网 + 绊线

def test_prefix_runs_with_network_and_subprocess_blocked(tmp_path, monkeypatch):
    """断网、禁子进程、网关绊线全开：确定性前缀整段照跑（含冻结上下文）。"""
    workspace = _mini_book(tmp_path)
    parse = workspace.root / "book" / "parse"
    parse_before = _tree(parse)
    _no_io(monkeypatch)
    _gateway_tripwire(monkeypatch)

    result, store, _engine = run_request(_request("mini-book"), workspace)
    nodes = store.node_states()
    for stage in DETERMINISTIC_STAGES:
        assert nodes[stage].execution_state.value == "succeeded", \
            f"{stage} 在断网下没跑成：{nodes[stage].error}"
        assert nodes[stage].quality_state.value == "accepted", \
            f"{stage} 的质量态不是 accepted：{nodes[stage].quality_state}"
    _assert_products_have_fingerprints(nodes, DETERMINISTIC_STAGES)

    # 索引完整 → 上下文快照真的被冻结（§6：全书概念之后冻结上下文）
    context_outputs = [o for o in nodes["concepts"].outputs
                       if ArtifactRef.from_dict(o).kind == KIND_CONTEXT]
    assert len(context_outputs) == 1, "索引冻结时必须产出上下文快照"
    snapshot = json.loads(Path(context_outputs[0]["path"]).read_text("utf-8"))["payload"]
    assert snapshot["digest"].startswith("sha256:")
    assert snapshot["unit_keys"] and snapshot["toc_tree"] and snapshot["entry_index"]
    assert snapshot["selected_text_sha256"].startswith("sha256:")

    # 默认 v4 所有模型阶段都已注册；未注入传输时作者必须明确阻断，
    # 后续内容审核、媒体规划和消费阶段不得产出伪造的成功结果。
    V4_MODEL_STAGES = ('lesson','text_audit','media_plan','figures','audit','interaction')
    assert _engine.graph.graph_version == '7.0'
    for node in nodes.values():
        if node.stage not in V4_MODEL_STAGES:
            continue
        assert node.execution_state.value == "blocked", \
            f"{node.stage}:{node.scope_key} 不该跑成：{node.error}"
        assert node.outputs == []
    for node in nodes.values():
        if node.stage == "lesson":
            assert (node.error or {}).get("reason") == (
                "content author/patch blocked: ContractError: missing independent source inventory transport"), node.error
    assert all(n.execution_state.value == "blocked"
               for n in nodes.values() if n.stage in MODEL_STAGES)
    assert result.release_ready is False
    # 产物全在 tmp 的 run work 目录里，解析产物没被动
    assert _tree(parse) == parse_before
    _assert_no_model_requests(store)


# ────────────────────────────────────────── 4) 不上传、不回退 OCR

def test_cold_input_blocks_instead_of_uploading_or_falling_back_to_ocr(tmp_path):
    """没有可复用产物 → 明确 blocked，理由点名"不会退回本地 OCR"，且没有任何上传路径。"""
    workspace = _mini_book(tmp_path, with_mineru=False)
    result, store, _engine = run_request(_request("mini-book"), workspace)
    nodes = store.node_states()
    state = nodes["mineru"]
    assert state.execution_state.value == "blocked"
    reason = (state.error or {}).get("reason", "")
    assert "退回本地 OCR" in reason and "不" in reason
    # 阻断沿依赖传播：目录及之后都不许"接着跑"
    assert nodes["toc"].execution_state.value == "blocked"
    assert nodes["concepts"].execution_state.value == "blocked"
    assert result.release_ready is False
    _assert_no_model_requests(store)


def test_the_shell_has_no_api_or_upload_path():
    """结构性核对：过渡壳只调 `decide(..., allow_api=False)`，没有云端/上传分支。

    为什么结构核对也要有：行为核对只能证明"这次没走云端"，而"代码里根本没有云端分支"
    才能覆盖"某个输入组合下会走"。`providers/mineru` 的三条路线常量里，
    本层**只允许** `reuse-local` 与 `blocked`。
    """
    source = (ROOT / "src" / "aiforstudy" / "pipeline" / "preparation_checks.py"
              ).read_text(encoding="utf-8")
    assert "allow_api=False" in source
    assert "ROUTE_API" not in source and "ROUTE_BLOCKED" not in source
    assert "require_route" not in source      # 只判定 + 自己翻成 blocked，不抛能力异常
    # ⚠️ 不检查 `requires_api_key` 这种**如实记账**的字段（它记的正是"不需要密钥"），
    #    只查真正的凭据/网络/上传入口。
    # ⚠️ `requests` 必须写成**导入/调用形式**，不能拿裸词做子串匹配 ——
    #    图台账里有一个正当地叫 `"requests": []` 的字段（"本单元有哪些图请求"），
    #    裸词匹配会把它当成 HTTP 库。这正是本项目反复踩的那类假阳性：
    #    **把常见词当依赖**。判据的本意是"没有 HTTP 客户端"，不是"没有这个词"。
    for forbidden in ("has_api_credentials", "os.environ", ".secrets", "import requests",
                      "requests.get", "requests.post",
                      "urlopen", "socket", "upload"):
        assert forbidden not in source, f"过渡壳里出现了凭据/网络/上传路径：{forbidden}"


# ────────────────────────────────────────── 目录复核**生成**（M10 新接线）

def test_toc_review_generation_is_recorded_and_never_written_into_data(tmp_path):
    workspace=_mini_book(tmp_path)
    for path in workspace.root.rglob('toc.review.json'):path.unlink()
    before=_tree(workspace.root/'book/parse')
    result,store,_=run_request(_request('mini-book'),workspace,until='toc',providers=Providers())
    assert store.node_states()['toc'].execution_state.value=='succeeded'
    assert store.node_states()['toc'].quality_state.value=='needs_review'
    payload=json.loads((store.work/'products/toc/toc.json').read_text())['payload']
    assert payload['review_generated']['state']=='no-transport'
    assert '缺少' in payload['review_generated']['why']
    assert payload['blocked_for_release'] is True
    assert result.release_ready is False
    assert _tree(workspace.root/'book/parse')==before
    assert not (store.work/'toc.review.suggested.json').exists()
    _assert_no_model_requests(store)


def test_toc_review_generation_says_no_transport_instead_of_connecting(tmp_path):
    workspace=_mini_book(tmp_path)
    for path in workspace.root.rglob('toc.review.json'):path.unlink()
    before=_tree(workspace.root/'book/parse')
    result,store,_=run_request(_request('mini-book'),workspace,until='toc',providers=Providers())
    assert store.node_states()['toc'].execution_state.value=='succeeded'
    assert store.node_states()['toc'].quality_state.value=='needs_review'
    payload=json.loads((store.work/'products/toc/toc.json').read_text())['payload']
    assert payload['review_generated']['state']=='no-transport'
    assert '缺少' in payload['review_generated']['why']
    assert payload['blocked_for_release'] is True
    assert result.release_ready is False
    assert _tree(workspace.root/'book/parse')==before
    assert not (store.work/'toc.review.suggested.json').exists()
    _assert_no_model_requests(store)


def _scripted_transport():
    """最小的脚本化传输：只为把链路推过 `toc`（不校验复核内容本身）。"""
    import support.book_factory as _T
    return _T.ScriptedTransport()
