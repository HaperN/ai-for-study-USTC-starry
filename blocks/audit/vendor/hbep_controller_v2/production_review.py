from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
from types import MappingProxyType
import unicodedata

from .errors import ControllerV2Error
from .path_safety import assert_no_link_components, is_link_like


REQUIRED_CLEAN_PASSES = 1
MAX_ANALYSIS_FILES = 1024
MAX_ANALYSIS_PATH_CHARS = 128
MAX_ANALYSIS_FILE_BYTES = 32 * 1024 * 1024
MAX_ANALYSIS_TOTAL_BYTES = 256 * 1024 * 1024
MAX_REVIEW_ISSUES = 4096
MAX_REVIEW_TEXT_CHARS = 65536
MAX_REVIEW_SEQUENCE_ITEMS = 4096
_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)


class WorkflowStatus(str, Enum):
    NEEDS_ANALYSIS = "NEEDS_ANALYSIS"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    REVIEWING = "REVIEWING"
    REVISION_REQUIRED = "REVISION_REQUIRED"
    BLOCKED = "BLOCKED"
    READY_TO_SUBMIT = "READY_TO_SUBMIT"
    SUBMITTED = "SUBMITTED"


def _unsafe_windows_path_part(part: str) -> bool:
    if (
        not part
        or part[-1:] in {" ", "."}
        or any(ord(character) < 32 for character in part)
        or any(character in '<>"|?*' for character in part)
    ):
        return True
    return part.split(".", 1)[0].upper() in _WINDOWS_DEVICE_NAMES


def _portable_path_key(value: str) -> tuple[str, ...]:
    return tuple(
        unicodedata.normalize("NFC", part).casefold()
        for part in PurePosixPath(value).parts
    )


@dataclass(frozen=True, slots=True)
class AnalysisFile:
    relative_path: str
    content: bytes

    def __post_init__(self) -> None:
        if (
            not isinstance(self.relative_path, str)
            or not self.relative_path
            or len(self.relative_path) > MAX_ANALYSIS_PATH_CHARS
        ):
            raise ValueError("relative_path must be a bounded non-empty string")
        if "\\" in self.relative_path or ":" in self.relative_path:
            raise ValueError("relative_path must use forward slashes")
        path = PurePosixPath(self.relative_path)
        if (
            self.relative_path == "."
            or
            path.is_absolute()
            or ".." in path.parts
            or str(path) != self.relative_path
            or any(_unsafe_windows_path_part(part) for part in path.parts)
        ):
            raise ValueError("relative_path must stay inside the package")
        if not isinstance(self.content, bytes):
            raise TypeError("analysis content must be bytes")
        if len(self.content) > MAX_ANALYSIS_FILE_BYTES:
            raise ValueError("analysis file is too large")


@dataclass(frozen=True, slots=True)
class AnalysisPackage:
    files: tuple[AnalysisFile, ...]
    package_sha256: str = field(init=False)
    _files_by_path: Mapping[str, bytes] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.files, tuple) or not self.files:
            raise ValueError("analysis package must contain files")
        if len(self.files) > MAX_ANALYSIS_FILES:
            raise ValueError("analysis package contains too many files")
        if any(not isinstance(item, AnalysisFile) for item in self.files):
            raise TypeError("analysis package files must be AnalysisFile values")
        ordered = tuple(sorted(self.files, key=lambda item: item.relative_path))
        paths = tuple(item.relative_path for item in ordered)
        if len(paths) != len(set(paths)):
            raise ValueError("analysis package paths must be unique")
        portable = tuple(_portable_path_key(path) for path in paths)
        if len(portable) != len(set(portable)):
            raise ValueError("analysis package paths must be portable")
        portable_set = set(portable)
        if any(
            parts[:prefix_length] in portable_set
            for parts in portable
            for prefix_length in range(1, len(parts))
        ):
            raise ValueError("analysis package paths collide as files and directories")
        if sum(len(item.content) for item in ordered) > MAX_ANALYSIS_TOTAL_BYTES:
            raise ValueError("analysis package is too large")
        object.__setattr__(self, "files", ordered)
        object.__setattr__(self, "_files_by_path", MappingProxyType({item.relative_path: item.content for item in ordered}))
        object.__setattr__(self, "package_sha256", self._calculate_sha256(ordered))

    @classmethod
    def from_files(cls, files: Mapping[str, bytes]) -> AnalysisPackage:
        if not isinstance(files, Mapping):
            raise TypeError("files must be a mapping")
        return cls(tuple(AnalysisFile(path, content) for path, content in files.items()))

    @classmethod
    def from_directory(cls, root: str | Path) -> AnalysisPackage:
        root_path = assert_no_link_components(root, label="analysis root").resolve()
        if is_link_like(root_path) or not root_path.is_dir():
            raise ValueError("analysis root must be a regular directory")
        files: dict[str, bytes] = {}
        total = 0
        for path in sorted(root_path.rglob("*")):
            assert_no_link_components(path, label="analysis package entry")
            if is_link_like(path):
                raise ValueError("analysis package must not contain links")
            if path.is_dir():
                continue
            if not path.is_file():
                raise ValueError("analysis package must contain regular files only")
            expected = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(expected.st_mode) or expected.st_nlink != 1:
                raise ValueError("analysis package must contain regular files only")
            if expected.st_size > MAX_ANALYSIS_FILE_BYTES:
                raise ValueError("analysis package contains an oversized file")
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            try:
                opened = os.fstat(descriptor)
                if (opened.st_dev, opened.st_ino, opened.st_size) != (expected.st_dev, expected.st_ino, expected.st_size):
                    raise ValueError("analysis package changed while reading")
                content = bytearray()
                while True:
                    chunk = os.read(descriptor, 65536)
                    if not chunk:
                        break
                    content.extend(chunk)
                    if len(content) > MAX_ANALYSIS_FILE_BYTES:
                        raise ValueError("analysis package contains an oversized file")
            finally:
                os.close(descriptor)
            total += len(content)
            if total > MAX_ANALYSIS_TOTAL_BYTES or len(files) >= MAX_ANALYSIS_FILES:
                raise ValueError("analysis package exceeds its bounds")
            files[path.relative_to(root_path).as_posix()] = bytes(content)
        return cls.from_files(files)

    def materialize(self, destination: str | Path) -> Path:
        destination_path = assert_no_link_components(destination, label="analysis destination")
        if destination_path.exists() or is_link_like(destination_path):
            raise FileExistsError(destination_path)
        destination_path.mkdir(parents=True)
        try:
            for item in self.files:
                output = destination_path.joinpath(*PurePosixPath(item.relative_path).parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                with output.open("xb") as handle:
                    handle.write(item.content)
                    handle.flush()
                    os.fsync(handle.fileno())
            materialized = AnalysisPackage.from_directory(destination_path)
            if materialized.package_sha256 != self.package_sha256:
                raise ValueError("materialized package identity changed")
            return destination_path
        except Exception:
            shutil.rmtree(destination_path, ignore_errors=True)
            raise

    def read_bytes(self, relative_path: str) -> bytes:
        try:
            return self._files_by_path[relative_path]
        except KeyError:
            raise KeyError(relative_path) from None

    def single_file_content(self) -> bytes:
        if len(self.files) != 1:
            raise ValueError("analysis contains more than one file")
        return self.files[0].content

    @staticmethod
    def _calculate_sha256(files: tuple[AnalysisFile, ...]) -> str:
        digest = hashlib.sha256(b"HBEP-PRODUCTION-ANALYSIS/1\0")
        for item in files:
            path = item.relative_path.encode("utf-8")
            digest.update(len(path).to_bytes(8, "big"))
            digest.update(path)
            digest.update(len(item.content).to_bytes(8, "big"))
            digest.update(item.content)
        return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class ReviewIssue:
    location: str
    problem: str
    required_change: str
    code: str = "UNSPECIFIED"
    exact_quote: str = ""
    severity: str = "P1"
    producers: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("location", "problem", "required_change", "code", "exact_quote"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or len(value) > MAX_REVIEW_TEXT_CHARS:
                raise ValueError(f"{field_name} must be a bounded string")
            if field_name != "exact_quote" and not value.strip():
                raise ValueError(f"{field_name} must be non-empty")
        if self.severity not in {"P0", "P1", "P2", "P3"}:
            raise ValueError("severity must be P0, P1, P2, or P3")
        for field_name in ("producers", "evidence"):
            value = getattr(self, field_name)
            if (
                not isinstance(value, tuple)
                or len(value) > MAX_REVIEW_SEQUENCE_ITEMS
                or any(not isinstance(item, str) or not item or len(item) > MAX_REVIEW_TEXT_CHARS for item in value)
            ):
                raise ValueError(f"{field_name} must contain bounded strings")


@dataclass(frozen=True, slots=True)
class AnalysisVersion:
    version_number: int
    content_sha256: str
    package: AnalysisPackage

    @property
    def content(self) -> bytes:
        return self.package.single_file_content()


@dataclass(frozen=True, slots=True)
class ReviewTask:
    auditor_id: str
    version_number: int
    content_sha256: str
    package: AnalysisPackage
    pass_number: int


@dataclass(frozen=True, slots=True)
class ReviewReport:
    auditor_id: str
    version_number: int
    content_sha256: str
    pass_number: int
    issues: tuple[ReviewIssue, ...]
    coverage_sha256: str
    execution_sha256: str
    complete: bool

    def __post_init__(self) -> None:
        if not self.auditor_id or not isinstance(self.auditor_id, str):
            raise ValueError("auditor_id must be non-empty")
        if self.pass_number not in range(1, REQUIRED_CLEAN_PASSES + 1):
            raise ValueError("pass_number is outside the required clean sequence")
        if not isinstance(self.issues, tuple) or len(self.issues) > MAX_REVIEW_ISSUES:
            raise ValueError("issues must be a bounded tuple")
        if any(not isinstance(issue, ReviewIssue) for issue in self.issues):
            raise TypeError("issues must contain ReviewIssue values")
        if len(self.coverage_sha256) != 64 or any(character not in "0123456789abcdef" for character in self.coverage_sha256):
            raise ValueError("coverage_sha256 must be a SHA-256 value")
        if len(self.execution_sha256) != 64 or any(character not in "0123456789abcdef" for character in self.execution_sha256):
            raise ValueError("execution_sha256 must be a SHA-256 value")
        if self.complete is not True:
            raise ValueError("review report must attest complete coverage")


AuditFunction = Callable[[ReviewTask], ReviewReport]


@dataclass(frozen=True, slots=True)
class AuditorWorker:
    auditor_id: str
    audit: AuditFunction

    def __post_init__(self) -> None:
        if not isinstance(self.auditor_id, str) or not self.auditor_id.strip():
            raise ValueError("auditor_id must be non-empty")
        if not callable(self.audit):
            raise TypeError("audit must be callable")


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    status: WorkflowStatus
    version_number: int
    content_sha256: str
    report: ReviewReport
    issues: tuple[ReviewIssue, ...]
    clean_pass_count: int

    @property
    def reports(self) -> tuple[ReviewReport, ...]:
        return (self.report,)


AnalysisInput = bytes | AnalysisPackage


@dataclass(frozen=True, slots=True)
class ReviewCompletion:
    version_number: int
    content_sha256: str
    package: AnalysisPackage
    clean_reports: tuple[ReviewReport, ...]

    def __post_init__(self) -> None:
        if self.content_sha256 != self.package.package_sha256:
            raise ValueError("completion package identity is invalid")
        if len(self.clean_reports) != REQUIRED_CLEAN_PASSES:
            raise ValueError("completion requires exactly one clean report")


class ProductionReviewLoop:
    """Audit externally produced versions and accept one complete clean pass."""

    def __init__(self) -> None:
        self._status = WorkflowStatus.NEEDS_ANALYSIS
        self._current: AnalysisVersion | None = None
        self._clean_reports: list[ReviewReport] = []
        self._last_decision: ReviewDecision | None = None

    @classmethod
    def from_checkpoint(
        cls,
        version: AnalysisVersion,
        clean_reports: tuple[ReviewReport, ...] = (),
    ) -> ProductionReviewLoop:
        if not isinstance(version, AnalysisVersion):
            raise TypeError("version must be an AnalysisVersion")
        if len(clean_reports) >= REQUIRED_CLEAN_PASSES:
            raise ValueError("a completed clean sequence is not resumable")
        for index, report in enumerate(clean_reports, start=1):
            if (
                not isinstance(report, ReviewReport)
                or report.pass_number != index
                or report.issues
                or report.version_number != version.version_number
                or report.content_sha256 != version.content_sha256
            ):
                raise ValueError("saved clean report does not match the checkpoint")
        loop = cls()
        loop._current = version
        loop._clean_reports = list(clean_reports)
        loop._status = WorkflowStatus.READY_FOR_REVIEW
        return loop

    @property
    def status(self) -> WorkflowStatus:
        return self._status

    @property
    def current_version(self) -> AnalysisVersion | None:
        return self._current

    @property
    def clean_reports(self) -> tuple[ReviewReport, ...]:
        return tuple(self._clean_reports)

    def add_initial_analysis(self, content: AnalysisInput) -> AnalysisVersion:
        if self._status is not WorkflowStatus.NEEDS_ANALYSIS:
            raise ControllerV2Error("INITIAL_ANALYSIS_ALREADY_SET", "initial analysis can be added only once")
        package = self._require_package(content)
        self._current = AnalysisVersion(1, package.package_sha256, package)
        self._status = WorkflowStatus.READY_FOR_REVIEW
        return self._current

    def review_current(self, worker: AuditorWorker) -> ReviewDecision:
        if self._status is not WorkflowStatus.READY_FOR_REVIEW:
            raise ControllerV2Error("REVIEW_NOT_ALLOWED", f"cannot audit while status is {self._status.value}")
        if not isinstance(worker, AuditorWorker):
            raise TypeError("worker must be an AuditorWorker")
        current = self._require_current()
        pass_number = len(self._clean_reports) + 1
        task = ReviewTask(worker.auditor_id, current.version_number, current.content_sha256, current.package, pass_number)
        self._status = WorkflowStatus.REVIEWING
        try:
            raw_report = worker.audit(task)
            report = _bind_execution_evidence(raw_report, task, os.urandom(32))
            self._validate_report(report, worker, current, pass_number)
        except ControllerV2Error:
            self._status = WorkflowStatus.READY_FOR_REVIEW
            raise
        except Exception as exc:
            self._status = WorkflowStatus.READY_FOR_REVIEW
            raise ControllerV2Error("REVIEW_EXECUTION_FAILED", "the unified audit did not complete") from exc
        issues = merge_review_issues((report,))
        if issues:
            self._clean_reports.clear()
            status = WorkflowStatus.REVISION_REQUIRED
        else:
            self._clean_reports.append(report)
            status = WorkflowStatus.READY_TO_SUBMIT if len(self._clean_reports) == REQUIRED_CLEAN_PASSES else WorkflowStatus.READY_FOR_REVIEW
        decision = ReviewDecision(status, current.version_number, current.content_sha256, report, issues, len(self._clean_reports))
        self._last_decision = decision
        self._status = status
        return decision

    def add_revision(self, content: AnalysisInput) -> AnalysisVersion:
        if self._status is not WorkflowStatus.REVISION_REQUIRED:
            raise ControllerV2Error("REVISION_NOT_ALLOWED", "revision requires reported audit issues")
        current = self._require_current()
        package = self._require_package(content)
        if package.package_sha256 == current.content_sha256:
            raise ControllerV2Error("REVISION_UNCHANGED", "revision must change the package bytes")
        self._current = AnalysisVersion(current.version_number + 1, package.package_sha256, package)
        self._clean_reports.clear()
        self._status = WorkflowStatus.READY_FOR_REVIEW
        return self._current

    def complete(self) -> ReviewCompletion:
        if self._status is not WorkflowStatus.READY_TO_SUBMIT:
            raise ControllerV2Error("SUBMISSION_NOT_ALLOWED", "submission requires one complete clean audit of the same version")
        current = self._require_current()
        _validate_clean_reports(tuple(self._clean_reports), current)
        self._status = WorkflowStatus.SUBMITTED
        return ReviewCompletion(current.version_number, current.content_sha256, current.package, tuple(self._clean_reports))

    @staticmethod
    def _require_package(content: AnalysisInput) -> AnalysisPackage:
        if isinstance(content, AnalysisPackage):
            return content
        if isinstance(content, bytes) and content:
            return AnalysisPackage.from_files({"analysis.md": content})
        raise TypeError("analysis must be non-empty bytes or an AnalysisPackage")

    def _require_current(self) -> AnalysisVersion:
        if self._current is None:
            raise ControllerV2Error("ANALYSIS_MISSING", "no analysis is available")
        return self._current

    @staticmethod
    def _validate_report(report: ReviewReport, worker: AuditorWorker, current: AnalysisVersion, pass_number: int) -> None:
        if not isinstance(report, ReviewReport):
            raise ControllerV2Error("REVIEW_REPORT_INVALID", "auditor returned an invalid report")
        if (
            report.auditor_id != worker.auditor_id
            or report.version_number != current.version_number
            or report.content_sha256 != current.content_sha256
            or report.pass_number != pass_number
            or report.complete is not True
        ):
            raise ControllerV2Error("REVIEW_REPORT_IDENTITY_MISMATCH", "audit report does not match the assigned frozen version and pass")
        for issue in report.issues:
            validate_review_issue_binding(issue, current.package)


def merge_review_issues(reports: tuple[ReviewReport, ...]) -> tuple[ReviewIssue, ...]:
    result: list[ReviewIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for report in reports:
        for issue in report.issues:
            key = (issue.code, issue.location, issue.exact_quote)
            if key not in seen:
                seen.add(key)
                result.append(issue)
    return tuple(result)


def completed_review_report(
    task: ReviewTask,
    issues: tuple[ReviewIssue, ...],
    *,
    coverage_items: tuple[str, ...] = ("complete-audit",),
) -> ReviewReport:
    if (
        not isinstance(coverage_items, tuple)
        or not coverage_items
        or len(coverage_items) != len(set(coverage_items))
        or any(not isinstance(item, str) or not item for item in coverage_items)
    ):
        raise ValueError("coverage_items must be unique non-empty strings")
    digest = hashlib.sha256(b"HBEP-UNIFIED-AUDIT-COVERAGE/1\0")
    for value in (
        task.auditor_id,
        str(task.version_number),
        task.content_sha256,
        *coverage_items,
    ):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return ReviewReport(
        task.auditor_id,
        task.version_number,
        task.content_sha256,
        task.pass_number,
        issues,
        digest.hexdigest(),
        "0" * 64,
        True,
    )


def _bind_execution_evidence(report: ReviewReport, task: ReviewTask, nonce: bytes) -> ReviewReport:
    if not isinstance(report, ReviewReport):
        return report
    digest = hashlib.sha256(b"HBEP-UNIFIED-AUDIT-EXECUTION/1\0" + nonce)
    digest.update(report.coverage_sha256.encode("ascii"))
    digest.update(task.content_sha256.encode("ascii"))
    digest.update(str(task.pass_number).encode("ascii"))
    for issue in report.issues:
        digest.update(issue.code.encode("utf-8"))
        digest.update(issue.location.encode("utf-8"))
        digest.update(issue.exact_quote.encode("utf-8"))
    return replace(report, execution_sha256=digest.hexdigest())


def _validate_clean_reports(reports: tuple[ReviewReport, ...], current: AnalysisVersion) -> None:
    if len(reports) != REQUIRED_CLEAN_PASSES:
        raise ControllerV2Error("SUBMISSION_EVIDENCE_INCOMPLETE", "one clean audit report is required")
    if tuple(report.pass_number for report in reports) != tuple(
        range(1, REQUIRED_CLEAN_PASSES + 1)
    ):
        raise ControllerV2Error("SUBMISSION_EVIDENCE_INCOMPLETE", "clean audit passes must be consecutive")
    if len({report.coverage_sha256 for report in reports}) != 1:
        raise ControllerV2Error("SUBMISSION_EVIDENCE_INCOMPLETE", "clean audit passes must cover the same audit program")
    if len({report.execution_sha256 for report in reports}) != REQUIRED_CLEAN_PASSES:
        raise ControllerV2Error("SUBMISSION_EVIDENCE_INCOMPLETE", "clean audit passes must come from separate executions")
    for report in reports:
        if report.issues or report.content_sha256 != current.content_sha256 or report.version_number != current.version_number:
            raise ControllerV2Error("SUBMISSION_EVIDENCE_INCOMPLETE", "clean audit reports do not bind the submitted version")


def validate_review_issue_binding(issue: ReviewIssue, package: AnalysisPackage) -> None:
    relative_path, separator, span = issue.location.rpartition(":")
    start_text, dash, end_text = span.partition("-")
    if not separator or not dash or not relative_path or not start_text.isdecimal() or not end_text.isdecimal() or not issue.exact_quote:
        raise ControllerV2Error("REVIEW_ISSUE_SOURCE_MISMATCH", "audit issue lacks an exact source location")
    start, end = int(start_text), int(end_text)
    try:
        source = package.read_bytes(relative_path).decode("utf-8")
    except (KeyError, UnicodeDecodeError) as exc:
        raise ControllerV2Error("REVIEW_ISSUE_SOURCE_MISMATCH", "audit issue refers to unavailable source text") from exc
    if start < 0 or end <= start or end > len(source) or source[start:end] != issue.exact_quote:
        raise ControllerV2Error("REVIEW_ISSUE_SOURCE_MISMATCH", "audit issue does not match the frozen source bytes")


__all__ = [
    "AnalysisFile",
    "AnalysisPackage",
    "AnalysisVersion",
    "AuditorWorker",
    "ProductionReviewLoop",
    "REQUIRED_CLEAN_PASSES",
    "ReviewCompletion",
    "ReviewDecision",
    "ReviewIssue",
    "ReviewReport",
    "ReviewTask",
    "WorkflowStatus",
    "completed_review_report",
    "merge_review_issues",
    "validate_review_issue_binding",
]
