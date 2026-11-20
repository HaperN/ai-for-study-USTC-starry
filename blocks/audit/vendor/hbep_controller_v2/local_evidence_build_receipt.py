from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path


BUILD_RECEIPT_SCHEMA = "local-evidence-build-receipt-v1"
CODEX_EXECUTION_SCHEMA = "codex-local-review-execution-v1"


class LocalEvidenceBuildReceiptError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LocalEvidenceBuildReceiptError(f"{label}: expected UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise LocalEvidenceBuildReceiptError(f"{label}: expected object")
    return value


def _resolve_binding(
    root: Path, raw: object, label: str
) -> tuple[Path, dict[str, str]]:
    if not isinstance(raw, Mapping) or not {"path", "sha256"} <= set(raw):
        raise LocalEvidenceBuildReceiptError(f"{label}: expected file binding")
    relative_raw = raw.get("path")
    if not isinstance(relative_raw, str) or not relative_raw.strip():
        raise LocalEvidenceBuildReceiptError(f"{label}.path: required")
    relative = Path(relative_raw.replace("\\", "/"))
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise LocalEvidenceBuildReceiptError(f"{label}.path: expected normalized relative path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
        raise LocalEvidenceBuildReceiptError(f"{label}.path: unavailable or escapes bundle")
    digest = _sha256(path)
    if raw.get("sha256") != digest:
        raise LocalEvidenceBuildReceiptError(f"{label}.sha256: mismatch")
    return path, {"path": str(path), "sha256": digest, "role": "local_evidence_build_artifact"}


def validate_local_evidence_build_receipt(
    *,
    root: Path,
    receipt_path: Path,
    source_id: str,
    source_kind: str,
    expected_builder_path: Path,
) -> list[dict[str, str]]:
    root = Path(root).resolve()
    receipt_path = Path(receipt_path).resolve()
    expected_builder_path = Path(expected_builder_path).resolve()
    receipt = _json(receipt_path, "local evidence build receipt")
    required = {
        "schema_version",
        "source_id",
        "source_kind",
        "status",
        "receipt_origin",
        "builder_program",
        "request",
        "snapshot_manifest",
        "requires_executed_adapter_receipt",
        "stage_order",
        "stages",
        "semantics",
        "artifact_bindings",
    }
    if set(receipt) != required or receipt.get("schema_version") != BUILD_RECEIPT_SCHEMA:
        raise LocalEvidenceBuildReceiptError("local evidence build receipt schema is invalid")
    if (
        receipt.get("source_id") != source_id
        or receipt.get("source_kind") != source_kind
        or receipt.get("status") != "completed"
        or receipt.get("receipt_origin") != "executed_local_evidence_builder"
        or receipt.get("requires_executed_adapter_receipt") is not True
    ):
        raise LocalEvidenceBuildReceiptError("local evidence build receipt identity is invalid")
    program = receipt.get("builder_program")
    if not isinstance(program, Mapping) or set(program) != {"module", "path", "sha256"}:
        raise LocalEvidenceBuildReceiptError("local evidence builder identity is invalid")
    declared_builder = Path(str(program.get("path", ""))).resolve()
    if (
        program.get("module") != "stem_runner.local_evidence_builder"
        or declared_builder != expected_builder_path
        or not expected_builder_path.is_file()
        or program.get("sha256") != _sha256(expected_builder_path)
    ):
        raise LocalEvidenceBuildReceiptError("local evidence builder program hash is stale")

    _resolve_binding(root, receipt["request"], "receipt.request")
    _resolve_binding(root, receipt["snapshot_manifest"], "receipt.snapshot_manifest")
    raw_artifacts = receipt.get("artifact_bindings")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise LocalEvidenceBuildReceiptError("receipt.artifact_bindings: expected nonempty list")
    artifacts: list[dict[str, str]] = []
    artifact_paths: set[Path] = set()
    for index, raw in enumerate(raw_artifacts):
        path, binding = _resolve_binding(root, raw, f"receipt.artifact_bindings[{index}]")
        if path in artifact_paths:
            raise LocalEvidenceBuildReceiptError("receipt.artifact_bindings: duplicate path")
        artifact_paths.add(path)
        artifacts.append(binding)

    def nested(raw: object, label: str) -> Path:
        path, _ = _resolve_binding(root, raw, label)
        if path not in artifact_paths:
            raise LocalEvidenceBuildReceiptError(f"{label}: absent from artifact inventory")
        return path

    stages = receipt.get("stages")
    order = receipt.get("stage_order")
    if not isinstance(stages, list) or not isinstance(order, list):
        raise LocalEvidenceBuildReceiptError("receipt stages/order are invalid")
    if [stage.get("stage_id") if isinstance(stage, Mapping) else None for stage in stages] != order:
        raise LocalEvidenceBuildReceiptError("receipt stage order is not exact")
    if source_kind == "article":
        if order != [
            "browser_acquisition",
            "article_body_extraction",
            "article_image_inventory",
            "codex_article_read",
        ]:
            raise LocalEvidenceBuildReceiptError("article build receipt stage order is incomplete")
    elif source_kind == "video":
        if (
            not order
            or order[:5]
            != [
                "platform_acquisition",
                "media_acquisition",
                "full_stream_decode",
                "local_asr",
                "frame_sampling",
            ]
            or order[-1:] != ["codex_video_synthesis"]
        ):
            raise LocalEvidenceBuildReceiptError("video build receipt stage order is incomplete")
        batches = order[5:-1]
        if not batches or batches != [
            f"codex_frame_batch_{index:04d}" for index in range(1, len(batches) + 1)
        ]:
            raise LocalEvidenceBuildReceiptError("video frame-batch sequence is incomplete")
    else:
        raise LocalEvidenceBuildReceiptError("unsupported source kind")

    semantics = receipt.get("semantics")
    if not isinstance(semantics, Mapping):
        raise LocalEvidenceBuildReceiptError("receipt.semantics: expected object")
    if source_kind == "video" and not (
        semantics.get("full_stream_decoded") is True
        and semantics.get("sampled_visual_review") is True
        and semantics.get("continuous_dynamic_semantics_covered") is False
        and semantics.get("reader_viewing_required") is True
        and type(semantics.get("residual_dynamic_gap_count")) is int
        and semantics["residual_dynamic_gap_count"] > 0
    ):
        raise LocalEvidenceBuildReceiptError(
            "video build receipt conflates decode/sample review with continuous semantics"
        )
    article_semantics = {
        "full_text_read",
        "page_snapshot_reviewed",
        "article_image_ledger_complete",
        "discovered_image_count",
        "captured_image_count",
        "article_image_failure_count",
    }
    current_article_semantics = article_semantics | {
        "raw_visuals_transferred_externally", "unrecognizable_visual_count"
    }
    if source_kind == "article" and (
        set(semantics) not in (article_semantics, current_article_semantics)
        or semantics.get("full_text_read") is not True
        or semantics.get("page_snapshot_reviewed") is not True
        or semantics.get("article_image_ledger_complete") is not True
        or type(semantics.get("discovered_image_count")) is not int
        or type(semantics.get("captured_image_count")) is not int
        or type(semantics.get("article_image_failure_count")) is not int
        or not (
            semantics["discovered_image_count"]
            >= semantics["captured_image_count"]
            >= 0
        )
        or semantics["article_image_failure_count"] != 0
    ):
        raise LocalEvidenceBuildReceiptError("article build receipt image ledger is incomplete")
    if source_kind == "article" and set(semantics) == current_article_semantics and (
        semantics["raw_visuals_transferred_externally"] is not False
        or type(semantics["unrecognizable_visual_count"]) is not int
        or semantics["unrecognizable_visual_count"] < semantics["captured_image_count"]
    ):
        raise LocalEvidenceBuildReceiptError("article build receipt visual boundary is invalid")

    for index, raw in enumerate(stages):
        if not isinstance(raw, Mapping) or raw.get("status") != "completed":
            raise LocalEvidenceBuildReceiptError(f"receipt.stages[{index}]: incomplete")
        stage_id = str(raw.get("stage_id"))
        if stage_id in {"platform_acquisition", "browser_acquisition"} and raw.get(
            "requires_executed_adapter_receipt"
        ) is not True:
            raise LocalEvidenceBuildReceiptError(f"{stage_id}: executed adapter receipt is required")
        for field in ("snapshot_manifest", "report", "review"):
            if field in raw:
                nested(raw[field], f"receipt.stages[{index}].{field}")
        if "execution" not in raw:
            if stage_id.startswith("codex_") or stage_id in {
                "media_acquisition", "full_stream_decode", "local_asr", "frame_sampling"
            }:
                raise LocalEvidenceBuildReceiptError(f"{stage_id}: execution binding is required")
            continue
        execution_path = nested(raw["execution"], f"receipt.stages[{index}].execution")
        execution = _json(execution_path, f"receipt.stages[{index}].execution")
        if stage_id.startswith("codex_"):
            if (
                execution.get("schema_version") != CODEX_EXECUTION_SCHEMA
                or execution.get("actual_process_executed") is not True
                or execution.get("status") != "completed"
                or execution.get("return_code") != 0
                or not isinstance(execution.get("thread_id"), str)
                or not execution["thread_id"]
            ):
                raise LocalEvidenceBuildReceiptError(f"{stage_id}: Codex execution is incomplete")
            argv = execution.get("argv")
            if not isinstance(argv, list) or not all(
                token in argv
                for token in (
                    "exec",
                    "--json",
                    "--ephemeral",
                    "--ignore-user-config",
                    "--ignore-rules",
                    "-",
                )
            ):
                raise LocalEvidenceBuildReceiptError(f"{stage_id}: Codex argv is not fixed")
            executable = Path(str(execution.get("executable", ""))).resolve()
            if (
                not executable.is_file()
                or execution.get("executable_sha256") != _sha256(executable)
                or argv[0] != str(executable)
            ):
                raise LocalEvidenceBuildReceiptError(f"{stage_id}: Codex executable is stale")
            for field in (
                "request",
                "output_schema",
                "stdin",
                "stdout",
                "stderr",
                "raw_last_message",
                "review",
            ):
                nested(execution.get(field), f"{stage_id}.{field}")
            inputs = execution.get("input_bindings")
            if not isinstance(inputs, Mapping) or not inputs:
                raise LocalEvidenceBuildReceiptError(f"{stage_id}: input bindings are missing")
            for role, binding in inputs.items():
                if not isinstance(binding, Mapping):
                    raise LocalEvidenceBuildReceiptError(f"{stage_id}.{role}: invalid input binding")
                path = Path(str(binding.get("path", ""))).resolve()
                if not path.is_file() or binding.get("sha256") != _sha256(path):
                    raise LocalEvidenceBuildReceiptError(f"{stage_id}.{role}: stale input binding")
        elif stage_id == "full_stream_decode":
            argv = execution.get("argv")
            if (
                execution.get("schema_version") != "ffmpeg-full-media-decode-execution-v1"
                or execution.get("actual_process_executed") is not True
                or execution.get("complete_stream_decode") is not True
                or execution.get("return_code") != 0
                or not isinstance(argv, list)
                or not all(token in argv for token in ("-xerror", "0:v", "0:a?", "null"))
            ):
                raise LocalEvidenceBuildReceiptError("full_stream_decode execution is incomplete")
            for field in ("stdout", "stderr"):
                nested(execution.get(field), f"full_stream_decode.{field}")
        elif stage_id == "local_asr":
            if (
                execution.get("schema_version") != "local-asr-execution-v1"
                or execution.get("actual_process_executed") is not True
                or execution.get("status") != "completed"
                or execution.get("return_code") != 0
                or execution.get("full_media_input_processed") is not True
            ):
                raise LocalEvidenceBuildReceiptError("local_asr execution is incomplete")
            for field in ("stdout", "stderr", "raw_output"):
                nested(execution.get(field), f"local_asr.{field}")
        elif stage_id == "frame_sampling":
            if (
                execution.get("schema_version") != "ffmpeg-frame-sampling-execution-v1"
                or execution.get("actual_process_executed") is not True
                or execution.get("status") != "completed"
                or execution.get("fixed_interval_ms") != 1000
                or execution.get("return_codes") != [0, 0]
            ):
                raise LocalEvidenceBuildReceiptError("frame_sampling execution is incomplete")
            logs = execution.get("logs")
            if not isinstance(logs, Mapping) or len(logs) != 4:
                raise LocalEvidenceBuildReceiptError("frame_sampling logs are incomplete")
            for name, binding in logs.items():
                nested(binding, f"frame_sampling.logs.{name}")
        elif stage_id == "media_acquisition":
            if (
                execution.get("schema_version") != "local-media-acquisition-execution-v1"
                or execution.get("actual_process_executed") is not True
                or execution.get("status") != "completed"
                or not isinstance(execution.get("media_files"), list)
                or len(execution["media_files"]) != 1
            ):
                raise LocalEvidenceBuildReceiptError("media acquisition execution is incomplete")
    return artifacts
