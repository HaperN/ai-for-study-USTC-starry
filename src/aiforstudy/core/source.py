"""原文的统一表示：`CanonicalSource`。

方案 §7 逐条：

> 原文统一为 CanonicalSource：raw source hash、可选修复版本、修复依据与差异、
> selected_text hash、source revision。概念、教案、审核、页面原文视图**全部读取同一
> selected_text**。旧 latex **不再因路径存在就获得优先级**；需要显式迁移并校验来源。
> **原始正文永久保留。**

> 原 clean 的有效清洗能力进入显式 source-repair 阶段：**只有输入要求且策略启用时执行**，
> 修复后先通过来源/公式差异检查，再成为 canonical source。**默认不全书自动清洗。**
> 此能力有单独的主线分支与测试，不保留第二套 OCR 教案生产路线。

> source-repair 位于 raw export/body/catalog **之后**、概念抽取**之前**。
> 原 U 始终按 raw body 的既有内容寻址规则计算；修复文本另以 `selected_text` hash /
> `source revision` 标识，**不重定义 U**。修复版本改变使所有消费 `selected_text` 的阶段失效。
> **只修公式表示而未改变正文切分时不重切目录**；确需改切分则回到解析阶段形成新版本。

三处刻意做成"构造即失败"的地方，因为它们都是"错了不会报错"的形态：

1. **修复必须有依据。** 一条没有 basis 的修复记录，等于把"某次悄悄改了原文"写进契约；
2. **改切分必须换解析版本。** 只改公式表示（`spans_changed=False`）可以只升级
   `source revision`；一旦动了切分，目录/单元身份就得重算，不许沿用旧 revision；
3. **只有一个 `selected_text`。** 谁都不许"我这儿也有一份原文"。旧 latex 曾经因为
   "路径存在"就获得优先级 —— 那是隐式决策，出了错查不出来。现在要显式迁移 + 校验来源。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from ..errors import ContractError

#: 消费 selected_text 的阶段 —— 修复版本一变，这些全部失效（方案 §7 末句）
SELECTED_TEXT_CONSUMERS = ("concepts", "lesson", "audit", "render", "interaction",
                           "figures")

#: 修复可以动的范围（用于差异检查与体积约束）
REPAIR_SCOPES = ("formula-notation", "ocr-typo", "whitespace", "spans")


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class RepairRecord:
    """一次原文修复。**依据、差异、检查结果三样缺一不可。**"""
    version: str
    basis: str
    scope: str
    diff_digest: str
    enabled_by: str
    checked: dict = field(default_factory=dict)
    changed_lines: int = 0
    formula_only: bool = True

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ContractError("修复记录必须有 version")
        if not self.basis.strip():
            raise ContractError(
                "修复记录必须有 basis（依据）：没有依据的修复等于把'某次悄悄改了原文'写进契约")
        if not self.enabled_by.strip():
            raise ContractError(
                "修复记录必须写明谁要求启用（enabled_by）—— 方案 §7：只有输入要求且策略启用时才执行")
        if self.scope not in REPAIR_SCOPES:
            raise ContractError(f"修复 scope 必须是 {REPAIR_SCOPES} 之一，实际 {self.scope!r}")
        if not self.diff_digest.startswith("sha256:"):
            raise ContractError(f"修复记录必须带差异指纹，实际 {self.diff_digest!r}")
        if not self.checked:
            raise ContractError(
                "修复记录必须带来源/公式差异检查结果（方案 §7：修复后**先通过差异检查**，"
                "再成为 canonical source）")

    @property
    def passed_checks(self) -> bool:
        return all(bool(v) for v in self.checked.values()) if self.checked else False

    def as_dict(self) -> dict:
        return {"version": self.version, "basis": self.basis, "scope": self.scope,
                "diff_digest": self.diff_digest, "enabled_by": self.enabled_by,
                "checked": dict(self.checked), "changed_lines": self.changed_lines,
                "formula_only": self.formula_only, "passed_checks": self.passed_checks}


@dataclass(frozen=True)
class CanonicalSource:
    """一本书的原文权威表示。"""
    raw_sha256: str
    selected_text_sha256: str
    revision: str
    repairs: tuple[RepairRecord, ...] = ()
    #: 修复是否改变了**正文切分**。True 就必须回到解析阶段形成新版本（方案 §7）
    spans_changed: bool = False
    raw_path: str = ""
    selected_path: str = ""
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        for name in ("raw_sha256", "selected_text_sha256"):
            value = getattr(self, name)
            if not str(value).startswith("sha256:"):
                raise ContractError(f"CanonicalSource.{name} 必须是 sha256 指纹，实际 {value!r}")
        if not self.revision.strip():
            raise ContractError("CanonicalSource.revision 必填")
        if self.spans_changed and not self.repairs:
            raise ContractError("spans_changed=True 但没有修复记录 —— 谁改的切分？")
        if self.spans_changed and not self.revision.startswith("parse-"):
            raise ContractError(
                f"改了正文切分就必须形成**新的解析版本**（revision 以 parse- 开头），"
                f"实际是 {self.revision!r}。方案 §7：确需改切分则回到解析阶段形成新版本，"
                f"不许在同一个解析版本里偷偷把切分改掉")
        failed = [r.version for r in self.repairs if not r.passed_checks]
        if failed:
            raise ContractError(
                f"这些修复没有通过来源/公式差异检查，不能成为 canonical source：{failed}")

    # ── 构造
    @classmethod
    def build(cls, raw: bytes, selected: bytes | None = None, *,
              revision: str, repairs: tuple[RepairRecord, ...] = (),
              spans_changed: bool = False, raw_path: str = "",
              selected_path: str = "") -> "CanonicalSource":
        selected = raw if selected is None else selected
        return cls(raw_sha256=_digest(raw), selected_text_sha256=_digest(selected),
                   revision=revision, repairs=repairs, spans_changed=spans_changed,
                   raw_path=raw_path, selected_path=selected_path)

    # ── 语义
    @property
    def is_repaired(self) -> bool:
        return bool(self.repairs) and self.raw_sha256 != self.selected_text_sha256

    @property
    def raw_preserved(self) -> bool:
        """原始正文永久保留：raw 指纹始终在册。"""
        return bool(self.raw_sha256)

    def consumer_view(self, role: str) -> dict:
        """**唯一的原文视图入口。** 任何消费者要原文都从这里拿。

        方案 §7 要求概念/教案/审核/页面原文视图读同一份 selected_text。
        给出一个函数而不是让各处自己找文件，是因为"各处自己找"就会各自决定
        （本项目已经出现过：正文修复分叉成两条路线）。
        """
        if role not in SELECTED_TEXT_CONSUMERS:
            raise ContractError(
                f"{role!r} 不在 selected_text 的消费者名单里：{SELECTED_TEXT_CONSUMERS}")
        return {"role": role, "selected_text_sha256": self.selected_text_sha256,
                "revision": self.revision, "is_repaired": self.is_repaired,
                "selected_path": self.selected_path}

    def invalidates(self, previous: "CanonicalSource") -> tuple[str, ...]:
        """与上一版相比，哪些阶段必须失效。

        规则来自方案 §7 末句 + §11 的失效表：
        selected_text 一变，**所有**读它的阶段失效；只有 raw 变（罕见）也一样，
        因为 raw 是 selected 的来源。
        """
        if previous.selected_text_sha256 == self.selected_text_sha256 and \
                previous.raw_sha256 == self.raw_sha256:
            return ()
        return tuple(SELECTED_TEXT_CONSUMERS) + ("parse.body",)

    def as_dict(self) -> dict:
        return {"_meta": {"file_type": "canonical_source", "schema_version": self.schema_version,
                          "generator": "aiforstudy.core.source",
                          "field_notes": {
                              "raw_sha256": "原始正文指纹；**永久保留**，不因修复被覆盖",
                              "selected_text_sha256": "下游实际读的那份文本的指纹",
                              "revision": "source revision；改了切分必须以 parse- 开头（新解析版本）",
                              "repairs[].basis": "修复依据 —— 没有依据的修复不许存在",
                              "repairs[].checked": "来源/公式差异检查结果；未通过不许成为 canonical",
                              "spans_changed": "是否改了正文切分；True 则必须回到解析阶段",
                              "消费者": "概念/教案/审核/页面原文视图都读**同一份** selected_text",
                          }},
                "raw_sha256": self.raw_sha256,
                "selected_text_sha256": self.selected_text_sha256,
                "revision": self.revision,
                "spans_changed": self.spans_changed,
                "is_repaired": self.is_repaired,
                "raw_preserved": self.raw_preserved,
                "raw_path": self.raw_path, "selected_path": self.selected_path,
                "repairs": [r.as_dict() for r in self.repairs],
                "consumers": list(SELECTED_TEXT_CONSUMERS)}

    @classmethod
    def from_dict(cls, payload: dict) -> "CanonicalSource":
        return cls(raw_sha256=str(payload.get("raw_sha256") or ""),
                   selected_text_sha256=str(payload.get("selected_text_sha256") or ""),
                   revision=str(payload.get("revision") or ""),
                   repairs=tuple(RepairRecord(**{k: v for k, v in r.items()
                                                 if k in RepairRecord.__dataclass_fields__})
                                 for r in payload.get("repairs") or ()),
                   spans_changed=bool(payload.get("spans_changed")),
                   raw_path=str(payload.get("raw_path") or ""),
                   selected_path=str(payload.get("selected_path") or ""),
                   schema_version=str(payload.get("schema_version") or "1.0"))


@dataclass(frozen=True)
class SourceRepairPolicy:
    """是否允许修复。**默认不允许**（方案 §7：默认不全书自动清洗）。"""
    enabled: bool = False
    requested_by: str = ""
    allowed_scopes: tuple[str, ...] = ("formula-notation",)

    def __post_init__(self) -> None:
        if self.enabled and not self.requested_by.strip():
            raise ContractError(
                "启用 source-repair 必须写明请求方（requested_by）："
                "方案 §7 要求'只有输入要求且策略启用时执行'，两个条件缺一不可")
        bad = [s for s in self.allowed_scopes if s not in REPAIR_SCOPES]
        if bad:
            raise ContractError(f"allowed_scopes 里有未知范围：{bad}；合法值 {REPAIR_SCOPES}")

    def check(self, scope: str) -> None:
        if not self.enabled:
            raise ContractError(
                "source-repair 未启用（默认不全书自动清洗）。要修复请显式设置策略并写明请求方。"
                "方案 §7：只有输入要求**且**策略启用时才执行。")
        if scope not in self.allowed_scopes:
            raise ContractError(
                f"修复范围 {scope!r} 不在允许清单 {self.allowed_scopes} 里。"
                f"改切分（spans）尤其不能顺手做 —— 它必须回到解析阶段形成新版本。")


__all__ = ["SELECTED_TEXT_CONSUMERS", "REPAIR_SCOPES", "RepairRecord", "CanonicalSource",
           "SourceRepairPolicy"]
