"""Shared artifact IO and explicitly allowed runtime adapters for graph 7."""
from __future__ import annotations
from pathlib import Path
import sys
from ..contracts.artifact import ArtifactRef
from ..contracts.stage import StageResult
from ..blocks.audit.contracts import RepairDirective
from ..core.digest import digest_file,digest_of
from ..core.jsonio import read_json_soft,write_json_atomic
from ..errors import ContractError
from .services import StageContext
SHELL_VERSION = "quality-engine-7"
PRODUCTS_DIR = "products"
KIND_CONTEXT = "context.snapshot"
KIND_BY_STAGE = {"mineru":"parse.mineru","toc":"parse.toc","structure":"parse.structure","export":"parse.catalog","body":"parse.body","concepts":"parse.concepts","lesson":"lesson.unit","text_audit":"audit.text","media_plan":"media.requirements","figures":"media.manifest","audit":"audit.unit"}
def _store_root(ctx: StageContext) -> Path:
    from .work_paths import store_root
    return store_root(getattr(ctx,'workspace',None),ctx.services.store)


def _product_path(ctx: StageContext, name: str) -> Path:
    node_part = ctx.node.node_id.replace("/", "_")
    # 一个节点可以产出不止一份产物（`concepts` 同时产索引与上下文快照），所以文件名里
    # 带上产物名，避免 `products/context/concepts.json` 这种"名字对不上内容"的读法。
    stem = node_part if name == ctx.node.stage else f"{node_part}.{name}"
    return _store_root(ctx) / PRODUCTS_DIR / name / f"{stem}.json"


def _inputs_fingerprint(inputs: dict) -> str:
    """输入的逻辑指纹。调用方必须只放**内容哈希与逻辑身份**（§11）。"""
    return digest_of(inputs)


def _emit(ctx: StageContext, *, name: str, kind: str, file_type: str, payload: dict,
          inputs: dict, findings: list[dict] | None = None,
          path: Path | None = None) -> ArtifactRef:
    """写一份带 `_meta` 与内容指纹的产物，并返回它的带版本引用。

    `path` 只给**单元粒度的壳**用：它们的产物必须能被下游按"阶段 + 单元键"找到
    （`audit` 要读 `lesson` 那一份），所以不能用 `node_id` 拼路径 —— 那会让
    `lesson:CH01-S01` 与 `audit:CH01-S01` 各自算出不同的文件名，谁也读不到谁。
    """
    fingerprint = _inputs_fingerprint(inputs)
    path = Path(path) if path is not None else _product_path(ctx, name)
    write_json_atomic(path, {
        "_meta": {
            "file_type": file_type,
            "schema_version": "1.0",
            "generator": "aiforstudy.pipeline.stage_io",
            "shell_version": SHELL_VERSION,
            "note": "正式流水线内部适配产物；执行状态、内容核实与展示状态分别记录",
            "book_id": ctx.context.book_id,
            "revision": ctx.context.revision,
            "stage": ctx.node.stage,
            "node_id": ctx.node.node_id,
            "scope_key": ctx.node.scope_key,
            "run_id": str(getattr(ctx.services.store, "run_id", "") or ""),
            # 逻辑输入指纹：不含 run_id/时间/绝对路径，可跨机器比较
            "inputs_fingerprint": fingerprint,
            "field_notes": {
                "inputs_fingerprint": "这份产物吃的是哪些输入（内容哈希 + 逻辑身份）",
            },
        },
        "payload": payload,
        "findings": list(findings or []),
    })
    ref = ArtifactRef(book_id=ctx.context.book_id, kind=kind,
                      logical_key=f"{ctx.node.stage}:{ctx.node.scope_key}/{name}",
                      path=path, producer_stage=ctx.node.stage,
                      producer_version=SHELL_VERSION,
                      dependency_digest=fingerprint,
                      meta={"inputs_fingerprint": fingerprint, "shell": SHELL_VERSION})
    return ref.with_content()


def _file_digest(path: Path) -> str | None:
    path = Path(path)
    return digest_file(path) if path.is_file() else None


def _blocked_if_missing(ctx: StageContext, path: Path, what: str) -> StageResult | None:
    """缺输入就**明确阻断**，不去"顺手重跑一遍"。

    为什么不当成"重新解析"：那会触发 MinerU（可能要 API）或模型，而本层承诺零成本；
    更坏的是它会掩盖"产物被挪走/没生成"这件事 —— 方案 §11 的原话是
    **文件存在不是缓存有效证据**，反过来，文件不在就是不在，只能如实报缺什么。
    """
    if Path(path).is_file():
        return None
    return StageResult.blocked(
        ctx.node.stage,
        f"缺 {what}：{path}。本次只**复用已有解析产物**：不重新解析、不上传、不回退 OCR。",
        outputs=())


def _repo_root() -> Path:
    """旧树（`blocks/`、`lib/`）所在的仓库根。

    正式流水线的内容适配仍复用这些实现。源码运行时取仓库目录，wheel 安装时
    优先取随包携带的 `_legacy_tree`；路径解析集中在这里，避免误读数据工作区的旧代码。
    retire_manifest 退休的是外部旧入口，不是当前正式链路需要的内部适配。
    """
    bundled = Path(__file__).resolve().parents[1] / '_legacy_tree'
    if (bundled / 'blocks').is_dir() and (bundled / 'lib').is_dir():
        return bundled
    return Path(__file__).resolve().parents[3]


def _unit_product_path(ctx: StageContext, stage: str) -> Path:
    """单元产物路径：`products/<stage>/<单元键>.json`。

    按**阶段 + 单元键**（而不是 `node_id`）命名，`audit` 才能确定性地找到
    `lesson` 那一份 —— 见 `_emit` 的 `path` 参数说明。
    """
    key = str(ctx.node.scope_key).replace("/", "_")
    return _store_root(ctx) / PRODUCTS_DIR / stage / f"{key}.json"


def _unit_definition(ctx: StageContext, unit_id: str) -> dict | None:
    """从 `chapters.json` 取这个单元的定义。

    为什么从产物读而不是自己拼一个 dict：旧实现要的 `original_text` / `anchor` / `title`
    是**身份**，多处拼一份必然会漂移；`chapters.json` 是唯一的权威来源。
    """
    chapters = read_json_soft(Path(ctx.context.parse) / "chapters.json") or {}
    for unit in chapters.get("lesson_units") or []:
        if isinstance(unit, dict) and str(unit.get("unit_id")) == unit_id:
            return unit
    return None


def _ticket_directives(tickets, unit_id, lesson_hash):
    from .repair import content_ticket_dispatch
    from ..blocks.audit.contracts import ticket_verdict_content
    directives = []
    for ticket in tickets:
        owner, kind, _ = content_ticket_dispatch(ticket)
        directives.append(RepairDirective(unit_id=unit_id, ticket_ids=(ticket.ticket_id,),
            owner=owner, kind=kind,
            reason=('补齐依据、修复方法和逐条通过标准：' if owner == 'reviewer' else '') + ticket.problem,
            artifact_hashes={f'lesson:{unit_id}': lesson_hash},
            verdict_content=ticket_verdict_content(ticket)))
    return directives

# The loader is a whitelist, not a route to retired authors or validators.
_ADAPTER_MODULES = {
    "build": "blocks.render.build",
    "model_generate": "blocks.model.model_generate",
}
_ADAPTERS = {}

def _legacy(name: str):
    if name not in _ADAPTER_MODULES and not name.startswith("blocks.parse."):
        raise ContractError("retired or unsupported adapter: " + name)
    if name not in _ADAPTERS:
        import importlib
        root = _repo_root()
        for rel in ("", "lib", "blocks/model"):
            entry = str(root / rel)
            if entry not in sys.path:
                sys.path.insert(0, entry)
        _ADAPTERS[name] = importlib.import_module(_ADAPTER_MODULES.get(name,name))
    return _ADAPTERS[name]


def _gateway_client(ctx, *, role, bucket):
    from .gateway_client import _gateway_client as client
    return client(ctx, role=role, bucket=bucket)
