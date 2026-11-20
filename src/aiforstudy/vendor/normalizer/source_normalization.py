from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Sequence
from pathlib import Path
from .paths import io_path as _io_path
from .publication import image_references as _image_references



_HEADING = re.compile(r"^(#{1,6})\s*(.*?)\s*$")
_CHAPTER = re.compile(r"^第\s*(\d+)\s*章(?:\s*(.*))?$")
_SECTION = re.compile(r"^§\s*(\d+(?:\.\d+)*)\s*(.*)$")
_SUBSECTION = re.compile(r"^(\d+\.\d+\.\d+)\s*(.*)$")
_SECTION_EXERCISE = re.compile(r"^习题\s*(\d+(?:\.\d+)*)\s*$")
_CHAPTER_EXERCISE = re.compile(r"^第\s*(\d+)\s*章\s*综合习题\s*$")
_WORKED_EXAMPLE_START = re.compile(
    r"^(?:#{1,6}\s*)?例\s*(\d+(?:\.\d+)+)\b"
)
_EXERCISE_HEADING = re.compile(
    r"^(?:#{1,6}\s*)?(?:课后作业|今日[^\n]{0,20}作业|作业|思考题|练习题|复习题)\s*[：:]?\s*$"
)
_BOOK_EXERCISE_HEADING = re.compile(
    r"^(?:#{1,6}\s*)?(?:全书习题|总习题|综合习题|复习题|思考题|练习题|习题)\s*[：:]?\s*$"
)
_BOOK_ANSWER_HEADING = re.compile(
    r"^(?:#{1,6}\s*)?(?:习题参考答案|习题答案|参考答案)\s*[：:]?\s*$"
)
_CHAPTER_HEADING_ANY = re.compile(r"^#{1,6}\s*\*?\s*第\s*\d+\s*章")
_NUMBERED_TASK = re.compile(
    r"^\s*(?:\d+[.．、)]|[（(]\d+[)）])\s*"
    r"(?:(?:请|试)?(?:求|证明|计算|判断|讨论|作图|画出|说明|确定|比较)"
    r"|(?:根据|由|利用|结合|观察|已知|如图).{0,20}?"
    r"(?:求|证明|计算|判断|讨论|作图|画出|说明|确定|比较))"
)
_NORMALIZER_VERSION = "logical-markdown-v6-images"


class SourceStructureError(ValueError):
    """Raised when Markdown parts cannot form one ordered logical book."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_text(path: Path, text: str) -> None:
    _io_path(path.parent).mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        _io_path(temporary).write_text(text, encoding="utf-8")
        os.replace(_io_path(temporary), _io_path(path))
    finally:
        try:
            _io_path(temporary).unlink()
        except FileNotFoundError:
            pass


def _atomic_json(path: Path, payload: object) -> None:
    _atomic_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )




def _title_record(title: str) -> dict[str, str] | None:
    title = title.strip()
    match = _CHAPTER_EXERCISE.match(title)
    if match:
        return {"block_type": "chapter_exercises", "anchor": match.group(1)}
    match = _SECTION_EXERCISE.match(title)
    if match:
        return {"block_type": "section_exercises", "anchor": match.group(1)}
    match = _CHAPTER.match(title)
    if match:
        return {"block_type": "chapter", "anchor": match.group(1)}
    match = _SECTION.match(title)
    if match:
        return {"block_type": "section", "anchor": match.group(1)}
    match = _SUBSECTION.match(title)
    if match:
        return {"block_type": "subsection", "anchor": match.group(1)}
    return None


def _normalized_heading(line: str) -> tuple[str, list[dict[str, str]]]:
    match = _HEADING.match(line)
    if not match:
        return line, []
    marks, title = match.groups()
    record = _title_record(title)
    if record is None:
        return line, []
    if record["block_type"] == "chapter":
        normalized_title = re.sub(r"^第\s*(\d+)\s*章", r"第\1章", title)
    elif record["block_type"] == "section":
        normalized_title = re.sub(r"^§\s*", "§", title)
    elif record["block_type"] == "section_exercises":
        normalized_title = re.sub(r"^习题\s*", "习题 ", title)
    else:
        normalized_title = re.sub(
            r"^第\s*(\d+)\s*章\s*综合习题",
            r"第\1章综合习题",
            title,
        )
    normalized = f"{marks} {normalized_title.strip()}"
    if normalized == line:
        return line, []
    return normalized, [
        {"type": "heading_spacing", "before": line, "after": normalized}
    ]




def _block_id(anchor: str | None, index: int) -> str:
    numbers = [int(value) for value in re.findall(r"\d+", anchor or "")]
    chapter = numbers[0] if numbers else 0
    section = numbers[1] if len(numbers) > 1 else 0
    subsection = numbers[2] if len(numbers) > 2 else 0
    return f"CH{chapter:02d}-S{section:02d}-SS{subsection:02d}-B{index:04d}"


def _exercise_fingerprint(markdown: str) -> str:
    text = markdown
    for row in reversed(_image_references(markdown)):
        if str(row["target"]).lower().startswith(("https://", "http://")):
            text = text[:row["start"]] + text[row["end"]:]
    text = "\n".join(
        line
        for line in text.splitlines()
        if not _EXERCISE_HEADING.fullmatch(line.strip())
        and not _BOOK_EXERCISE_HEADING.fullmatch(line.strip())
        and not _CHAPTER_HEADING_ANY.match(line.strip())
    )
    text = re.sub(r"^\s*(?:#{1,6}\s*)?", "", text)
    text = re.sub(r"^\s*(?:\d+[.．、)]|[（(]\d+[)）])\s*", "", text)
    return hashlib.sha256(
        re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text).casefold().encode(
            "utf-8"
        )
    ).hexdigest()


def _book_exercise_start(
    blocks: Sequence[dict[str, object]],
) -> tuple[int, int] | None:
    """Find a terminal, chapter-spanning exercise area without using TOC hits."""
    flattened: list[tuple[int, int, str]] = []
    for block_index, block in enumerate(blocks):
        if block.get("disposition") == "toc_only":
            continue
        for line_index, line in enumerate(str(block["raw_markdown"]).splitlines()):
            flattened.append((block_index, line_index, line.strip()))
    for index, (block_index, line_index, line) in enumerate(flattened):
        if not _BOOK_EXERCISE_HEADING.fullmatch(line):
            continue
        tail = [value for _, _, value in flattened[index + 1 :]]
        answer_at = next(
            (
                offset
                for offset, value in enumerate(tail)
                if _BOOK_ANSWER_HEADING.fullmatch(value)
            ),
            len(tail),
        )
        chapter_count = sum(
            bool(_CHAPTER_HEADING_ANY.match(value))
            for value in tail[:answer_at]
        )
        if chapter_count >= 2:
            return block_index, line_index
    return None


def _annotate_source_routing(
    blocks: list[dict[str, object]],
    block_images: dict[str, list[dict[str, object]]],
) -> dict[str, list[dict[str, object]]]:
    """Add backward-compatible semantic regions and per-image routing."""
    book_start = _book_exercise_start(blocks)
    occurrences: dict[str, list[dict[str, object]]] = {}
    figure_order = 0
    exercise_order = 0
    example_order = 0
    in_book_exercises = False
    in_book_answers = False

    for block_index, block in enumerate(blocks):
        raw_lines = str(block["raw_markdown"]).splitlines()
        base_line = int(block.get("source_lines", [1, 1])[0])
        regions: list[dict[str, object]] = []
        current_role = "textbook"
        current_start = 0
        current_lines: list[str] = []
        current_owner_exercise = ""
        current_owner_example = ""

        def flush() -> None:
            nonlocal current_lines, current_start
            if not current_lines or not "\n".join(current_lines).strip():
                current_lines = []
                return
            markdown = "\n".join(current_lines).strip()
            region_index = len(regions) + 1
            role = current_role
            target_volume = {
                "textbook": "textbook",
                "worked_example": "textbook",
                "embedded_exercise": "exercise_book",
                "book_exercises": "exercise_book",
                "book_exercise_answers": "exercise_book_support",
            }[role]
            region: dict[str, object] = {
                "region_id": f"{block['block_id']}-R{region_index:03d}",
                "semantic_role": role,
                "source_area": (
                    "book_exercises"
                    if role in {"book_exercises", "book_exercise_answers"}
                    else "body"
                ),
                "target_volume": target_volume,
                "source_lines": [
                    base_line + current_start,
                    base_line + current_start + len(current_lines) - 1,
                ],
                "markdown": markdown,
                "exclude_from_textbook": target_volume not in {
                    "textbook",
                    "shared",
                },
                "inherited_anchor": (
                    None if role == "book_exercises" else block.get("anchor")
                ),
                "requires_reclassification": role == "book_exercises",
            }
            if current_owner_exercise:
                region["exercise_id"] = current_owner_exercise
                region["exercise_fingerprint"] = _exercise_fingerprint(markdown)
            if current_owner_example:
                region["example_id"] = current_owner_example
            regions.append(region)
            current_lines = []

        for line_index, line in enumerate(raw_lines):
            stripped = line.strip()
            at_book_start = book_start == (block_index, line_index)
            if at_book_start:
                flush()
                in_book_exercises = True
                in_book_answers = False
                current_role = "book_exercises"
                current_owner_example = ""
                current_owner_exercise = ""
                current_start = line_index
            elif _BOOK_ANSWER_HEADING.fullmatch(stripped):
                flush()
                in_book_exercises = False
                in_book_answers = True
                current_role = "book_exercise_answers"
                current_owner_example = ""
                current_owner_exercise = ""
                current_start = line_index
            elif in_book_exercises:
                current_role = "book_exercises"
                if _CHAPTER_HEADING_ANY.match(stripped):
                    if current_owner_exercise:
                        flush()
                        current_start = line_index
                    current_owner_exercise = ""
                elif _NUMBERED_TASK.match(stripped):
                    if current_owner_exercise:
                        flush()
                        current_start = line_index
                    exercise_order += 1
                    current_owner_exercise = (
                        f"BEX-{block['block_id']}-{exercise_order:04d}"
                    )
            elif in_book_answers:
                current_role = "book_exercise_answers"
            else:
                example_match = _WORKED_EXAMPLE_START.match(stripped)
                exercise_heading = _EXERCISE_HEADING.fullmatch(stripped)
                numbered_task = _NUMBERED_TASK.match(stripped)
                heading = _HEADING.match(line)
                if example_match:
                    flush()
                    example_order += 1
                    current_role = "worked_example"
                    current_owner_example = (
                        f"EXAMPLE-{example_match.group(1)}-{example_order:03d}"
                    )
                    current_owner_exercise = ""
                    current_start = line_index
                elif exercise_heading or (
                    numbered_task and current_role == "textbook"
                ):
                    flush()
                    exercise_order += 1
                    current_role = "embedded_exercise"
                    current_owner_exercise = (
                        f"EX-{block['block_id']}-{exercise_order:04d}"
                    )
                    current_owner_example = ""
                    current_start = line_index
                elif (
                    heading
                    and current_role in {"worked_example", "embedded_exercise"}
                    and not re.match(
                        r"^(?:#{1,6}\s*)?(?:解|解析|分析|证明|验证)\b",
                        stripped,
                    )
                ):
                    flush()
                    current_role = "textbook"
                    current_owner_exercise = ""
                    current_owner_example = ""
                    current_start = line_index
            current_lines.append(line)
        flush()
        block["source_regions"] = regions
        block["contains_roles"] = sorted(
            {str(region["semantic_role"]) for region in regions}
        )

        for region in regions:
            markdown = str(region["markdown"])
            for reference in block_images.get(str(block["block_id"]), []):
                if not region["source_lines"][0] <= reference["line"] <= region["source_lines"][1]:
                    continue
                alt, url = reference["alt"], reference["target"]
                figure_order += 1
                role = str(region["semantic_role"])
                figure_role = {
                    "textbook": "textbook_explanation",
                    "worked_example": "worked_example",
                    "embedded_exercise": "student_exercise",
                    "book_exercises": "student_exercise",
                    "book_exercise_answers": "excluded",
                }[role]
                target_volume = str(region["target_volume"])
                occurrence = {
                    "occurrence_id": f"IMG-OCC-{figure_order:06d}",
                    "source_block_id": str(block["block_id"]),
                    "source_region_id": str(region["region_id"]),
                    "source_order": figure_order,
                    "figure_role": figure_role,
                    "target_volume": target_volume,
                    "owner_exercise_id": str(region.get("exercise_id", "")),
                    "owner_example_id": str(region.get("example_id", "")),
                    "problem_statement_image": bool(
                        role in {"embedded_exercise", "book_exercises"}
                        and re.search(r"(?:见图|根据图|如图|图示|作答)", markdown)
                    ),
                    "alt_text": alt,
                }
                occurrences.setdefault(url, []).append(occurrence)
    return occurrences


def _body_start(lines: list[str], part_index: int) -> int:
    if part_index > 0:
        return 0
    recognized: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        match = _HEADING.match(line)
        record = _title_record(match.group(2)) if match else None
        if record is not None:
            recognized.append(
                (index, record["block_type"], record["anchor"])
            )
    if recognized:
        first_identity = recognized[0][1:]
        for index, block_type, anchor in recognized[1:]:
            if (block_type, anchor) == first_identity:
                return index
        return recognized[0][0]
    return len(lines)


def _part_blocks(
    path: Path,
    part_index: int,
    start_index: int,
) -> tuple[list[dict[str, object]], list[str], int]:
    text = _io_path(path).read_text(encoding="utf-8")
    lines = text.splitlines()
    body_start = _body_start(lines, part_index)
    blocks: list[dict[str, object]] = []
    anchors: list[str] = []
    block_count = start_index

    boundaries = [0]
    for index, line in enumerate(lines):
        if index == 0:
            continue
        match = _HEADING.match(line)
        if match and _title_record(match.group(2)) is not None:
            boundaries.append(index)
    boundaries.append(len(lines))
    boundaries = sorted(set(boundaries))

    current_anchor: str | None = None
    for boundary_index in range(len(boundaries) - 1):
        start = boundaries[boundary_index]
        end = boundaries[boundary_index + 1]
        if start == end:
            continue
        raw = "\n".join(lines[start:end]).strip()
        if not raw:
            continue
        first_line = lines[start] if start < len(lines) else ""
        heading_match = _HEADING.match(first_line)
        title = heading_match.group(2) if heading_match else ""
        title_record = _title_record(title) if heading_match else None
        if title_record is not None:
            current_anchor = title_record["anchor"]
            anchors.append(current_anchor)
            block_type = title_record["block_type"]
        else:
            block_type = "paragraph_group"
        normalized_lines: list[str] = []
        operations: list[dict[str, str]] = []
        for line in lines[start:end]:
            normalized, line_operations = _normalized_heading(line.rstrip())
            normalized_lines.append(normalized)
            operations.extend(line_operations)
        normalized_markdown = "\n".join(normalized_lines).strip()
        disposition = "toc_only" if start < body_start else "unprocessed"
        block_count += 1
        block = {
            "block_id": _block_id(current_anchor, block_count),
            "source_file": str(path),
            "source_lines": [start + 1, end],
            "block_type": "toc" if disposition == "toc_only" else block_type,
            "scope": (
                "starred_extension"
                if "*" in title
                else "core"
            ),
            "anchor": current_anchor,
            "raw_markdown": raw,
            "normalized_markdown": normalized_markdown,
            "normalization_ops": operations,
            "content_sha256": _sha256_bytes(raw.encode("utf-8")),
            "disposition": disposition,
        }
        blocks.append(block)
    return blocks, anchors, block_count


def _anchor_numbers(value: str) -> tuple[int, ...]:
    return tuple(int(item) for item in value.split(".") if item.isdigit())


def normalize_logical_book(
    parts: Sequence[Path],
    project_root: Path,
) -> dict[str, object]:
    """Build a deterministic logical book and block-level normalized source."""
    normalized_parts = tuple(Path(path).resolve() for path in parts)
    if not normalized_parts:
        raise SourceStructureError("logical book requires at least one Markdown part")
    for path in normalized_parts:
        if not _io_path(path).is_file() or path.suffix.casefold() not in {".md", ".markdown"}:
            raise SourceStructureError(f"invalid Markdown part: {path}")

    project_root = Path(project_root).resolve()
    source_dir = project_root / "sources"
    input_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "normalizer_version": _NORMALIZER_VERSION,
                "parts": [
                    {
                        "path": str(path),
                        "sha256": _sha256_bytes(_io_path(path).read_bytes()),
                    }
                    for path in normalized_parts
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    logical_book_path = source_dir / "logical-book.json"
    normalized_path = source_dir / "normalized-source.jsonl"
    coverage_path = source_dir / "coverage-ledger.jsonl"
    image_path = source_dir / "remote-images.json"
    cached_report = None
    if (
        _io_path(logical_book_path).is_file()
        and _io_path(normalized_path).is_file()
        and _io_path(coverage_path).is_file()
        and _io_path(image_path).is_file()
    ):
        existing = json.loads(_io_path(logical_book_path).read_text(encoding="utf-8"))
        if existing.get("input_fingerprint") == input_fingerprint:
            images = json.loads(_io_path(image_path).read_text(encoding="utf-8")).get(
                "images", []
            )
            cached_report = {
                "passed": True,
                "cached": True,
                "part_count": len(normalized_parts),
                "logical_book": str(logical_book_path),
                "normalized_source": str(normalized_path),
                "coverage_ledger": str(coverage_path),
                "remote_image_reference_count": sum(
                    int(item.get("reference_count", 0))
                    for item in images
                    if isinstance(item, dict)
                ),
                "remote_image_unique_count": len(images),
                "boundary": existing["boundary"],
            }
            expected_sha = existing.get("normalized_source_sha256")
            if expected_sha is not None:
                if _sha256_bytes(_io_path(normalized_path).read_bytes()) != expected_sha:
                    raise SourceStructureError("cached normalized source bytes changed")
                return cached_report
    all_blocks: list[dict[str, object]] = []
    part_records: list[dict[str, object]] = []
    image_references: dict[str, list[str]] = {}
    block_images: dict[str, list[dict[str, object]]] = {}
    block_count = 0
    previous_last: str | None = None
    boundary_duplicate = False
    boundary_missing = False

    for part_index, path in enumerate(normalized_parts):
        blocks, anchors, block_count = _part_blocks(path, part_index, block_count)
        if not anchors:
            raise SourceStructureError(f"Markdown part has no body anchor: {path}")
        first_anchor, last_anchor = anchors[0], anchors[-1]
        if previous_last == first_anchor:
            boundary_duplicate = True
        if previous_last is not None:
            previous_numbers = _anchor_numbers(previous_last)
            first_numbers = _anchor_numbers(first_anchor)
            if first_numbers < previous_numbers:
                boundary_duplicate = True
            if (
                len(previous_numbers) >= 2
                and len(first_numbers) >= 2
                and previous_numbers[0] == first_numbers[0]
                and first_numbers[1] > previous_numbers[1] + 1
            ):
                boundary_missing = True
            if (
                previous_numbers
                and first_numbers
                and first_numbers[0] > previous_numbers[0] + 1
            ):
                boundary_missing = True
        previous_last = last_anchor
        part_records.append(
            {
                "order": part_index + 1,
                "file": str(path),
                "sha256": _sha256_bytes(_io_path(path).read_bytes()),
                "first_anchor": first_anchor,
                "last_anchor": last_anchor,
            }
        )
        source_text = _io_path(path).read_text(encoding="utf-8")
        # Resolve definitions in the entire original file before assigning
        # each use to its block and teaching/exercise region.
        references = _image_references(source_text)
        if any(not isinstance(row["target"], str) or not row["target"].strip() for row in references):
            raise SourceStructureError(f"Markdown image lacks its source: {path}")
        source_images = [{**row, "line": source_text.count("\n", 0, row["start"]) + 1}
                         for row in references
                         if str(row["target"]).lower().startswith(("https://", "http://"))]
        for block in blocks:
            rows = [row for row in source_images
                    if block["source_lines"][0] <= row["line"] <= block["source_lines"][1]]
            block_images[str(block["block_id"])] = rows
            for row in rows:
                url = row["target"]
                image_references.setdefault(url, []).append(str(block["block_id"]))
        all_blocks.extend(blocks)

    logical_book = {
        "logical_book_id": f"logical-book-{input_fingerprint[:16]}",
        "normalizer_version": _NORMALIZER_VERSION,
        "input_fingerprint": input_fingerprint,
        "parts": part_records,
        "boundary": {
            "duplicate": boundary_duplicate,
            "missing": boundary_missing,
        },
    }
    if boundary_duplicate or boundary_missing:
        raise SourceStructureError(
            "SOURCE_STRUCTURE_ERROR: duplicated or missing Markdown boundary"
        )

    image_occurrences = _annotate_source_routing(all_blocks, block_images)
    normalized_text = "".join(
        json.dumps(block, ensure_ascii=False, sort_keys=True) + "\n"
        for block in all_blocks
    )
    if cached_report is not None:
        # Older manifests have no output hash. Verify the full deterministic
        # reconstruction without resetting their mutable coverage/image records.
        if _io_path(normalized_path).read_text(encoding="utf-8") != normalized_text:
            raise SourceStructureError("cached normalized source differs from current inputs")
        return cached_report
    _atomic_text(normalized_path, normalized_text)
    logical_book["normalized_source_sha256"] = _sha256_bytes(_io_path(normalized_path).read_bytes())
    _atomic_json(logical_book_path, logical_book)
    image_registry = [
        {
            "original_url": url,
            "reference_block_ids": block_ids,
            "reference_count": len(block_ids),
            "occurrences": image_occurrences.get(url, []),
            "figure_roles": sorted(
                {
                    str(item["figure_role"])
                    for item in image_occurrences.get(url, [])
                }
            ),
            "target_volumes": sorted(
                {
                    str(item["target_volume"])
                    for item in image_occurrences.get(url, [])
                }
            ),
            "status": "referenced",
        }
        for url, block_ids in sorted(image_references.items())
    ]
    _atomic_json(image_path, {"images": image_registry})
    _atomic_text(
        coverage_path,
        "".join(
            json.dumps(
                {
                    "source_block_id": block["block_id"],
                    "disposition": block["disposition"],
                    **(
                        {"reason": "目录不进入语义生成"}
                        if block["disposition"] == "toc_only"
                        else {}
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
            for block in all_blocks
        ),
    )
    return {
        "passed": True,
        "cached": False,
        "part_count": len(part_records),
        "logical_book": str(source_dir / "logical-book.json"),
        "normalized_source": str(normalized_path),
        "coverage_ledger": str(coverage_path),
        "remote_image_reference_count": sum(
            len(value) for value in image_references.values()
        ),
        "remote_image_unique_count": len(image_references),
        "boundary": logical_book["boundary"],
    }
