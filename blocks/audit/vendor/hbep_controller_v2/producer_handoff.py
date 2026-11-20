from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import PurePosixPath
from typing import NoReturn

from .errors import ControllerV2Error
from .production_review import (
    AnalysisPackage,
    AuditorWorker,
    REQUIRED_CLEAN_PASSES,
    ReviewIssue,
    ReviewReport,
    ReviewTask,
    _bind_execution_evidence,
    validate_review_issue_binding,
)


REQUEST_SCHEMA = "hbep-producer-audit-request/v1"
RESPONSE_SCHEMA = "hbep-producer-audit-response/v1"
PRODUCER_KINDS = frozenset(
    {"textbook_analysis", "fitted_exam", "exercise_set"}
)
AUDIT_SCOPE = (
    "content_completeness",
    "correctness",
    "diagram_semantics",
    "learner_blockers",
    "reasoning_closure",
    "source_boundary",
)
PRODUCER_OWNED_OUTPUT_ASPECTS = ("formatting", "layout", "style", "voice")
ISSUE_CATEGORIES = frozenset(
    {
        "content_completeness",
        "content_error",
        "correctness",
        "diagram_semantics",
        "learner_blocker",
        "reasoning_closure",
        "source_boundary",
    }
)
_STYLE_CODE_PREFIXES = (
    "FORMAT_",
    "FORMATTING_",
    "LAYOUT_",
    "STYLE_",
    "TONE_",
    "VOICE_",
)
_REQUEST_KEYS = frozenset(
    {
        "schema_version",
        "request_sha256",
        "producer_kind",
        "producer_system_id",
        "candidate_id",
        "candidate_revision",
        "pass_number",
        "package_sha256",
        "artifacts",
        "audit_scope",
        "producer_owned_output_aspects",
        "auditor_output_mode",
    }
)
_ARTIFACT_KEYS = frozenset({"relative_path", "bytes", "sha256"})
_RESPONSE_KEYS = frozenset(
    {
        "schema_version",
        "request_sha256",
        "producer_kind",
        "producer_system_id",
        "candidate_id",
        "candidate_revision",
        "pass_number",
        "package_sha256",
        "auditor_id",
        "coverage_sha256",
        "execution_sha256",
        "complete",
        "decision",
        "next_action",
        "issues",
    }
)
_ISSUE_KEYS = frozenset(
    {
        "issue_sha256",
        "category",
        "code",
        "severity",
        "artifact_path",
        "start",
        "end",
        "exact_quote",
        "problem",
        "required_condition",
        "evidence",
    }
)
_HEX = frozenset("0123456789abcdef")
_MAX_TEXT = 65536
_MAX_ISSUES = 4096


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX for character in value)
    )


def _bounded_text(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_TEXT
        or "\x00" in value
    ):
        raise ValueError(f"{name} must be a bounded non-empty string")
    return value


def _positive_integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class ArtifactBinding:
    relative_path: str
    byte_count: int
    sha256: str

    def __post_init__(self) -> None:
        _bounded_text(self.relative_path, "artifact relative_path")
        path = PurePosixPath(self.relative_path)
        if (
            "\\" in self.relative_path
            or ":" in self.relative_path
            or path.is_absolute()
            or ".." in path.parts
            or str(path) != self.relative_path
        ):
            raise ValueError("artifact relative_path must stay inside the package")
        if self.byte_count < 0:
            raise ValueError("artifact byte_count must not be negative")
        if not _is_sha256(self.sha256):
            raise ValueError("artifact sha256 must be a SHA-256 value")

    def as_dict(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "bytes": self.byte_count,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class ProducerAuditRequest:
    producer_kind: str
    producer_system_id: str
    candidate_id: str
    candidate_revision: int
    pass_number: int
    package_sha256: str
    artifacts: tuple[ArtifactBinding, ...]

    def __post_init__(self) -> None:
        if self.producer_kind not in PRODUCER_KINDS:
            raise ValueError("producer_kind is not supported")
        _bounded_text(self.producer_system_id, "producer_system_id")
        _bounded_text(self.candidate_id, "candidate_id")
        _positive_integer(self.candidate_revision, "candidate_revision")
        if self.pass_number not in range(1, REQUIRED_CLEAN_PASSES + 1):
            raise ValueError("pass_number must be 1")
        if not _is_sha256(self.package_sha256):
            raise ValueError("package_sha256 must be a SHA-256 value")
        if (
            not isinstance(self.artifacts, tuple)
            or not self.artifacts
            or any(not isinstance(item, ArtifactBinding) for item in self.artifacts)
        ):
            raise ValueError("artifacts must contain artifact bindings")
        paths = tuple(item.relative_path for item in self.artifacts)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("artifacts must be unique and sorted")

    @classmethod
    def from_package(
        cls,
        *,
        producer_kind: str,
        producer_system_id: str,
        candidate_id: str,
        candidate_revision: int,
        pass_number: int,
        package: AnalysisPackage,
    ) -> ProducerAuditRequest:
        if not isinstance(package, AnalysisPackage):
            raise TypeError("package must be an AnalysisPackage")
        return cls(
            producer_kind=producer_kind,
            producer_system_id=producer_system_id,
            candidate_id=candidate_id,
            candidate_revision=candidate_revision,
            pass_number=pass_number,
            package_sha256=package.package_sha256,
            artifacts=tuple(
                ArtifactBinding(
                    relative_path=item.relative_path,
                    byte_count=len(item.content),
                    sha256=_sha256(item.content),
                )
                for item in package.files
            ),
        )

    def body_dict(self) -> dict[str, object]:
        return {
            "schema_version": REQUEST_SCHEMA,
            "producer_kind": self.producer_kind,
            "producer_system_id": self.producer_system_id,
            "candidate_id": self.candidate_id,
            "candidate_revision": self.candidate_revision,
            "pass_number": self.pass_number,
            "package_sha256": self.package_sha256,
            "artifacts": [item.as_dict() for item in self.artifacts],
            "audit_scope": list(AUDIT_SCOPE),
            "producer_owned_output_aspects": list(PRODUCER_OWNED_OUTPUT_ASPECTS),
            "auditor_output_mode": "ERRORS_ONLY",
        }

    @property
    def request_sha256(self) -> str:
        return _sha256(canonical_json_bytes(self.body_dict()))

    def as_dict(self) -> dict[str, object]:
        return {**self.body_dict(), "request_sha256": self.request_sha256}

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    def validate_package(self, package: AnalysisPackage) -> None:
        expected = ProducerAuditRequest.from_package(
            producer_kind=self.producer_kind,
            producer_system_id=self.producer_system_id,
            candidate_id=self.candidate_id,
            candidate_revision=self.candidate_revision,
            pass_number=self.pass_number,
            package=package,
        )
        if (
            expected.package_sha256 != self.package_sha256
            or expected.artifacts != self.artifacts
        ):
            raise ControllerV2Error(
                "PRODUCER_AUDIT_PACKAGE_MISMATCH",
                "the audit request does not bind the supplied candidate bytes",
            )


@dataclass(frozen=True, slots=True)
class ProducerAuditIssue:
    category: str
    code: str
    severity: str
    artifact_path: str
    start: int
    end: int
    exact_quote: str
    problem: str
    required_condition: str
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.category not in ISSUE_CATEGORIES:
            raise ValueError("issue category is outside the content-audit scope")
        _reject_style_authority(self.code)
        for name in (
            "code",
            "severity",
            "artifact_path",
            "exact_quote",
            "problem",
            "required_condition",
        ):
            _bounded_text(getattr(self, name), name)
        if self.severity not in {"P0", "P1", "P2", "P3"}:
            raise ValueError("severity must be P0, P1, P2, or P3")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("issue span must be a non-empty bounded interval")
        if (
            not isinstance(self.evidence, tuple)
            or any(not isinstance(item, str) or not item for item in self.evidence)
        ):
            raise ValueError("issue evidence must contain non-empty strings")

    def body_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "code": self.code,
            "severity": self.severity,
            "artifact_path": self.artifact_path,
            "start": self.start,
            "end": self.end,
            "exact_quote": self.exact_quote,
            "problem": self.problem,
            "required_condition": self.required_condition,
            "evidence": list(self.evidence),
        }

    @property
    def issue_sha256(self) -> str:
        return _sha256(canonical_json_bytes(self.body_dict()))

    def as_dict(self) -> dict[str, object]:
        return {**self.body_dict(), "issue_sha256": self.issue_sha256}


@dataclass(frozen=True, slots=True)
class ProducerAuditResponse:
    request_sha256: str
    producer_kind: str
    producer_system_id: str
    candidate_id: str
    candidate_revision: int
    pass_number: int
    package_sha256: str
    auditor_id: str
    coverage_sha256: str
    execution_sha256: str
    complete: bool
    decision: str
    next_action: str
    issues: tuple[ProducerAuditIssue, ...]

    def __post_init__(self) -> None:
        for name in (
            "request_sha256",
            "package_sha256",
            "coverage_sha256",
            "execution_sha256",
        ):
            if not _is_sha256(getattr(self, name)):
                raise ValueError(f"{name} must be a SHA-256 value")
        if self.producer_kind not in PRODUCER_KINDS:
            raise ValueError("producer_kind is not supported")
        _bounded_text(self.producer_system_id, "producer_system_id")
        _bounded_text(self.candidate_id, "candidate_id")
        _bounded_text(self.auditor_id, "auditor_id")
        _positive_integer(self.candidate_revision, "candidate_revision")
        if self.pass_number not in range(1, REQUIRED_CLEAN_PASSES + 1):
            raise ValueError("pass_number must be 1")
        if self.complete is not True:
            raise ValueError("audit response must attest complete execution")
        if (
            not isinstance(self.issues, tuple)
            or len(self.issues) > _MAX_ISSUES
            or any(not isinstance(item, ProducerAuditIssue) for item in self.issues)
        ):
            raise ValueError("issues must be a bounded issue tuple")
        if self.issues:
            expected = ("REVISION_REQUIRED", "PRODUCER_REVISION_REQUIRED")
        elif self.pass_number < REQUIRED_CLEAN_PASSES:
            expected = ("CLEAN", "REPEAT_UNCHANGED_AUDIT")
        else:
            expected = ("CLEAN", "VALIDATE_AUDIT_SEQUENCE")
        if (self.decision, self.next_action) != expected:
            raise ValueError("decision and next_action do not match the audit result")

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": RESPONSE_SCHEMA,
            "request_sha256": self.request_sha256,
            "producer_kind": self.producer_kind,
            "producer_system_id": self.producer_system_id,
            "candidate_id": self.candidate_id,
            "candidate_revision": self.candidate_revision,
            "pass_number": self.pass_number,
            "package_sha256": self.package_sha256,
            "auditor_id": self.auditor_id,
            "coverage_sha256": self.coverage_sha256,
            "execution_sha256": self.execution_sha256,
            "complete": self.complete,
            "decision": self.decision,
            "next_action": self.next_action,
            "issues": [item.as_dict() for item in self.issues],
        }

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())


def parse_producer_audit_request(content: bytes) -> ProducerAuditRequest:
    value = _decode_canonical_object(content, "producer audit request")
    _require_exact_keys(value, _REQUEST_KEYS, "producer audit request")
    if value["schema_version"] != REQUEST_SCHEMA:
        raise ValueError("producer audit request schema is unsupported")
    _require_exact_list(value["audit_scope"], AUDIT_SCOPE, "audit_scope")
    _require_exact_list(
        value["producer_owned_output_aspects"],
        PRODUCER_OWNED_OUTPUT_ASPECTS,
        "producer_owned_output_aspects",
    )
    if value["auditor_output_mode"] != "ERRORS_ONLY":
        raise ValueError("auditor_output_mode must be ERRORS_ONLY")
    artifacts_value = value["artifacts"]
    if not isinstance(artifacts_value, list):
        raise ValueError("artifacts must be a JSON array")
    artifacts: list[ArtifactBinding] = []
    for index, item in enumerate(artifacts_value):
        if not isinstance(item, Mapping):
            raise ValueError(f"artifacts[{index}] must be an object")
        _require_exact_keys(item, _ARTIFACT_KEYS, f"artifacts[{index}]")
        artifacts.append(
            ArtifactBinding(
                relative_path=_bounded_text(
                    item["relative_path"], f"artifacts[{index}].relative_path"
                ),
                byte_count=_nonnegative_integer(
                    item["bytes"], f"artifacts[{index}].bytes"
                ),
                sha256=item["sha256"],
            )
        )
    request = ProducerAuditRequest(
        producer_kind=value["producer_kind"],
        producer_system_id=value["producer_system_id"],
        candidate_id=value["candidate_id"],
        candidate_revision=value["candidate_revision"],
        pass_number=value["pass_number"],
        package_sha256=value["package_sha256"],
        artifacts=tuple(artifacts),
    )
    if value["request_sha256"] != request.request_sha256:
        raise ValueError("producer audit request digest mismatch")
    return request


def parse_producer_audit_response(
    content: bytes,
    *,
    request: ProducerAuditRequest,
) -> ProducerAuditResponse:
    value = _decode_canonical_object(content, "producer audit response")
    _require_exact_keys(value, _RESPONSE_KEYS, "producer audit response")
    if value["schema_version"] != RESPONSE_SCHEMA:
        raise ValueError("producer audit response schema is unsupported")
    issues_value = value["issues"]
    if not isinstance(issues_value, list) or len(issues_value) > _MAX_ISSUES:
        raise ValueError("issues must be a bounded JSON array")
    issues: list[ProducerAuditIssue] = []
    for index, item in enumerate(issues_value):
        if not isinstance(item, Mapping):
            raise ValueError(f"issues[{index}] must be an object")
        _require_exact_keys(item, _ISSUE_KEYS, f"issues[{index}]")
        evidence = item["evidence"]
        if not isinstance(evidence, list):
            raise ValueError(f"issues[{index}].evidence must be an array")
        issue = ProducerAuditIssue(
            category=item["category"],
            code=item["code"],
            severity=item["severity"],
            artifact_path=item["artifact_path"],
            start=_nonnegative_integer(item["start"], f"issues[{index}].start"),
            end=_positive_integer(item["end"], f"issues[{index}].end"),
            exact_quote=item["exact_quote"],
            problem=item["problem"],
            required_condition=item["required_condition"],
            evidence=tuple(evidence),
        )
        if item["issue_sha256"] != issue.issue_sha256:
            raise ValueError(f"issues[{index}] digest mismatch")
        issues.append(issue)
    response = ProducerAuditResponse(
        request_sha256=value["request_sha256"],
        producer_kind=value["producer_kind"],
        producer_system_id=value["producer_system_id"],
        candidate_id=value["candidate_id"],
        candidate_revision=value["candidate_revision"],
        pass_number=value["pass_number"],
        package_sha256=value["package_sha256"],
        auditor_id=value["auditor_id"],
        coverage_sha256=value["coverage_sha256"],
        execution_sha256=value["execution_sha256"],
        complete=value["complete"],
        decision=value["decision"],
        next_action=value["next_action"],
        issues=tuple(issues),
    )
    _validate_response_binding(response, request)
    return response


def run_producer_audit(
    request: ProducerAuditRequest,
    package: AnalysisPackage,
    worker: AuditorWorker,
) -> ProducerAuditResponse:
    if not isinstance(request, ProducerAuditRequest):
        raise TypeError("request must be a ProducerAuditRequest")
    if not isinstance(worker, AuditorWorker):
        raise TypeError("worker must be an AuditorWorker")
    request.validate_package(package)
    task = ReviewTask(
        auditor_id=worker.auditor_id,
        version_number=request.candidate_revision,
        content_sha256=request.package_sha256,
        package=package,
        pass_number=request.pass_number,
    )
    try:
        raw_report = worker.audit(task)
        report = _bind_execution_evidence(raw_report, task, os.urandom(32))
        _validate_report(report, task)
    except (ControllerV2Error, TypeError, ValueError):
        raise
    except Exception as exc:
        raise ControllerV2Error(
            "PRODUCER_AUDIT_EXECUTION_FAILED",
            "the read-only producer audit did not complete",
        ) from exc
    issues = tuple(_producer_issue(item) for item in report.issues)
    decision = "REVISION_REQUIRED" if issues else "CLEAN"
    if issues:
        next_action = "PRODUCER_REVISION_REQUIRED"
    elif request.pass_number < REQUIRED_CLEAN_PASSES:
        next_action = "REPEAT_UNCHANGED_AUDIT"
    else:
        next_action = "VALIDATE_AUDIT_SEQUENCE"
    return ProducerAuditResponse(
        request_sha256=request.request_sha256,
        producer_kind=request.producer_kind,
        producer_system_id=request.producer_system_id,
        candidate_id=request.candidate_id,
        candidate_revision=request.candidate_revision,
        pass_number=request.pass_number,
        package_sha256=request.package_sha256,
        auditor_id=report.auditor_id,
        coverage_sha256=report.coverage_sha256,
        execution_sha256=report.execution_sha256,
        complete=report.complete,
        decision=decision,
        next_action=next_action,
        issues=issues,
    )


def _validate_report(report: ReviewReport, task: ReviewTask) -> None:
    if not isinstance(report, ReviewReport):
        raise TypeError("auditor returned the wrong report type")
    if (
        report.auditor_id != task.auditor_id
        or report.version_number != task.version_number
        or report.content_sha256 != task.content_sha256
        or report.pass_number != task.pass_number
        or report.complete is not True
    ):
        raise ControllerV2Error(
            "PRODUCER_AUDIT_REPORT_MISMATCH",
            "the audit report does not bind the requested candidate",
        )
    for issue in report.issues:
        validate_review_issue_binding(issue, task.package)
        _reject_style_authority(issue.code)


def _producer_issue(issue: ReviewIssue) -> ProducerAuditIssue:
    artifact_path, separator, span = issue.location.rpartition(":")
    start_text, dash, end_text = span.partition("-")
    if not separator or not dash:
        raise ValueError("audit issue location is not an exact source span")
    return ProducerAuditIssue(
        category=_category_for_code(issue.code),
        code=issue.code,
        severity=issue.severity,
        artifact_path=artifact_path,
        start=int(start_text),
        end=int(end_text),
        exact_quote=issue.exact_quote,
        problem=issue.problem,
        required_condition=issue.required_change,
        evidence=issue.evidence,
    )


def _category_for_code(code: str) -> str:
    _reject_style_authority(code)
    if code.startswith("DIAGRAM_"):
        return "diagram_semantics"
    if code.startswith(("NUMERIC_", "MATHEMATICAL_")) or code == "SOURCE_RELATION_CONTRADICTED":
        return "correctness"
    if code.startswith(
        ("MINIMAL_BRIDGE_", "IMPLICIT_", "VISIBLE_REASONING_", "INFERENCE_")
    ):
        return "reasoning_closure"
    if code.startswith(("REQUIRED_", "THEOREM_", "CONVENTION_")):
        return "content_completeness"
    if code.startswith("LEARNER_"):
        return "learner_blocker"
    if code.startswith("SOURCE_"):
        return "source_boundary"
    return "content_error"


def _reject_style_authority(code: object) -> None:
    value = _bounded_text(code, "issue code").upper()
    if value.startswith(_STYLE_CODE_PREFIXES):
        raise ValueError("audit issue attempts to exercise producer-owned style authority")


def _validate_response_binding(
    response: ProducerAuditResponse,
    request: ProducerAuditRequest,
) -> None:
    if (
        response.request_sha256 != request.request_sha256
        or response.producer_kind != request.producer_kind
        or response.producer_system_id != request.producer_system_id
        or response.candidate_id != request.candidate_id
        or response.candidate_revision != request.candidate_revision
        or response.pass_number != request.pass_number
        or response.package_sha256 != request.package_sha256
    ):
        raise ValueError("producer audit response does not bind the request")


def _decode_canonical_object(content: bytes, name: str) -> Mapping[str, object]:
    if not isinstance(content, bytes):
        raise TypeError(f"{name} must be bytes")
    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name} is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    if canonical_json_bytes(value) != content:
        raise ValueError(f"{name} must use canonical JSON encoding")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_nonfinite(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _require_exact_keys(
    value: Mapping[str, object], expected: frozenset[str], name: str
) -> None:
    if set(value) != expected:
        raise ValueError(f"{name} has unknown or missing fields")


def _require_exact_list(value: object, expected: tuple[str, ...], name: str) -> None:
    if value != list(expected):
        raise ValueError(f"{name} does not match the fixed authority boundary")


def _nonnegative_integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


__all__ = [
    "AUDIT_SCOPE",
    "ArtifactBinding",
    "PRODUCER_KINDS",
    "PRODUCER_OWNED_OUTPUT_ASPECTS",
    "ProducerAuditIssue",
    "ProducerAuditRequest",
    "ProducerAuditResponse",
    "canonical_json_bytes",
    "parse_producer_audit_request",
    "parse_producer_audit_response",
    "run_producer_audit",
]
