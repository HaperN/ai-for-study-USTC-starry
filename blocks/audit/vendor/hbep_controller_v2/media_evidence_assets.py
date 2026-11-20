from __future__ import annotations

import hashlib
import json
from html.parser import HTMLParser
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError


class MediaEvidenceAssetError(ValueError):
    """Raised when a claimed media-analysis asset is only a stub or is stale."""


class _SnapshotHTMLParser(HTMLParser):
    """Small structural parser used to reject text files with an HTML prefix."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.has_html_root = False
        self.payload_characters = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.casefold()
        self.tags.append(normalized)
        if normalized == "html":
            self.has_html_root = True
        if normalized in {"img", "svg", "canvas", "video"}:
            self.payload_characters += 1

    def handle_data(self, data: str) -> None:
        self.payload_characters += len(data.strip())


@dataclass(frozen=True)
class MediaBoundAsset:
    role: str
    path: Path
    sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path, field: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MediaEvidenceAssetError(f"{field}: expected UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise MediaEvidenceAssetError(f"{field}: expected JSON object")
    return value


def _required(value: Mapping[str, object], fields: tuple[str, ...], field: str) -> None:
    missing = [name for name in fields if name not in value]
    if missing:
        raise MediaEvidenceAssetError(
            f"{field}: missing required field(s): {', '.join(missing)}"
        )


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MediaEvidenceAssetError(f"{field}: expected substantive string")
    return value.strip()


def _timestamp(value: object, field: str) -> datetime:
    text = _string(value, field)
    try:
        parsed = datetime.fromisoformat(
            text[:-1] + "+00:00" if text.endswith("Z") else text
        )
    except ValueError as exc:
        raise MediaEvidenceAssetError(
            f"{field}: expected ISO-8601 timestamp with timezone"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MediaEvidenceAssetError(
            f"{field}: expected ISO-8601 timestamp with timezone"
        )
    return parsed


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise MediaEvidenceAssetError(f"{field}: expected positive integer")
    return value


def _nonnegative_int(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise MediaEvidenceAssetError(f"{field}: expected nonnegative integer")
    return value


def _list(value: object, field: str, *, nonempty: bool = False) -> list[object]:
    if not isinstance(value, list) or (nonempty and not value):
        expectation = "nonempty list" if nonempty else "list"
        raise MediaEvidenceAssetError(f"{field}: expected {expectation}")
    return value


def _object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise MediaEvidenceAssetError(f"{field}: expected object")
    return value


def _schema(
    value: Mapping[str, object],
    *,
    expected: str,
    source_id: str,
    field: str,
) -> None:
    if value.get("schema_version") != expected:
        raise MediaEvidenceAssetError(f"{field}.schema_version: expected {expected}")
    if value.get("source_id") != source_id:
        raise MediaEvidenceAssetError(f"{field}.source_id: expected {source_id}")


def _full_ranges(
    value: object,
    *,
    field: str,
    duration_ms: int,
    extra_required: tuple[str, ...] = (),
) -> list[dict[str, object]]:
    raw_ranges = _list(value, field, nonempty=True)
    ranges: list[dict[str, object]] = []
    cursor = 0
    for index, raw in enumerate(raw_ranges):
        item_field = f"{field}[{index}]"
        item = _object(raw, item_field)
        _required(item, ("start_ms", "end_ms", *extra_required), item_field)
        start = _nonnegative_int(item["start_ms"], f"{item_field}.start_ms")
        end = _positive_int(item["end_ms"], f"{item_field}.end_ms")
        if start != cursor or end <= start or end > duration_ms:
            raise MediaEvidenceAssetError(
                f"{field}: must cover [0,duration_ms] contiguously and exactly"
            )
        cursor = end
        ranges.append(item)
    if cursor != duration_ms:
        raise MediaEvidenceAssetError(
            f"{field}: must cover [0,duration_ms] contiguously and exactly"
        )
    return ranges


def _safe_child(
    root: Path,
    value: Mapping[str, object],
    *,
    field: str,
    role: str,
) -> MediaBoundAsset:
    _required(value, ("path", "sha256"), field)
    relative = Path(_string(value["path"], f"{field}.path"))
    if relative.is_absolute() or relative.drive or relative.anchor:
        raise MediaEvidenceAssetError(f"{field}.path: expected relative path")
    try:
        path = (root / relative).resolve()
        path.relative_to(root)
    except (OSError, ValueError) as exc:
        raise MediaEvidenceAssetError(f"{field}.path: escapes evidence bundle") from exc
    if not path.is_file() or path.is_symlink() or path.stat().st_size <= 0:
        raise MediaEvidenceAssetError(f"{field}.path: unavailable or empty asset")
    expected = _string(value["sha256"], f"{field}.sha256").lower()
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise MediaEvidenceAssetError(f"{field}.sha256: expected 64 hexadecimal characters")
    actual = _sha256(path)
    if actual != expected:
        raise MediaEvidenceAssetError(f"{field}.sha256: mismatch")
    return MediaBoundAsset(role=role, path=path, sha256=actual)


def _validate_raster_image(path: Path, field: str) -> None:
    """Fully decode a raster image instead of trusting a suffix or magic bytes."""

    try:
        with Image.open(path) as probe:
            detected = str(probe.format or "").upper()
            width, height = probe.size
            probe.verify()
        with Image.open(path) as decoded:
            decoded.load()
            if decoded.size != (width, height) or not decoded.getbands():
                raise MediaEvidenceAssetError(f"{field}: invalid decoded pixel payload")
            decoded.getextrema()
    except MediaEvidenceAssetError:
        raise
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError) as exc:
        raise MediaEvidenceAssetError(
            f"{field}: image cannot be fully decoded"
        ) from exc
    if width <= 0 or height <= 0:
        raise MediaEvidenceAssetError(f"{field}: image dimensions must be positive")
    expected_by_suffix = {
        ".png": {"PNG"},
        ".jpg": {"JPEG"},
        ".jpeg": {"JPEG"},
        ".gif": {"GIF"},
        ".webp": {"WEBP"},
        ".bmp": {"BMP"},
        ".tif": {"TIFF"},
        ".tiff": {"TIFF"},
    }
    expected = expected_by_suffix.get(path.suffix.casefold())
    if expected is not None and detected not in expected:
        raise MediaEvidenceAssetError(
            f"{field}: decoded format {detected or 'unknown'} conflicts with suffix"
        )


def _validate_html_snapshot(path: Path, field: str) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise MediaEvidenceAssetError(f"{field}: HTML is not UTF-8") from exc
    if "\ufffd" in text:
        raise MediaEvidenceAssetError(f"{field}: HTML contains replacement characters")
    parser = _SnapshotHTMLParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        raise MediaEvidenceAssetError(f"{field}: HTML parsing failed") from exc
    if (
        not parser.has_html_root
        or not ({"head", "body"} & set(parser.tags))
        or parser.payload_characters <= 0
    ):
        raise MediaEvidenceAssetError(
            f"{field}: HTML lacks a document root and substantive page payload"
        )


def _validate_pdf_snapshot(path: Path, field: str) -> None:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path), strict=True)
        if not reader.pages:
            raise MediaEvidenceAssetError(f"{field}: PDF has no pages")
        # Traverse every page object so a truncated xref/page tree cannot pass.
        for page in reader.pages:
            _ = page.mediabox
            _ = page.get_contents()
    except MediaEvidenceAssetError:
        raise
    except Exception as exc:
        raise MediaEvidenceAssetError(f"{field}: PDF parsing failed") from exc


def _validate_page_snapshot(path: Path, field: str) -> None:
    suffix = path.suffix.casefold()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}:
        _validate_raster_image(path, field)
        return
    if suffix == ".pdf":
        _validate_pdf_snapshot(path, field)
        return
    if suffix in {".html", ".htm"}:
        _validate_html_snapshot(path, field)
        return
    raise MediaEvidenceAssetError(
        f"{field}: expected a decodable image, parseable PDF, or structured HTML file"
    )


def _https(value: object, field: str) -> str:
    text = _string(value, field)
    parsed = urlsplit(text)
    if parsed.scheme != "https" or not parsed.hostname:
        raise MediaEvidenceAssetError(f"{field}: expected HTTPS URL")
    return text


def _validate_watch_receipt(
    path: Path,
    *,
    source_id: str,
    duration_ms: int,
    execution_id: str,
) -> None:
    value = _json(path, "watch_receipt")
    _schema(
        value,
        expected="video-watch-receipt-v1",
        source_id=source_id,
        field="watch_receipt",
    )
    _required(
        value,
        (
            "execution_id",
            "viewer",
            "started_at",
            "completed_at",
            "duration_ms",
            "full_timeline_watched",
            "full_stream_decoded",
            "sampled_visual_review",
            "continuous_dynamic_semantics_covered",
            "audio_track_read",
            "video_track_read",
        ),
        "watch_receipt",
    )
    if value["execution_id"] != execution_id or value["viewer"] != "codex_runtime":
        raise MediaEvidenceAssetError("watch_receipt: execution or viewer mismatch")
    started = _timestamp(value["started_at"], "watch_receipt.started_at")
    completed = _timestamp(value["completed_at"], "watch_receipt.completed_at")
    if completed < started or value["duration_ms"] != duration_ms:
        raise MediaEvidenceAssetError("watch_receipt: time or duration mismatch")
    for field in ("full_stream_decoded", "sampled_visual_review", "audio_track_read"):
        if value[field] is not True:
            raise MediaEvidenceAssetError(f"watch_receipt.{field}: expected true")
    for field in (
        "full_timeline_watched",
        "video_track_read",
        "continuous_dynamic_semantics_covered",
    ):
        if value[field] is not False:
            raise MediaEvidenceAssetError(
                f"watch_receipt.{field}: sampled review must not claim continuous coverage"
            )


def _validate_article_read_receipt(
    path: Path,
    *,
    source_id: str,
    execution_id: str,
) -> None:
    value = _json(path, "read_receipt")
    _schema(
        value,
        expected="article-read-receipt-v1",
        source_id=source_id,
        field="read_receipt",
    )
    _required(
        value,
        (
            "execution_id",
            "reader",
            "started_at",
            "completed_at",
            "full_text_read",
            "page_images_reviewed",
            "embedded_images_reviewed",
        ),
        "read_receipt",
    )
    if value["execution_id"] != execution_id or value["reader"] != "codex_runtime":
        raise MediaEvidenceAssetError("read_receipt: execution or reader mismatch")
    started = _timestamp(value["started_at"], "read_receipt.started_at")
    completed = _timestamp(value["completed_at"], "read_receipt.completed_at")
    if completed < started:
        raise MediaEvidenceAssetError("read_receipt: completion precedes start")
    for field in (
        "full_text_read",
        "page_images_reviewed",
        "embedded_images_reviewed",
    ):
        if value[field] is not True:
            raise MediaEvidenceAssetError(f"read_receipt.{field}: expected true")


def _validate_asr(path: Path, *, source_id: str, duration_ms: int) -> None:
    value = _json(path, "asr")
    _schema(value, expected="video-asr-v1", source_id=source_id, field="asr")
    _required(
        value,
        ("duration_ms", "language", "engine", "audio_coverage", "segments"),
        "asr",
    )
    if value["duration_ms"] != duration_ms:
        raise MediaEvidenceAssetError("asr.duration_ms: parent mismatch")
    _string(value["language"], "asr.language")
    _string(value["engine"], "asr.engine")
    coverage = _full_ranges(
        value["audio_coverage"],
        field="asr.audio_coverage",
        duration_ms=duration_ms,
        extra_required=("status",),
    )
    for index, item in enumerate(coverage):
        if item["status"] not in {"speech", "silence", "unintelligible"}:
            raise MediaEvidenceAssetError(
                f"asr.audio_coverage[{index}].status: unsupported value"
            )
    segments = _list(value["segments"], "asr.segments")
    for index, raw in enumerate(segments):
        item = _object(raw, f"asr.segments[{index}]")
        _required(item, ("start_ms", "end_ms", "text"), f"asr.segments[{index}]")
        start = _nonnegative_int(item["start_ms"], f"asr.segments[{index}].start_ms")
        end = _positive_int(item["end_ms"], f"asr.segments[{index}].end_ms")
        if end <= start or end > duration_ms:
            raise MediaEvidenceAssetError(f"asr.segments[{index}]: invalid range")
        _string(item["text"], f"asr.segments[{index}].text")


def _validate_scene_timeline(path: Path, *, source_id: str, duration_ms: int) -> None:
    value = _json(path, "scene_timeline")
    _schema(
        value,
        expected="video-scene-timeline-v1",
        source_id=source_id,
        field="scene_timeline",
    )
    if value.get("duration_ms") != duration_ms:
        raise MediaEvidenceAssetError("scene_timeline.duration_ms: parent mismatch")
    entries = _full_ranges(
        value.get("entries"),
        field="scene_timeline.entries",
        duration_ms=duration_ms,
        extra_required=("scene_kind", "summary", "evidence_checked"),
    )
    for index, item in enumerate(entries):
        _string(item["scene_kind"], f"scene_timeline.entries[{index}].scene_kind")
        _string(item["summary"], f"scene_timeline.entries[{index}].summary")
        checked = _list(
            item["evidence_checked"],
            f"scene_timeline.entries[{index}].evidence_checked",
            nonempty=True,
        )
        for check_index, raw in enumerate(checked):
            _string(raw, f"scene_timeline.entries[{index}].evidence_checked[{check_index}]")


def _validate_frame_manifest(
    path: Path,
    *,
    root: Path,
    source_id: str,
    duration_ms: int,
) -> tuple[set[str], tuple[MediaBoundAsset, ...]]:
    value = _json(path, "frame_manifest")
    _schema(
        value,
        expected="video-frame-manifest-v1",
        source_id=source_id,
        field="frame_manifest",
    )
    if value.get("duration_ms") != duration_ms:
        raise MediaEvidenceAssetError("frame_manifest.duration_ms: parent mismatch")
    frames = _list(value.get("frames"), "frame_manifest.frames", nonempty=True)
    ids: set[str] = set()
    timestamps: set[int] = set()
    assets: list[MediaBoundAsset] = []
    for index, raw in enumerate(frames):
        field = f"frame_manifest.frames[{index}]"
        frame = _object(raw, field)
        _required(frame, ("frame_id", "timestamp_ms", "sampling_reason", "path", "sha256"), field)
        frame_id = _string(frame["frame_id"], f"{field}.frame_id")
        timestamp = _nonnegative_int(frame["timestamp_ms"], f"{field}.timestamp_ms")
        if frame_id in ids or timestamp in timestamps or timestamp > duration_ms:
            raise MediaEvidenceAssetError(f"{field}: duplicate id/time or out of range")
        ids.add(frame_id)
        timestamps.add(timestamp)
        _string(frame["sampling_reason"], f"{field}.sampling_reason")
        assets.append(
            _safe_child(root, frame, field=field, role=f"video_frame:{frame_id}")
        )
        _validate_raster_image(assets[-1].path, field)
    return ids, tuple(assets)


def _validate_frame_ocr(path: Path, *, source_id: str, frame_ids: set[str]) -> None:
    value = _json(path, "frame_ocr")
    _schema(value, expected="video-frame-ocr-v1", source_id=source_id, field="frame_ocr")
    entries = _list(value.get("entries"), "frame_ocr.entries", nonempty=True)
    seen: set[str] = set()
    for index, raw in enumerate(entries):
        field = f"frame_ocr.entries[{index}]"
        item = _object(raw, field)
        _required(item, ("frame_id", "status", "text"), field)
        frame_id = _string(item["frame_id"], f"{field}.frame_id")
        if frame_id in seen or frame_id not in frame_ids:
            raise MediaEvidenceAssetError(f"{field}.frame_id: duplicate or unknown")
        seen.add(frame_id)
        if item["status"] not in {"recognized", "no_text", "unreadable"}:
            raise MediaEvidenceAssetError(f"{field}.status: unsupported value")
        if not isinstance(item["text"], str):
            raise MediaEvidenceAssetError(f"{field}.text: expected string")
        if item["status"] == "recognized" and not item["text"].strip():
            raise MediaEvidenceAssetError(f"{field}.text: recognized text is empty")
    if seen != frame_ids:
        raise MediaEvidenceAssetError("frame_ocr.entries: must cover every sampled frame")


def _validate_multimodal(path: Path, *, source_id: str, duration_ms: int) -> None:
    value = _json(path, "multimodal_analysis")
    _schema(
        value,
        expected="video-multimodal-analysis-v1",
        source_id=source_id,
        field="multimodal_analysis",
    )
    if value.get("duration_ms") != duration_ms:
        raise MediaEvidenceAssetError("multimodal_analysis.duration_ms: parent mismatch")
    entries = _full_ranges(
        value.get("entries"),
        field="multimodal_analysis.entries",
        duration_ms=duration_ms,
        extra_required=(
            "audio_analyzed",
            "visuals_analyzed",
            "onscreen_text_analyzed",
            "gestures_and_animation_analyzed",
            "summary",
            "evidence_limit",
        ),
    )
    for index, item in enumerate(entries):
        for name in ("audio_analyzed", "visuals_analyzed", "onscreen_text_analyzed"):
            if item[name] is not True:
                raise MediaEvidenceAssetError(
                    f"multimodal_analysis.entries[{index}].{name}: expected true"
                )
        if item["gestures_and_animation_analyzed"] is not False:
            raise MediaEvidenceAssetError(
                f"multimodal_analysis.entries[{index}].gestures_and_animation_analyzed: "
                "sampled frames cannot prove continuous dynamics"
            )
        _string(item["summary"], f"multimodal_analysis.entries[{index}].summary")
        if not isinstance(item["evidence_limit"], str):
            raise MediaEvidenceAssetError(
                f"multimodal_analysis.entries[{index}].evidence_limit: expected string"
            )


def validate_video_evidence_assets(
    *,
    root: Path,
    role_paths: Mapping[str, Path],
    source_id: str,
    duration_ms: int,
    watch_execution_id: str,
) -> tuple[MediaBoundAsset, ...]:
    """Reject placeholder media assets and expand every sampled frame."""

    _validate_watch_receipt(
        role_paths["watch_receipt"],
        source_id=source_id,
        duration_ms=duration_ms,
        execution_id=watch_execution_id,
    )
    _validate_asr(role_paths["asr"], source_id=source_id, duration_ms=duration_ms)
    _validate_scene_timeline(
        role_paths["scene_timeline"], source_id=source_id, duration_ms=duration_ms
    )
    frame_ids, nested = _validate_frame_manifest(
        role_paths["frame_manifest"],
        root=root,
        source_id=source_id,
        duration_ms=duration_ms,
    )
    _validate_frame_ocr(role_paths["frame_ocr"], source_id=source_id, frame_ids=frame_ids)
    _validate_multimodal(
        role_paths["multimodal_analysis"], source_id=source_id, duration_ms=duration_ms
    )
    return nested


def validate_article_evidence_assets(
    *,
    root: Path,
    role_paths: Mapping[str, Path],
    source_id: str,
    read_execution_id: str,
) -> tuple[MediaBoundAsset, ...]:
    """Validate full readable article evidence and expand embedded images."""

    _validate_article_read_receipt(
        role_paths["read_receipt"],
        source_id=source_id,
        execution_id=read_execution_id,
    )

    readable_path = role_paths["readable_text"]
    try:
        text = readable_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise MediaEvidenceAssetError("readable_text: expected UTF-8 text") from exc
    if not text.strip() or "\ufffd" in text:
        raise MediaEvidenceAssetError("readable_text: empty or contains replacement characters")

    _validate_page_snapshot(role_paths["page_snapshot"], "page_snapshot")

    manifest_path = role_paths["embedded_image_manifest"]
    value = _json(manifest_path, "embedded_image_manifest")
    _schema(
        value,
        expected="article-embedded-image-manifest-v1",
        source_id=source_id,
        field="embedded_image_manifest",
    )
    images = _list(value.get("images"), "embedded_image_manifest.images")
    discovered = _list(
        value.get("discovered_images"),
        "embedded_image_manifest.discovered_images",
    )
    discovered_ids: set[str] = set()
    for index, raw in enumerate(discovered):
        field = f"embedded_image_manifest.discovered_images[{index}]"
        item = _object(raw, field)
        _required(
            item,
            ("discovery_id", "source_url", "status", "captured_image_id"),
            field,
        )
        discovery_id = _string(item["discovery_id"], f"{field}.discovery_id")
        if discovery_id in discovered_ids:
            raise MediaEvidenceAssetError(f"{field}.discovery_id: duplicate")
        discovered_ids.add(discovery_id)
        if not isinstance(item["source_url"], str) or not item["source_url"].strip():
            raise MediaEvidenceAssetError(f"{field}.source_url: expected string")
        if item["status"] != "captured_file":
            raise MediaEvidenceAssetError(
                f"{field}.status: every discovered image must be a decoded captured file"
            )
        if not isinstance(item["captured_image_id"], str) or not item[
            "captured_image_id"
        ].strip():
            raise MediaEvidenceAssetError(f"{field}.captured_image_id: required")
    seen: set[str] = set()
    assets: list[MediaBoundAsset] = []
    asset_records: dict[str, tuple[Mapping[str, object], MediaBoundAsset]] = {}
    for index, raw in enumerate(images):
        field = f"embedded_image_manifest.images[{index}]"
        item = _object(raw, field)
        _required(
            item,
            ("image_id", "source_url", "source_locator", "description", "path", "sha256"),
            field,
        )
        image_id = _string(item["image_id"], f"{field}.image_id")
        if image_id in seen:
            raise MediaEvidenceAssetError(f"{field}.image_id: duplicate")
        seen.add(image_id)
        _https(item["source_url"], f"{field}.source_url")
        _string(item["source_locator"], f"{field}.source_locator")
        _string(item["description"], f"{field}.description")
        assets.append(
            _safe_child(root, item, field=field, role=f"article_image:{image_id}")
        )
        _validate_raster_image(assets[-1].path, field)
        asset_records[image_id] = (item, assets[-1])
    referenced_captured_ids = {
        str(item["captured_image_id"])
        for item in discovered
        if isinstance(item, Mapping) and item.get("captured_image_id") is not None
    }
    if referenced_captured_ids != seen:
        raise MediaEvidenceAssetError(
            "embedded_image_manifest.discovered_images: captured-image inventory is not closed"
        )

    ledger_path = role_paths["article_image_ledger"]
    ledger = _json(ledger_path, "article_image_ledger")
    ledger_required = {
        "schema_version",
        "article_url",
        "root_found",
        "discovered_count",
        "success_count",
        "failure_count",
        "complete",
        "items",
    }
    if set(ledger) != ledger_required or ledger.get(
        "schema_version"
    ) != "wechat-article-image-ledger-v1":
        raise MediaEvidenceAssetError("article_image_ledger: invalid schema")
    ledger_items = _list(ledger.get("items"), "article_image_ledger.items")
    if (
        ledger.get("root_found") is not True
        or ledger.get("complete") is not True
        or type(ledger.get("discovered_count")) is not int
        or type(ledger.get("success_count")) is not int
        or type(ledger.get("failure_count")) is not int
        or ledger["discovered_count"] != len(ledger_items)
        or ledger["success_count"] != len(ledger_items)
        or ledger["failure_count"] != 0
        or len(discovered) != len(ledger_items)
    ):
        raise MediaEvidenceAssetError("article_image_ledger: incomplete or failed inventory")
    for index, raw in enumerate(ledger_items, start=1):
        field = f"article_image_ledger.items[{index - 1}]"
        item = _object(raw, field)
        required_item = {"dom_index", "status", "source_url", "path", "sha256"}
        if not required_item <= set(item) or set(item) - required_item - {"duplicate_of"}:
            raise MediaEvidenceAssetError(f"{field}: invalid schema")
        if type(item["dom_index"]) is not int or item["dom_index"] != index:
            raise MediaEvidenceAssetError(f"{field}.dom_index: expected exact DOM order")
        if item["status"] != "decoded":
            raise MediaEvidenceAssetError(f"{field}.status: unresolved image")
        duplicate_of = item.get("duplicate_of")
        if duplicate_of is not None and (
            type(duplicate_of) is not int or duplicate_of < 1 or duplicate_of >= index
        ):
            raise MediaEvidenceAssetError(f"{field}.duplicate_of: invalid")
        discovery = _object(discovered[index - 1], f"embedded_image_manifest.discovered_images[{index - 1}]")
        image_id = str(discovery["captured_image_id"])
        record = asset_records.get(image_id)
        if record is None:
            raise MediaEvidenceAssetError(f"{field}: captured image is not declared")
        image_record, asset = record
        if (
            item["source_url"] != discovery["source_url"]
            or item["source_url"] != image_record["source_url"]
            or item["path"] != asset.path.relative_to(root.resolve()).as_posix()
            or item["sha256"] != asset.sha256
        ):
            raise MediaEvidenceAssetError(f"{field}: ledger binding does not match captured image")
    return tuple(assets)
