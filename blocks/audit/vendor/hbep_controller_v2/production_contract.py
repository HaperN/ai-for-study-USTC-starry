from __future__ import annotations

import codecs
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType
import unicodedata

from .path_safety import assert_no_link_components, is_link_like
from .local_acquisition_receipt import (
    LocalAcquisitionReceiptError,
    SUPPORTED_PLATFORMS as LOCAL_ACQUISITION_PLATFORMS,
    validate_portable_local_source_receipt,
)
from .local_evidence_build_receipt import (
    LocalEvidenceBuildReceiptError,
    validate_local_evidence_build_receipt,
)
from .media_evidence_assets import (
    MediaEvidenceAssetError,
    validate_article_evidence_assets,
    validate_video_evidence_assets,
)


_CONTENT_SPEC_SCHEMA_V3 = "hbep-production-content-spec-v3"
_CONTENT_SPEC_SCHEMA_V4 = "hbep-production-content-spec-v4"
_CONTENT_SPEC_SCHEMA_V5 = "hbep-production-content-spec-v5"
_ANALYSIS_CONTRACT_SCHEMA = "hbep-production-analysis-contract-v2"
_PLACEHOLDER = re.compile(r"\{(?P<name>[A-Za-z][A-Za-z0-9_]*)\}")
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,127}\Z")
_UNIT_IDENTIFIER = re.compile(r"u[0-9]{8}\Z")
_TITLE_LABEL = re.compile(r"\[unit:u[0-9]{8}\]\Z")
_EXTERNAL_EVIDENCE_ROLES = frozenset(
    {
        "external_video_bundle",
        "external_video_asset",
        "external_article_bundle",
        "external_article_asset",
    }
)
_ARTIFACT_ROLES = frozenset(
    {"reviewed_source", "reviewed_image", "control"}
) | _EXTERNAL_EVIDENCE_ROLES
_EXTERNAL_BUNDLE_ROLE_TO_KIND = {
    "external_video_bundle": "video",
    "external_article_bundle": "article",
}
_EXTERNAL_ASSET_ROLE_BY_KIND = {
    "video": "external_video_asset",
    "article": "external_article_asset",
}
_INTEGRATED_EXTERNAL_DISPOSITIONS = frozenset(
    {"integrate_core", "integrate_minimal_bridge"}
)
_EXCLUDED_EXTERNAL_DISPOSITIONS = frozenset(
    {"exclude_specialist", "exclude_duplicate"}
)
_VIDEO_EVIDENCE_FILE_ROLES = frozenset(
    {
        "scene_timeline",
        "frame_manifest",
        "frame_ocr",
        "multimodal_analysis",
        "watch_receipt",
    }
)
_ARTICLE_EVIDENCE_FILE_ROLES = frozenset(
    {
        "readable_text",
        "page_snapshot",
        "embedded_image_manifest",
        "article_image_ledger",
        "read_receipt",
    }
)


def _expected_local_evidence_builder() -> Path:
    try:
        spec = importlib.util.find_spec("stem_runner.local_evidence_builder")
    except (ImportError, ModuleNotFoundError, ValueError):
        spec = None
    if spec is not None and spec.origin:
        return Path(spec.origin).resolve()
    return (
        Path(__file__).resolve().parents[3]
        / "教材解析与拟合卷"
        / "scripts"
        / "stem_runner"
        / "local_evidence_builder.py"
    ).resolve()
_RELATION_KINDS = frozenset(
    {
        "causal_bridge",
        "symbol_anchor",
        "transformation_invariant",
        "theorem_trigger",
        "example_correspondence",
        "diagram_relation",
        "unresolved_explanation",
        "inference_rule",
        "convention",
        "theorem_statement",
        "validation",
        "learning_objective",
    }
)
_DIAGRAM_EXTRACTION_KINDS = frozenset({"ocr_only", "structured_relations"})
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MAX_SPEC_ARTIFACTS = 1024
MAX_REQUIRED_UNITS = 4096
MAX_TRUSTED_RULES = 4096
MAX_REASONING_STEPS = 16384
MAX_SEQUENCE_ITEMS = 4096
MAX_TEXT_FIELD_CHARS = 16384
MAX_CONTROL_FILE_BYTES = 16 * 1024 * 1024
MAX_REVIEWED_IMAGE_BYTES = 64 * 1024 * 1024
MAX_CANONICAL_TEXT_CHARS = 2 * 1024 * 1024
MAX_REPAIRABLE_FINDINGS_PER_APPROACH = 4 * 4096
MAX_PROJECT_ENTRIES = 8192
_READ_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class RequiredArtifact:
    path: str
    role: str


@dataclass(frozen=True, slots=True)
class RequiredUnit:
    unit_id: str
    artifact_path: str
    title: str
    kind: str
    depends_on: tuple[str, ...]
    required_conclusions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NumericCheck:
    expression: str
    result_binding: str


@dataclass(frozen=True, slots=True)
class TrustedRule:
    rule_id: str
    premises: tuple[str, ...]
    conclusion: str
    relation_kind: str = "causal_bridge"
    numeric_checks: tuple[NumericCheck, ...] = ()


@dataclass(frozen=True, slots=True)
class LearningObligation:
    obligation_id: str
    unit_id: str
    kind: str
    target_fact: str


@dataclass(frozen=True, slots=True)
class DiagramEvidence:
    evidence_id: str
    image_path: str
    image_sha256: str
    extraction_kind: str
    ocr_labels: tuple[str, ...]
    relations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContentSpec:
    artifact_inventory: tuple[RequiredArtifact, ...]
    initial_facts: tuple[str, ...]
    initial_fact_kinds: Mapping[str, str]
    required_units: tuple[RequiredUnit, ...]
    trusted_rules: tuple[TrustedRule, ...]
    learning_obligations: tuple[LearningObligation, ...]
    diagram_evidence: tuple[DiagramEvidence, ...]
    _units_by_id: Mapping[str, RequiredUnit] = field(
        init=False, repr=False, compare=False
    )
    _rules_by_id: Mapping[str, TrustedRule] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "initial_fact_kinds",
            MappingProxyType(dict(self.initial_fact_kinds)),
        )
        object.__setattr__(
            self,
            "_units_by_id",
            MappingProxyType({item.unit_id: item for item in self.required_units}),
        )
        object.__setattr__(
            self,
            "_rules_by_id",
            MappingProxyType({item.rule_id: item for item in self.trusted_rules}),
        )

    def required_unit(self, unit_id: str) -> RequiredUnit | None:
        return self._units_by_id.get(unit_id)

    def rule(self, rule_id: str) -> TrustedRule | None:
        return self._rules_by_id.get(rule_id)

    def initial_fact_kind(self, fact: str) -> str | None:
        return self.initial_fact_kinds.get(fact)

    @property
    def reviewed_artifacts(self) -> tuple[str, ...]:
        return tuple(
            item.path for item in self.artifact_inventory if item.role == "reviewed_source"
        )

    @property
    def reviewed_image_artifacts(self) -> tuple[str, ...]:
        return tuple(
            item.path
            for item in self.artifact_inventory
            if item.role == "reviewed_image"
        )

    @property
    def supporting_artifacts(self) -> tuple[str, ...]:
        return tuple(
            item.path
            for item in self.artifact_inventory
            if item.role not in {"reviewed_source", "control"}
        )

    @property
    def external_evidence_artifacts(self) -> tuple[str, ...]:
        return tuple(
            item.path
            for item in self.artifact_inventory
            if item.role in _EXTERNAL_EVIDENCE_ROLES
        )


@dataclass(frozen=True, slots=True)
class ContractUnit:
    unit_id: str
    kind: str
    artifact_path: str
    start: int
    end: int
    exact_quote: str
    depends_on: tuple[str, ...]
    reasoning_step_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PremiseBinding:
    fact: str
    source_kind: str
    source_id: str


@dataclass(frozen=True, slots=True)
class ReasoningStep:
    step_id: str
    unit_id: str
    artifact_path: str
    start: int
    end: int
    exact_quote: str
    premises: tuple[PremiseBinding, ...]
    conclusion: str
    verification_kind: str
    rule_id: str | None


@dataclass(frozen=True, slots=True)
class ProductionContract:
    spec: ContentSpec
    units: tuple[ContractUnit, ...]
    steps: tuple[ReasoningStep, ...]
    contract_relative_path: str
    contract_text: str
    content_spec_sha256: str
    sources: Mapping[str, str]
    _units_by_id: Mapping[str, ContractUnit] = field(
        init=False, repr=False, compare=False
    )
    _steps_by_id: Mapping[str, ReasoningStep] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "sources",
            MappingProxyType(dict(self.sources)),
        )
        object.__setattr__(
            self,
            "_units_by_id",
            MappingProxyType({item.unit_id: item for item in self.units}),
        )
        object.__setattr__(
            self,
            "_steps_by_id",
            MappingProxyType({item.step_id: item for item in self.steps}),
        )

    def unit(self, unit_id: str) -> ContractUnit | None:
        return self._units_by_id.get(unit_id)

    def step(self, step_id: str) -> ReasoningStep | None:
        return self._steps_by_id.get(step_id)

    def contract_anchor(self, value: str = "") -> tuple[int, int, str]:
        if value:
            start = self.contract_text.find(value)
            if start >= 0:
                return start, start + len(value), value
        start = next(
            (index for index, character in enumerate(self.contract_text) if not character.isspace()),
            0,
        )
        end = min(len(self.contract_text), start + 1)
        return start, end, self.contract_text[start:end]


def load_production_contract(
    *,
    project: Path,
    canonical_sources: Sequence[str],
    content_spec: Path | bytes,
    analysis_contract: Path,
    artifact_manifest: Path,
) -> ProductionContract:
    raw_root = assert_no_link_components(project, label="analysis project")
    raw_spec: Path | None
    if isinstance(content_spec, bytes):
        raw_spec = None
        content_spec_bytes = content_spec
    else:
        raw_spec = assert_no_link_components(content_spec, label="content spec")
        content_spec_bytes = b""
    raw_contract = assert_no_link_components(
        analysis_contract, label="analysis contract"
    )
    raw_manifest = assert_no_link_components(
        artifact_manifest, label="artifact manifest"
    )
    root = raw_root.resolve()
    spec_path = raw_spec.resolve() if raw_spec is not None else None
    contract_path = raw_contract.resolve()
    manifest_path = raw_manifest.resolve()
    if is_link_like(raw_root) or not root.is_dir():
        raise ValueError("ANALYSIS_PROJECT_UNAVAILABLE")
    if raw_spec is not None:
        if is_link_like(raw_spec) or spec_path is None or not spec_path.is_file():
            raise ValueError("CONTENT_SPEC_UNAVAILABLE")
        content_spec_bytes = _read_bounded_bytes(spec_path, "CONTENT_SPEC_INVALID")
    if (
        is_link_like(raw_contract)
        or not contract_path.is_file()
        or not contract_path.is_relative_to(root)
    ):
        raise ValueError("ANALYSIS_CONTRACT_UNAVAILABLE")
    if (
        is_link_like(raw_manifest)
        or not manifest_path.is_file()
        or not manifest_path.is_relative_to(root)
    ):
        raise ValueError("ARTIFACT_MANIFEST_UNAVAILABLE")

    spec_value = _object_from_bytes(content_spec_bytes, "CONTENT_SPEC_INVALID")
    spec = _parse_spec(spec_value)
    relative_contract = contract_path.relative_to(root).as_posix()
    relative_manifest = manifest_path.relative_to(root).as_posix()
    _validate_artifact_inventory(root, spec, relative_manifest, relative_contract)
    _validate_diagram_files(root, spec)
    _validate_external_evidence_files(root, spec)
    source_paths = tuple(canonical_sources)
    if source_paths != spec.reviewed_artifacts:
        raise ValueError("CANONICAL_ARTIFACT_SET_INVALID")
    sources: dict[str, str] = {}
    source_characters = 0
    for relative in source_paths:
        safe = _safe_relative(relative)
        path = root.joinpath(*safe.parts)
        assert_no_link_components(path, label="canonical artifact")
        if is_link_like(path) or not path.is_file():
            raise ValueError("CANONICAL_ARTIFACT_UNAVAILABLE")
        text = _read_utf8(
            path,
            "CANONICAL_ARTIFACT_INVALID",
            max_chars=MAX_CANONICAL_TEXT_CHARS - source_characters,
            char_limit_error="ANALYSIS_CONTRACT_AUDIT_BUDGET_EXCEEDED",
        )
        source_characters += len(text)
        if source_characters > MAX_CANONICAL_TEXT_CHARS:
            raise ValueError("ANALYSIS_CONTRACT_AUDIT_BUDGET_EXCEEDED")
        sources[relative] = text

    contract_text = _read_utf8(contract_path, "ANALYSIS_CONTRACT_INVALID")
    try:
        contract_value = json.loads(contract_text, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("ANALYSIS_CONTRACT_INVALID") from exc
    if not isinstance(contract_value, Mapping):
        raise ValueError("ANALYSIS_CONTRACT_INVALID")

    units = _parse_units(contract_value, sources)
    steps = _parse_steps(contract_value, sources)
    _validate_contract_references(units, steps, spec)
    _validate_contract_layout(units, steps, sources, spec)
    _validate_audit_budget(units, steps, sources, spec)
    return ProductionContract(
        spec=spec,
        units=units,
        steps=steps,
        contract_relative_path=relative_contract,
        contract_text=contract_text,
        content_spec_sha256=hashlib.sha256(content_spec_bytes).hexdigest(),
        sources=sources,
    )


def parse_content_spec_bytes(content: bytes) -> ContentSpec:
    if not isinstance(content, bytes) or not content:
        raise ValueError("CONTENT_SPEC_INVALID")
    return _parse_spec(_object_from_bytes(content, "CONTENT_SPEC_INVALID"))


def bind_rule_conclusion(rule: TrustedRule, target: str) -> dict[str, str] | None:
    if not isinstance(rule, TrustedRule) or not isinstance(target, str) or not target:
        raise ValueError("RULE_BINDING_INVALID")
    bindings: dict[str, str] = {}
    return bindings if _unify(rule.conclusion, target, bindings) else None


def bind_rule_pattern(
    pattern: str, actual: str, bindings: Mapping[str, str]
) -> dict[str, str] | None:
    if (
        not isinstance(pattern, str)
        or not pattern
        or not isinstance(actual, str)
        or not actual
        or not isinstance(bindings, Mapping)
        or any(not isinstance(key, str) or not isinstance(value, str) for key, value in bindings.items())
    ):
        raise ValueError("RULE_BINDING_INVALID")
    result = dict(bindings)
    return result if _unify(pattern, actual, result) else None


def instantiate_rule_pattern(
    pattern: str, bindings: Mapping[str, str]
) -> str | None:
    if not isinstance(pattern, str) or not isinstance(bindings, Mapping):
        raise ValueError("RULE_BINDING_INVALID")
    cursor = 0
    pieces: list[str] = []
    for match in _PLACEHOLDER.finditer(pattern):
        pieces.append(pattern[cursor : match.start()])
        value = bindings.get(match.group("name"))
        if not isinstance(value, str) or not value:
            return None
        pieces.append(value)
        cursor = match.end()
    pieces.append(pattern[cursor:])
    return "".join(pieces)


def derive_rule_conclusion(
    rule: TrustedRule, premise_facts: Sequence[str]
) -> str | None:
    if not isinstance(rule, TrustedRule) or len(premise_facts) != len(rule.premises):
        return None
    bindings: dict[str, str] = {}
    for pattern, fact in zip(rule.premises, premise_facts, strict=True):
        if not isinstance(fact, str) or not _unify(pattern, fact, bindings):
            return None
    return instantiate_rule_pattern(rule.conclusion, bindings)


def _validate_audit_budget(
    units: tuple[ContractUnit, ...],
    steps: tuple[ReasoningStep, ...],
    sources: Mapping[str, str],
    spec: ContentSpec,
) -> None:
    source_characters = sum(len(text) for text in sources.values())
    physical_lines = sum(len(text.splitlines()) for text in sources.values())
    formula_markers = sum(
        text.count("$") + text.count(r"\(") + text.count(r"\[")
        for text in sources.values()
    )
    beginner_upper_bound = (
        2 * len(units)
        + len(spec.required_units)
        + 5 * physical_lines
    )
    shadow_upper_bound = 3 * len(units) + len(steps) + physical_lines
    mathematical_upper_bound = (
        2 * len(steps)
        + 2 * len(units)
        + 2 * formula_markers
        + len(spec.initial_facts)
    )
    if (
        source_characters > MAX_CANONICAL_TEXT_CHARS
        or max(
            beginner_upper_bound,
            shadow_upper_bound,
            mathematical_upper_bound,
        )
        > MAX_REPAIRABLE_FINDINGS_PER_APPROACH
    ):
        raise ValueError("ANALYSIS_CONTRACT_AUDIT_BUDGET_EXCEEDED")


def verify_rule_step(
    step: ReasoningStep,
    rule: TrustedRule,
) -> tuple[bool, str]:
    if len(step.premises) != len(rule.premises):
        return False, "premise-count-mismatch"
    bindings: dict[str, str] = {}
    for pattern, premise in zip(rule.premises, step.premises, strict=True):
        if not _unify(pattern, premise.fact, bindings):
            return False, "premise-does-not-match-rule"
    if not _unify(rule.conclusion, step.conclusion, bindings):
        return False, "conclusion-does-not-match-rule"
    return True, "verified-rule"


def _parse_spec(value: Mapping[str, object]) -> ContentSpec:
    schema = value.get("schema_version")
    base_keys = {
        "schema_version",
        "artifact_inventory",
        "initial_facts",
        "required_units",
        "trusted_rules",
    }
    if schema == _CONTENT_SPEC_SCHEMA_V3:
        if set(value) != base_keys:
            raise ValueError("CONTENT_SPEC_INVALID")
    elif schema in {_CONTENT_SPEC_SCHEMA_V4, _CONTENT_SPEC_SCHEMA_V5}:
        if set(value) != base_keys | {"learning_obligations", "diagram_evidence"}:
            raise ValueError("CONTENT_SPEC_INVALID")
    else:
        raise ValueError("CONTENT_SPEC_INVALID")
    raw_artifacts = value.get("artifact_inventory")
    if (
        not isinstance(raw_artifacts, list)
        or not raw_artifacts
        or len(raw_artifacts) > MAX_SPEC_ARTIFACTS
    ):
        raise ValueError("CONTENT_SPEC_INVALID")
    artifacts: list[RequiredArtifact] = []
    for item in raw_artifacts:
        if not isinstance(item, Mapping) or set(item) != {"path", "role"}:
            raise ValueError("CONTENT_SPEC_INVALID")
        path = _string(item.get("path"), "CONTENT_SPEC_INVALID")
        _safe_relative(path)
        role = _string(item.get("role"), "CONTENT_SPEC_INVALID")
        if role not in _ARTIFACT_ROLES:
            raise ValueError("CONTENT_SPEC_INVALID")
        artifacts.append(RequiredArtifact(path=path, role=role))
    _require_portable_unique(
        (item.path for item in artifacts), "CONTENT_SPEC_INVALID"
    )
    initial_facts, initial_fact_kinds = _parse_initial_facts(
        value.get("initial_facts")
    )
    raw_units = value.get("required_units")
    raw_rules = value.get("trusted_rules")
    if (
        not isinstance(raw_units, list)
        or not raw_units
        or len(raw_units) > MAX_REQUIRED_UNITS
        or not isinstance(raw_rules, list)
        or len(raw_rules) > MAX_TRUSTED_RULES
    ):
        raise ValueError("CONTENT_SPEC_INVALID")
    units: list[RequiredUnit] = []
    for item in raw_units:
        if not isinstance(item, Mapping) or set(item) != {
            "unit_id",
            "artifact_path",
            "title",
            "kind",
            "depends_on",
            "required_conclusions",
        }:
            raise ValueError("CONTENT_SPEC_INVALID")
        unit_id = _unit_identifier_value(
            item.get("unit_id"), "CONTENT_SPEC_INVALID"
        )
        title = _title(item.get("title"), "CONTENT_SPEC_INVALID")
        if title != f"[unit:{unit_id}]":
            raise ValueError("CONTENT_SPEC_INVALID")
        units.append(
            RequiredUnit(
                unit_id=unit_id,
                artifact_path=_string(
                    item.get("artifact_path"), "CONTENT_SPEC_INVALID"
                ),
                title=title,
                kind=_string(item.get("kind"), "CONTENT_SPEC_INVALID"),
                depends_on=_identifier_tuple(
                    item.get("depends_on"), "CONTENT_SPEC_INVALID"
                ),
                required_conclusions=_fact_tuple(
                    item.get("required_conclusions"), "CONTENT_SPEC_INVALID"
                ),
            )
        )
    rules: list[TrustedRule] = []
    for item in raw_rules:
        expected_rule_keys = {"rule_id", "premises", "conclusion"}
        if schema in {_CONTENT_SPEC_SCHEMA_V4, _CONTENT_SPEC_SCHEMA_V5}:
            expected_rule_keys.add("relation_kind")
        if schema == _CONTENT_SPEC_SCHEMA_V5:
            expected_rule_keys.add("numeric_checks")
        if not isinstance(item, Mapping) or set(item) != expected_rule_keys:
            raise ValueError("CONTENT_SPEC_INVALID")
        premises = _fact_tuple(item.get("premises"), "CONTENT_SPEC_INVALID")
        if not premises:
            raise ValueError("CONTENT_SPEC_INVALID")
        conclusion = _fact(item.get("conclusion"), "CONTENT_SPEC_INVALID")
        for pattern in (*premises, conclusion):
            _validate_rule_pattern(pattern)
        premise_placeholders = {
            match.group("name")
            for pattern in premises
            for match in _PLACEHOLDER.finditer(pattern)
        }
        conclusion_placeholders = {
            match.group("name") for match in _PLACEHOLDER.finditer(conclusion)
        }
        if not conclusion_placeholders.issubset(premise_placeholders):
            raise ValueError("CONTENT_SPEC_RULE_FREE_VARIABLE")
        relation_kind = (
            "causal_bridge"
            if schema == _CONTENT_SPEC_SCHEMA_V3
            else _string(item.get("relation_kind"), "CONTENT_SPEC_INVALID")
        )
        if relation_kind not in _RELATION_KINDS:
            raise ValueError("CONTENT_SPEC_INVALID")
        numeric_checks = (
            _parse_numeric_checks(item.get("numeric_checks"))
            if schema == _CONTENT_SPEC_SCHEMA_V5
            else ()
        )
        rules.append(
            TrustedRule(
                rule_id=_identifier_value(
                    item.get("rule_id"), "CONTENT_SPEC_INVALID"
                ),
                premises=premises,
                conclusion=conclusion,
                relation_kind=relation_kind,
                numeric_checks=numeric_checks,
            )
        )
    _require_unique((item.unit_id for item in units), "CONTENT_SPEC_INVALID")
    _require_unique((item.rule_id for item in rules), "CONTENT_SPEC_INVALID")
    unit_ids = {item.unit_id for item in units}
    if any(set(item.depends_on) - unit_ids for item in units):
        raise ValueError("CONTENT_SPEC_INVALID")
    for unit in units:
        _safe_relative(unit.artifact_path)
    reviewed_paths = {item.path for item in artifacts if item.role == "reviewed_source"}
    if not reviewed_paths or any(item.artifact_path not in reviewed_paths for item in units):
        raise ValueError("CONTENT_SPEC_INVALID")
    if {item.artifact_path for item in units} != reviewed_paths:
        raise ValueError("CONTENT_SPEC_INVALID")
    obligations: tuple[LearningObligation, ...] = ()
    diagram_evidence: tuple[DiagramEvidence, ...] = ()
    if schema in {_CONTENT_SPEC_SCHEMA_V4, _CONTENT_SPEC_SCHEMA_V5}:
        obligations = _parse_learning_obligations(
            value.get("learning_obligations"), units
        )
        diagram_evidence = _parse_diagram_evidence(
            value.get("diagram_evidence"), artifacts
        )
        structured_relations = {
            relation
            for evidence in diagram_evidence
            if evidence.extraction_kind == "structured_relations"
            for relation in evidence.relations
        }
        declared_diagram_facts = {
            fact
            for fact, kind in initial_fact_kinds.items()
            if kind == "diagram_relation"
        }
        if structured_relations != declared_diagram_facts:
            raise ValueError("CONTENT_SPEC_INVALID")
        required_by_unit = {
            unit.unit_id: set(unit.required_conclusions) for unit in units
        }
        if any(
            obligation.target_fact not in required_by_unit[obligation.unit_id]
            for obligation in obligations
        ):
            raise ValueError("CONTENT_SPEC_INVALID")
        _require_unique(
            (item.target_fact for item in obligations), "CONTENT_SPEC_INVALID"
        )
        if any(
            not any(
                rule.relation_kind == obligation.kind
                and bind_rule_conclusion(rule, obligation.target_fact) is not None
                for rule in rules
            )
            for obligation in obligations
        ):
            raise ValueError("CONTENT_SPEC_INVALID")
    return ContentSpec(
        tuple(artifacts),
        initial_facts,
        initial_fact_kinds,
        tuple(units),
        tuple(rules),
        obligations,
        diagram_evidence,
    )


def _parse_numeric_checks(value: object) -> tuple[NumericCheck, ...]:
    if not isinstance(value, list) or len(value) > MAX_SEQUENCE_ITEMS:
        raise ValueError("CONTENT_SPEC_INVALID")
    result: list[NumericCheck] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {
            "expression",
            "result_binding",
        }:
            raise ValueError("CONTENT_SPEC_INVALID")
        result.append(
            NumericCheck(
                expression=_fact(item.get("expression"), "CONTENT_SPEC_INVALID"),
                result_binding=_fact(
                    item.get("result_binding"), "CONTENT_SPEC_INVALID"
                ),
            )
        )
    identities = tuple(
        (item.expression, item.result_binding) for item in result
    )
    if len(identities) != len(set(identities)):
        raise ValueError("CONTENT_SPEC_INVALID")
    return tuple(result)


def _parse_learning_obligations(
    value: object, units: Sequence[RequiredUnit]
) -> tuple[LearningObligation, ...]:
    if not isinstance(value, list) or len(value) > MAX_SEQUENCE_ITEMS:
        raise ValueError("CONTENT_SPEC_INVALID")
    unit_ids = {unit.unit_id for unit in units}
    result: list[LearningObligation] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {
            "obligation_id",
            "unit_id",
            "kind",
            "target_fact",
        }:
            raise ValueError("CONTENT_SPEC_INVALID")
        unit_id = _unit_identifier_value(
            item.get("unit_id"), "CONTENT_SPEC_INVALID"
        )
        kind = _string(item.get("kind"), "CONTENT_SPEC_INVALID")
        if unit_id not in unit_ids or kind not in _RELATION_KINDS:
            raise ValueError("CONTENT_SPEC_INVALID")
        result.append(
            LearningObligation(
                obligation_id=_identifier_value(
                    item.get("obligation_id"), "CONTENT_SPEC_INVALID"
                ),
                unit_id=unit_id,
                kind=kind,
                target_fact=_fact(item.get("target_fact"), "CONTENT_SPEC_INVALID"),
            )
        )
    _require_unique(
        (item.obligation_id for item in result), "CONTENT_SPEC_INVALID"
    )
    return tuple(result)


def _parse_diagram_evidence(
    value: object, artifacts: Sequence[RequiredArtifact]
) -> tuple[DiagramEvidence, ...]:
    if not isinstance(value, list) or len(value) > MAX_SEQUENCE_ITEMS:
        raise ValueError("CONTENT_SPEC_INVALID")
    image_paths = {
        artifact.path for artifact in artifacts if artifact.role == "reviewed_image"
    }
    result: list[DiagramEvidence] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {
            "evidence_id",
            "image_path",
            "image_sha256",
            "extraction_kind",
            "ocr_labels",
            "relations",
        }:
            raise ValueError("CONTENT_SPEC_INVALID")
        image_path = _string(item.get("image_path"), "CONTENT_SPEC_INVALID")
        _safe_relative(image_path)
        image_sha256 = _string(
            item.get("image_sha256"), "CONTENT_SPEC_INVALID"
        )
        extraction_kind = _string(
            item.get("extraction_kind"), "CONTENT_SPEC_INVALID"
        )
        labels = _string_tuple(item.get("ocr_labels"), "CONTENT_SPEC_INVALID")
        relations = _fact_tuple(item.get("relations"), "CONTENT_SPEC_INVALID")
        if (
            image_path not in image_paths
            or _SHA256.fullmatch(image_sha256) is None
            or extraction_kind not in _DIAGRAM_EXTRACTION_KINDS
            or (extraction_kind == "ocr_only" and relations)
            or (extraction_kind == "structured_relations" and not relations)
        ):
            raise ValueError("CONTENT_SPEC_INVALID")
        result.append(
            DiagramEvidence(
                evidence_id=_identifier_value(
                    item.get("evidence_id"), "CONTENT_SPEC_INVALID"
                ),
                image_path=image_path,
                image_sha256=image_sha256,
                extraction_kind=extraction_kind,
                ocr_labels=labels,
                relations=relations,
            )
        )
    _require_unique((item.evidence_id for item in result), "CONTENT_SPEC_INVALID")
    _require_portable_unique(
        (item.image_path for item in result), "CONTENT_SPEC_INVALID"
    )
    if {item.image_path for item in result} != image_paths:
        raise ValueError("CONTENT_SPEC_INVALID")
    return tuple(result)


def _parse_initial_facts(value: object) -> tuple[tuple[str, ...], Mapping[str, str]]:
    if not isinstance(value, list) or len(value) > MAX_SEQUENCE_ITEMS:
        raise ValueError("CONTENT_SPEC_INVALID")
    facts: list[str] = []
    kinds: dict[str, str] = {}
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"fact", "kind"}:
            raise ValueError("CONTENT_SPEC_INVALID")
        fact = _fact(item.get("fact"), "CONTENT_SPEC_INVALID")
        kind = _string(item.get("kind"), "CONTENT_SPEC_INVALID")
        if kind not in {
            "external_assumption",
            "numeric_equality",
            "diagram_relation",
        } or fact in kinds:
            raise ValueError("CONTENT_SPEC_INVALID")
        facts.append(fact)
        kinds[fact] = kind
    return tuple(facts), kinds


def _parse_units(
    value: Mapping[str, object], sources: Mapping[str, str]
) -> tuple[ContractUnit, ...]:
    if value.get("schema_version") != _ANALYSIS_CONTRACT_SCHEMA:
        raise ValueError("ANALYSIS_CONTRACT_INVALID")
    if set(value) != {"schema_version", "units", "reasoning_steps"}:
        raise ValueError("ANALYSIS_CONTRACT_INVALID")
    raw_units = value.get("units")
    if not isinstance(raw_units, list) or len(raw_units) > MAX_REQUIRED_UNITS:
        raise ValueError("ANALYSIS_CONTRACT_INVALID")
    units: list[ContractUnit] = []
    for item in raw_units:
        if not isinstance(item, Mapping) or set(item) != {
            "unit_id",
            "kind",
            "artifact_path",
            "start",
            "end",
            "exact_quote",
            "depends_on",
            "reasoning_step_ids",
        }:
            raise ValueError("ANALYSIS_CONTRACT_INVALID")
        unit = ContractUnit(
            unit_id=_unit_identifier_value(
                item.get("unit_id"), "ANALYSIS_CONTRACT_INVALID"
            ),
            kind=_string(item.get("kind"), "ANALYSIS_CONTRACT_INVALID"),
            artifact_path=_string(
                item.get("artifact_path"), "ANALYSIS_CONTRACT_INVALID"
            ),
            start=_integer(item.get("start"), "ANALYSIS_CONTRACT_INVALID"),
            end=_integer(item.get("end"), "ANALYSIS_CONTRACT_INVALID"),
            exact_quote=_string(item.get("exact_quote"), "ANALYSIS_CONTRACT_INVALID"),
            depends_on=_identifier_tuple(
                item.get("depends_on"), "ANALYSIS_CONTRACT_INVALID"
            ),
            reasoning_step_ids=_identifier_tuple(
                item.get("reasoning_step_ids"), "ANALYSIS_CONTRACT_INVALID"
            ),
        )
        _validate_anchor(unit.artifact_path, unit.start, unit.end, unit.exact_quote, sources)
        units.append(unit)
    _require_unique((item.unit_id for item in units), "ANALYSIS_CONTRACT_INVALID")
    return tuple(units)


def _parse_steps(
    value: Mapping[str, object], sources: Mapping[str, str]
) -> tuple[ReasoningStep, ...]:
    raw_steps = value.get("reasoning_steps")
    if not isinstance(raw_steps, list) or len(raw_steps) > MAX_REASONING_STEPS:
        raise ValueError("ANALYSIS_CONTRACT_INVALID")
    steps: list[ReasoningStep] = []
    for item in raw_steps:
        if not isinstance(item, Mapping) or set(item) != {
            "step_id",
            "unit_id",
            "artifact_path",
            "start",
            "end",
            "exact_quote",
            "premises",
            "conclusion",
            "verification",
        }:
            raise ValueError("ANALYSIS_CONTRACT_INVALID")
        verification = item.get("verification")
        if not isinstance(verification, Mapping):
            raise ValueError("ANALYSIS_CONTRACT_INVALID")
        kind = _string(verification.get("kind"), "ANALYSIS_CONTRACT_INVALID")
        if set(verification) - {"kind", "rule_id"}:
            raise ValueError("ANALYSIS_CONTRACT_INVALID")
        raw_rule_id = verification.get("rule_id")
        if raw_rule_id is not None:
            raw_rule_id = _identifier_value(
                raw_rule_id, "ANALYSIS_CONTRACT_INVALID"
            )
        step = ReasoningStep(
            step_id=_identifier_value(
                item.get("step_id"), "ANALYSIS_CONTRACT_INVALID"
            ),
            unit_id=_unit_identifier_value(
                item.get("unit_id"), "ANALYSIS_CONTRACT_INVALID"
            ),
            artifact_path=_string(
                item.get("artifact_path"), "ANALYSIS_CONTRACT_INVALID"
            ),
            start=_integer(item.get("start"), "ANALYSIS_CONTRACT_INVALID"),
            end=_integer(item.get("end"), "ANALYSIS_CONTRACT_INVALID"),
            exact_quote=_string(item.get("exact_quote"), "ANALYSIS_CONTRACT_INVALID"),
            premises=_premise_bindings(
                item.get("premises"), "ANALYSIS_CONTRACT_INVALID"
            ),
            conclusion=_fact(item.get("conclusion"), "ANALYSIS_CONTRACT_INVALID"),
            verification_kind=kind,
            rule_id=raw_rule_id,
        )
        _validate_anchor(step.artifact_path, step.start, step.end, step.exact_quote, sources)
        if step.exact_quote != render_reasoning_step(step):
            raise ValueError("ANALYSIS_CONTRACT_SURFACE_MISMATCH")
        steps.append(step)
    _require_unique((item.step_id for item in steps), "ANALYSIS_CONTRACT_INVALID")
    return tuple(steps)


def _validate_contract_references(
    units: tuple[ContractUnit, ...],
    steps: tuple[ReasoningStep, ...],
    spec: ContentSpec,
) -> None:
    unit_ids = {item.unit_id for item in units}
    step_ids = {item.step_id for item in steps}
    if any(set(item.depends_on) - unit_ids for item in units):
        raise ValueError("ANALYSIS_CONTRACT_REFERENCE_INVALID")
    if any(set(item.reasoning_step_ids) - step_ids for item in units):
        raise ValueError("ANALYSIS_CONTRACT_REFERENCE_INVALID")
    if any(item.unit_id not in unit_ids for item in steps):
        raise ValueError("ANALYSIS_CONTRACT_REFERENCE_INVALID")
    owners: dict[str, int] = {item.step_id: 0 for item in steps}
    by_id = {item.step_id: item for item in steps}
    for unit in units:
        for step_id in unit.reasoning_step_ids:
            owners[step_id] += 1
            step = by_id[step_id]
            if step.unit_id != unit.unit_id:
                raise ValueError("ANALYSIS_CONTRACT_REFERENCE_INVALID")
    if any(count != 1 for count in owners.values()):
        raise ValueError("ANALYSIS_CONTRACT_REFERENCE_INVALID")
    step_positions = {item.step_id: index for index, item in enumerate(steps)}
    initial_facts = set(spec.initial_facts)
    for consumer_index, step in enumerate(steps):
        for premise in step.premises:
            if premise.source_kind == "initial_fact":
                if premise.source_id != premise.fact or premise.fact not in initial_facts:
                    raise ValueError("ANALYSIS_CONTRACT_PREMISE_SOURCE_INVALID")
                continue
            producer = by_id.get(premise.source_id)
            if (
                producer is None
                or step_positions[producer.step_id] >= consumer_index
                or producer.conclusion != premise.fact
            ):
                raise ValueError("ANALYSIS_CONTRACT_PREMISE_SOURCE_INVALID")


def _validate_contract_layout(
    units: tuple[ContractUnit, ...],
    steps: tuple[ReasoningStep, ...],
    sources: Mapping[str, str],
    spec: ContentSpec,
) -> None:
    path_order = {path: index for index, path in enumerate(sources)}

    def anchor_key(item: ContractUnit | ReasoningStep) -> tuple[int, int, int, str]:
        return (path_order[item.artifact_path], item.start, item.end, item.unit_id)

    if units != tuple(sorted(units, key=anchor_key)):
        raise ValueError("ANALYSIS_CONTRACT_READING_ORDER_INVALID")
    if steps != tuple(sorted(steps, key=lambda item: (*anchor_key(item), item.step_id))):
        raise ValueError("ANALYSIS_CONTRACT_READING_ORDER_INVALID")
    _reject_overlapping_ranges(units, "ANALYSIS_CONTRACT_UNIT_OVERLAP")
    _reject_overlapping_ranges(steps, "ANALYSIS_CONTRACT_STEP_OVERLAP")
    by_id = {item.step_id: item for item in steps}
    required = {item.unit_id: item for item in spec.required_units}
    for unit in units:
        owned = tuple(by_id[step_id] for step_id in unit.reasoning_step_ids)
        if owned != tuple(sorted(owned, key=lambda item: (*anchor_key(item), item.step_id))):
            raise ValueError("ANALYSIS_CONTRACT_READING_ORDER_INVALID")
        for step in owned:
            if (
                step.artifact_path != unit.artifact_path
                or step.start < unit.start
                or step.end > unit.end
            ):
                raise ValueError("ANALYSIS_CONTRACT_STEP_OUTSIDE_UNIT")
        expected = required.get(unit.unit_id)
        if expected is not None:
            if not unit.reasoning_step_ids:
                raise ValueError("ANALYSIS_CONTRACT_REQUIRED_UNIT_EMPTY")
            heading = f"# {expected.title}"
            source = sources[unit.artifact_path]
            if (
                unit.artifact_path != expected.artifact_path
                or unit.start + len(heading) > unit.end
                or source[unit.start : unit.start + len(heading)] != heading
                or (
                    unit.start + len(heading) < len(source)
                    and source[unit.start + len(heading)] not in "\r\n"
                )
            ):
                raise ValueError("ANALYSIS_CONTRACT_UNIT_HEADING_MISMATCH")


def _reject_overlapping_ranges(
    values: tuple[ContractUnit, ...] | tuple[ReasoningStep, ...],
    error: str,
) -> None:
    last_end: dict[str, int] = {}
    for item in values:
        previous_end = last_end.get(item.artifact_path)
        if previous_end is not None and item.start < previous_end:
            raise ValueError(error)
        last_end[item.artifact_path] = item.end


def render_reasoning_step(step: ReasoningStep) -> str:
    if step.verification_kind == "given":
        if step.premises or step.rule_id is not None:
            raise ValueError("ANALYSIS_CONTRACT_VERIFICATION_INVALID")
        return f"【已知】{step.conclusion}"
    if step.verification_kind == "numeric_equality":
        if step.premises or step.rule_id is not None:
            raise ValueError("ANALYSIS_CONTRACT_VERIFICATION_INVALID")
        return f"【计算】{step.conclusion}"
    if step.verification_kind == "rule" and step.rule_id:
        premises = "；".join(_render_premise(item) for item in step.premises)
        return f"【推导:{step.rule_id}】{premises} => {step.conclusion}"
    raise ValueError("ANALYSIS_CONTRACT_VERIFICATION_INVALID")


def render_rule_declaration(rule: TrustedRule) -> str:
    if not isinstance(rule, TrustedRule) or rule.relation_kind not in _RELATION_KINDS:
        raise ValueError("CONTENT_SPEC_INVALID")
    premises = "；".join(rule.premises)
    declaration = (
        f"【桥梁:{rule.rule_id}|{rule.relation_kind}】"
        f"若 {premises}，则 {rule.conclusion}"
    )
    checks = "\n".join(
        render_numeric_check(rule, index, check)
        for index, check in enumerate(rule.numeric_checks, 1)
    )
    return declaration if not checks else f"{declaration}\n{checks}"


def render_numeric_check(
    rule: TrustedRule, index: int, check: NumericCheck
) -> str:
    if (
        not isinstance(rule, TrustedRule)
        or not isinstance(check, NumericCheck)
        or not isinstance(index, int)
        or isinstance(index, bool)
        or index < 1
        or index > len(rule.numeric_checks)
        or rule.numeric_checks[index - 1] != check
    ):
        raise ValueError("CONTENT_SPEC_INVALID")
    return (
        f"【中间计算:{rule.rule_id}:{index}】"
        f"{check.expression} => {check.result_binding}"
    )


def _validate_artifact_inventory(
    root: Path,
    spec: ContentSpec,
    manifest_relative: str,
    contract_relative: str,
) -> None:
    expected = tuple(item.path for item in spec.artifact_inventory)
    controls = {
        item.path for item in spec.artifact_inventory if item.role == "control"
    }
    if controls != {manifest_relative, contract_relative}:
        raise ValueError("CONTENT_SPEC_CONTROL_ARTIFACTS_INVALID")
    actual: list[str] = []
    pending = [root]
    entry_count = 0
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    entry_count += 1
                    if entry_count > MAX_PROJECT_ENTRIES:
                        raise ValueError("ANALYSIS_PROJECT_ENTRY_BUDGET_EXCEEDED")
                    path = Path(entry.path)
                    if is_link_like(path):
                        raise ValueError("ANALYSIS_PROJECT_SYMLINK_FORBIDDEN")
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        raise ValueError("ANALYSIS_PROJECT_FILE_INVALID")
                    actual.append(path.relative_to(root).as_posix())
        except OSError as exc:
            raise ValueError("ANALYSIS_PROJECT_FILE_INVALID") from exc
    _require_portable_unique(actual, "ANALYSIS_PROJECT_ARTIFACT_SET_INVALID")
    if set(actual) != set(expected) or len(actual) != len(expected):
        raise ValueError("ANALYSIS_PROJECT_ARTIFACT_SET_INVALID")


def _validate_diagram_files(root: Path, spec: ContentSpec) -> None:
    for evidence in spec.diagram_evidence:
        safe = _safe_relative(evidence.image_path)
        path = root.joinpath(*safe.parts)
        assert_no_link_components(path, label="diagram artifact")
        if is_link_like(path) or not path.is_file():
            raise ValueError("DIAGRAM_ARTIFACT_UNAVAILABLE")
        try:
            if path.stat().st_size > MAX_REVIEWED_IMAGE_BYTES:
                raise ValueError("DIAGRAM_ARTIFACT_TOO_LARGE")
            digest_builder = hashlib.sha256()
            with path.open("rb") as handle:
                while chunk := handle.read(_READ_CHUNK_BYTES):
                    digest_builder.update(chunk)
            digest = digest_builder.hexdigest()
        except OSError as exc:
            raise ValueError("DIAGRAM_ARTIFACT_UNAVAILABLE") from exc
        if digest != evidence.image_sha256:
            raise ValueError("DIAGRAM_ARTIFACT_IDENTITY_MISMATCH")


def _external_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("EXTERNAL_EVIDENCE_INVALID")
    return value


def _external_list(value: object, *, nonempty: bool = False) -> list[object]:
    if (
        not isinstance(value, list)
        or len(value) > MAX_SEQUENCE_ITEMS
        or (nonempty and not value)
    ):
        raise ValueError("EXTERNAL_EVIDENCE_INVALID")
    return value


def _external_strings(value: object, *, nonempty: bool = False) -> tuple[str, ...]:
    raw = _external_list(value, nonempty=nonempty)
    result = tuple(_string(item, "EXTERNAL_EVIDENCE_INVALID") for item in raw)
    if len(result) != len(set(result)):
        raise ValueError("EXTERNAL_EVIDENCE_INVALID")
    return result


def _external_sha256(value: object) -> str:
    digest = _string(value, "EXTERNAL_EVIDENCE_INVALID").lower()
    if _SHA256.fullmatch(digest) is None:
        raise ValueError("EXTERNAL_EVIDENCE_INVALID")
    return digest


def _sha256_regular_file(path: Path, *, label: str) -> str:
    assert_no_link_components(path, label=label)
    if is_link_like(path) or not path.is_file():
        raise ValueError("EXTERNAL_EVIDENCE_ARTIFACT_UNAVAILABLE")
    try:
        if path.stat().st_size <= 0:
            raise ValueError("EXTERNAL_EVIDENCE_ARTIFACT_UNAVAILABLE")
    except OSError as exc:
        raise ValueError("EXTERNAL_EVIDENCE_ARTIFACT_UNAVAILABLE") from exc
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_READ_CHUNK_BYTES):
                digest.update(chunk)
    except OSError as exc:
        raise ValueError("EXTERNAL_EVIDENCE_ARTIFACT_UNAVAILABLE") from exc
    return digest.hexdigest()


def _external_reference_binding(
    *,
    root: Path,
    bundle_relative: str,
    reference: object,
    inventory_roles: Mapping[str, str],
    expected_asset_role: str,
) -> tuple[str, str]:
    value = _external_mapping(reference)
    if not {"path", "sha256"}.issubset(value):
        raise ValueError("EXTERNAL_EVIDENCE_INVALID")
    relative_to_bundle = _safe_relative(
        _string(value.get("path"), "EXTERNAL_EVIDENCE_INVALID")
    )
    bundle_parent = PurePosixPath(bundle_relative).parent
    project_relative = str(bundle_parent / relative_to_bundle)
    _safe_relative(project_relative)
    if inventory_roles.get(project_relative) != expected_asset_role:
        raise ValueError("EXTERNAL_EVIDENCE_ARTIFACT_SET_INVALID")
    expected_sha256 = _external_sha256(value.get("sha256"))
    asset_path = root.joinpath(*PurePosixPath(project_relative).parts)
    if _sha256_regular_file(asset_path, label="external evidence asset") != expected_sha256:
        raise ValueError("EXTERNAL_EVIDENCE_ARTIFACT_IDENTITY_MISMATCH")
    return project_relative, expected_sha256


def _validate_external_admission(
    payload: Mapping[str, object],
) -> tuple[str, frozenset[str]]:
    classification = _string(
        payload.get("classification"), "EXTERNAL_EVIDENCE_INVALID"
    )
    if classification not in {"definitely_useful", "definitely_useless"}:
        raise ValueError("EXTERNAL_EVIDENCE_INVALID")
    _string(payload.get("classification_reason"), "EXTERNAL_EVIDENCE_INVALID")
    admission = _external_mapping(payload.get("admission"))
    disposition = _string(
        admission.get("disposition"), "EXTERNAL_EVIDENCE_INVALID"
    )
    allowed = _INTEGRATED_EXTERNAL_DISPOSITIONS | _EXCLUDED_EXTERNAL_DISPOSITIONS
    if disposition not in allowed:
        raise ValueError("EXTERNAL_EVIDENCE_INVALID")
    exam_atom_ids = frozenset(
        _external_strings(admission.get("exam_atom_ids"))
    )
    for field in (
        "necessity_rationale",
        "minimality_test",
        "removal_impact",
        "depth_ceiling",
    ):
        _string(admission.get(field), "EXTERNAL_EVIDENCE_INVALID")
    if disposition in _INTEGRATED_EXTERNAL_DISPOSITIONS:
        if classification != "definitely_useful" or not exam_atom_ids:
            raise ValueError("EXTERNAL_EVIDENCE_EXAM_ADMISSION_INVALID")
    else:
        if exam_atom_ids:
            raise ValueError("EXTERNAL_EVIDENCE_EXAM_ADMISSION_INVALID")
        for owner in (payload, admission):
            if any(
                owner.get(field) is True
                for field in (
                    "integrated_into_body",
                    "included_in_core_text",
                    "adopted_in_body",
                )
            ):
                raise ValueError("EXTERNAL_EVIDENCE_EXAM_ADMISSION_INVALID")
            for field in (
                "body_anchor_ids",
                "body_integration_targets",
                "integration_targets",
            ):
                if field in owner and _external_strings(owner.get(field)):
                    raise ValueError("EXTERNAL_EVIDENCE_EXAM_ADMISSION_INVALID")
    return disposition, exam_atom_ids


def _validate_full_video_timeline(payload: Mapping[str, object]) -> int:
    duration = payload.get("duration_ms")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
        raise ValueError("EXTERNAL_EVIDENCE_VIDEO_WATCH_INVALID")
    ranges = _external_list(payload.get("timeline_covered_ranges"), nonempty=True)
    cursor = 0
    for raw_range in ranges:
        time_range = _external_mapping(raw_range)
        start = time_range.get("start_ms")
        end = time_range.get("end_ms")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start != cursor
            or end <= start
            or end > duration
        ):
            raise ValueError("EXTERNAL_EVIDENCE_VIDEO_WATCH_INVALID")
        cursor = end
    ratio = payload.get("timeline_coverage_ratio")
    if cursor != duration or isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or float(ratio) != 1.0:
        raise ValueError("EXTERNAL_EVIDENCE_VIDEO_WATCH_INVALID")
    return duration


def _validate_external_atoms(
    payload: Mapping[str, object],
    *,
    source_kind: str,
    disposition: str,
    admission_exam_atom_ids: frozenset[str],
    internal_roles: frozenset[str],
    duration_ms: int | None,
) -> None:
    atoms = _external_list(payload.get("extracted_knowledge_atoms"), nonempty=True)
    seen: set[str] = set()
    covered_exam_atoms: set[str] = set()
    for raw_atom in atoms:
        atom = _external_mapping(raw_atom)
        atom_id = _string(atom.get("atom_id"), "EXTERNAL_EVIDENCE_INVALID")
        if atom_id in seen:
            raise ValueError("EXTERNAL_EVIDENCE_INVALID")
        seen.add(atom_id)
        if disposition not in _INTEGRATED_EXTERNAL_DISPOSITIONS:
            continue
        evidence_roles = frozenset(
            _external_strings(atom.get("evidence_roles"), nonempty=True)
        )
        atom_exam_ids = frozenset(
            _external_strings(atom.get("exam_atom_ids"), nonempty=True)
        )
        if (
            not evidence_roles <= internal_roles
            or not atom_exam_ids <= admission_exam_atom_ids
            or atom.get("independent_correctness_status") != "passed"
        ):
            raise ValueError("EXTERNAL_EVIDENCE_INTEGRATION_INVALID")
        if source_kind == "video":
            if duration_ms is None:
                raise ValueError("EXTERNAL_EVIDENCE_VIDEO_WATCH_INVALID")
            time_ranges = _external_list(atom.get("time_ranges"), nonempty=True)
            for raw_range in time_ranges:
                time_range = _external_mapping(raw_range)
                start = time_range.get("start_ms")
                end = time_range.get("end_ms")
                if (
                    not isinstance(start, int)
                    or isinstance(start, bool)
                    or not isinstance(end, int)
                    or isinstance(end, bool)
                    or start < 0
                    or end <= start
                    or end > duration_ms
                ):
                    raise ValueError("EXTERNAL_EVIDENCE_INTEGRATION_INVALID")
        else:
            _string(atom.get("source_locator"), "EXTERNAL_EVIDENCE_INVALID")
        covered_exam_atoms.update(atom_exam_ids)
    if (
        disposition in _INTEGRATED_EXTERNAL_DISPOSITIONS
        and covered_exam_atoms != set(admission_exam_atom_ids)
    ):
        raise ValueError("EXTERNAL_EVIDENCE_INTEGRATION_INVALID")


def _validate_video_policy(
    payload: Mapping[str, object],
    *,
    disposition: str,
    admission_exam_atom_ids: frozenset[str],
    internal_roles: frozenset[str],
) -> None:
    if (
        payload.get("watched_by_system") is not False
        or payload.get("viewer") != "codex_runtime"
        or payload.get("full_stream_decoded") is not True
        or payload.get("sampled_visual_review") is not True
        or payload.get("continuous_dynamic_semantics_covered") is not False
        or payload.get("audio_analyzed") is not True
        or payload.get("visuals_analyzed") is not True
        or payload.get("onscreen_text_analyzed") is not True
        or payload.get("gestures_and_animation_analyzed") is not False
    ):
        raise ValueError("EXTERNAL_EVIDENCE_VIDEO_WATCH_INVALID")
    _string(payload.get("watch_execution_id"), "EXTERNAL_EVIDENCE_VIDEO_WATCH_INVALID")
    duration_ms = _validate_full_video_timeline(payload)
    if (
        not _VIDEO_EVIDENCE_FILE_ROLES <= internal_roles
        or "asr" not in internal_roles
    ):
        raise ValueError("EXTERNAL_EVIDENCE_VIDEO_WATCH_INVALID")
    gaps = _external_list(
        payload.get("residual_interpretive_gaps"), nonempty=True
    )
    residual_gap_ids: set[str] = set()
    for raw_gap in gaps:
        gap = _external_mapping(raw_gap)
        gap_id = _string(
            gap.get("gap_id"), "EXTERNAL_EVIDENCE_VIDEO_WATCH_INVALID"
        )
        if gap_id in residual_gap_ids:
            raise ValueError("EXTERNAL_EVIDENCE_VIDEO_WATCH_INVALID")
        residual_gap_ids.add(gap_id)
        _string(gap.get("description"), "EXTERNAL_EVIDENCE_VIDEO_WATCH_INVALID")
        for field in (
            "confirmed_content",
            "evidence_limit",
            "why_static_insufficient",
            "reader_observation_task",
        ):
            _string(gap.get(field), "EXTERNAL_EVIDENCE_VIDEO_WATCH_INVALID")
        for raw_range in _external_list(gap.get("time_ranges"), nonempty=True):
            time_range = _external_mapping(raw_range)
            start = time_range.get("start_ms")
            end = time_range.get("end_ms")
            if (
                not isinstance(start, int)
                or isinstance(start, bool)
                or not isinstance(end, int)
                or isinstance(end, bool)
                or start < 0
                or end <= start
                or end > duration_ms
            ):
                raise ValueError("EXTERNAL_EVIDENCE_VIDEO_WATCH_INVALID")
        if gap.get("must_not_infer_author_intent") is not True:
            raise ValueError("EXTERNAL_EVIDENCE_AUTHOR_INTENT_INFERENCE_FORBIDDEN")
    if disposition in _INTEGRATED_EXTERNAL_DISPOSITIONS:
        if payload.get("reader_viewing_required") is not True:
            raise ValueError("EXTERNAL_EVIDENCE_REQUIRED_VIEWING_INVALID")
        required_viewing = _external_mapping(payload.get("required_viewing"))
        for field in (
            "viewing_id",
            "scope",
            "what_has_been_integrated",
            "what_reader_must_observe_personally",
            "why_text_cannot_fully_replace_video",
        ):
            _string(
                required_viewing.get(field),
                "EXTERNAL_EVIDENCE_REQUIRED_VIEWING_INVALID",
            )
        if required_viewing.get("scope") not in {"full", "segments"}:
            raise ValueError("EXTERNAL_EVIDENCE_REQUIRED_VIEWING_INVALID")
        _external_list(required_viewing.get("time_ranges"), nonempty=True)
        if set(
            _external_strings(
                required_viewing.get("residual_gap_ids"), nonempty=True
            )
        ) != residual_gap_ids:
            raise ValueError("EXTERNAL_EVIDENCE_REQUIRED_VIEWING_INVALID")
    elif (
        payload.get("reader_viewing_required") is not False
        or "required_viewing" in payload
    ):
        raise ValueError("EXTERNAL_EVIDENCE_REQUIRED_VIEWING_INVALID")
    _validate_external_atoms(
        payload,
        source_kind="video",
        disposition=disposition,
        admission_exam_atom_ids=admission_exam_atom_ids,
        internal_roles=internal_roles,
        duration_ms=duration_ms,
    )


def _validate_article_policy(
    payload: Mapping[str, object],
    *,
    disposition: str,
    admission_exam_atom_ids: frozenset[str],
    internal_roles: frozenset[str],
) -> None:
    if (
        payload.get("read_by_system") is not True
        or payload.get("reader") != "codex_runtime"
        or payload.get("full_text_read") is not True
        or payload.get("page_images_reviewed") is not True
        or payload.get("embedded_images_reviewed") is not True
    ):
        raise ValueError("EXTERNAL_EVIDENCE_ARTICLE_READ_INVALID")
    _string(payload.get("read_execution_id"), "EXTERNAL_EVIDENCE_ARTICLE_READ_INVALID")
    if not _ARTICLE_EVIDENCE_FILE_ROLES <= internal_roles:
        raise ValueError("EXTERNAL_EVIDENCE_ARTICLE_READ_INVALID")
    if disposition in _INTEGRATED_EXTERNAL_DISPOSITIONS:
        _validate_external_atoms(
            payload,
            source_kind="article",
            disposition=disposition,
            admission_exam_atom_ids=admission_exam_atom_ids,
            internal_roles=internal_roles,
            duration_ms=None,
        )


def _validate_external_evidence_files(root: Path, spec: ContentSpec) -> None:
    inventory_roles = {item.path: item.role for item in spec.artifact_inventory}
    referenced_assets: dict[str, set[str]] = {"video": set(), "article": set()}
    for bundle_relative, bundle_role in (
        (item.path, item.role)
        for item in spec.artifact_inventory
        if item.role in _EXTERNAL_BUNDLE_ROLE_TO_KIND
    ):
        source_kind = _EXTERNAL_BUNDLE_ROLE_TO_KIND[bundle_role]
        bundle_path = root.joinpath(*PurePosixPath(bundle_relative).parts)
        assert_no_link_components(bundle_path, label="external evidence bundle")
        if is_link_like(bundle_path) or not bundle_path.is_file():
            raise ValueError("EXTERNAL_EVIDENCE_BUNDLE_UNAVAILABLE")
        payload = _object_from_bytes(
            _read_bounded_bytes(bundle_path, "EXTERNAL_EVIDENCE_INVALID"),
            "EXTERNAL_EVIDENCE_INVALID",
        )
        if (
            payload.get("schema_version") != "external-evidence-bundle-v1"
            or payload.get("source_kind") != source_kind
        ):
            raise ValueError("EXTERNAL_EVIDENCE_INVALID")
        for field in (
            "source_id",
            "platform",
            "canonical_url",
            "title",
            "creator_or_publisher",
            "published_at",
            "retrieved_at",
        ):
            _string(payload.get(field), "EXTERNAL_EVIDENCE_INVALID")
        source_id = _string(payload.get("source_id"), "EXTERNAL_EVIDENCE_INVALID")
        platform = _string(payload.get("platform"), "EXTERNAL_EVIDENCE_INVALID")
        canonical_url = _string(payload.get("canonical_url"), "EXTERNAL_EVIDENCE_INVALID")
        disposition, admission_exam_atom_ids = _validate_external_admission(payload)
        expected_asset_role = _EXTERNAL_ASSET_ROLE_BY_KIND[source_kind]
        internal_roles: set[str] = set()
        role_paths: dict[str, Path] = {}
        raw_bundle_files = _external_list(payload.get("bundle_files"), nonempty=True)
        for raw_file in raw_bundle_files:
            file_record = _external_mapping(raw_file)
            role = _string(file_record.get("role"), "EXTERNAL_EVIDENCE_INVALID")
            if role in internal_roles:
                raise ValueError("EXTERNAL_EVIDENCE_INVALID")
            internal_roles.add(role)
        original_snapshot = _external_mapping(payload.get("original_snapshot"))
        original_relative, original_sha256 = _external_reference_binding(
            root=root,
            bundle_relative=bundle_relative,
            reference=original_snapshot,
            inventory_roles=inventory_roles,
            expected_asset_role=expected_asset_role,
        )
        referenced_assets[source_kind].add(original_relative)
        for raw_file in raw_bundle_files:
            file_record = _external_mapping(raw_file)
            role = _string(file_record.get("role"), "EXTERNAL_EVIDENCE_INVALID")
            relative, _ = _external_reference_binding(
                root=root,
                bundle_relative=bundle_relative,
                reference=file_record,
                inventory_roles=inventory_roles,
                expected_asset_role=expected_asset_role,
            )
            referenced_assets[source_kind].add(relative)
            role_paths[role] = root.joinpath(*PurePosixPath(relative).parts)
        local_receipt_role = "local_acquisition_receipt"
        if platform in LOCAL_ACQUISITION_PLATFORMS and local_receipt_role not in role_paths:
            raise ValueError("EXTERNAL_EVIDENCE_LOCAL_ACQUISITION_INVALID")
        if local_receipt_role in role_paths:
            try:
                local_receipt = _object_from_bytes(
                    _read_bounded_bytes(
                        role_paths[local_receipt_role],
                        "EXTERNAL_EVIDENCE_LOCAL_ACQUISITION_INVALID",
                    ),
                    "EXTERNAL_EVIDENCE_LOCAL_ACQUISITION_INVALID",
                )
                validated_local_receipt = validate_portable_local_source_receipt(
                    local_receipt,
                    expected_source_id=source_id,
                    expected_platform=platform,
                    expected_kind=source_kind,
                    expected_url=canonical_url,
                    expected_snapshot_sha256=original_sha256,
                )
                if (
                    validated_local_receipt["receipt"].get("receipt_origin")
                    != "executed_adapter"
                ):
                    raise ValueError("local acquisition was not executed by the adapter")
            except (LocalAcquisitionReceiptError, ValueError) as exc:
                raise ValueError(
                    "EXTERNAL_EVIDENCE_LOCAL_ACQUISITION_INVALID"
                ) from exc
        build_receipt_role = "local_evidence_build_receipt"
        if platform in LOCAL_ACQUISITION_PLATFORMS and build_receipt_role not in role_paths:
            raise ValueError("EXTERNAL_EVIDENCE_BUILD_RECEIPT_INVALID")
        build_nested: list[dict[str, str]] = []
        if build_receipt_role in role_paths:
            try:
                build_nested = validate_local_evidence_build_receipt(
                    root=bundle_path.parent.resolve(),
                    receipt_path=role_paths[build_receipt_role],
                    source_id=source_id,
                    source_kind=source_kind,
                    expected_builder_path=_expected_local_evidence_builder(),
                )
            except LocalEvidenceBuildReceiptError as exc:
                raise ValueError("EXTERNAL_EVIDENCE_BUILD_RECEIPT_INVALID") from exc
        if source_kind == "video":
            watch_receipt = _external_mapping(payload.get("watch_receipt"))
            receipt_path, receipt_sha256 = _external_reference_binding(
                root=root,
                bundle_relative=bundle_relative,
                reference=watch_receipt,
                inventory_roles=inventory_roles,
                expected_asset_role=expected_asset_role,
            )
            receipt_records = [
                _external_mapping(item)
                for item in raw_bundle_files
                if _external_mapping(item).get("role") == "watch_receipt"
            ]
            if len(receipt_records) != 1:
                raise ValueError("EXTERNAL_EVIDENCE_VIDEO_WATCH_INVALID")
            bound_receipt_path, bound_receipt_sha256 = _external_reference_binding(
                root=root,
                bundle_relative=bundle_relative,
                reference=receipt_records[0],
                inventory_roles=inventory_roles,
                expected_asset_role=expected_asset_role,
            )
            if (receipt_path, receipt_sha256) != (
                bound_receipt_path,
                bound_receipt_sha256,
            ):
                raise ValueError("EXTERNAL_EVIDENCE_VIDEO_WATCH_INVALID")
            _validate_video_policy(
                payload,
                disposition=disposition,
                admission_exam_atom_ids=admission_exam_atom_ids,
                internal_roles=frozenset(internal_roles),
            )
            try:
                nested_assets = validate_video_evidence_assets(
                    root=bundle_path.parent.resolve(),
                    role_paths=role_paths,
                    source_id=source_id,
                    duration_ms=int(payload["duration_ms"]),
                    watch_execution_id=str(payload["watch_execution_id"]),
                )
            except (KeyError, MediaEvidenceAssetError) as exc:
                raise ValueError("EXTERNAL_EVIDENCE_VIDEO_ASSET_INVALID") from exc
        else:
            _validate_article_policy(
                payload,
                disposition=disposition,
                admission_exam_atom_ids=admission_exam_atom_ids,
                internal_roles=frozenset(internal_roles),
            )
            try:
                top_read_receipt = _external_mapping(payload.get("read_receipt"))
                top_receipt_path, top_receipt_sha256 = _external_reference_binding(
                    root=root,
                    bundle_relative=bundle_relative,
                    reference=top_read_receipt,
                    inventory_roles=inventory_roles,
                    expected_asset_role=expected_asset_role,
                )
                role_receipt = next(
                    _external_mapping(item)
                    for item in raw_bundle_files
                    if _external_mapping(item).get("role") == "read_receipt"
                )
                role_receipt_path, role_receipt_sha256 = _external_reference_binding(
                    root=root,
                    bundle_relative=bundle_relative,
                    reference=role_receipt,
                    inventory_roles=inventory_roles,
                    expected_asset_role=expected_asset_role,
                )
                if (top_receipt_path, top_receipt_sha256) != (
                    role_receipt_path,
                    role_receipt_sha256,
                ):
                    raise ValueError("read receipt binding mismatch")
            except (StopIteration, ValueError) as exc:
                raise ValueError("EXTERNAL_EVIDENCE_ARTICLE_READ_INVALID") from exc
            try:
                nested_assets = validate_article_evidence_assets(
                    root=bundle_path.parent.resolve(),
                    role_paths=role_paths,
                    source_id=source_id,
                    read_execution_id=str(payload["read_execution_id"]),
                )
            except (KeyError, MediaEvidenceAssetError) as exc:
                raise ValueError("EXTERNAL_EVIDENCE_ARTICLE_ASSET_INVALID") from exc
        for asset in nested_assets:
            try:
                nested_relative = asset.path.resolve().relative_to(root).as_posix()
            except ValueError as exc:
                raise ValueError("EXTERNAL_EVIDENCE_ARTIFACT_SET_INVALID") from exc
            if inventory_roles.get(nested_relative) != expected_asset_role:
                raise ValueError("EXTERNAL_EVIDENCE_ARTIFACT_SET_INVALID")
            if _sha256_regular_file(
                asset.path.resolve(), label="nested external evidence asset"
            ) != asset.sha256:
                raise ValueError("EXTERNAL_EVIDENCE_ARTIFACT_IDENTITY_MISMATCH")
            referenced_assets[source_kind].add(nested_relative)
        for item in build_nested:
            path = Path(item["path"]).resolve()
            try:
                nested_relative = path.relative_to(root).as_posix()
            except ValueError as exc:
                raise ValueError("EXTERNAL_EVIDENCE_ARTIFACT_SET_INVALID") from exc
            if inventory_roles.get(nested_relative) != expected_asset_role:
                raise ValueError("EXTERNAL_EVIDENCE_ARTIFACT_SET_INVALID")
            if _sha256_regular_file(
                path, label="local evidence build artifact"
            ) != item["sha256"]:
                raise ValueError("EXTERNAL_EVIDENCE_ARTIFACT_IDENTITY_MISMATCH")
            referenced_assets[source_kind].add(nested_relative)
    for source_kind, asset_role in _EXTERNAL_ASSET_ROLE_BY_KIND.items():
        declared_assets = {
            item.path for item in spec.artifact_inventory if item.role == asset_role
        }
        if declared_assets != referenced_assets[source_kind]:
            raise ValueError("EXTERNAL_EVIDENCE_ARTIFACT_SET_INVALID")


def _validate_anchor(
    artifact_path: str,
    start: int,
    end: int,
    exact_quote: str,
    sources: Mapping[str, str],
) -> None:
    _safe_relative(artifact_path)
    source = sources.get(artifact_path)
    if source is None:
        raise ValueError("ANALYSIS_CONTRACT_SOURCE_INVALID")
    if start < 0 or end <= start or end > len(source) or source[start:end] != exact_quote:
        raise ValueError("ANALYSIS_CONTRACT_ANCHOR_MISMATCH")


def _unify(pattern: str, actual: str, bindings: dict[str, str]) -> bool:
    tokens = _rule_pattern_tokens(pattern)
    cursor = 0
    pending = dict(bindings)
    for index, (kind, value) in enumerate(tokens):
        if kind == "literal":
            if not actual.startswith(value, cursor):
                return False
            cursor += len(value)
            continue
        bound = pending.get(value)
        if bound is not None:
            if not actual.startswith(bound, cursor):
                return False
            cursor += len(bound)
            continue
        next_literal = ""
        if index + 1 < len(tokens):
            next_kind, next_value = tokens[index + 1]
            if next_kind != "literal":
                raise ValueError("CONTENT_SPEC_RULE_PATTERN_AMBIGUOUS")
            next_literal = next_value
        end = len(actual) if not next_literal else actual.find(next_literal, cursor)
        if end < cursor:
            return False
        captured = actual[cursor:end]
        if not captured or any(character in "()," for character in captured):
            return False
        pending[value] = captured
        cursor = end
    if cursor != len(actual):
        return False
    bindings.clear()
    bindings.update(pending)
    return True


def _validate_rule_pattern(pattern: str) -> None:
    matches = tuple(_PLACEHOLDER.finditer(pattern))
    if len(matches) > 32:
        raise ValueError("CONTENT_SPEC_RULE_PATTERN_AMBIGUOUS")
    if any(left.end() == right.start() for left, right in zip(matches, matches[1:])):
        raise ValueError("CONTENT_SPEC_RULE_PATTERN_AMBIGUOUS")


def _rule_pattern_tokens(pattern: str) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    cursor = 0
    for match in _PLACEHOLDER.finditer(pattern):
        if match.start() > cursor:
            result.append(("literal", pattern[cursor : match.start()]))
        result.append(("placeholder", match.group("name")))
        cursor = match.end()
    if cursor < len(pattern):
        result.append(("literal", pattern[cursor:]))
    if not result:
        result.append(("literal", pattern))
    return tuple(result)


def _object_from_bytes(content: bytes, error: str) -> Mapping[str, object]:
    if len(content) > MAX_CONTROL_FILE_BYTES:
        raise ValueError(error)
    try:
        value = json.loads(
            content.decode("utf-8"), object_pairs_hook=_unique_object
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(error) from exc
    if not isinstance(value, Mapping):
        raise ValueError(error)
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _safe_relative(value: str) -> PurePosixPath:
    if not value or "\\" in value or ":" in value:
        raise ValueError("ANALYSIS_CONTRACT_PATH_INVALID")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        raise ValueError("ANALYSIS_CONTRACT_PATH_INVALID")
    return path


def _string(value: object, error: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_TEXT_FIELD_CHARS
        or "\x00" in value
    ):
        raise ValueError(error)
    return value


def _identifier_value(value: object, error: str) -> str:
    identifier = _string(value, error)
    if _IDENTIFIER.fullmatch(identifier) is None:
        raise ValueError(error)
    return identifier


def _unit_identifier_value(value: object, error: str) -> str:
    identifier = _string(value, error)
    if _UNIT_IDENTIFIER.fullmatch(identifier) is None:
        raise ValueError(error)
    return identifier


def _title(value: object, error: str) -> str:
    title = _string(value, error)
    if title != title.strip() or _TITLE_LABEL.fullmatch(title) is None:
        raise ValueError(error)
    return title


def _fact(value: object, error: str) -> str:
    fact = _string(value, error)
    if (
        fact != fact.strip()
        or any(character in fact for character in "\r\n")
        or any(marker in fact for marker in ("【", "】", "=>", "；"))
    ):
        raise ValueError(error)
    return fact


def _fact_tuple(value: object, error: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > MAX_SEQUENCE_ITEMS:
        raise ValueError(error)
    result = tuple(_fact(item, error) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(error)
    return result


def _premise_bindings(value: object, error: str) -> tuple[PremiseBinding, ...]:
    if not isinstance(value, list) or len(value) > MAX_SEQUENCE_ITEMS:
        raise ValueError(error)
    result: list[PremiseBinding] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {
            "fact",
            "source_kind",
            "source_id",
        }:
            raise ValueError(error)
        fact = _fact(item.get("fact"), error)
        source_kind = _string(item.get("source_kind"), error)
        if source_kind == "initial_fact":
            source_id = _fact(item.get("source_id"), error)
        elif source_kind == "reasoning_step":
            source_id = _identifier_value(item.get("source_id"), error)
        else:
            raise ValueError(error)
        result.append(
            PremiseBinding(
                fact=fact,
                source_kind=source_kind,
                source_id=source_id,
            )
        )
    if len({item.fact for item in result}) != len(result):
        raise ValueError(error)
    return tuple(result)


def _render_premise(premise: PremiseBinding) -> str:
    if premise.source_kind == "initial_fact":
        return f"{premise.fact}〔来源:初始条件〕"
    return f"{premise.fact}〔来源步骤:{premise.source_id}〕"


def _integer(value: object, error: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(error)
    return value


def _string_tuple(value: object, error: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or len(value) > MAX_SEQUENCE_ITEMS
        or any(
        not isinstance(item, str) or not item for item in value
        )
    ):
        raise ValueError(error)
    result = tuple(value)
    if len(result) != len(set(result)):
        raise ValueError(error)
    return result


def _identifier_tuple(value: object, error: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > MAX_SEQUENCE_ITEMS:
        raise ValueError(error)
    result = tuple(_identifier_value(item, error) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(error)
    return result


def _read_utf8(
    path: Path,
    error: str,
    *,
    max_chars: int | None = None,
    char_limit_error: str | None = None,
) -> str:
    if max_chars is not None and max_chars < 0:
        raise ValueError(char_limit_error or error)
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    chunks: list[str] = []
    byte_count = 0
    character_count = 0
    try:
        with path.open("rb") as stream:
            while True:
                remaining_bytes = MAX_CONTROL_FILE_BYTES + 1 - byte_count
                chunk = stream.read(min(_READ_CHUNK_BYTES, max(1, remaining_bytes)))
                if not chunk:
                    break
                byte_count += len(chunk)
                if byte_count > MAX_CONTROL_FILE_BYTES:
                    raise ValueError(error)
                decoded = decoder.decode(chunk, final=False)
                character_count += len(decoded)
                if max_chars is not None and character_count > max_chars:
                    raise ValueError(char_limit_error or error)
                chunks.append(decoded)
            final = decoder.decode(b"", final=True)
            character_count += len(final)
            if max_chars is not None and character_count > max_chars:
                raise ValueError(char_limit_error or error)
            chunks.append(final)
    except UnicodeDecodeError as exc:
        raise ValueError(error) from exc
    except OSError as exc:
        raise ValueError(error) from exc
    return "".join(chunks)


def _read_bounded_bytes(path: Path, error: str) -> bytes:
    try:
        with path.open("rb") as stream:
            content = stream.read(MAX_CONTROL_FILE_BYTES + 1)
    except OSError as exc:
        raise ValueError(error) from exc
    if len(content) > MAX_CONTROL_FILE_BYTES:
        raise ValueError(error)
    return content


def _require_unique(values: Iterable[str], error: str) -> None:
    result = tuple(values)
    if len(result) != len(set(result)):
        raise ValueError(error)


def _require_portable_unique(values: Iterable[str], error: str) -> None:
    result = tuple(values)
    portable = tuple(
        tuple(
            unicodedata.normalize("NFC", part).casefold()
            for part in PurePosixPath(value).parts
        )
        for value in result
    )
    if len(portable) != len(set(portable)):
        raise ValueError(error)


__all__ = [
    "ContentSpec",
    "ContractUnit",
    "DiagramEvidence",
    "LearningObligation",
    "NumericCheck",
    "ProductionContract",
    "RequiredArtifact",
    "ReasoningStep",
    "RequiredUnit",
    "TrustedRule",
    "bind_rule_conclusion",
    "bind_rule_pattern",
    "derive_rule_conclusion",
    "instantiate_rule_pattern",
    "load_production_contract",
    "parse_content_spec_bytes",
    "render_reasoning_step",
    "render_rule_declaration",
    "verify_rule_step",
]
