"""合成阶段实现：给 `smoke` 档一条**真的会跑完整链**的路径。

方案 §4 的档位表：

> | smoke | 使用明确的**合成教材和替身外部服务**；**内部真实编排和落盘** |
>   | 机制测试通过；**不得显示为真实教材质量验收** |
> | replay | 只取固定响应与本地输入；**缓存缺失立即阻断，禁止回退真实服务** |

"内部真实编排和落盘"是关键字：替身换的是**外部服务**（模型、MinerU），不是编排本身。
所以这里的实现会真的走图、真的写文件、真的记指纹、真的发事件 —— 只是算出来的内容是
合成的。这样"引擎机制对不对"和"教材质量好不好"就是两个可以分别回答的问题。

⚠️ 这些实现产出的产物**永远不参与正式交付**：它们的 `kind` 带 `synthetic.` 前缀，
`release_ready` 也恒为 false（§4 与 §15 A26）。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..contracts.artifact import ArtifactRef
from ..contracts.enums import ExecutionState, QualityState
from ..contracts.stage import StageResult
from ..core.jsonio import write_json_atomic
from .services import StageContext
from .graph import PREPARATION_STAGES

SYNTHETIC_PREFIX = "synthetic."


def _synthetic_path(ctx: StageContext, name: str) -> Path:
    """合成产物写在**本次运行的 work 目录**里 —— 不碰 `data/` 下的真实产物。"""
    store = ctx.services.store
    root = (store.work if store is not None
            else ctx.workspace.root / ".cache" / "smoke")
    node_part = ctx.node.node_id.replace("/", "_")
    return Path(root) / "synthetic" / name / f"{node_part}.json"


def _emit_synthetic(ctx: StageContext, name: str, payload: dict) -> ArtifactRef:
    path = _synthetic_path(ctx, name)
    write_json_atomic(path, {"_meta": {"file_type": f"{SYNTHETIC_PREFIX}{name}",
                                       "schema_version": "1.0",
                                       "generator": "aiforstudy.pipeline.stubs",
                                       "note": "合成产物：只用于机制测试，不参与正式交付",
                                       "node": ctx.node.node_id,
                                       "stage": ctx.node.stage,
                                       "scope_key": ctx.node.scope_key},
                             "payload": payload})
    ref = ArtifactRef(book_id=ctx.context.book_id, kind=f"{SYNTHETIC_PREFIX}{name}",
                      logical_key=f"{ctx.node.scope_key}:{ctx.node.stage}",
                      path=path, producer_stage=ctx.node.stage,
                      producer_version="smoke")
    return ref.with_content()


def synthetic_book_stage(kind: str):
    """整本级阶段的替身：写出一个带内容指纹的合成产物。"""
    def impl(ctx: StageContext) -> StageResult:
        ctx.services.cancel.checkpoint()
        ref = _emit_synthetic(ctx, kind, {"scope": ctx.node.scope_key,
                                          "stage": ctx.node.stage})
        ctx.log(f"合成阶段 {ctx.node.stage} 完成（{kind}）")
        return StageResult.ok(ctx.node.stage, outputs=(ref,),
                              quality_state=QualityState.ACCEPTED)
    return impl


def synthetic_unit_stage(kind: str):
    """单元级阶段的替身。**故意不是"永远成功"**：可按 scope 注入失败，好让
    "结构化失败传播"这件事有真实样本可测（§15 A19/A31）。"""
    def impl(ctx: StageContext) -> StageResult:
        ctx.services.cancel.checkpoint()
        fail_marker = getattr(ctx.services.providers, "fail_scopes", frozenset())
        if ctx.node.scope_key in fail_marker:
            return StageResult.failed(
                ctx.node.stage,
                f"合成注入的失败（scope={ctx.node.scope_key}）：用于验证失败传播与不缩小范围",
                outputs=())
        ref = _emit_synthetic(ctx, kind, {"unit": ctx.node.scope_key,
                                          "stage": ctx.node.stage})
        return StageResult.ok(ctx.node.stage, outputs=(ref,),
                              quality_state=QualityState.ACCEPTED)
    return impl


def synthetic_not_applicable_stage(kind: str):
    """声明了 `allow_not_applicable` 的阶段：对指定 scope 走"经政策认可的不适用"。"""
    inner = synthetic_unit_stage(kind)

    def impl(ctx: StageContext) -> StageResult:
        if ctx.node.scope_key in getattr(ctx.services.providers, "not_applicable_scopes",
                                        frozenset()):
            return StageResult.not_applicable(
                ctx.node.stage, policy=f"合成替身：{kind} 对该单元无适用对象",
                evidence="smoke 档的确定性规则，非'缺工具'")
        return inner(ctx)
    return impl


def synthetic_delivery_stage(ctx: StageContext) -> StageResult:
    """范围交付的替身：写一份**明确标为节选/合成**的清单。"""
    ctx.services.cancel.checkpoint()
    ref = _emit_synthetic(ctx, "delivery", {"scope": ctx.node.scope_key,
                                            "note": "合成交付清单，不是可阅读的页面"})
    return StageResult.ok(ctx.node.stage, outputs=(ref,),
                          quality_state=QualityState.ACCEPTED)


def smoke_registry():
    """smoke 档的阶段注册表：**每个阶段都有实现**，所以整链真的会跑完。

    失败/不适用由 `Providers.fail_scopes` / `not_applicable_scopes` 注入 ——
    替身的行为跟着**注入的边界**走，而不是改注册表本身。
    """
    from .services import StageRegistry
    registry = StageRegistry()
    from .graph import GRAPH_VERSION
    registry.graph_version = GRAPH_VERSION
    for name in PREPARATION_STAGES:
        registry.register(name, synthetic_book_stage(name))
    registry.register("source_format", synthetic_book_stage("source_format"))
    for name in ("lesson", "text_audit", "media_plan", "audit"):
        registry.register(name, synthetic_unit_stage(name))
    registry.register("figures", synthetic_not_applicable_stage("figures"))
    registry.register("interaction", synthetic_not_applicable_stage("interaction"))
    registry.register("render", synthetic_unit_stage("render"))
    registry.register("delivery", synthetic_delivery_stage)
    return registry


def replay_registry(cache_dir: Path | None):
    """Replay uses the sole production graph; Gateway enforces cache-only calls."""
    from .registry import release_registry
    return release_registry()


__all__ = ["SYNTHETIC_PREFIX", "synthetic_book_stage", "synthetic_unit_stage",
           "synthetic_not_applicable_stage", "synthetic_delivery_stage", "smoke_registry",
           "replay_registry"]
