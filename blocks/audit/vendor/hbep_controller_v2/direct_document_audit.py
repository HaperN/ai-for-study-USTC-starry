"""Audit a finished analysis directly against its source.

The public boundary is deliberately two documents.  The program discovers a
bounded semantic graph from those documents; callers cannot inject expected
issues, expected relations, or a content contract.
"""

from __future__ import annotations

from .restricted_reasoning import check_text

import ast
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Iterable

from .path_safety import assert_managed_path, assert_no_link_components, is_link_like
from .production_review import (
    AuditorWorker,
    ReviewIssue,
    ReviewTask,
    completed_review_report,
)
from .source_identity import tree_identity
from .unified_beginner_auditor import DiagramObservation, DiagramObserver


_SCHEMA = "HBEP-DIRECT-DOCUMENT-AUDIT/2"
_PROGRAM_VERSION = "hbep.direct-document-audit.v2"
_MATHBB = re.compile(
    r"\\mathbb\s*(?:\{(?P<braced>[A-Za-z])\}|(?P<plain>[A-Za-z]))"
    r"(?P<subscript>_\{?[A-Za-z0-9]+\}?)?"
)
_KNOWN_BASE_SETS = frozenset({"R", "Q", "Z", "N", "C"})
_CONCLUSION_PREFIX = re.compile(r"^\s*(?:因此|所以|从而|于是|故|这说明|这表明)")
_RHETORICAL_TRANSITION = re.compile(
    r"^\s*(?:(?:因此|所以|从而|于是|故)[，,：:]?\s*)?(?:我们)?"
    r"(?:将(?:构造|给出|讨论|介绍|证明)|下面(?:将|给出|讨论|介绍|证明)|"
    r"接下来|余下的问题|要解决(?:的)?|关键是|给出如下定义|"
    r"(?:取|选择|令|设|定义|记|把|将)\b)"
)
_EXPLANATORY_CONCLUSION = re.compile(
    r"^\s*(?:因此|所以|从而|于是|故|这说明|这表明)[^。！？\n]{0,220}"
    r"(?:不是|不能|必须|状态|环节|信息|价值|不只是|只控制|"
    r"不可|不得|需要|意味着|指向)"
)
_RULE_APPLICATION = re.compile(
    r"^\s*(?:由|根据|利用)\s*(?P<rule>[\u4e00-\u9fffA-Za-z0-9_-]{2,24}?(?:定理|原理|法则))\s*(?:可知|可得|得到|推出)"
)
_RULE_STATEMENT = re.compile(
    r"(?P<name>[\u4e00-\u9fffA-Za-z0-9_-]{2,24}?(?:定理|原理|法则))\s*[：:]"
)
_CONVENTION_STATEMENT = re.compile(
    r"(?:约定|规定|取)[^。！？\n]{0,48}(?:为正|为负|正方向|负方向|参考方向|正负号)"
)
_CONVENTION_USE = re.compile(
    r"(?:按|依照)[^。！？\n]{0,32}(?:方向|正负)[^。！？\n]{0,16}(?:确定|处理|列式|取号)|"
    r"(?:正负|符号)[^。！？\n]{0,24}(?:按|由)[^。！？\n]{0,24}(?:方向|约定)"
)
_TRANSFORMATION_REASON = re.compile(
    r"代入|展开|通分|约分|移项|合并同类项|因式分解|配方|有理化|"
    r"相加|相减|两式|开方|平方根|逐项|分步|第一步|第二步|先.+再|利用|根据|由.+(?:可得|得到)"
)
_SOURCE_CLAIM = re.compile(
    r"已核验|考试补充|考试范围|对应训练|训练定位|高频考法|命题趋势"
)
_BOUNDED_CONVERGES = re.compile(
    r"(?:有界[^。！？\n]{0,40}(?:逼近|近似|序列)[^。！？\n]{0,30}(?:一定|必然|总会)[^。！？\n]{0,20}(?:收敛|极限)|"
    r"完备性[^。！？\n]{0,30}(?:保证|确保)[^。！？\n]{0,30}有界[^。！？\n]{0,30}(?:逼近|序列|过程)[^。！？\n]{0,30}(?:一定|必然|总会)?(?:收敛|(?:存在|具有|有)极限))"
)
_NEIGHBORHOOD = re.compile(
    r"(?:邻域|U\s*\()[^。！？\n]{0,160}(?:\\lvert|\|)"
    r"[^。！？\n|]{1,48}(?:\\rvert|\|)\s*<\s*r"
)
_POSITIVE_RADIUS = re.compile(
    r"(?:r\s*>\s*0|0\s*<\s*r|r\s*\\in\s*\\mathbb\s*\{?R\}?[_^+{}]*)"
)
_STRICT_EXTERIOR = re.compile(
    r"严格[^。！？\n]{0,20}(?:外|外部)|(?:外|外部)[^。！？\n]{0,20}严格"
)
_NONSTRICT_EXTERIOR = re.compile(r"(?:\\geq?|≥|>=)")
_BECAUSE_CONCLUSION = re.compile(
    r"^\s*因为[^。！？]{1,160}[，,][^。！？]{1,160}(?:就是|所以|从而|可得)"
)
_DISPLAY_CUE = re.compile(
    r"(?:就有|可写成|写成|形成包含链|得到|可得|如下|使|并且|从而|因此)\s*[：:]?\s*$"
)
_INTERNAL_REASON = re.compile(
    r"(?:因为|由于|根据|利用|由|已知|注意到|这说明)[^。！？\n]{1,220}"
    r"(?:因此|所以|从而|于是|可得|得到|推出|故)"
)
_LOCAL_REASON_CUE = re.compile(
    r"因为|由于|根据|利用|注意到|可见|这说明|这表明|意味着|"
    r"(?:由|再由|又由)[^。！？\n]{0,120}(?:可知|可得|得到|推出|给出|知)|"
    r"(?:代入|展开|通分|约分|移项|整理|比较|相加|相减|逐项|反证)|"
    r"(?:定义|定理|单调性|连续性|极限唯一性|三角不等式|夹逼|上确界|下确界)"
    r"[^。！？\n]{0,100}(?:允许|保证|给出|得到|推出|迫使|说明|可知|成立)|"
    r"(?:证明|例如|上式|前式|上述|由此|代回|可唯一写成|"
    r"不等式链|公式|等式)[^。！？\n]{0,120}(?:给出|得到|推出|说明|成立|可知|可得)?|"
    r"否则[^。！？\n]{0,100}(?:矛盾|不成立)|与[^。！？\n]{0,100}矛盾"
)
_INFINITE_CLOSED_FORM = re.compile(
    r"(?:\\cdots|\\dots|…)(?:\\right)?\s*\)?\s*="
)
_INFINITE_SERIES_CONTEXT = re.compile(
    r"\\sum|级数|无穷|尾和|等比|\+\s*(?:\\cdots|\\dots|…)"
)
_INFINITE_RULE_VISIBLE = re.compile(
    r"等比(?:级数|数列)[^。！？]{0,40}(?:求和|公式)|无穷级数[^。！？]{0,40}(?:定义|收敛)|前\s*n\s*项和[^。！？]{0,40}极限",
    re.IGNORECASE,
)
_FORMULA_INTRODUCTION = re.compile(
    r"(?:参数方程(?:为)?|极坐标方程(?:为)?|闭曲线|"
    r"在直角坐标系下[，,]\s*设|我们来求椭圆|"
    r"(?:方程|表达式|公式)(?:为|如下|可写为|可化为|变成)|"
    r"定义为|记为|写成|表示为|可写为)\s*[：:]?\s*$"
)
_INCOMPLETE_DEPENDENCY_SUMMARY = re.compile(
    r"^-\s*正式陈述摘要：.+(?:并有|且有|则有|有|为|且|则|如果|若|满足|"
    r"表示为|等于|给出|差商的极限|下列公式|下列不等式)\s*$"
)
_STUDENT_ENGLISH_PHRASE = re.compile(
    r"\b[A-Za-z][A-Za-z'-]{1,}(?:[ \t,;:/()]+[A-Za-z][A-Za-z'-]{1,})+\b"
)
_STUDENT_SURFACE_MATH_OR_CODE = re.compile(
    r"\$+.+?\$+|\\\(.+?\\\)|\\\[.+?\\\]|`[^`]*`|:::\s*[A-Za-z][A-Za-z-]*",
    re.DOTALL,
)
_DISPLAY_MATH_LINE = re.compile(
    r"^\s*(?:\\\[|\$\$|\\begin\{|\\\(|\$[^$]+\$)"
)
_DEFINITION_CHAIN_CUE = re.compile(
    r"(?:^|[，,。；;：:]|\s)(?:定义|记|令|设|表示|写作|称为|记作)"
)
_DERIVATION_CONTEXT_CUE = re.compile(
    r"代入|展开|通分|约分|移项|合并|因式分解|配方|有理化|"
    r"链式法则|求导法则|逐项|两边|相加|相减|比较|取极限|"
    r"证明|定理|推论|定义|反函数|例如|由此|上式|前式|上述|"
    r"计算得|计算如下|可知|可得|得到|所以|因此|故|即|表明|说明|验证"
)
_MATH_RELATION_TOKEN = re.compile(
    r"(?<![<>=!])=(?!=)|\\(?:ne|leq?|geq?|in|notin|subset|sup|inf|lim|to|longrightarrow)\b|"
    r"[≠≤≥∈∉⊂⊆→]"
)
_DIAGRAM_CLAIM = re.compile(
    r"由图|从图|图中|如图|见图|图示|示意图|坐标图|曲线|箭头|节点|连线"
)
_DIAGRAM_RELATION_TERMS = (
    "递增",
    "递减",
    "相交",
    "平行",
    "垂直",
    "包含",
    "连接",
    "指向",
    "高于",
    "低于",
    "左侧",
    "右侧",
    "上方",
    "下方",
)
_PREREQUISITES = (
    (
        "无穷级数",
        re.compile(r"无穷级数|等比级数|级数求和|\\sum\s*_.*?\^\s*\{?\\infty\}?"),
        re.compile(r"(?:无穷级数|级数)\s*(?:定义为|定义是|是指|称为)|定义[^。！？\n]{0,24}(?:无穷级数|级数)"),
    ),
    (
        "上确界原理",
        re.compile(r"上确界原理"),
        re.compile(r"上确界原理\s*[：:]"),
    ),
)


@dataclass(frozen=True, slots=True)
class DirectAuditIssue:
    code: str
    start: int
    end: int
    exact_quote: str
    problem: str
    beginner_block: str
    required_condition: str
    evidence: str = ""
    severity: str = "P1"
    status: str = "unresolved"

    def __post_init__(self) -> None:
        if not self.code or not self.problem or not self.required_condition:
            raise ValueError("audit issue fields must be non-empty")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("audit issue span is invalid")
        if not self.exact_quote:
            raise ValueError("audit issue quote is empty")

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "status": self.status,
            "severity": self.severity,
            "start": self.start,
            "end": self.end,
            "exact_quote": self.exact_quote,
            "problem": self.problem,
            "beginner_block": self.beginner_block,
            "required_condition": self.required_condition,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class SemanticNode:
    node_id: str
    role: str
    start: int
    end: int
    text: str
    object_signatures: tuple[str, ...]
    operation_signatures: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SemanticEdge:
    edge_id: str
    premise_ids: tuple[str, ...]
    conclusion_id: str
    operation: str
    visible: bool


@dataclass(frozen=True, slots=True)
class DiscoveredSemanticGraph:
    nodes: tuple[SemanticNode, ...]
    edges: tuple[SemanticEdge, ...]
    target_ids: tuple[str, ...]
    topology_sha256: str


@dataclass(frozen=True, slots=True)
class ShadowEntity:
    entity_id: str
    role: str
    object_arity: int
    operation_signatures: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ShadowRule:
    rule_id: str
    premise_entity_ids: tuple[str, ...]
    conclusion_entity_id: str
    operation_signature: str
    granted: bool


@dataclass(frozen=True, slots=True)
class StructuralShadowWorld:
    entities: tuple[ShadowEntity, ...]
    rules: tuple[ShadowRule, ...]
    blocked_targets: tuple[str, ...]
    topology_sha256: str
    isomorphic: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": "HBEP-STRUCTURAL-SHADOW-WORLD/1",
            "carrier": [
                {
                    "entity_id": item.entity_id,
                    "role": item.role,
                    "object_slots": [
                        f"slot-{index:02d}"
                        for index in range(1, item.object_arity + 1)
                    ],
                    "operation_signatures": list(item.operation_signatures),
                }
                for item in self.entities
            ],
            "rules": [
                {
                    "rule_id": item.rule_id,
                    "premises": list(item.premise_entity_ids),
                    "conclusion": item.conclusion_entity_id,
                    "operation_signature": item.operation_signature,
                    "granted": item.granted,
                }
                for item in self.rules
            ],
            "blocked_targets": list(self.blocked_targets),
            "topology_sha256": self.topology_sha256,
            "isomorphic": self.isomorphic,
        }


@dataclass(frozen=True, slots=True)
class DirectAuditResult:
    source_sha256: str
    analysis_sha256: str
    program_sha256: str
    issues: tuple[DirectAuditIssue, ...]
    semantic_graph: DiscoveredSemanticGraph
    shadow_world: StructuralShadowWorld
    coverage_evidence: tuple[str, ...] = ()
    complete: bool = False
    restricted_reasoning: dict | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": _SCHEMA,
            "source_sha256": self.source_sha256,
            "analysis_sha256": self.analysis_sha256,
            "program_sha256": self.program_sha256,
            "complete": self.complete,
            "restricted_reasoning": self.restricted_reasoning,
            "issues": [item.as_dict() for item in self.issues],
        }


@dataclass(frozen=True, slots=True)
class _Sentence:
    start: int
    end: int
    text: str


class DirectDocumentAuditProgram:
    """One deterministic production auditor over a source and finished analysis."""

    auditor_id = _PROGRAM_VERSION

    def audit(
        self,
        source: str,
        analysis: str,
        *,
        artifacts: Mapping[str, bytes] | None = None,
        diagram_observer: DiagramObserver | None = None,
        source_artifact: str = "source.md",
        reviewed_artifact: str = "analysis.md",
    ) -> DirectAuditResult:
        _validate_document("source", source)
        _validate_document("analysis", analysis)
        bound_artifacts = _validate_artifacts(artifacts)
        sentences = _sentences(analysis)
        restricted = check_text(analysis)
        graph = _discover_graph(sentences, restricted=restricted)
        shadow = _compile_structural_shadow(graph)
        diagram_issues, diagram_evidence = _diagram_semantics(
            source,
            analysis,
            sentences,
            bound_artifacts,
            diagram_observer,
            source_artifact=source_artifact,
            reviewed_artifact=reviewed_artifact,
        )
        issues = _deduplicate(
            (
                *_introduced_formula_gaps(analysis),
                *_untranslated_student_visible_english(sentences),
                *_incomplete_dependency_summaries(analysis),
                *_undefined_symbols(analysis, sentences),
                *_missing_radius_condition(analysis, sentences),
                *_false_boundary_language(sentences),
                *_counterexamples(sentences),
                *_infinite_process_rules(analysis, sentences),
                *_unsupported_source_claims(source, sentences),
                *_prerequisite_order(sentences),
                *_source_relation_coverage(source, analysis, sentences),
                *_source_relation_contradictions(source, analysis, sentences),
                *_source_definition_coverage(source, analysis, sentences),
                *_false_numeric_equalities(sentences),
                *diagram_issues,
                *_separator_identification(sentences),
                *_bijection_bridges(analysis, sentences),
                *_missing_theorem_statements(sentences),
                *_missing_conventions(sentences),
                *_compressed_derivations(sentences),
                *_blocked_inferences(graph, shadow),
            )
        )
        return DirectAuditResult(
            source_sha256=_sha256(source),
            analysis_sha256=_sha256(analysis),
            program_sha256=_program_sha256(),
            issues=issues,
            semantic_graph=graph,
            shadow_world=shadow,
            coverage_evidence=diagram_evidence,
            complete=restricted["complete"] and not issues,
            restricted_reasoning=restricted,
        )

    def as_worker(
        self,
        source: str,
        *,
        source_artifact: str = "source.md",
        reviewed_artifact: str = "analysis.md",
        diagram_observer: DiagramObserver | None = None,
    ) -> AuditorWorker:
        _validate_document("source", source)
        if not isinstance(reviewed_artifact, str) or not reviewed_artifact.strip():
            raise ValueError("reviewed_artifact must be a non-empty package path")

        def run(task: ReviewTask):
            try:
                analysis = task.package.read_bytes(reviewed_artifact).decode("utf-8")
            except (KeyError, UnicodeDecodeError) as exc:
                raise ValueError(
                    "reviewed_artifact must name the UTF-8 reviewed document"
                ) from exc
            artifacts = {
                item.relative_path: item.content for item in task.package.files
            }
            result = self.audit(
                source,
                analysis,
                artifacts=artifacts,
                diagram_observer=diagram_observer,
                source_artifact=source_artifact,
                reviewed_artifact=reviewed_artifact,
            )
            restricted = result.restricted_reasoning
            # A fully decided negative review requests a revision. Unknown or
            # unresolved reasoning is still an incomplete execution, even when
            # another statement already has a confirmed content error.
            completed_negative_review = bool(
                result.issues
                and restricted
                and not restricted["unverified_spans"]
                and (restricted["checked_steps"] or restricted["blocked_steps"])
                and all(item["status"] == "confirmed_issue"
                        for item in restricted["blocked_steps"])
            )
            if not result.complete and not completed_negative_review:
                raise ValueError("RESTRICTED_REVIEW_INCOMPLETE: semantic review is required for unverified text")
            review_issues = tuple(
                ReviewIssue(
                    location=f"{reviewed_artifact}:{item.start}-{item.end}",
                    exact_quote=item.exact_quote,
                    problem=f"{item.problem} 初学者阻断：{item.beginner_block}",
                    required_change=item.required_condition,
                    code=item.code,
                    severity=item.severity,
                    evidence=(item.evidence,) if item.evidence else (),
                )
                for item in result.issues
            )
            return completed_review_report(
                task,
                review_issues,
                coverage_items=(
                    _PROGRAM_VERSION,
                    result.program_sha256,
                    f"source:{result.source_sha256}",
                    f"reviewed-artifact:{reviewed_artifact}",
                    "direct-source-boundary",
                    "student-visible-chinese",
                    "symbol-first-use",
                    "condition-boundary",
                    "counterexample-search",
                    "prerequisite-order",
                    "theorem-statement-before-use",
                    "convention-before-use",
                    "derivation-granularity",
                    "structural-shadow-replay",
                    "source-relation-replay",
                    "source-definition-coverage",
                    "numeric-equality-replay",
                    "diagram-semantic-observation",
                    *result.coverage_evidence,
                ),
            )

        return AuditorWorker(self.auditor_id, run)


def audit_dependency_structure(analysis: str, *, allowed_prerequisites=()) -> dict[str, object]:
    """Separate source-bound restricted checks from structural conversion.

    Unknown spans and blocked steps must be resolved by the existing semantic
    audit. Neither a successful conversion nor a legal return is a quality pass.
    """
    _validate_document("analysis", analysis)
    result = check_text(analysis, allowed_prerequisites=allowed_prerequisites)
    graph = _discover_graph(_sentences(analysis), restricted=result)
    shadow = _compile_structural_shadow(graph)
    return {**result, "isomorphic": shadow.isomorphic, "issues": []}


def audit_document_files(
    source_path: str | Path,
    analysis_path: str | Path,
    output_path: str | Path,
    *,
    diagram_observer: DiagramObserver | None = None,
) -> DirectAuditResult:
    source_file = assert_no_link_components(source_path, label="source")
    analysis_file = assert_no_link_components(analysis_path, label="analysis")
    source = _read_utf8_regular(source_file, "source")
    analysis = _read_utf8_regular(analysis_file, "analysis")
    root = Path(os.path.commonpath((source_file.parent, analysis_file.parent)))
    source_artifact = source_file.relative_to(root).as_posix()
    reviewed_artifact = analysis_file.relative_to(root).as_posix()
    artifacts = _read_local_diagram_artifacts(
        root,
        source,
        analysis,
        source_artifact=source_artifact,
        reviewed_artifact=reviewed_artifact,
    )
    result = DirectDocumentAuditProgram().audit(
        source,
        analysis,
        artifacts=artifacts,
        diagram_observer=diagram_observer,
        source_artifact=source_artifact,
        reviewed_artifact=reviewed_artifact,
    )
    destination = assert_no_link_components(output_path, label="audit output")
    _write_atomic_no_follow(
        destination,
        (json.dumps(result.as_dict(), ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        ),
    )
    return result


def _read_utf8_regular(path: str | Path, label: str) -> str:
    value = assert_no_link_components(path, label=label)
    if is_link_like(value) or not value.is_file():
        raise ValueError(f"{label} must be a regular file")
    if value.stat().st_size > 16 * 1024 * 1024:
        raise ValueError(f"{label} exceeds the bounded input size")
    return value.read_text(encoding="utf-8")


def _write_atomic_no_follow(path: Path, content: bytes) -> None:
    destination = assert_no_link_components(path, label="audit output")
    if is_link_like(destination) or (
        destination.exists() and not destination.is_file()
    ):
        raise ValueError("audit output must be a regular file path without links")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination = assert_no_link_components(destination, label="audit output")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        destination = assert_no_link_components(destination, label="audit output")
        if is_link_like(destination) or (
            destination.exists() and not destination.is_file()
        ):
            raise ValueError("audit output must be a regular file path without links")
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _read_local_diagram_artifacts(
    root: Path,
    source: str,
    analysis: str,
    *,
    source_artifact: str,
    reviewed_artifact: str,
) -> Mapping[str, bytes]:
    references = (
        *_resolved_image_references(source, source_artifact),
        *_resolved_image_references(analysis, reviewed_artifact),
    )
    result: dict[str, bytes] = {}
    for reference in dict.fromkeys(references):
        if reference.startswith("UNBOUND_"):
            continue
        path = assert_managed_path(
            root.joinpath(*PurePosixPath(reference).parts),
            root,
            label="diagram artifact",
        )
        if is_link_like(path) or not path.is_file():
            continue
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("diagram artifact exceeds the bounded input size")
        result[reference] = path.read_bytes()
    return result


def _validate_document(label: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    if len(value.encode("utf-8")) > 16 * 1024 * 1024:
        raise ValueError(f"{label} exceeds the bounded input size")


def _untranslated_student_visible_english(
    sentences: tuple[_Sentence, ...],
) -> tuple[DirectAuditIssue, ...]:
    issues: list[DirectAuditIssue] = []
    for sentence in sentences:
        visible = _STUDENT_SURFACE_MATH_OR_CODE.sub(" ", sentence.text)
        match = _STUDENT_ENGLISH_PHRASE.search(visible)
        if match is None:
            continue
        issues.append(
            _issue(
                "STUDENT_VISIBLE_LANGUAGE_NOT_CHINESE",
                sentence,
                "学生可见正文或图示文字中出现了未翻译的英文短语。",
                "中文读者会在术语、图例或读图步骤处被迫切换语言，且可能无法把英文标签与正文概念对应。",
                "把学生可见标题、图例、标注、说明和正文改为中文；必要的人名、缩写或数学变量须放在中文解释中。",
                evidence=match.group(0),
            )
        )
    return tuple(issues)


def _introduced_formula_gaps(analysis: str) -> tuple[DirectAuditIssue, ...]:
    lines: list[tuple[int, str]] = []
    offset = 0
    for raw in analysis.splitlines(keepends=True):
        text = raw.rstrip("\r\n")
        lines.append((offset, text))
        offset += len(raw)
    if not lines and analysis:
        lines.append((0, analysis))

    result: list[DirectAuditIssue] = []
    for index, (start, line) in enumerate(lines):
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith(("#", "!", "<!--", ":::", "|"))
            or not _FORMULA_INTRODUCTION.search(stripped)
        ):
            continue
        following = ""
        for _, candidate in lines[index + 1 :]:
            candidate = candidate.strip()
            if not candidate or candidate.startswith("<!--"):
                continue
            following = candidate
            break
        if following and _DISPLAY_MATH_LINE.search(following):
            continue
        sentence = _Sentence(start, start + len(line), stripped)
        result.append(
            _issue(
                "INTRODUCED_FORMULA_MISSING",
                sentence,
                "正文明确引出了一个公式或方程，但紧随其后的公式内容不存在。",
                "初学者随后看到的条件、代换或结论没有可对应的原式，推导从这里断开。",
                "补回被引出的完整公式，并让后续符号、条件和推导逐项对应。",
            )
        )
    return tuple(result)


def _incomplete_dependency_summaries(analysis: str) -> tuple[DirectAuditIssue, ...]:
    result: list[DirectAuditIssue] = []
    offset = 0
    for raw in analysis.splitlines(keepends=True):
        line = raw.rstrip("\r\n")
        stripped = line.strip()
        if _INCOMPLETE_DEPENDENCY_SUMMARY.search(stripped):
            sentence = _Sentence(offset, offset + len(line), stripped)
            result.append(
                _issue(
                    "INCOMPLETE_DEPENDENCY_SUMMARY",
                    sentence,
                    "依赖索引把只剩引导语、缺少紧随公式的摘录标成了正式陈述摘要。",
                    "初学者会把残缺索引误认为完整定理，并在真正需要条件或公式时无从核对。",
                    "补全摘要中的公式和结论，或明确改成只用于跳转正文的检索摘录。",
                )
            )
        offset += len(raw)
    return tuple(result)


def _sentences(text: str) -> tuple[_Sentence, ...]:
    raw_result: list[_Sentence] = []
    in_fence = False
    token_pattern = re.compile(r"\\\[.*?\\\]|\$\$.*?\$\$|[^\n]+", re.DOTALL)
    for token_match in token_pattern.finditer(text):
        token = token_match.group()
        stripped = token.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith((r"\[", "$$")):
            left = len(token) - len(token.lstrip())
            right = len(token.rstrip())
            raw_result.append(
                _Sentence(
                    token_match.start() + left,
                    token_match.start() + right,
                    token[left:right],
                )
            )
            continue
        for sentence_match in re.finditer(r".+?[。！？](?:\s*|$)|.+$", token):
            raw = sentence_match.group()
            left = len(raw) - len(raw.lstrip())
            right = len(raw.rstrip())
            if right <= left:
                continue
            start = token_match.start() + sentence_match.start() + left
            end = token_match.start() + sentence_match.start() + right
            raw_result.append(_Sentence(start, end, raw[left:right]))
    result: list[_Sentence] = []
    index = 0
    while index < len(raw_result):
        current = raw_result[index]
        if (
            index + 1 < len(raw_result)
            and (
                re.fullmatch(r"\s*(?:因此|所以|从而|于是|故)[，,：:]?\s*", current.text)
                or (
                    raw_result[index + 1].text.lstrip().startswith((r"\[", "$$"))
                    and _DISPLAY_CUE.search(current.text)
                )
            )
            and not text[current.end : raw_result[index + 1].start].strip()
        ):
            following = raw_result[index + 1]
            result.append(
                _Sentence(
                    current.start,
                    following.end,
                    text[current.start : following.end].strip(),
                )
            )
            index += 2
            continue
        result.append(current)
        index += 1
    return tuple(result)


def _discover_graph(sentences: tuple[_Sentence, ...], *, restricted=None) -> DiscoveredSemanticGraph:
    if restricted is None:
        end=max((s.end for s in sentences),default=0)
        chars=[" "]*end
        for sentence in sentences:
            chars[sentence.start:sentence.end]=sentence.text
        restricted=check_text("".join(chars))
    nodes=[]; edges=[]; targets=[]; by_span={}
    def node(item, role):
        key=(item.get("start"),item.get("end"))
        if None in key: return None
        if key in by_span: return by_span[key]
        ident=f"n{len(nodes)+1:08d}"
        quote=item.get("quote","")
        nodes.append(SemanticNode(ident,role,key[0],key[1],quote,
                                  _object_signatures(quote),_operation_signatures(quote)))
        by_span[key]=ident
        return ident
    for item in restricted.get("unverified_spans",[]):node(item,"unverified")
    for item in restricted.get("declarations",[]):node(item,"allowed_prerequisite")
    for item in [*restricted.get("checked_steps",[]),*restricted.get("blocked_steps",[])]:
        premises=[]
        for evidence in item.get("evidence",[]):
            ident=node(evidence,"condition")
            if ident:premises.append(ident)
        target=node(item,"conclusion")
        if target is None:continue
        targets.append(target)
        edges.append(SemanticEdge(f"e{len(edges)+1:08d}",tuple(premises),target,
                                  item.get("rule","unverified"),item.get("status")=="checked_clear"))
    digest=_topology_digest(tuple(nodes),tuple(edges),tuple(targets))
    return DiscoveredSemanticGraph(tuple(nodes),tuple(edges),tuple(targets),digest)


def _compile_structural_shadow(graph: DiscoveredSemanticGraph) -> StructuralShadowWorld:
    entity_by_node = {
        node.node_id: f"carrier-{index:08d}"
        for index, node in enumerate(graph.nodes, start=1)
    }
    entities = tuple(
        ShadowEntity(
            entity_by_node[node.node_id],
            node.role,
            len(node.object_signatures),
            node.operation_signatures,
        )
        for node in graph.nodes
    )
    rules = tuple(
        ShadowRule(
            rule_id=f"gate-{index:08d}",
            premise_entity_ids=tuple(entity_by_node[item] for item in edge.premise_ids),
            conclusion_entity_id=entity_by_node[edge.conclusion_id],
            operation_signature=f"{edge.operation}/{len(edge.premise_ids)}->1",
            granted=edge.visible,
        )
        for index, edge in enumerate(graph.edges, start=1)
    )
    conclusion_entities = {item.conclusion_entity_id for item in rules}
    reachable = {
        item.entity_id
        for item in entities
        if item.entity_id not in conclusion_entities and item.role in {"condition", "allowed_prerequisite"}
    }
    changed = True
    while changed:
        changed = False
        for rule in rules:
            if (
                rule.granted
                and all(item in reachable for item in rule.premise_entity_ids)
                and rule.conclusion_entity_id not in reachable
            ):
                reachable.add(rule.conclusion_entity_id)
                changed = True
    blocked = tuple(
        entity_by_node[item]
        for item in graph.target_ids
        if entity_by_node[item] not in reachable
    )
    topology = _shadow_topology_digest(entities, rules, graph.target_ids)
    return StructuralShadowWorld(
        entities=entities,
        rules=rules,
        blocked_targets=blocked,
        topology_sha256=topology,
        isomorphic=topology == graph.topology_sha256,
    )


def _undefined_symbols(
    analysis: str, sentences: tuple[_Sentence, ...]
) -> tuple[DirectAuditIssue, ...]:
    result: list[DirectAuditIssue] = []
    for match in _MATHBB.finditer(analysis):
        base = match.group("braced") or match.group("plain")
        subscript = match.group("subscript")
        if subscript is None and base in _KNOWN_BASE_SETS:
            continue
        prefix = analysis[: match.start()]
        current = next((item for item in sentences if item.start <= match.start() < item.end), None)
        if _symbol_is_defined(prefix, current.text if current else "", match.group()):
            continue
        sentence = current or _sentence_for_offset(sentences, match.start())
        result.append(
            _issue(
                "SYMBOL_DEFINITION_MISSING",
                sentence,
                f"符号 {match.group()} 在首次使用前没有定义。",
                "初学者无法判断该符号包含哪些对象，也无法核对后续量词范围。",
                "在首次使用前明确给出该符号的集合定义或约定。",
            )
        )
    return tuple(result)


def _symbol_is_defined(prefix: str, current: str, symbol: str) -> bool:
    escaped = re.escape(symbol)
    math_open = r"(?:\$|\\\()?"
    math_close = r"(?:\$|\\\))?"
    definition = re.compile(
        rf"(?:记|定义|规定|令)\s*{math_open}{escaped}{math_close}\s*(?:为|表示|是)|"
        rf"{math_open}{escaped}{math_close}\s*(?:表示|定义为|是指)"
    )
    return bool(definition.search(prefix) or definition.search(current))


def _missing_radius_condition(
    analysis: str, sentences: tuple[_Sentence, ...]
) -> tuple[DirectAuditIssue, ...]:
    result: list[DirectAuditIssue] = []
    for index, sentence in enumerate(sentences):
        if re.search(r"(?:\\\(|\$)?r(?:\\\)|\$)?\s*(?:次|阶)", sentence.text):
            continue
        local = "\n".join(
            item.text for item in sentences[max(0, index - 2) : index + 1]
        )
        if not (
            _NEIGHBORHOOD.search(local)
            or (
                "邻域" in sentence.text
                and re.search(r"(?:\\\(|\$)r(?:\\\)|\$)", sentence.text)
            )
        ):
            continue
        context = analysis[max(0, sentence.start - 240) : sentence.end]
        if _POSITIVE_RADIUS.search(context):
            continue
        result.append(
            _issue(
                "CONDITION_POSITIVE_RADIUS_MISSING",
                sentence,
                "邻域半径使用了 r，但没有声明 r>0。",
                "当 r<=0 时该表达式不再表示通常的邻域，初学者无法知道公式的适用范围。",
                "在定义邻域前明确声明 r>0。",
            )
        )
    return tuple(result)


def _false_boundary_language(
    sentences: tuple[_Sentence, ...],
) -> tuple[DirectAuditIssue, ...]:
    return tuple(
        _issue(
            "BOUNDARY_DESCRIPTION_FALSE",
            sentence,
            "非严格不等式包含边界点，却被描述为严格位于外部。",
            "取等号时点在边界上，初学者会把边界与外部混为一谈。",
            "区分边界 |x-a|=r 与严格外部 |x-a|>r，或把文字改为邻域之外（含边界）。",
        )
        for sentence in sentences
        if _STRICT_EXTERIOR.search(sentence.text)
        and _NONSTRICT_EXTERIOR.search(sentence.text)
        and not re.search(
            r"(?:含边界|等号[^。！？]{0,24}边界|开区间[^。！？]{0,32}之外|严格外部[^。！？]{0,24}>)",
            sentence.text,
        )
    )


def _counterexamples(
    sentences: tuple[_Sentence, ...],
) -> tuple[DirectAuditIssue, ...]:
    return tuple(
        _issue(
            "MATHEMATICAL_COUNTEREXAMPLE_FOUND",
            sentence,
            "有界序列不一定收敛。",
            "该断言把有界性误当成收敛性的充分条件，会使后续极限推理失效。",
            "补充保证收敛的真实条件，或只陈述可抽取收敛子列等正确结论。",
            evidence="反例：x_n=(-1)^n 有界但不收敛。",
        )
        for sentence in sentences
        if _BOUNDED_CONVERGES.search(sentence.text)
    )


def _infinite_process_rules(
    analysis: str, sentences: tuple[_Sentence, ...]
) -> tuple[DirectAuditIssue, ...]:
    result: list[DirectAuditIssue] = []
    for sentence in sentences:
        if not _INFINITE_CLOSED_FORM.search(sentence.text):
            continue
        if not _INFINITE_SERIES_CONTEXT.search(sentence.text):
            continue
        prefix = analysis[: sentence.start]
        if _INFINITE_RULE_VISIBLE.search(prefix):
            continue
        result.append(
            _issue(
                "INFINITE_PROCESS_RULE_NOT_VISIBLE",
                sentence,
                "解析把无限和直接替换成闭式结果，却没有说明该无限过程为何收敛以及使用了什么求和规则。",
                "初学者只能模仿等号，无法判断无限相加何时允许或结果为何成立。",
                "先给出有限部分和公式，再说明取极限的依据；若极限尚未定义，则改用有限余项界并明确留待后文证明。",
            )
        )
    return tuple(result)


def _unsupported_source_claims(
    source: str, sentences: tuple[_Sentence, ...]
) -> tuple[DirectAuditIssue, ...]:
    result: list[DirectAuditIssue] = []
    for sentence in sentences:
        claims = tuple(dict.fromkeys(_SOURCE_CLAIM.findall(sentence.text)))
        unsupported = tuple(item for item in claims if item not in source)
        if not unsupported:
            continue
        result.append(
            _issue(
                "SOURCE_AUTHORITY_UNSUPPORTED",
                sentence,
                "解析作出了来源中没有的考试或核验声明。",
                "初学者会把没有证据的范围判断当成教材事实。",
                "删除该权威性声明，或提供冻结来源中逐字节可核对的依据。",
                evidence="未在冻结来源中找到：" + "、".join(unsupported),
            )
        )
    return tuple(result)


def _prerequisite_order(
    sentences: tuple[_Sentence, ...],
) -> tuple[DirectAuditIssue, ...]:
    result: list[DirectAuditIssue] = []
    analysis = "\n".join(item.text for item in sentences)
    for name, use_pattern, definition_pattern in _PREREQUISITES:
        definition = definition_pattern.search(analysis)
        for sentence in sentences:
            use = use_pattern.search(sentence.text)
            if use is None:
                continue
            local_prefix = sentence.text[max(0, use.start() - 32) : use.start()]
            if re.search(
                r"(?:不需要|无需|不使用|不调用|尚未调用|还未调用|避免|不必)"
                r"[^。！？\n]{0,20}$",
                local_prefix,
            ):
                continue
            if re.search(
                r"^\s*(?:\d+[.、]\s*)?(?:讨论|判断|研究|证明|计算|求|说明|检验)",
                sentence.text,
            ):
                continue
            absolute_use = analysis.find(sentence.text) + use.start()
            if definition is not None and definition.start() <= absolute_use:
                break
            result.append(
                _issue(
                    "PREREQUISITE_USED_BEFORE_VISIBLE",
                    sentence,
                    f"{name} 在使用前没有给出可执行的定义或陈述。",
                    "初学者只能记住结论，不能判断当前步骤为何允许使用它。",
                    f"在首次使用前陈述 {name} 的内容、适用条件和本步满足条件的对应关系。",
                )
            )
            break
    return tuple(result)


def _source_relation_coverage(
    source: str,
    analysis: str,
    sentences: tuple[_Sentence, ...],
) -> tuple[DirectAuditIssue, ...]:
    marker = source.find("绝对值满足")
    if marker < 0:
        return ()
    source_tail = source[marker : marker + 600]
    required: list[tuple[str, str]] = []
    for formula in re.findall(r"\$([^$]+)\$", source_tail):
        if re.search(r"\\circ", formula):
            continue
        if "|" in formula and any(
            symbol in formula for symbol in ("=", "\\le", "\\ge", "≤", "≥")
        ):
            signature = _absolute_relation_signature(formula)
            if signature is not None:
                required.append((signature, formula))
        if len(required) >= 3:
            break
    missing = [
        item
        for item in required
        if not _analysis_has_absolute_relation(analysis, item[0])
    ]
    if not missing:
        return ()
    anchor = next(
        (item for item in reversed(sentences) if "绝对值" in item.text),
        sentences[0],
    )
    return tuple(
        _issue(
            "SOURCE_REQUIRED_RELATION_OMITTED",
            anchor,
            "冻结来源明确列出的绝对值基本关系在解析中没有完整出现。",
            "后续距离与不等式推导会调用未曾交代的基本关系。",
            f"补出并解释来源关系：{formula.strip()}。",
            evidence=f"冻结来源关系：{formula.strip()}",
        )
        for _, formula in missing
    )


@dataclass(frozen=True, slots=True)
class _MathRelation:
    formula: str
    left: str
    operator: str
    right: str
    section_context: tuple[str, ...] = ()
    definition_like: bool = False


def _math_relations(text: str) -> tuple[_MathRelation, ...]:
    result: list[_MathRelation] = []
    seen: set[tuple[str, str, str]] = set()
    for formula, start, _ in _math_segments_with_spans(text):
        normalized = _normalize_math(formula).strip(".,;，。；")
        matches = tuple(
            re.finditer(r"(?<![<>=!])(?:=|\\le|\\ge|≤|≥|<|>)(?!=)", normalized)
        )
        if len(matches) != 1:
            continue
        match = matches[0]
        operator = match.group()
        left = normalized[: match.start()]
        right = normalized[match.end() :]
        if not left or not right:
            continue
        key = (left, operator, right)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            _MathRelation(
                formula=formula,
                left=left,
                operator=operator,
                right=right,
                section_context=_section_context(text, start),
                definition_like=_relation_context_is_definition(text, start),
            )
        )
    return tuple(result)


def _math_segments(text: str) -> tuple[str, ...]:
    return tuple(item[0] for item in _math_segments_with_spans(text))


def _math_segments_with_spans(text: str) -> tuple[tuple[str, int, int], ...]:
    segments: list[str] = []
    spans: list[tuple[int, int]] = []
    occupied: list[tuple[int, int]] = []
    for match in re.finditer(
        r"\$\$(.+?)\$\$|\$(.+?)\$|\\\((.+?)\\\)|\\\[(.+?)\\\]",
        text,
        re.DOTALL,
    ):
        segments.append(next(item for item in match.groups() if item is not None))
        spans.append(match.span())
        occupied.append(match.span())
    for match in re.finditer(
        r"(?<![A-Za-z0-9_])(?:\d+(?:\.\d+)?\s*[+\-*/^]\s*)+\d+(?:\.\d+)?\s*=\s*[-+]?\d+(?:\.\d+)?",
        text,
    ):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        segments.append(match.group())
        spans.append(match.span())
    return tuple(
        (formula, span[0], span[1])
        for formula, span in zip(segments, spans, strict=True)
    )


def _section_context(text: str, offset: int) -> tuple[str, ...]:
    headings = re.findall(r"(?m)^#{1,6}\s+(.+?)\s*$", text[:offset])
    normalized: list[str] = []
    for heading in headings[-4:]:
        value = re.sub(r"\s*\{[^}]*\}\s*$", "", heading).strip()
        value = re.sub(r"\s+", " ", value)
        if value and value not in {"完整题面", "逐题完整解析", "解析", "正文"}:
            normalized.append(value)
    return tuple(normalized)


def _relation_context_is_definition(text: str, offset: int) -> bool:
    start = max(
        text.rfind("\n", 0, offset),
        text.rfind("。", 0, offset),
        text.rfind("；", 0, offset),
    )
    context = text[start + 1 : offset]
    return bool(re.search(r"(?:定义|规定|记|令|称为|写作|写成|表示)", context[-160:]))


def _source_relation_contradictions(
    source: str,
    analysis: str,
    sentences: tuple[_Sentence, ...],
) -> tuple[DirectAuditIssue, ...]:
    source_relations = _math_relations(source)
    if not source_relations:
        return ()
    result: list[DirectAuditIssue] = []
    context_required = len(source) > 4096 or len(analysis) > 4096
    for current in _math_relations(analysis):
        conflicting = next(
            (
                expected
                for expected in source_relations
                if _relations_demonstrably_conflict(expected, current)
                and (
                    not context_required
                    or (
                        expected.definition_like
                        and bool(expected.section_context)
                        and bool(current.section_context)
                        and expected.section_context[-1]
                        == current.section_context[-1]
                    )
                )
            ),
            None,
        )
        if conflicting is None:
            continue
        offset = analysis.find(current.formula)
        anchor = _sentence_for_offset(sentences, max(0, offset))
        result.append(
            _issue(
                "SOURCE_RELATION_CONTRADICTED",
                anchor,
                "解析中的数学关系与冻结来源明确给出的关系冲突。",
                "初学者无法判断应当相信来源还是解析，后续推导也会建立在互相矛盾的前提上。",
                "按冻结来源重新核对该关系，改正冲突的一侧，并同步修正依赖它的后续步骤。",
                evidence=(
                    f"来源：{conflicting.formula.strip()}；"
                    f"解析：{current.formula.strip()}"
                ),
            )
        )
    return tuple(result)


def _relations_demonstrably_conflict(
    expected: _MathRelation, current: _MathRelation
) -> bool:
    bare_symbol = re.compile(r"[A-Za-z](?:_\{?[A-Za-z0-9]+\}?)?")
    if bare_symbol.fullmatch(expected.left) or bare_symbol.fullmatch(current.left):
        return False
    if expected.left == current.left:
        if expected.operator == current.operator == "=":
            return _expressions_demonstrably_different(expected.right, current.right)
    if expected.operator == current.operator == "=" and expected.right == current.right:
        return _expressions_demonstrably_different(expected.left, current.left)
    return False


def _expressions_demonstrably_different(left: str, right: str) -> bool:
    if left == right:
        return False
    left_value = _numeric_value(left)
    right_value = _numeric_value(right)
    if left_value is not None and right_value is not None:
        return left_value != right_value
    monomial = re.compile(r"(?P<sign>-?)(?P<name>[A-Za-z])(?:\^(?:\{)?(?P<power>\d+)(?:\})?)?")
    left_match = monomial.fullmatch(left)
    right_match = monomial.fullmatch(right)
    if left_match is None or right_match is None:
        return False
    return (
        left_match.group("name") == right_match.group("name")
        and (
            left_match.group("sign") != right_match.group("sign")
            or (left_match.group("power") or "1")
            != (right_match.group("power") or "1")
        )
    )


def _source_definition_coverage(
    source: str,
    analysis: str,
    sentences: tuple[_Sentence, ...],
) -> tuple[DirectAuditIssue, ...]:
    result: list[DirectAuditIssue] = []
    for definition in _sentences(source):
        text = definition.text
        has_universal = bool(re.search(r"任意|对每|\\forall", text))
        has_existential = bool(re.search(r"存在|\\exists", text))
        if not (
            has_universal
            and has_existential
            and re.search(r"定义|称为|若|如果", text)
            and len(_math_relations(text)) >= 2
        ):
            continue
        required_commands = {
            command
            for command in re.findall(r"\\([A-Za-z]+)", text)
            if command
            not in {
                "lim",
                "to",
                "left",
                "right",
                "lvert",
                "rvert",
                "in",
            }
        }
        analysis_commands = set(re.findall(r"\\([A-Za-z]+)", analysis))
        quantifiers_visible = bool(
            re.search(r"任意|对每|\\forall", analysis)
            and re.search(r"存在|\\exists", analysis)
            and re.search(r"使得|满足|当", analysis)
        )
        relations_visible = len(_math_relations(analysis)) >= 2
        if quantifiers_visible and relations_visible and required_commands <= analysis_commands:
            continue
        anchor = _best_analysis_anchor(sentences, text)
        result.append(
            _issue(
                "SOURCE_DEFINITION_CONTENT_OMITTED",
                anchor,
                "解析把来源中的带条件定义压缩成了不能执行的概括，量词、条件链或关键关系没有讲全。",
                "初学者只知道直观说法，却无法按定义判断一个具体对象是否满足条件。",
                "补全来源定义中的对象范围、任意条件、存在条件、触发条件和结论关系，并逐项说明它们的先后次序。",
                evidence="冻结来源定义：" + text[:512],
            )
        )
    return tuple(result)


def _best_analysis_anchor(
    sentences: tuple[_Sentence, ...], source_text: str
) -> _Sentence:
    source_terms = set(
        re.findall(r"[\u4e00-\u9fff]{2,8}|\\[A-Za-z]+|[A-Za-z]", source_text)
    )
    return max(
        sentences,
        key=lambda item: len(
            source_terms
            & set(
                re.findall(
                    r"[\u4e00-\u9fff]{2,8}|\\[A-Za-z]+|[A-Za-z]", item.text
                )
            )
        ),
    )


def _false_numeric_equalities(
    sentences: tuple[_Sentence, ...],
) -> tuple[DirectAuditIssue, ...]:
    result: list[DirectAuditIssue] = []
    for sentence in sentences:
        for formula in _math_segments(sentence.text):
            normalized = _normalize_math(formula).strip(".,;，。；")
            pieces = re.split(r"(?<![<>=!])=(?!=)", normalized)
            if len(pieces) < 2:
                continue
            values = tuple(_numeric_value(piece) for piece in pieces)
            if any(value is None for value in values):
                continue
            if len(set(values)) == 1:
                continue
            result.append(
                _issue(
                    "MATHEMATICAL_EQUALITY_FALSE",
                    sentence,
                    "解析中的数值等式没有通过独立精确复算。",
                    "初学者会沿用错误结果，且无法从后续文字判断错误发生在哪一步。",
                    "重新逐步计算该等式，改正首个不相等的位置，并同步修正所有后续结果。",
                    evidence=f"复算式：{formula.strip()}",
                )
            )
    return tuple(result)


def _numeric_value(expression: str) -> Fraction | None:
    value = expression
    for _ in range(8):
        replaced = re.sub(
            r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"((\1)/(\2))", value
        )
        if replaced == value:
            break
        value = replaced
    value = (
        value.replace(r"\cdot", "*")
        .replace(r"\times", "*")
        .replace(r"\div", "/")
        .replace("^", "**")
        .replace("{", "(")
        .replace("}", ")")
    )
    if not re.fullmatch(r"[0-9+\-*/().\s]+", value):
        return None
    try:
        tree = ast.parse(value, mode="eval")
        return _fraction_from_ast(tree.body)
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError):
        return None


def _fraction_from_ast(node: ast.AST) -> Fraction:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        if isinstance(node.value, bool):
            raise ValueError("boolean is not numeric")
        return Fraction(str(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _fraction_from_ast(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left = _fraction_from_ast(node.left)
        right = _fraction_from_ast(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.Pow):
            if right.denominator != 1 or abs(right.numerator) > 20:
                raise ValueError("unsupported exponent")
            return left ** right.numerator
    raise ValueError("unsupported numeric expression")


def _validate_artifacts(
    artifacts: Mapping[str, bytes] | None,
) -> Mapping[str, bytes]:
    if artifacts is None:
        return {}
    if not isinstance(artifacts, Mapping):
        raise TypeError("artifacts must be a mapping of package paths to bytes")
    result: dict[str, bytes] = {}
    for path, content in artifacts.items():
        if not isinstance(path, str) or not path:
            raise ValueError("artifact paths must be non-empty strings")
        logical = PurePosixPath(path)
        if (
            "\\" in path
            or ":" in path
            or logical.is_absolute()
            or ".." in logical.parts
            or str(logical) != path
        ):
            raise ValueError("artifact paths must stay inside the frozen package")
        if not isinstance(content, bytes):
            raise TypeError("artifact content must be bytes")
        result[path] = content
    return result


def _diagram_semantics(
    source: str,
    analysis: str,
    sentences: tuple[_Sentence, ...],
    artifacts: Mapping[str, bytes],
    observer: DiagramObserver | None,
    *,
    source_artifact: str,
    reviewed_artifact: str,
) -> tuple[tuple[DirectAuditIssue, ...], tuple[str, ...]]:
    source_references = _resolved_image_reference_spans(source, source_artifact)
    analysis_references = _resolved_image_reference_spans(analysis, reviewed_artifact)
    references: list[str] = []
    references.extend(item[0] for item in source_references)
    references.extend(item[0] for item in analysis_references)
    references = list(dict.fromkeys(references))
    if not references:
        return (), ()
    fallback_anchor = next(
        (item for item in sentences if _DIAGRAM_CLAIM.search(item.text)), sentences[0]
    )
    issues: list[DirectAuditIssue] = []
    evidence: list[str] = []
    for reference in references:
        local_entries = tuple(
            item for item in analysis_references if item[0] == reference
        )
        if local_entries:
            _, reference_start, reference_end, local_context = local_entries[0]
            anchor = min(
                sentences,
                key=lambda item: min(
                    abs(item.start - reference_start),
                    abs(item.end - reference_end),
                ),
            )
        else:
            anchor = fallback_anchor
            local_context = anchor.text
        content = artifacts.get(reference)
        if content is None:
            issues.append(
                _issue(
                    "DIAGRAM_ARTIFACT_MISSING",
                    anchor,
                    "解析或来源引用了没有绑定进当前冻结包的图像。",
                    "初学者无法看到被推理调用的图，审核程序也不能核对图中关系。",
                    "把当前版本实际引用的图像字节纳入同一冻结包，再重新审核。",
                    evidence=f"缺失图像：{reference}",
                )
            )
            continue
        image_sha256 = hashlib.sha256(content).hexdigest()
        evidence.append(f"diagram:{reference}:{image_sha256}")
        if observer is None:
            issues.append(
                _issue(
                    "DIAGRAM_OBSERVER_UNAVAILABLE",
                    anchor,
                    "当前解析调用了图像关系，但没有经过独立图像观察器复核。",
                    "只读文字无法确认箭头、位置、相交、单调等图中关系是否真的存在。",
                    "配置只读取当前冻结图像字节的观察器，核对图中文字和结构关系后重新审核。",
                    evidence=f"未复核图像：{reference}；sha256={image_sha256}",
                )
            )
            continue
        try:
            observation = observer(reference, content)
            if not isinstance(observation, DiagramObservation):
                raise TypeError("observer returned the wrong value type")
        except Exception as exc:
            issues.append(
                _issue(
                    "DIAGRAM_OBSERVATION_FAILED",
                    anchor,
                    "图像观察器没有产生完整、可绑定的复核结果。",
                    "图像推理仍然没有独立证据，初学者可能被错误图示带偏。",
                    "修复图像观察器后，用同一冻结图像重新审核。",
                    evidence=f"{reference}:OBSERVER_ERROR:{type(exc).__name__}",
                )
            )
            continue
        if observation.image_sha256 != image_sha256:
            issues.append(
                _issue(
                    "DIAGRAM_OBSERVATION_IMAGE_MISMATCH",
                    anchor,
                    "图像观察结果不属于当前冻结图像。",
                    "审核证据与学生实际看到的图不是同一份内容。",
                    "使用当前图像字节重新运行观察器并绑定其摘要。",
                    evidence=(
                        f"expected={image_sha256};actual={observation.image_sha256}"
                    ),
                )
            )
            continue
        if not observation.complete:
            issues.append(
                _issue(
                    "DIAGRAM_OBSERVATION_INCOMPLETE",
                    anchor,
                    "图像观察器没有完整读取当前冻结图像。",
                    "不完整的像素或文字观察不能证明学生实际看到的图与解析一致。",
                    "修复解码或观察能力后，用当前图像字节重新观察；不得把不完整结果当成通过。",
                    evidence=f"图像：{reference}；sha256={image_sha256}",
                )
            )
            continue
        evidence.append(
            "diagram-observation:"
            + reference
            + ":"
            + observation.observer_id
            + ":"
            + hashlib.sha256(
                _canonical_json(
                    {
                        "labels": observation.ocr_labels,
                        "relations": observation.relations,
                    }
                )
            ).hexdigest()
        )
        claim_terms = {
            item for item in _DIAGRAM_RELATION_TERMS if item in local_context
        }
        observed_text = "\n".join(
            (*observation.ocr_labels, *observation.relations)
        )
        missing_terms = sorted(item for item in claim_terms if item not in observed_text)
        if (_DIAGRAM_CLAIM.search(local_context) and not observation.relations) or missing_terms:
            issues.append(
                _issue(
                    "DIAGRAM_RELATION_UNVERIFIED",
                    anchor,
                    "解析声称的图像关系没有在独立观察结果中得到支持。",
                    "初学者可能把文字断言误当成从图中可以直接读出的事实。",
                    "让解析只使用观察结果能够支持的关系，或更换能清楚显示该关系的图像。",
                    evidence=(
                        f"图像：{reference}；未支持关系："
                        + ("、".join(missing_terms) if missing_terms else "未返回结构关系")
                    ),
                )
            )
    return tuple(issues), tuple(evidence)


def _resolved_image_references(text: str, document_path: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            item[0] for item in _resolved_image_reference_spans(text, document_path)
        )
    )


def _resolved_image_reference_spans(
    text: str, document_path: str
) -> tuple[tuple[str, int, int, str], ...]:
    from .courseforge_publication import image_references
    raw_references = [(row['target'] or '', row['start'], row['end'])
                      for row in image_references(text)]
    result: list[tuple[str, int, int, str]] = []
    base = PurePosixPath(document_path).parent
    for index, (raw, start, end) in enumerate(raw_references):
        cleaned = raw.split("#", 1)[0].split("?", 1)[0]
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", cleaned) or cleaned.startswith("//"):
            resolved = f"UNBOUND_REMOTE:{raw}"
            result.append((resolved, start, end, _local_diagram_context(text, raw_references, index)))
            continue
        candidate = base / PurePosixPath(cleaned)
        stack: list[str] = []
        escaped = False
        for part in candidate.parts:
            if part in ("", "."):
                continue
            if part == "..":
                if not stack:
                    escaped = True
                    break
                stack.pop()
                continue
            stack.append(part)
        resolved = (
            f"UNBOUND_PATH:{raw}"
            if escaped or not stack
            else PurePosixPath(*stack).as_posix()
        )
        result.append((resolved, start, end, _local_diagram_context(text, raw_references, index)))
    return tuple(result)


def _local_diagram_context(
    text: str,
    references: list[tuple[str, int, int]],
    index: int,
) -> str:
    _, start, end = references[index]
    previous_end = references[index - 1][2] if index else 0
    next_start = references[index + 1][1] if index + 1 < len(references) else len(text)
    paragraph_start = text.rfind("\n\n", previous_end, start)
    left = max(previous_end, paragraph_start + 2 if paragraph_start >= 0 else start - 400)
    first_break = text.find("\n\n", end, next_start)
    second_break = (
        text.find("\n\n", first_break + 2, next_start) if first_break >= 0 else -1
    )
    right = min(next_start, second_break if second_break >= 0 else end + 900)
    return text[max(0, left) : max(end, right)]


def _separator_identification(
    sentences: tuple[_Sentence, ...],
) -> tuple[DirectAuditIssue, ...]:
    result: list[DirectAuditIssue] = []
    for index, sentence in enumerate(sentences):
        identification = re.search(
            r"(?:它|这个\s*c|该分隔数)就是\s*(?:\\\()?\\sqrt", sentence.text
        )
        if identification is None:
            continue
        local_prefix = sentence.text[max(0, identification.start() - 24) : identification.start()]
        if re.search(r"(?:不能|尚不能|并非|不可|不应)[^。！？\n]{0,16}$", local_prefix):
            continue
        proof_window = "\n".join(
            item.text for item in sentences[index : index + 7]
        )
        if re.search(r"\^2\s*=\s*2", proof_window) and re.search(
            r"(?:唯一|严格递增)", proof_window
        ):
            continue
        result.append(
            _issue(
                "EXISTENCE_IDENTIFICATION_BRIDGE_MISSING",
                sentence,
                "解析把完备性给出的分隔数直接认定为某个根，没有证明该分隔数满足方程及其唯一性。",
                "初学者看不到“存在分隔数”怎样变成“这个数就是指定的平方根”。",
                "证明分隔数的平方等于目标值，并用正性与单调性说明正根唯一。",
            )
        )
    return tuple(result)


def _bijection_bridges(
    analysis: str, sentences: tuple[_Sentence, ...]
) -> tuple[DirectAuditIssue, ...]:
    result: list[DirectAuditIssue] = []
    for sentence in sentences:
        if not (
            "对应唯一" in sentence.text
            and "反过来" in sentence.text
            and "实数" in sentence.text
        ):
            continue
        following = analysis[sentence.end : sentence.end + 700]
        if re.search(r"(?:分割|完备性|存在性|唯一性|构造)", following):
            continue
        result.append(
            _issue(
                "BIJECTION_BRIDGE_MISSING",
                sentence,
                "解析声明了数轴点与实数的一一对应，却没有说明任意点对应实数的存在性和唯一性。",
                "初学者只能接受结论，无法把有理点构造、无理点分割和实数完备性连起来。",
                "分别说明实数到点、点到实数的构造，并解释次序为何保证唯一。",
            )
        )
    return tuple(result)


def _normalize_math(value: str) -> str:
    return (
        re.sub(r"\s+", "", value)
        .replace(r"\leqslant", r"\le")
        .replace(r"\leq", r"\le")
        .replace(r"\geqslant", r"\ge")
        .replace(r"\geq", r"\ge")
    )


def _absolute_relation_signature(formula: str) -> str | None:
    value = _normalize_math(formula)
    if re.search(r"\|[A-Za-z]\|\\ge0", value):
        return "absolute-nonnegative"
    if re.search(r"\|([A-Za-z])-([A-Za-z])\|=\|\2-\1\|", value):
        return "absolute-symmetry"
    if re.search(
        r"\|([A-Za-z])\+([A-Za-z])\|\\le\|\1\|\+\|\2\|", value
    ):
        return "triangle-inequality"
    return None


def _analysis_has_absolute_relation(analysis: str, signature: str) -> bool:
    value = _normalize_math(analysis)
    if signature == "absolute-nonnegative":
        return bool(re.search(r"\|[A-Za-z]\|\\ge0", value))
    if signature == "absolute-symmetry":
        return bool(
            re.search(r"\|([A-Za-z])-([A-Za-z])\|=\|\2-\1\|", value)
        )
    if signature == "triangle-inequality":
        return bool(
            re.search(
                r"\|([A-Za-z])\+([A-Za-z])\|\\le\|\1\|\+\|\2\|",
                value,
            )
        )
    raise ValueError("unknown absolute-value relation signature")


def _missing_theorem_statements(
    sentences: tuple[_Sentence, ...],
) -> tuple[DirectAuditIssue, ...]:
    visible: set[str] = set()
    result: list[DirectAuditIssue] = []
    for sentence in sentences:
        statement = _RULE_STATEMENT.search(sentence.text)
        if statement is not None:
            visible.add(statement.group("name"))
        application = _RULE_APPLICATION.search(sentence.text)
        if application is None or application.group("rule") in visible:
            continue
        name = application.group("rule")
        result.append(
            _issue(
                "THEOREM_STATEMENT_NOT_VISIBLE",
                sentence,
                f"解析使用了{name}，但在此前没有陈述定理内容。",
                "初学者不知道该定理允许推出什么、需要满足什么条件，因而无法核对本步是否合法。",
                f"在首次使用前陈述{name}的结论与适用条件，并逐项说明当前对象满足这些条件。",
            )
        )
    return tuple(result)


def _missing_conventions(
    sentences: tuple[_Sentence, ...],
) -> tuple[DirectAuditIssue, ...]:
    convention_visible = False
    result: list[DirectAuditIssue] = []
    for sentence in sentences:
        if _CONVENTION_STATEMENT.search(sentence.text):
            convention_visible = True
        if not _CONVENTION_USE.search(sentence.text) or convention_visible:
            continue
        result.append(
            _issue(
                "CONVENTION_NOT_VISIBLE",
                sentence,
                "本步依赖方向或正负号约定，但解析此前没有说明该约定。",
                "同一对象在不同约定下会得到不同符号；初学者无法判断本步取号从何而来。",
                "在首次使用前明确参考方向或正负号约定，并让后续符号与该约定逐项对应。",
            )
        )
    return tuple(result)


def _compressed_derivations(
    sentences: tuple[_Sentence, ...],
) -> tuple[DirectAuditIssue, ...]:
    result: list[DirectAuditIssue] = []
    for index, sentence in enumerate(sentences):
        if len(sentence.text) > 240:
            continue
        chains = tuple(
            clause
            for segment in _math_segments(sentence.text)
            for clause in _equality_clauses(segment)
            if len(re.findall(r"(?<![<>=!])=(?!=)", clause)) >= 3
            and re.search(r"(?:\\frac|\\cdot|\^|[+\-*/])", clause)
            and not re.search(
                r"\\begin\{(?:aligned|align\*?|cases|array|matrix|pmatrix|bmatrix|split|gathered)\}|\\node\b",
                clause,
            )
        )
        if not chains:
            continue
        surrounding = tuple(
            item
            for item in sentences[max(0, index - 5) : index + 3]
            if item is not sentence
            and item.start - sentence.end <= 1200
            and sentence.start - item.end <= 1200
        )
        nearby_context = "\n".join(item.text for item in surrounding)
        if (
            _TRANSFORMATION_REASON.search(sentence.text)
            or _INTERNAL_REASON.search(sentence.text)
            or _DEFINITION_CHAIN_CUE.search(sentence.text)
            or re.search(r"(?:是否|能否|可否|为什么|何时|怎样)", sentence.text)
            or _TRANSFORMATION_REASON.search(nearby_context)
            or _DERIVATION_CONTEXT_CUE.search(nearby_context)
            or _LOCAL_REASON_CUE.search(nearby_context)
            or _DEFINITION_CHAIN_CUE.search(nearby_context)
        ):
            continue
        result.append(
            _issue(
                "DERIVATION_STEP_COMPRESSED",
                sentence,
                "一行中压入了多次非机械等式变形，却没有标明中间使用的运算规则。",
                "初学者看到结果链却无法确定每个等号为何成立，也难以定位自己的第一处错误。",
                "保留原结论，只拆出必要的中间等式，并在每次关键变形旁写明代入、展开、通分、约分或所用定理。",
            )
        )
    return tuple(result)


def _equality_clauses(segment: str) -> tuple[str, ...]:
    without_index_equalities = re.sub(r"_\{[^{}]*=[^{}]*\}", "_{}", segment)
    without_index_equalities = re.sub(
        r"_(?:[A-Za-z][A-Za-z0-9]*)\s*=\s*[-+A-Za-z0-9]+",
        "_{}",
        without_index_equalities,
    )
    return tuple(
        item.strip()
        for item in re.split(r"(?:\\qquad|\\quad|\\\\|[，,；;])", without_index_equalities)
        if item.strip()
    )


def _blocked_inferences(
    graph: DiscoveredSemanticGraph,
    shadow: StructuralShadowWorld,
) -> tuple[DirectAuditIssue, ...]:
    blocked_nodes = {
        node_id
        for node_id, entity in zip(
            (item.node_id for item in graph.nodes),
            (item.entity_id for item in shadow.entities),
            strict=True,
        )
        if entity in shadow.blocked_targets
    }
    result: list[DirectAuditIssue] = []
    for node in graph.nodes:
        if node.node_id not in blocked_nodes:
            continue
        result.append(
            DirectAuditIssue(
                code="INFERENCE_RULE_NOT_VISIBLE",
                start=node.start,
                end=node.end,
                exact_quote=node.text,
                problem="结论出现了，但解析没有给出把前提变成该结论的规则。",
                beginner_block="影子初学者只使用解析中明说的规则时无法走到这一步。",
                required_condition="补出所用规则、规则的适用条件，以及当前对象满足这些条件的对应关系。",
                evidence="结构影子推演在该结论前停止。",
            )
        )
    return tuple(result)


def _issue(
    code: str,
    sentence: _Sentence,
    problem: str,
    beginner_block: str,
    required_condition: str,
    *,
    evidence: str = "",
) -> DirectAuditIssue:
    return DirectAuditIssue(
        code=code,
        start=sentence.start,
        end=sentence.end,
        exact_quote=sentence.text,
        problem=problem,
        beginner_block=beginner_block,
        required_condition=required_condition,
        evidence=evidence,
    )


def _sentence_for_offset(
    sentences: tuple[_Sentence, ...], offset: int
) -> _Sentence:
    for item in sentences:
        if item.start <= offset < item.end:
            return item
    raise ValueError("symbol is outside a reviewable sentence")


def _deduplicate(issues: Iterable[DirectAuditIssue]) -> tuple[DirectAuditIssue, ...]:
    result: list[DirectAuditIssue] = []
    seen: set[tuple[str, int, int, str]] = set()
    for issue in sorted(issues, key=lambda item: (item.start, item.end, item.code)):
        key = (issue.code, issue.start, issue.end, issue.evidence)
        if key not in seen:
            seen.add(key)
            result.append(issue)
    return tuple(result)


def _topology_digest(
    nodes: tuple[SemanticNode, ...],
    edges: tuple[SemanticEdge, ...],
    targets: tuple[str, ...],
) -> str:
    payload = {
        "nodes": [
            (
                item.node_id,
                item.role,
                len(item.object_signatures),
                item.operation_signatures,
            )
            for item in nodes
        ],
        "edges": [
            (
                item.edge_id,
                item.premise_ids,
                item.conclusion_id,
                item.operation,
                item.visible,
            )
            for item in edges
        ],
        "targets": targets,
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _shadow_topology_digest(
    entities: tuple[ShadowEntity, ...],
    rules: tuple[ShadowRule, ...],
    target_ids: tuple[str, ...],
) -> str:
    node_ids = {item.entity_id: f"n{index:08d}" for index, item in enumerate(entities, start=1)}
    edge_payload = []
    for index, item in enumerate(rules, start=1):
        operation = item.operation_signature.split("/", 1)[0]
        edge_payload.append(
            (
                f"e{index:08d}",
                tuple(node_ids[value] for value in item.premise_entity_ids),
                node_ids[item.conclusion_entity_id],
                operation,
                item.granted,
            )
        )
    payload = {
        "nodes": [
            (
                f"n{index:08d}",
                item.role,
                item.object_arity,
                item.operation_signatures,
            )
            for index, item in enumerate(entities, start=1)
        ],
        "edges": edge_payload,
        "targets": target_ids,
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _program_sha256() -> str:
    digest = hashlib.sha256(b"HBEP-DIRECT-DOCUMENT-AUDIT-PROGRAM/1\0")
    digest.update(_PROGRAM_VERSION.encode("ascii"))
    digest.update(tree_identity(Path(__file__).parent).sha256.encode("ascii"))
    return digest.hexdigest()


def _object_signatures(text: str) -> tuple[str, ...]:
    math_segments: list[str] = []
    for match in re.finditer(
        r"\$+(.+?)\$+|\\\((.+?)\\\)|\\\[(.+?)\\\]", text, re.DOTALL
    ):
        math_segments.append(next(item for item in match.groups() if item is not None))
    tokens: list[str] = []
    for segment in math_segments:
        tokens.extend(
            re.findall(
                r"\\mathbb\s*\{?[A-Za-z]\}?|\\[A-Za-z]+|(?<![A-Za-z])[A-Za-z](?:_[A-Za-z0-9{}]+)?",
                segment,
            )
        )
    theorem_names = re.findall(
        r"[\u4e00-\u9fff]{2,16}(?:定理|原理|法则)", text
    )
    concepts = re.findall(
        r"自然数|整数|有理数|无理数|实数|数集|集合|十进制|小数|数列|序列|"
        r"区间|邻域|极限|上确界|下确界|上界|下界|函数|连续|可导|导数|微分|"
        r"积分|原函数|级数|收敛|发散|有界|单调|凸性|凹性|凸|凹|曲率|"
        r"微分方程|通解|特解|长度|坐标|分数",
        text,
    )
    return tuple(dict.fromkeys((*tokens, *theorem_names, *concepts)))


def _operation_signatures(text: str) -> tuple[str, ...]:
    signatures: list[str] = []
    patterns = (
        (r"\\in|∈", "membership/2"),
        (r"\\notin|∉", "nonmembership/2"),
        (r"<=|>=|\\leq?|\\geq?|≤|≥|(?<![<>=])<(?![=])|(?<![<>=])>(?![=])", "order/2"),
        (r"(?<![<>=!])=(?!=)", "equality/2"),
        (r"\\sum|∑", "aggregation/1"),
        (r"\\lim|\\to|\\longrightarrow|趋于|极限", "limit/1"),
        (r"\\sup|上确界", "least-upper-bound/1"),
        (r"\\inf|下确界", "greatest-lower-bound/1"),
        (r"\\cup|∪", "union/2"),
        (r"\\cap|∩", "intersection/2"),
        (r"\\subset|⊂|⊆", "inclusion/2"),
        (r"\\Longleftrightarrow|\\iff|⇔", "equivalence/2"),
        (r"\\Longrightarrow|\\Rightarrow|⇒", "implication/2"),
        (r"任意|对所有|\\forall", "universal-quantifier/1"),
        (r"存在|\\exists", "existential-quantifier/1"),
    )
    for pattern, signature in patterns:
        if re.search(pattern, text):
            signatures.append(signature)
    return tuple(signatures)


def _visible_transition(previous: SemanticNode, current: SemanticNode) -> bool:
    shared_objects = set(previous.object_signatures) & set(current.object_signatures)
    if not shared_objects:
        return False
    if previous.operation_signatures and current.operation_signatures:
        if set(previous.operation_signatures) & set(current.operation_signatures):
            return True
    if re.search(r"(?:不是.+而是|换言之|也就是说|写成|形成包含链)", current.text):
        return True
    if re.search(r"(?:仍然|仍是|定义|表示|解决的正是|相加得到|穷尽)", previous.text):
        return True
    if re.search(
        r"(?:因为|由于|证明|已知|假设|由|根据|利用|结合|可见|说明|意味着)",
        previous.text,
    ):
        return True
    return bool(
        re.search(r"(?:可得|推出|等价于|化为|整理得|即)", previous.text)
        or previous.text.count("=") >= 2
    )


def _local_reason_nodes(
    previous_nodes: tuple[SemanticNode, ...], current: SemanticNode
) -> tuple[str, ...]:
    nearby = tuple(
        item
        for item in previous_nodes
        if current.start - item.end <= 1400
    )
    current_objects = set(current.object_signatures)
    reason_index = None
    for index in range(len(nearby) - 1, -1, -1):
        reason = nearby[index]
        if not _LOCAL_REASON_CUE.search(reason.text):
            continue
        tail = nearby[index:]
        shared = any(
            current_objects & set(item.object_signatures)
            for item in tail
        )
        if shared or current.start - reason.end <= 450:
            reason_index = index
            break
    if reason_index is None:
        return ()
    return tuple(item.node_id for item in nearby[reason_index:])


def _equational_reason_nodes(
    previous_nodes: tuple[SemanticNode, ...], current: SemanticNode
) -> tuple[str, ...]:
    if re.search(r"上确界|下确界", current.text):
        return ()
    current_objects = set(current.object_signatures)
    for item in reversed(previous_nodes):
        distance = current.start - item.end
        if distance > 700:
            break
        if item.role in {"condition", "theorem_statement"}:
            continue
        if not _MATH_RELATION_TOKEN.search(item.text):
            continue
        shared_objects = current_objects & set(item.object_signatures)
        if shared_objects or distance <= 180:
            return (item.node_id,)
    return ()


__all__ = [
    "DirectAuditIssue",
    "DirectAuditResult",
    "DirectDocumentAuditProgram",
    "DiscoveredSemanticGraph",
    "StructuralShadowWorld",
    "audit_document_files",
]
