from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, urlsplit


RUN_SCHEMA = "local-source-acquisition-run-v1"
SUPPORTED_PLATFORMS = frozenset(
    {"bilibili", "douyin", "wechat_official_accounts"}
)
_PLATFORM_KIND = {
    "bilibili": "video",
    "douyin": "video",
    "wechat_official_accounts": "article",
}
_PLATFORM_TARGET = {
    "bilibili": "public_video_sources",
    "douyin": "public_video_sources",
    "wechat_official_accounts": "public_article_sources",
}
_PLATFORM_HOST_SUFFIXES = {
    "bilibili": ("bilibili.com",),
    "douyin": ("douyin.com",),
    "wechat_official_accounts": ("mp.weixin.qq.com",),
}
_CORE_KEYS = {
    "schema_version",
    "run_id",
    "batch_index",
    "previous_run_id",
    "request_id",
    "execution_mode",
    "account_owner",
    "login_actor",
    "credential_storage",
    "platform",
    "target",
    "query",
    "status",
    "user_action_required",
    "started_at",
    "completed_at",
    "working_directory",
    "output_directory",
    "pagination",
    "continuation",
    "source_ids",
    "result_count",
    "results",
    "central_service_used",
    "publisher_operated_service_used",
    "credential_material_exported",
    "telemetry_used",
    "remote_auto_update_used",
}
_OPTIONAL_KEYS = {
    "runtime_contract_sha256",
    "request_sha256",
    "adapter_executable_sha256",
    "adapter_argv_sha256",
    "adapter_stdout_sha256",
    "adapter_receipt_input_sha256",
    "adapter_exit_code",
    "receipt_origin",
    "frozen_at",
}
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?:--)?(?:access[_-]?token|token|api[_-]?key|cookie|password|passwd|"
    r"secret|authorization|session[_-]?(?:id|key|token))\s*(?:=|:)\s*\S+"
)
_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "cookie",
    "key",
    "password",
    "secret",
    "session",
    "sessionid",
    "ticket",
    "token",
}


class LocalAcquisitionReceiptError(ValueError):
    """Raised when copied user-local acquisition evidence is incomplete or unsafe."""


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise LocalAcquisitionReceiptError(f"{label}: expected object")
    return dict(value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LocalAcquisitionReceiptError(f"{label}: expected nonempty string")
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise LocalAcquisitionReceiptError(f"{label}: control character forbidden")
    return value.strip()


def _id(value: object, label: str) -> str:
    text = _string(value, label)
    if _SAFE_ID.fullmatch(text) is None:
        raise LocalAcquisitionReceiptError(f"{label}: invalid identifier")
    return text


def _timestamp(value: object, label: str) -> datetime:
    text = _string(value, label)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise LocalAcquisitionReceiptError(f"{label}: invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LocalAcquisitionReceiptError(f"{label}: timezone is required")
    return parsed


def _relative_path(value: object, label: str) -> str:
    text = _string(value, label).replace("\\", "/")
    path = PurePosixPath(text)
    if path.is_absolute() or re.match(r"^[A-Za-z]:", text) or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise LocalAcquisitionReceiptError(f"{label}: normalized relative path required")
    return path.as_posix()


def _reject_secret(text: str, label: str) -> None:
    if _SECRET_ASSIGNMENT.search(text):
        raise LocalAcquisitionReceiptError(f"{label}: secret assignment forbidden")


def _url(value: object, *, platform: str, label: str) -> str:
    text = _string(value, label)
    _reject_secret(text, label)
    parsed = urlsplit(text)
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or not any(
            host == suffix or host.endswith("." + suffix)
            for suffix in _PLATFORM_HOST_SUFFIXES[platform]
        )
        or any(key.casefold() in _SENSITIVE_QUERY_KEYS for key, _ in parse_qsl(parsed.query))
    ):
        raise LocalAcquisitionReceiptError(f"{label}: unsupported or sensitive URL")
    return text


def _exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise LocalAcquisitionReceiptError(f"{label}: exact fields required")


def validate_portable_local_source_receipt(
    receipt: Mapping[str, object],
    *,
    expected_source_id: str,
    expected_platform: str,
    expected_kind: str,
    expected_url: str,
    expected_snapshot_sha256: str,
) -> dict[str, object]:
    """Validate one copied local run and its exact source binding without local paths."""

    payload = _object(receipt, "receipt")
    missing = _CORE_KEYS - set(payload)
    unknown = set(payload) - _CORE_KEYS - _OPTIONAL_KEYS
    if missing or unknown:
        raise LocalAcquisitionReceiptError("receipt: exact core fields required")
    if payload.get("schema_version") != RUN_SCHEMA:
        raise LocalAcquisitionReceiptError(f"receipt.schema_version: expected {RUN_SCHEMA}")
    run_id = _id(payload.get("run_id"), "receipt.run_id")
    _id(payload.get("request_id"), "receipt.request_id")
    batch_index = payload.get("batch_index")
    if not isinstance(batch_index, int) or isinstance(batch_index, bool) or batch_index < 1:
        raise LocalAcquisitionReceiptError("receipt.batch_index: positive integer required")
    if batch_index == 1:
        if payload.get("previous_run_id") is not None:
            raise LocalAcquisitionReceiptError("receipt.previous_run_id: first batch must be null")
    else:
        previous = _id(payload.get("previous_run_id"), "receipt.previous_run_id")
        if previous == run_id:
            raise LocalAcquisitionReceiptError("receipt.previous_run_id: self-reference forbidden")
    for field, expected in {
        "execution_mode": "per_user_local",
        "account_owner": "end_user",
        "login_actor": "end_user",
        "credential_storage": "local_only",
    }.items():
        if payload.get(field) != expected:
            raise LocalAcquisitionReceiptError(f"receipt.{field}: expected {expected}")
    for field in (
        "central_service_used",
        "publisher_operated_service_used",
        "credential_material_exported",
        "telemetry_used",
        "remote_auto_update_used",
    ):
        if payload.get(field) is not False:
            raise LocalAcquisitionReceiptError(f"receipt.{field}: expected false")

    platform = _string(payload.get("platform"), "receipt.platform")
    if platform not in SUPPORTED_PLATFORMS or platform != expected_platform:
        raise LocalAcquisitionReceiptError("receipt.platform: supported platform mismatch")
    if payload.get("target") != _PLATFORM_TARGET[platform]:
        raise LocalAcquisitionReceiptError("receipt.target: platform mismatch")
    if expected_kind != _PLATFORM_KIND[platform]:
        raise LocalAcquisitionReceiptError("expected_kind: platform mismatch")
    query = _string(payload.get("query"), "receipt.query")
    _reject_secret(query, "receipt.query")
    if payload.get("status") != "completed" or payload.get("user_action_required") is not None:
        raise LocalAcquisitionReceiptError("receipt.status: completed batch required")
    started = _timestamp(payload.get("started_at"), "receipt.started_at")
    completed = _timestamp(payload.get("completed_at"), "receipt.completed_at")
    if completed < started:
        raise LocalAcquisitionReceiptError("receipt.completed_at: precedes started_at")
    _relative_path(payload.get("working_directory"), "receipt.working_directory")
    _relative_path(payload.get("output_directory"), "receipt.output_directory")

    pagination = _object(payload.get("pagination"), "receipt.pagination")
    _exact_keys(pagination, {"pages_fetched", "complete", "termination_reason"}, "receipt.pagination")
    pages = pagination.get("pages_fetched")
    if not isinstance(pages, int) or isinstance(pages, bool) or pages < 1:
        raise LocalAcquisitionReceiptError("receipt.pagination.pages_fetched: positive integer required")
    if not isinstance(pagination.get("complete"), bool):
        raise LocalAcquisitionReceiptError("receipt.pagination.complete: boolean required")
    termination = _string(pagination.get("termination_reason"), "receipt.pagination.termination_reason")
    continuation = _object(payload.get("continuation"), "receipt.continuation")
    _exact_keys(
        continuation,
        {"status", "next_cursor", "platform_exhausted", "consecutive_no_new_batches", "stop_condition"},
        "receipt.continuation",
    )
    status = _string(continuation.get("status"), "receipt.continuation.status")
    cursor_raw = continuation.get("next_cursor")
    cursor = None if cursor_raw is None else _string(cursor_raw, "receipt.continuation.next_cursor")
    if cursor is not None:
        _reject_secret(cursor, "receipt.continuation.next_cursor")
    exhausted = continuation.get("platform_exhausted")
    no_new = continuation.get("consecutive_no_new_batches")
    stop = _string(continuation.get("stop_condition"), "receipt.continuation.stop_condition")
    if not isinstance(exhausted, bool) or not isinstance(no_new, int) or isinstance(no_new, bool) or no_new < 0:
        raise LocalAcquisitionReceiptError("receipt.continuation: invalid state types")
    if status == "continue":
        if pagination["complete"] is not False or termination != "batch_limit_reached" or cursor is None or exhausted or stop != "batch_limit_reached":
            raise LocalAcquisitionReceiptError("receipt.continuation: invalid continuing batch")
    elif status == "complete":
        if pagination["complete"] is not True or cursor is not None:
            raise LocalAcquisitionReceiptError("receipt.continuation: invalid terminal batch")
        if exhausted:
            if stop != "platform_exhausted" or termination not in {"end_of_results", "no_results", "platform_declared_end"}:
                raise LocalAcquisitionReceiptError("receipt.continuation: invalid platform exhaustion")
        elif stop != "consecutive_no_new" or termination != "consecutive_no_new" or no_new < 1:
            raise LocalAcquisitionReceiptError("receipt.continuation: invalid no-new stop")
    else:
        raise LocalAcquisitionReceiptError("receipt.continuation.status: unsupported")

    source_ids_raw = payload.get("source_ids")
    results_raw = payload.get("results")
    if not isinstance(source_ids_raw, list) or not isinstance(results_raw, list):
        raise LocalAcquisitionReceiptError("receipt.source_ids/results: lists required")
    source_ids = [_id(value, f"receipt.source_ids[{index}]") for index, value in enumerate(source_ids_raw)]
    if len(source_ids) != len(set(source_ids)):
        raise LocalAcquisitionReceiptError("receipt.source_ids: duplicates forbidden")
    result_count = payload.get("result_count")
    if not isinstance(result_count, int) or isinstance(result_count, bool) or result_count < 0 or result_count != len(source_ids) or result_count != len(results_raw):
        raise LocalAcquisitionReceiptError("receipt.result_count: list lengths mismatch")
    if result_count > 0 and no_new != 0:
        raise LocalAcquisitionReceiptError("receipt.continuation: results must reset no-new counter")
    expected_id = _id(expected_source_id, "expected_source_id")
    expected_canonical_url = _url(expected_url, platform=platform, label="expected_url")
    expected_sha = _string(expected_snapshot_sha256, "expected_snapshot_sha256").casefold()
    if _SHA256.fullmatch(expected_sha) is None:
        raise LocalAcquisitionReceiptError("expected_snapshot_sha256: invalid")
    normalized_results: list[dict[str, object]] = []
    matches: list[dict[str, object]] = []
    result_ids: list[str] = []
    for index, raw_result in enumerate(results_raw):
        result = _object(raw_result, f"receipt.results[{index}]")
        _exact_keys(result, {"source_id", "canonical_url", "source_kind", "snapshot"}, f"receipt.results[{index}]")
        source_id = _id(result.get("source_id"), f"receipt.results[{index}].source_id")
        source_kind = _string(result.get("source_kind"), f"receipt.results[{index}].source_kind")
        if source_kind != _PLATFORM_KIND[platform]:
            raise LocalAcquisitionReceiptError("receipt.results: source kind mismatch")
        canonical_url = _url(result.get("canonical_url"), platform=platform, label=f"receipt.results[{index}].canonical_url")
        snapshot = _object(result.get("snapshot"), f"receipt.results[{index}].snapshot")
        _exact_keys(snapshot, {"path", "sha256"}, f"receipt.results[{index}].snapshot")
        snapshot_path = _relative_path(snapshot.get("path"), f"receipt.results[{index}].snapshot.path")
        snapshot_sha = _string(snapshot.get("sha256"), f"receipt.results[{index}].snapshot.sha256").casefold()
        if _SHA256.fullmatch(snapshot_sha) is None:
            raise LocalAcquisitionReceiptError("receipt.results: invalid snapshot SHA-256")
        normalized = {
            "source_id": source_id,
            "canonical_url": canonical_url,
            "source_kind": source_kind,
            "snapshot": {"path": snapshot_path, "sha256": snapshot_sha},
        }
        result_ids.append(source_id)
        normalized_results.append(normalized)
        if source_id == expected_id:
            matches.append(normalized)
    if result_ids != source_ids:
        raise LocalAcquisitionReceiptError("receipt.source_ids: must match result order")
    if len(matches) != 1:
        raise LocalAcquisitionReceiptError("receipt.results: expected source must occur exactly once")
    source = matches[0]
    if source["source_kind"] != expected_kind or source["canonical_url"] != expected_canonical_url or source["snapshot"]["sha256"] != expected_sha:
        raise LocalAcquisitionReceiptError("receipt.results: expected source binding does not match")

    for field in _OPTIONAL_KEYS & set(payload):
        if field.endswith("sha256"):
            digest = _string(payload[field], f"receipt.{field}").casefold()
            if _SHA256.fullmatch(digest) is None:
                raise LocalAcquisitionReceiptError(f"receipt.{field}: invalid SHA-256")
    if "adapter_exit_code" in payload and payload["adapter_exit_code"] != 0:
        raise LocalAcquisitionReceiptError("receipt.adapter_exit_code: expected zero")
    if "receipt_origin" in payload and payload["receipt_origin"] not in {"executed_adapter", "registered_existing"}:
        raise LocalAcquisitionReceiptError("receipt.receipt_origin: unsupported")
    if "frozen_at" in payload:
        _timestamp(payload["frozen_at"], "receipt.frozen_at")
    return {"receipt": {**payload, "results": normalized_results}, "source": source}


__all__ = (
    "LocalAcquisitionReceiptError",
    "RUN_SCHEMA",
    "SUPPORTED_PLATFORMS",
    "validate_portable_local_source_receipt",
)
