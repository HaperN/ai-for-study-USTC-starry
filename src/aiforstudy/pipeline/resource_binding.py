"""资源级依赖、最终证书与旧运行边界（§6.2／§6.3，T05）。

## 为什么需要"比阶段更细"的失效

现有两层失效都只到**阶段**：

* `pipeline/repair.py` 的 `INVALIDATION_BY_KIND`：正文改了 → `figures/audit/render` 全失效；
* `pipeline/runner.py` 的阶段快照记录上游输入，单独使用这一层会让资源随整篇教案变化。
  历史独立 `pipeline/cache.py` 已退休；其局限的红证据见 `T05-red-behavior-probe.log`：
  三个图请求的缓存命中从 3/3 掉到 **0/3**，而它们的数学前提一个都没变。

本模块把粒度降到**单个资源**（`request_id`）：

* `ResourceBinding` 记录一个资源**实际依赖了哪些片段**（每个片段的路径 + 该片段的内容摘要），
  以及它绑定的是哪一版来源、哪一版数学规格、哪一版编译器与环境；
* `affected_resources(old_revision, new_revision, bindings)` 只返回**真的受影响**的资源集合；
  无关正文变化返回空集（付费新增请求 = 0）。

`source_hash`（整篇教案哈希）仍然保留，用于**追溯**"这份图是哪一版教案生成的"，
但它**不再是缓存键**（§6.2 最后一段）。缓存键是 `dependency_paths + spec_hash +
compiler_hash + environment_hash`。

## 两条"不许猜"的规则

1. **无可靠依赖就不是缓存，是赌注**：`dependency_paths` 为空、`source_hash` 为空、
   或当前事实（`new_revision.context_digest`）不可解析时，资源**保守失效一次**，
   而不是"看着像无关就复用"。§6.2：无可靠依赖的旧资源保守失效一次。
2. **声明过的片段路径消失也算变了**：新上下文里找不到该路径 → 失效，不猜它无关。

## 上下文放在哪

冻结签名只给了两个 `ArtifactRevision`，没有第三个参数可以携带"当前事实"。所以本模块
把 `ArtifactRevision.context_digest` 定义成一个**可解析的规范化 JSON 上下文**
（`encode_context` 生成、`decode_context` 读取），而不是一个不透明摘要：

```json
{"schema": "aiforstudy.resource-binding.context/1",
 "environment_hash": "...", "compiler_hash": "...",
 "fragments": {"fragments/second-derivative.md": "sha256:..."}}
```

做成可解析的，是为了让"字体变了"和"正文无关段落变了"在**资源级**能被分开判定 ——
不透明摘要做不到这件事，只能整段失效。

## 旧运行边界（§6.3）

缺 `graph_version` 的 run 视作 legacy：**可读、不可原地 resume**。
`resume_guard` / `assert_resumable` 给出 `legacy_graph_requires_new_run` 与新建命令建议；
判定过程**只读**（`read_json_soft`，不经过 `RunStore.open` 的 append 句柄），
所以"拒绝对旧目录的影响"是 **0 字节**。

## 本模块不做的事

不写文件、不调模型、不跑阶段、不 import runner。只有类型与纯函数。
"""
from __future__ import annotations
from ..core.resource_files import safe_relative_path as _safe_relative

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..blocks.audit.contracts import ArtifactRevision, RevisionLedger
from ..core.digest import canonical_json
from ..core.jsonio import read_json_soft
from ..errors import ContractError
from .graph import GRAPH_VERSION

# --------------------------------------------------------------------------- #
# §6.2 失效表：**逐行**写成数据，而不是散在注释里
# --------------------------------------------------------------------------- #
#: 无关正文变化：钱一分不花
UNRELATED_TEXT = "unrelated_text"
#: 相关条件／参数／目标公式变化：只有依赖它的资源重算
RELEVANT_PARAMETER = "relevant_parameter"
#: 字体／布局／编译器变化：重渲染，但数学规格不动
FONT_LAYOUT_COMPILER = "font_layout_compiler"
#: 仅 reviewer span／证据格式变化：媒体字节一个都不重算
EVIDENCE_FORMAT = "evidence_format"
#: 媒体文件丢失／篡改：损坏资源重算，且禁止沿用旧 accepted
MEDIA_LOST_OR_TAMPERED = "media_lost_or_tampered"
#: §6.2 的五类变化
CHANGE_KINDS = (UNRELATED_TEXT, RELEVANT_PARAMETER, FONT_LAYOUT_COMPILER,
                EVIDENCE_FORMAT, MEDIA_LOST_OR_TAMPERED)

#: 资源类别词表（`reuse` / `recompute` / `forbidden` 里的取值）
R_MATH_SPEC = "math_spec"
R_SOURCE = "source"
R_FIGURES = "figures"
R_ANIMATION_BYTES = "animation_bytes"
R_ALL_MEDIA_BYTES = "all_media_bytes"
R_CONTENT = "content"
R_VALID_MATH_EVIDENCE = "valid_math_evidence"
R_UNAFFECTED_MATH_EVIDENCE = "unaffected_math_evidence"
R_UNRELATED_RESOURCES = "unrelated_resources"
R_OTHER_RESOURCES_VALID = "other_resources_complete_and_independently_valid"
R_LESSON_BINDING = "lesson_binding"
R_FREE_REFERENCE_CHECK = "free_reference_consistency_check"
R_AFFECTED_SPECS = "affected_specs"
R_AFFECTED_RENDERS = "affected_renders"
R_AFFECTED_CRITERIA = "affected_criteria"
R_AFFECTED_FRAMES = "affected_frames"
R_FRAME_CHECKS = "frame_checks"
R_BROWSER_CHECKS = "browser_checks"
R_EVIDENCE_LOCATION = "evidence_location"
R_CLOSURE_VERIFICATION = "closure_verification"
R_DAMAGED_RESOURCE = "damaged_resource"
R_REUSE_OLD_ACCEPTED = "reuse_old_accepted"

#: §6.2 的失效表，**逐行**对应计划里那张表。
#: `model_requests` 是"这一类变化应当产生多少**新的付费请求**"的期望值：
#: `0` = 一个都不许新增（A05 的机器可读形式）。
INVALIDATION_TABLE: dict[str, dict] = {
    UNRELATED_TEXT: {
        "row": "无关正文",
        "reuse": (R_MATH_SPEC, R_FIGURES, R_ANIMATION_BYTES, R_VALID_MATH_EVIDENCE),
        "recompute": (R_LESSON_BINDING, R_FREE_REFERENCE_CHECK),
        "forbidden": (),
        "model_requests": 0,
        "note": "只做新教案绑定与免费引用一致性检查；图／动画字节一个都不重算",
    },
    RELEVANT_PARAMETER: {
        "row": "相关条件／参数／目标公式",
        "reuse": (R_UNRELATED_RESOURCES,),
        "recompute": (R_AFFECTED_SPECS, R_AFFECTED_RENDERS, R_AFFECTED_CRITERIA),
        "forbidden": (),
        "model_requests": "affected-only",
        "note": "只有依赖该片段的 request 失效；不相关资源照旧复用",
    },
    FONT_LAYOUT_COMPILER: {
        "row": "字体／布局／编译器",
        "reuse": (R_SOURCE, R_MATH_SPEC, R_UNAFFECTED_MATH_EVIDENCE),
        "recompute": (R_AFFECTED_FRAMES, R_FRAME_CHECKS, R_BROWSER_CHECKS),
        "forbidden": (),
        "model_requests": 0,
        "note": "重渲染不改数学规格（spec_hash 不变）；付费新增为 0（只重跑本地渲染／检查）",
    },
    EVIDENCE_FORMAT: {
        "row": "仅 reviewer span／证据格式",
        "reuse": (R_CONTENT, R_ALL_MEDIA_BYTES),
        "recompute": (R_EVIDENCE_LOCATION, R_CLOSURE_VERIFICATION),
        "forbidden": (),
        "model_requests": "evidence-only",
        "note": "内容与**全部媒体字节**复用；渲染新增必须为 0",
    },
    MEDIA_LOST_OR_TAMPERED: {
        "row": "媒体文件丢失／篡改",
        "reuse": (R_OTHER_RESOURCES_VALID,),
        "recompute": (R_DAMAGED_RESOURCE,),
        "forbidden": (R_REUSE_OLD_ACCEPTED,),
        "model_requests": "damaged-only",
        "note": "损坏资源必须重算；**禁止沿用旧 accepted**",
    },
}


def invalidation_row(change: str) -> dict:
    """取一行失效说明（给人读，也用于测试逐行核对 §6.2）。"""
    if change not in INVALIDATION_TABLE:
        raise ContractError(
            f"未知的变化类别 {change!r}；合法值 {list(CHANGE_KINDS)}")
    row = INVALIDATION_TABLE[change]
    return {"change": change, "row": row["row"], "reuse": list(row["reuse"]),
            "recompute": list(row["recompute"]), "forbidden": list(row["forbidden"]),
            "model_requests": row["model_requests"], "note": row["note"]}


# --------------------------------------------------------------------------- #
# 上下文指纹
# --------------------------------------------------------------------------- #
#: 上下文编码的 schema。改它等于让所有旧绑定的上下文不可解析（= 保守失效）。
CONTEXT_SCHEMA = "aiforstudy.resource-binding.context/1"


@dataclass(frozen=True)
class SourceContext:
    """一个 revision 的**可解析事实**：当前片段摘要 + 环境／编译器指纹。

    `fragments` 是 `路径 → 该片段当前的内容摘要`。片段路径必须是**可解析**的相对路径
    （不含 `..`、不是绝对路径），因为"依赖声明能对上号"是复用的前提（§6.2）。
    """
    environment_hash: str = ""
    compiler_hash: str = ""
    fragments: dict = field(default_factory=dict)

    def fragment_hash(self, path: str) -> str:
        return str(self.fragments.get(path) or "")


def encode_context(*, environment_hash: str = "", compiler_hash: str = "",
                   fragments: Mapping[str, str] | None = None) -> str:
    """把一个 revision 的事实编码进 `ArtifactRevision.context_digest`。"""
    return canonical_json({
        "schema": CONTEXT_SCHEMA,
        "environment_hash": str(environment_hash or ""),
        "compiler_hash": str(compiler_hash or ""),
        "fragments": {str(k): str(v) for k, v in sorted((fragments or {}).items())},
    })


def decode_context(value: Any) -> SourceContext | None:
    """读回上下文。**不可解析就返回 `None`** —— 由调用方保守失效，绝不猜。"""
    if isinstance(value, SourceContext):
        return value
    text = value if isinstance(value, str) else ""
    if not text.strip():
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("schema") != CONTEXT_SCHEMA:
        return None
    fragments = payload.get("fragments")
    if not isinstance(fragments, dict):
        return None
    return SourceContext(
        environment_hash=str(payload.get("environment_hash") or ""),
        compiler_hash=str(payload.get("compiler_hash") or ""),
        fragments={str(k): str(v) for k, v in fragments.items()})


def revision_context(revision: ArtifactRevision) -> SourceContext | None:
    return decode_context(getattr(revision, "context_digest", ""))


# --------------------------------------------------------------------------- #
# 资源绑定
# --------------------------------------------------------------------------- #
_RESOURCE_BINDING_FIELDS = frozenset({
    "request_id", "source_claims", "dependency_paths", "source_hash", "spec_hash",
    "compiler_hash", "environment_hash"})


def _require_text(value: Any, name: str, *, where: str) -> str:
    text = "" if value is None else str(value)
    if not text.strip():
        raise ContractError(f"{where}.{name} 必填且不能是空白")
    return text


def _check_fragment_path(path: Any) -> str:
    """片段路径必须**可解析**：相对、非空、不含 `..`／反斜杠／盘符。"""
    text = str(path or "")
    if not text.strip():
        raise ContractError("ResourceBinding.dependency_paths 里有空路径")
    if text.startswith(("/", "\\")) or "\\" in text or ":" in text:
        raise ContractError(
            f"片段路径必须是不含盘符的相对路径（POSIX），实际 {text!r} —— "
            f"绝对路径会让'同一片段'在不同机器上对不上号")
    if ".." in text.split("/"):
        raise ContractError(f"片段路径不许包含 '..'：{text!r}")
    return text


def _check_hash(value: Any, name: str) -> str:
    text = _require_text(value, name, where="ResourceBinding")
    if not text.startswith("sha256:") or len(text) != len("sha256:") + 64:
        raise ContractError(
            f"ResourceBinding.{name} 必须是 `sha256:` + 64 位十六进制的内容摘要，"
            f"实际 {text[:24]!r} —— 只有路径或版本号回答不了'这份依赖是不是那一版'")
    return text


@dataclass(frozen=True)
class ResourceBinding:
    """一个资源**到底依赖什么**（§6.2）。

    | 字段 | 语义 |
    |---|---|
    | `request_id` | 资源身份（图／动画请求的 id）；失效集合的元素就是它 |
    | `source_claims` | 该资源用到的数学前提／参数／目标公式（人可读，用于复核） |
    | `dependency_paths` | `片段路径 → 该片段的内容摘要`；**必须含数学前提／参数** |
    | `source_hash` | 生成时的整篇来源哈希（**追溯用**，不再当缓存键） |
    | `spec_hash` | 数学规格指纹；字体／布局／编译器变化**不许动它** |
    | `compiler_hash` | 可信编译器指纹；为空 = 与该资源无关 |
    | `environment_hash` | 渲染环境（字体等）指纹；为空 = 与该资源无关（纯规格资源） |

    `compiler_hash` / `environment_hash` 留空是**显式声明"我不依赖它"**：
    §6.2 第 3 行要求字体／编译器变化时"来源与数学规格可复用"，
    所以纯规格资源的这两个字段是空的，字体变了它也不失效。
    空 `dependency_paths` 或空 `source_hash` 都不是这个意思，而是"没有可靠依赖"，
    会被保守失效一次。
    """
    request_id: str
    source_claims: tuple[str, ...] = ()
    dependency_paths: dict = field(default_factory=dict)
    source_hash: str = ""
    spec_hash: str = ""
    compiler_hash: str = ""
    environment_hash: str = ""

    def __post_init__(self) -> None:
        _require_text(self.request_id, "request_id", where="ResourceBinding")
        object.__setattr__(self, "source_claims",
                           tuple(str(c) for c in (self.source_claims or ())))
        paths: dict[str, str] = {}
        for path, sha in dict(self.dependency_paths or {}).items():
            paths[_check_fragment_path(path)] = _check_hash(
                sha, f"dependency_paths[{path!r}]")
        object.__setattr__(self, "dependency_paths", paths)

    # ── 可信度
    @property
    def unreliable_reason(self) -> str:
        """为什么这个绑定**不足以**支撑复用。空串 = 可信。

        这三条都对应 §6.2 的"依赖声明通过审核后才能缓存复用"：
        声明不出依赖，就没资格谈复用。
        """
        if not self.dependency_paths:
            return "no-dependency-paths"
        if not str(self.source_hash or "").strip():
            return "no-source-hash"
        if not self.source_claims:
            return "no-source-claims"
        return ""

    @property
    def trustworthy(self) -> bool:
        return not self.unreliable_reason

    # ── 序列化
    def as_dict(self) -> dict:
        return {"request_id": self.request_id,
                "source_claims": list(self.source_claims),
                "dependency_paths": dict(sorted(self.dependency_paths.items())),
                "source_hash": self.source_hash, "spec_hash": self.spec_hash,
                "compiler_hash": self.compiler_hash,
                "environment_hash": self.environment_hash}

    @classmethod
    def from_dict(cls, data: Mapping) -> "ResourceBinding":
        unknown = sorted(set(data) - _RESOURCE_BINDING_FIELDS)
        if unknown:
            raise ContractError(
                f"ResourceBinding 里有未知字段 {unknown} —— 契约不静默吞字段")
        return cls(request_id=str(data.get("request_id") or ""),
                   source_claims=tuple(data.get("source_claims") or ()),
                   dependency_paths=dict(data.get("dependency_paths") or {}),
                   source_hash=str(data.get("source_hash") or ""),
                   spec_hash=str(data.get("spec_hash") or ""),
                   compiler_hash=str(data.get("compiler_hash") or ""),
                   environment_hash=str(data.get("environment_hash") or ""))

    def rebind(self, *, source_hash: str | None = None,
               environment_hash: str | None = None,
               compiler_hash: str | None = None) -> "ResourceBinding":
        """只升级"绑定到哪一版"，**不碰 `spec_hash` 与任何媒体字节**（§6.2 第 1／3 行）。"""
        from dataclasses import replace
        return replace(
            self, source_hash=self.source_hash if source_hash is None else source_hash,
            environment_hash=(self.environment_hash if environment_hash is None
                              else environment_hash),
            compiler_hash=(self.compiler_hash if compiler_hash is None else compiler_hash))


# --------------------------------------------------------------------------- #
# 资源级失效
# --------------------------------------------------------------------------- #
def _why_affected(binding: ResourceBinding, old_revision: ArtifactRevision,
                  new_context: SourceContext | None) -> str:
    if new_context is None:
        return ("当前上下文不可解析（revision.context_digest 不是本模块的上下文编码）"
                " → 保守失效一次")
    if not binding.trustworthy:
        return f"无可靠依赖（{binding.unreliable_reason}）→ 保守失效一次"
    if binding.source_hash != old_revision.hash:
        return (f"绑定不属于这一版来源（source_hash={binding.source_hash[:24]}… ≠ "
                f"{old_revision.hash[:24]}…）→ 保守失效一次")
    if binding.environment_hash and binding.environment_hash != new_context.environment_hash:
        return "渲染环境／字体指纹变化 → 对应画面重渲染（数学规格不变）"
    if binding.compiler_hash and binding.compiler_hash != new_context.compiler_hash:
        return "可信编译器指纹变化 → 对应画面重编译重渲染（数学规格不变）"
    for path, sha in sorted(binding.dependency_paths.items()):
        current = new_context.fragment_hash(path)
        if not current:
            return f"依赖片段路径在新上下文里消失：{path} → 保守失效一次"
        if current != sha:
            return f"依赖片段内容变化：{path} → 相关规格／渲染／criterion 重算"
    return ""


def explain_affected(old_revision: ArtifactRevision, new_revision: ArtifactRevision,
                     bindings: Mapping[str, ResourceBinding]) -> dict[str, str]:
    """`request_id → 为什么失效`。按 request_id 排序，方便直接进对比表。"""
    _check_revisions(old_revision, new_revision)
    _check_bindings(bindings)
    if not bindings:
        return {}
    if (old_revision.hash == new_revision.hash
            and old_revision.context_digest == new_revision.context_digest):
        return {}
    context = revision_context(new_revision)
    out: dict[str, str] = {}
    for request_id in sorted(bindings):
        reason = _why_affected(bindings[request_id], old_revision, context)
        if reason:
            out[request_id] = reason
    return out


def affected_resources(old_revision: ArtifactRevision, new_revision: ArtifactRevision,
                       bindings: dict[str, ResourceBinding]) -> set[str]:
    """**冻结接口**：哪些资源必须重算（细到单个 `request_id`）。

    返回空集 = 这次变化对**所有**付费资源都没有影响（例如只改了无关正文）。
    宁可失效也不瞎猜：无可靠依赖、路径消失、上下文不可解析一律进集合。
    """
    return set(explain_affected(old_revision, new_revision, bindings))


def _check_revisions(old_revision: Any, new_revision: Any) -> None:
    for name, value in (("old_revision", old_revision), ("new_revision", new_revision)):
        if not isinstance(value, ArtifactRevision):
            raise ContractError(
                f"affected_resources 需要 ArtifactRevision，{name} 拿到了 "
                f"{type(value).__name__}")


def _check_bindings(bindings: Any) -> None:
    if not isinstance(bindings, Mapping):
        raise ContractError("bindings 必须是 request_id → ResourceBinding 的映射")
    for request_id, binding in bindings.items():
        if not isinstance(binding, ResourceBinding):
            raise ContractError(
                f"bindings[{request_id!r}] 不是 ResourceBinding（是 {type(binding).__name__}）")
        if str(request_id) != binding.request_id:
            raise ContractError(
                f"bindings 的键 {request_id!r} 与 ResourceBinding.request_id "
                f"{binding.request_id!r} 不一致 —— 失效集合会指错资源")


def affected_since_previous(ledger: RevisionLedger,
                            bindings: dict[str, ResourceBinding],
                            *, steps: int = 1) -> set[str]:
    """在**不可变 revision 链**上判定：最近 `steps` 次修改让哪些资源失效。

    §6.2 最后一段要求"旧完整教案 hash 保留追溯"：`RevisionLedger` 里每一版都留着，
    所以"上次改了什么"是可查的事实，而不是靠当前文件猜。
    链上不足 `steps + 1` 版时返回空集（还没有可比的两版）。
    """
    if not isinstance(ledger, RevisionLedger):
        raise ContractError("affected_since_previous 需要 RevisionLedger")
    if int(steps) < 1:
        raise ContractError("steps 从 1 起算")
    revisions = ledger.revisions
    if len(revisions) < int(steps) + 1:
        return set()
    return affected_resources(revisions[-int(steps) - 1], revisions[-1], bindings)


@dataclass(frozen=True)
class ReusePlan:
    """一次变化的**资源级结论**：复用哪些、重算哪些、免费检查什么。"""
    reuse: tuple[str, ...]
    recompute: tuple[str, ...]
    reasons: dict
    rebound: dict
    free_checks: tuple[dict, ...]
    paid_requests: int = 0

    def as_dict(self) -> dict:
        return {"reuse": list(self.reuse), "recompute": list(self.recompute),
                "reasons": dict(sorted(self.reasons.items())),
                "rebound": {k: v.as_dict() for k, v in sorted(self.rebound.items())},
                "free_checks": [dict(c) for c in self.free_checks],
                "paid_requests": self.paid_requests}


def plan_reuse(old_revision: ArtifactRevision, new_revision: ArtifactRevision,
               bindings: dict[str, ResourceBinding]) -> ReusePlan:
    """把资源级失效做成一个**可执行的小计划**（§6.2 第 1 行的"免费"部分）。

    * `recompute` = `affected_resources` 的结果；
    * `reuse` = 其余资源；这些资源只做**免费**动作：升级 `source_hash` 到新教案版本，
      并把环境／编译器指纹刷新到当前值（`spec_hash` 与媒体字节不动）；
    * `free_checks` = 引用一致性检查（依赖声明是否仍可解析、来源声明是否齐全），
      **不调用任何模型**；因此 `paid_requests` 恒为 0。
    """
    reasons = explain_affected(old_revision, new_revision, bindings)
    _check_bindings(bindings)
    recompute = tuple(sorted(reasons))
    context = revision_context(new_revision)
    reuse: list[str] = []
    rebound: dict[str, ResourceBinding] = {}
    checks: list[dict] = []
    for request_id in sorted(bindings):
        if request_id in reasons:
            continue
        reuse.append(request_id)
        binding = bindings[request_id]
        fresh = binding.rebind(
            source_hash=new_revision.hash,
            environment_hash=(context.environment_hash
                              if (context and binding.environment_hash) else None),
            compiler_hash=(context.compiler_hash
                           if (context and binding.compiler_hash) else None))
        rebound[request_id] = fresh
        problems = []
        if context is None:
            problems.append("上下文不可解析")
        else:
            for path in sorted(binding.dependency_paths):
                if not context.fragment_hash(path):
                    problems.append(f"依赖片段路径不可解析：{path}")
        if not binding.source_claims:
            problems.append("没有来源声明（source_claims 为空）")
        checks.append({"check": "reference_consistency", "request_id": request_id,
                       "status": "pass" if not problems else "fail",
                       "problems": problems, "paid": False})
    return ReusePlan(reuse=tuple(reuse), recompute=recompute, reasons=reasons,
                     rebound=rebound, free_checks=tuple(checks), paid_requests=0)


# --------------------------------------------------------------------------- #
# 最终证书
# --------------------------------------------------------------------------- #
CERTIFICATE_SCHEMA = "1.0"
#: 单元最终证书：绑定 lesson／rubric／interaction／全部必需资源＋基础产物
KIND_UNIT_FINAL = "unit.final"
#: 基础产物复用证书：绑定 MinerU／目录／概念（§6.3 新 run 只可按 hash 复用）
KIND_BASE_INPUTS = "base.inputs"
CERTIFICATE_KINDS = (KIND_UNIT_FINAL, KIND_BASE_INPUTS)
#: 质量态（沿用既有词表，不另造一套）
QUALITY_STATES = ("accepted", "needs_review", "rejected")

#: 媒体出问题时**禁止沿用旧 accepted** 的那组错误码
_STALE_ACCEPTED_CODES = ("media_tampered", "missing_resource", "unsafe_resource_path",
                         "duplicate_resource", "missing_base_artifact",
                         "base_artifact_changed")


@dataclass(frozen=True)
class ValidationReport:
    """`verify_certificate` 的结果：**有效／错误／已验证哈希**。

    `errors` 的每一项是 `"code: message"`；`has(code)` 按 code 判，避免依赖文案。
    `verified_hashes` 只装**真的核对通过**的身份（`artifact_id` 或资源相对路径），
    所以"新 run 可复用哪些基础产物"是看得见的，而不是"文件在就算数"。
    """
    valid: bool
    errors: tuple[str, ...] = ()
    verified_hashes: dict = field(default_factory=dict)

    @property
    def codes(self) -> tuple[str, ...]:
        out: list[str] = []
        for item in self.errors:
            code = str(item).split(":", 1)[0].strip()
            if code and code not in out:
                out.append(code)
        return tuple(out)

    def has(self, code: str) -> bool:
        return code in self.codes

    def as_dict(self) -> dict:
        return {"valid": bool(self.valid), "errors": list(self.errors),
                "verified_hashes": dict(sorted(self.verified_hashes.items())),
                "codes": list(self.codes)}


def _artifact_fact(artifacts: Any, key: str) -> dict:
    """从 `artifacts` 里取一个身份的事实：`{sha256, bytes, version, revision}`。

    允许两种写法：直接给 `"sha256:..."` 字符串，或给带 `sha256`/`content_digest`/`digest`
    的字典（再加可选的 `bytes`／`version`／`revision`）。取不到就是 `{}`（= 缺）。
    """
    if not isinstance(artifacts, Mapping) or not str(key or "").strip():
        return {}
    raw = artifacts.get(key)
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        sha = (raw.get("sha256") or raw.get("content_digest") or raw.get("digest") or "")
        return {"sha256": str(sha or ""), "bytes": raw.get("bytes"),
                "version": raw.get("version"), "revision": raw.get("revision")}
    return {"sha256": str(raw), "bytes": None, "version": None, "revision": None}





def policy_facts(policy: Any) -> dict:
    """从 policy 里取**当前冻结的版本事实**。取不到就是 `None`（不猜、不套默认值）。

    支持 `Mapping` 与对象两种形状：`BudgetPolicy` 有 `policy_version` 与 `digest()`；
    rubric／graph 版本目前由调用方以属性或键显式给出（缺省 = 不做该项比对）。
    """
    if policy is None:
        return {}

    def take(*names):
        for name in names:
            if isinstance(policy, Mapping):
                value = policy.get(name)
            else:
                value = getattr(policy, name, None)
            if value not in (None, ""):
                return value
        return None

    digest = take("policy_digest")
    if digest is None:
        candidate = getattr(policy, "digest", None)
        if callable(candidate):
            try:
                digest = candidate()
            except Exception:                                     # noqa: BLE001
                digest = None
    return {"policy_version": take("policy_version"), "policy_digest": digest,
            "graph_version": take("graph_version"), "rubric_version": take("rubric_version"),
            "rubric_hash": take("rubric_hash")}


def _binding_error(cert: Mapping, facts: dict) -> list[str]:
    """校验 policy 与 graph 的版本绑定。"""
    errors: list[str] = []
    current_graph = str(facts.get("graph_version") or GRAPH_VERSION)
    declared_graph = str(cert.get("graph_version") or "")
    if not declared_graph:
        errors.append("certificate_missing_field: 证书缺 graph_version —— "
                      "缺它的证书按 legacy 处理，不能证明新图")
    elif declared_graph != current_graph:
        errors.append(f"graph_version_mismatch: 证书图版本 {declared_graph!r} ≠ 当前 "
                      f"{current_graph!r} —— 旧图的通过不能证明新图")

    block = cert.get("policy") if isinstance(cert.get("policy"), Mapping) else {}
    declared_version = str(block.get("version") or "")
    declared_digest = str(block.get("digest") or "")
    if not declared_version:
        errors.append("certificate_missing_field: 证书缺 policy.version")
    if not declared_digest:
        errors.append("certificate_missing_field: 证书缺 policy.digest")
    current_version = facts.get("policy_version")
    if current_version not in (None, "") and declared_version and \
            str(current_version) != declared_version:
        errors.append(f"policy_version_mismatch: 证书 policy v{declared_version} ≠ 当前 "
                      f"v{current_version}")
    current_digest = facts.get("policy_digest")
    if current_digest and declared_digest and str(current_digest) != declared_digest:
        errors.append("policy_digest_mismatch: 证书 policy digest 与当前冻结的不一致 —— "
                      "改过额度／护栏的运行不能沿用旧证书")
    return errors


def _rubric_errors(cert: Mapping, facts: dict, artifacts: Any,
                   verified: dict) -> list[str]:
    errors: list[str] = []
    block = cert.get("rubric") if isinstance(cert.get("rubric"), Mapping) else {}
    declared_version = str(block.get("version") or "")
    declared_hash = str(block.get("hash") or "")
    if not declared_version:
        errors.append("certificate_missing_field: 证书缺 rubric.version")
    if not declared_hash:
        errors.append("certificate_missing_field: 证书缺 rubric.hash")
    current_version = facts.get("rubric_version")
    if current_version not in (None, "") and declared_version and \
            str(current_version) != declared_version:
        errors.append(f"rubric_version_mismatch: 证书用的是 rubric v{declared_version}，"
                      f"当前冻结的是 v{current_version} —— 换了一套通过标准，"
                      f"旧证书的通过不算通过")
    current_hash = facts.get("rubric_hash")
    fact = _artifact_fact(artifacts, "rubric")
    if current_hash and declared_hash and str(current_hash) != declared_hash:
        errors.append("rubric_content_changed: 证书 rubric 内容摘要与当前不一致 —— "
                      "版本号没变但标准内容变了，同样拒绝")
    if fact.get("sha256") and declared_hash and fact["sha256"] != declared_hash:
        errors.append("rubric_content_changed: 证书 rubric 摘要与当前 rubric 产物不一致")
    if fact.get("version") not in (None, "") and declared_version and \
            str(fact["version"]) != declared_version:
        errors.append(f"rubric_version_mismatch: 当前 rubric 产物是 v{fact['version']}，"
                      f"证书是 v{declared_version}")
    if declared_hash and fact.get("sha256") == declared_hash:
        verified["rubric"] = declared_hash
    return errors


def _hash_binding_errors(*, label: str, key: str, declared: str, artifacts: Any,
                         verified: dict, missing_code: str, stale_code: str,
                         revision_key: str | None = None,
                         declared_revision: Any = None,
                         lookup_key: str | None = None) -> list[str]:
    """一个身份（artifact_id／相对路径）的 hash 绑定校验。

    `lookup_key` 允许"身份"与"在 `artifacts` 里的键"不同（基础产物常用路径当键、
    用 `parse.mineru` 当身份），`verified` 里仍然记**身份**。
    """
    errors: list[str] = []
    fact = _artifact_fact(artifacts, lookup_key or key)
    if not fact.get("sha256"):
        errors.append(f"{missing_code}: 证书绑定 {label}（{key}）但当前产物里没有它 —— "
                      f"缺 hash 就不能复用")
        return errors
    if declared and fact["sha256"] != declared:
        errors.append(f"{stale_code}: {label} 的当前摘要 {fact['sha256'][:24]}… ≠ 证书 "
                      f"{declared[:24]}… —— 正确但**过期**的证书不能证明新版本通过")
        return errors
    if (revision_key and declared_revision is not None
            and fact.get("revision") is not None
            and int(fact["revision"]) != int(declared_revision)):
        errors.append(f"stale_lesson_revision: {label} 当前 revision "
                      f"{fact['revision']} ≠ 证书 {declared_revision}")
        return errors
    if declared:
        verified[key] = declared
    return errors


def verify_certificate(certificate: dict, artifacts: dict,
                       policy) -> ValidationReport:
    """**冻结接口**：这份证书现在还成不成立。

    * `certificate`：`unit.final`（绑定 lesson／rubric／interaction／全部必需资源／基础产物）
      或 `base.inputs`（只绑定 MinerU／目录／概念基础产物，§6.3）；
    * `artifacts`：身份 → 当前事实（`"sha256:..."` 或 `{"sha256":…, "bytes":…, "version":…}`）；
    * `policy`：当前冻结策略（`BudgetPolicy` 或映射）。

    `valid=False` 的每一类原因都有稳定 code；调用方按 code 决定"重算哪个资源"，
    而不是靠文案猜。**正确但过期的证书**（旧教案／旧 rubric／旧图版本）一律 `valid=False`：
    这正是 A05 那条"旧哈希不能验收"的机器可读形式。
    """
    if not isinstance(certificate, Mapping):
        return ValidationReport(
            valid=False,
            errors=("certificate_malformed: 证书必须是 JSON 对象",
                    ), verified_hashes={})
    cert = dict(certificate)
    errors: list[str] = []
    verified: dict[str, str] = {}

    if str(cert.get("schema_version") or "") != CERTIFICATE_SCHEMA:
        errors.append(
            f"certificate_schema_version: 证书 schema_version 必须是 "
            f"{CERTIFICATE_SCHEMA!r}，实际 {cert.get('schema_version')!r} —— "
            f"旧证书只读留档，不自动升级")
    kind = str(cert.get("kind") or "")
    if kind not in CERTIFICATE_KINDS:
        errors.append(f"certificate_kind: 证书 kind 必须是 {list(CERTIFICATE_KINDS)}，"
                      f"实际 {kind!r}")
    unit_id = str(cert.get("unit_id") or "")
    if not unit_id and kind != KIND_BASE_INPUTS:
        errors.append("certificate_missing_field: 单元证书必须给 unit_id")

    facts = policy_facts(policy)
    errors.extend(_binding_error(cert, facts))
    if kind == KIND_UNIT_FINAL:
        errors.extend(_rubric_errors(cert, facts, artifacts, verified))

    # ── 基础产物（MinerU／目录／概念）：两种证书都要按 hash 核对
    base = cert.get("base_artifacts")
    if base is None:
        base = []
    if not isinstance(base, list):
        errors.append("certificate_malformed: base_artifacts 必须是列表")
        base = []
    if kind == KIND_BASE_INPUTS and not base:
        errors.append("certificate_missing_field: base.inputs 证书必须给 base_artifacts"
                      "（MinerU／目录／概念）—— 没有清单就没有可复用的东西")
    for raw in base:
        if not isinstance(raw, Mapping):
            errors.append("certificate_malformed: base_artifacts 的每一项必须是对象")
            continue
        key = str(raw.get("artifact_id") or raw.get("path") or "")
        declared = str(raw.get("sha256") or "")
        if not key or not declared:
            errors.append("certificate_missing_field: base_artifact 必须给 "
                          "artifact_id（或 path）与 sha256")
            continue
        if not _safe_relative(raw.get("path") or key) and not str(raw.get("path") or ""):
            errors.append(f"unsafe_resource_path: 基础产物路径不安全：{key!r}")
            continue
        # 身份优先按 artifact_id 找；找不到再按路径找（`artifacts` 两种键都常见）
        lookup = key
        if not _artifact_fact(artifacts, lookup):
            alternative = str(raw.get("path") or "")
            if alternative:
                lookup = alternative
        errors.extend(_hash_binding_errors(
            label=f"基础产物 {key}", key=key, lookup_key=lookup, declared=declared,
            artifacts=artifacts, verified=verified, missing_code="missing_base_artifact",
            stale_code="base_artifact_changed"))

    if kind == KIND_UNIT_FINAL:
        lesson = cert.get("lesson") if isinstance(cert.get("lesson"), Mapping) else None
        if not lesson:
            errors.append("certificate_missing_field: unit.final 证书必须绑定 lesson")
        else:
            key = str(lesson.get("artifact_id") or "")
            declared = str(lesson.get("hash") or "")
            if not key or not declared:
                errors.append("certificate_missing_field: lesson 必须给 artifact_id 与 hash")
            else:
                lookup = key if _artifact_fact(artifacts, key) else "lesson"
                errors.extend(_hash_binding_errors(
                    label=f"lesson {key}", key=key, lookup_key=lookup, declared=declared,
                    artifacts=artifacts, verified=verified,
                    missing_code="missing_lesson_binding", stale_code="stale_lesson_hash",
                    revision_key="revision",
                    declared_revision=lesson.get("revision")))

        interaction = (cert.get("interaction")
                       if isinstance(cert.get("interaction"), Mapping) else None)
        if not interaction:
            errors.append("certificate_missing_field: unit.final 证书必须绑定 interaction"
                          "（或显式 not_applicable + 政策 + 证据）")
        elif str(interaction.get("status") or "") == "not_applicable":
            if not str(interaction.get("policy_ref") or "").strip() or \
                    not str(interaction.get("evidence_hash") or "").strip():
                errors.append(
                    "unsupported_not_applicable: not_applicable 必须给政策引用与证据哈希 —— "
                    "缺环境／没运行不能冒充'不适用'（§4.1）")
        else:
            key = str(interaction.get("artifact_id") or "interaction.unit")
            declared = str(interaction.get("hash") or "")
            if not declared:
                errors.append("certificate_missing_field: 证书缺 interaction.hash")
            else:
                errors.extend(_hash_binding_errors(
                    label=f"interaction {key}", key=key, declared=declared,
                    artifacts=artifacts, verified=verified,
                    missing_code="missing_interaction_binding",
                    stale_code="stale_interaction_hash"))

        resources = cert.get("resources")
        if not isinstance(resources, list):
            errors.append("certificate_missing_field: unit.final 证书必须给 resources 列表"
                          "（缺清单 = 没有任何资源被验证过）")
            resources = []
        seen: dict[str, str] = {}
        for raw in resources:
            if not isinstance(raw, Mapping):
                errors.append("certificate_malformed: resources 的每一项必须是对象")
                continue
            request_id = str(raw.get("request_id") or "")
            relative = str(raw.get("path") or raw.get("relative_path") or "")
            declared = str(raw.get("sha256") or "")
            if not request_id:
                errors.append("certificate_missing_field: resource 缺 request_id")
            if not declared:
                errors.append(f"certificate_missing_field: resource "
                              f"{relative or request_id} 缺 sha256")
                continue
            if not _safe_relative(relative):
                errors.append(f"unsafe_resource_path: 资源路径必须是安全相对路径，"
                              f"实际 {relative!r}")
                continue
            if relative in seen and seen[relative] != declared:
                errors.append(f"duplicate_resource: 同名资源 {relative} 在同一证书里"
                              f"声明了两个不同摘要")
                continue
            seen[relative] = declared
            fact = _artifact_fact(artifacts, relative)
            if not fact.get("sha256"):
                errors.append(f"missing_resource: 证书声明的资源不在盘上：{relative} —— "
                              f"丢失的资源禁止沿用旧 accepted")
                continue
            if fact["sha256"] != declared:
                errors.append(f"media_tampered: {relative} 的内容指纹对不上："
                              f"证书 {declared[:24]}… ≠ 实际 {fact['sha256'][:24]}… —— "
                              f"篡改过的文件不许沿用旧 accepted")
                continue
            if raw.get("bytes") is not None and fact.get("bytes") is not None and \
                    int(raw["bytes"]) != int(fact["bytes"]):
                errors.append(f"media_tampered: {relative} 字节数不一致"
                              f"（证书 {raw['bytes']} ≠ 实际 {fact['bytes']}）")
                continue
            verified[relative] = declared

        # ── 质量态：不许把 needs_review 升级成 accepted
        quality = str(cert.get("quality") or "")
        if quality not in QUALITY_STATES:
            errors.append(f"certificate_missing_field: 证书必须给 quality ∈ "
                          f"{list(QUALITY_STATES)}，实际 {quality!r}")
        reports = cert.get("reports") if isinstance(cert.get("reports"), Mapping) else {}
        if quality == "accepted":
            pending = []
            for name in sorted(reports):
                raw = reports[name]
                status = str((raw or {}).get("status") or "") if isinstance(raw, Mapping) \
                    else str(raw)
                if status and status not in ("pass", "accepted"):
                    pending.append(f"{name}={status}")
            blocking = cert.get("blocking_open")
            if blocking not in (None, 0):
                pending.append(f"blocking_open={blocking}")
            if pending:
                errors.append(
                    f"quality_not_promotable: 证书声明 accepted，但绑定的报告没过：{pending} —— "
                    f"needs_review 不许被证书升级成 accepted")

    # ── 媒体出问题 → 明确"禁止沿用旧 accepted"
    media_failures = [item for item in errors
                      if item.split(":", 1)[0] in _STALE_ACCEPTED_CODES]
    if media_failures and str(cert.get("quality") or "") == "accepted":
        errors.append("stale_accepted_forbidden: 有资源被篡改／丢失，旧 accepted 不许沿用；"
                      "对应资源必须重算后再验")

    return ValidationReport(valid=not errors, errors=tuple(errors),
                            verified_hashes=verified)


# --------------------------------------------------------------------------- #
# 旧运行边界（§6.3）
# --------------------------------------------------------------------------- #
#: 缺 `graph_version`：legacy run，可读但**拒绝原地 resume**
LEGACY_GRAPH_CODE = "legacy_graph_requires_new_run"
#: 有 `graph_version` 但与当前代码不一致（跨版本不原地升级）
GRAPH_VERSION_CHANGED_CODE = "graph_version_changed_requires_new_run"
#: **没有这次 run**（state.json／plan.json 都读不到）。
#:
#: ⚠️ 它**不是** legacy，也**不是**拒绝：§6.3 管的是"**存在但老**的 run"。
#: 把"敲错 run_id"报成"旧图需要新建 run"，用户会照做 —— 既没找到原来的 run，
#: 又白建一个。所以这一档**放行**（`allowed=True`）且**不给新建命令**，
#: 让 `RunStore.open` 抛它本来的"没有这次运行的记录"。
RUN_NOT_FOUND_CODE = "run_not_found"


@dataclass(frozen=True)
class ResumeDecision:
    """能不能原地续跑这个 run。**不允许时必须给出可执行的下一步**。"""
    allowed: bool
    code: str = ""
    message: str = ""
    command: str = ""
    graph_version: str = ""
    run_id: str = ""

    @property
    def reason(self) -> str:
        return f"{self.code}: {self.message}" if self.code else self.message

    def as_dict(self) -> dict:
        return {"allowed": bool(self.allowed), "code": self.code,
                "message": self.message, "command": self.command,
                "graph_version": self.graph_version, "run_id": self.run_id}


class LegacyRunError(ContractError):
    """拒绝原地 resume。`code` / `command` / `decision` 都带出来，调用方不用解析文案。"""

    def __init__(self, decision: ResumeDecision) -> None:
        detail = f"{decision.reason}；新建命令：{decision.command}" if decision.command \
            else decision.reason
        super().__init__(detail)
        self.decision = decision
        self.code = decision.code
        self.command = decision.command


def read_run_identity(run_dir: str | Path) -> dict:
    """**只读**读一次 run 的身份。

    刻意不用 `RunStore.open`：它会 `open_for_append()`（在旧目录里碰事件流）。
    §6.3 要求"旧 run 可读但不修改"，所以判定路径只做 `read_json_soft`。
    """
    root = Path(run_dir)
    state = read_json_soft(root / "state.json") or {}
    plan = read_json_soft(root / "plan.json") or {}
    if not isinstance(state, dict):
        state = {}
    if not isinstance(plan, dict):
        plan = {}
    has_state = (root / "state.json").is_file()
    return {"run_id": str(state.get("run_id") or root.name),
            "book_id": str(state.get("book_id") or ""),
            "status": str(state.get("status") or ""),
            "graph_version": str(state.get("graph_version") or plan.get("graph_version") or ""),
            "resolved_scope": [str(u) for u in (plan.get("resolved_scope") or ())],
            "has_state": has_state,
            # "有没有这次 run" 与 "它是不是 legacy" 是两件事：分开报告
            "found": bool(has_state or plan)}


def new_run_command(book_id: str, units: Iterable[str] = (), *, goal: str = "deliver",
                    profile: str = "release") -> str:
    """legacy 拒绝里给出的**新建命令建议**（照现有 CLI 的真实开关拼）。"""
    selected = [str(u) for u in units if str(u).strip()]
    parts = ["aifs run", f"--book {book_id or '<book-id>'}"]
    parts.append("--units " + ",".join(selected) if selected else "--all")
    parts += [f"--goal {goal}", f"--profile {profile}"]
    return " ".join(parts)


def resume_guard(*, state: Mapping | None, plan: Mapping | None = None,
                 run_id: str = "", units: Iterable[str] = (),
                 book_id: str = "") -> ResumeDecision:
    """判定能否原地续跑。**只读、纯函数**（不打开 run 目录、不写任何文件）。

    四条规则，第 0 条最关键 —— **"没有这个 run" 与 "旧 run" 是两件事**：

    0. `state`／`plan` 都读不到 → `run_not_found`，**放行且不给新建命令**
       （交给 `RunStore.open` 报"没有这次运行的记录"）。敲错 run_id 的人
       不该被告知"去新建一个 run"；
    1. 缺 `graph_version` → `legacy_graph_requires_new_run`（§6.3）；
    2. 有 `graph_version` 但与当前 `GRAPH_VERSION` 不同 → 跨版本同样不原地升级；
    3. 一致 → 允许（后续仍按节点指纹复用）。
    """
    state = dict(state) if isinstance(state, Mapping) else {}
    plan = dict(plan) if isinstance(plan, Mapping) else {}
    resolved_run = str(run_id or state.get("run_id") or "")
    if not state and not plan:
        return ResumeDecision(
            allowed=True, code=RUN_NOT_FOUND_CODE,
            message=("没有读到这次 run 的 state.json／plan.json —— 这是"
                     "**没有这个 run**，不是 legacy 判定；交由读取方报"
                     "「没有这次运行的记录」，请核对 run_id（`aifs runs` 可列出）"),
            command="", graph_version="", run_id=resolved_run)
    graph_version = str(state.get("graph_version") or plan.get("graph_version") or "")
    resolved_book = str(book_id or state.get("book_id") or "")
    scope = [str(u) for u in units] or [str(u) for u in (plan.get("resolved_scope") or ())]
    command = new_run_command(resolved_book, scope)
    if not graph_version:
        return ResumeDecision(
            allowed=False, code=LEGACY_GRAPH_CODE,
            message=("这次 run 的 state/plan 里没有 graph_version，按 §6.3 视作 legacy："
                     "旧目录只读可浏览，但**不许原地 resume** —— "
                     "旧图与新图混跑会让节点集合、until 截断点和最终证书全部失去意义"),
            command=command, graph_version="", run_id=resolved_run)
    declarations = [str(doc.get("graph_version")) for doc in (state,plan) if doc.get("graph_version")]
    if graph_version != GRAPH_VERSION or any(version != GRAPH_VERSION for version in declarations):
        return ResumeDecision(
            allowed=False, code=GRAPH_VERSION_CHANGED_CODE,
            message=(f"这次 run 的图版本是 {graph_version}，当前代码是 {GRAPH_VERSION}；"
                     f"跨版本不原地升级（§6.3）"),
            command=command, graph_version=graph_version, run_id=resolved_run)
    return ResumeDecision(allowed=True, code="", message="图版本一致：允许按节点指纹续跑",
                          command=command, graph_version=graph_version,
                          run_id=resolved_run)


def assert_resumable(*, state: Mapping | None, plan: Mapping | None = None,
                     run_id: str = "", units: Iterable[str] = (),
                     book_id: str = "") -> str:
    """`resume_guard` 的抛异常版本，返回 `graph_version`。

    "没有这个 run"（`run_not_found`）**不抛异常**、返回空串：那时该由读取方
    （`RunStore.open`）报"没有这次运行的记录"，不是这里假装它是个旧图。
    """
    decision = resume_guard(state=state, plan=plan, run_id=run_id, units=units,
                            book_id=book_id)
    if not decision.allowed:
        raise LegacyRunError(decision)
    return decision.graph_version


__all__ = [
    "UNRELATED_TEXT", "RELEVANT_PARAMETER", "FONT_LAYOUT_COMPILER", "EVIDENCE_FORMAT",
    "MEDIA_LOST_OR_TAMPERED", "CHANGE_KINDS", "INVALIDATION_TABLE", "invalidation_row",
    "CONTEXT_SCHEMA", "SourceContext", "encode_context", "decode_context",
    "revision_context", "ResourceBinding", "affected_resources", "explain_affected",
    "affected_since_previous",
    "ReusePlan", "plan_reuse",
    "CERTIFICATE_SCHEMA", "KIND_UNIT_FINAL", "KIND_BASE_INPUTS", "CERTIFICATE_KINDS",
    "QUALITY_STATES", "ValidationReport", "verify_certificate", "policy_facts",
    "LEGACY_GRAPH_CODE", "GRAPH_VERSION_CHANGED_CODE", "RUN_NOT_FOUND_CODE",
    "ResumeDecision", "LegacyRunError",
    "read_run_identity", "new_run_command", "resume_guard", "assert_resumable",
]
