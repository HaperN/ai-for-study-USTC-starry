"""Complete visible-text inventory and lossless display-to-source slices."""

from __future__ import annotations
import re
from .content_contracts import (
    ReviewDocumentV1,
    as_dict,
    content_digest,
    REFERENCE_PATTERN,
)
from ...errors import ContractError

# Unknown future text fields are included. Only provenance/control fields are
# excluded; renderer additions cannot silently hide new student-facing text.
_METADATA = {
    "id",
    "unit_id",
    "book_id",
    "chapter",
    "section",
    "anchor",
    "kind",
    "type",
    "role",
    "schema_version",
    "original_text",
    "original_sha256",
    "source_refs",
    "source_evidence",
    "source_context_hash",
    "concept_id",
    "target_id",
    "claim_ids",
    "prerequisites",
    "_meta",
    "checks",
    "generated_by",
    "revision",
    "policy_digest",
    "status",
    "original_ref",
    "concepts",
    "notation_refs",
}
_MARKER = REFERENCE_PATTERN


def _labels(context):
    labels = dict((context or {}).get("reference_labels") or {})
    index = (context or {}).get("concept_index") or {}
    rows = index.get("concepts") or []
    for row in rows:
        if isinstance(row, dict):
            labels[str(row.get("id"))] = str(
                row.get("name") or row.get("title") or row.get("id")
            )
    return labels


def _projection(raw, reference_context=None):
    chars, mapping, markers = [], [], []
    cursor = 0
    labels = _labels(reference_context)
    for match in _MARKER.finditer(raw):
        before = raw[cursor : match.start()]
        before_indices = list(range(cursor, match.start()))
        kind = match.group(1)
        label = match.group(3)
        if kind not in ("CONCEPT", "EXPLAIN"):
            label = ""
        elif label is None:
            label = labels.get(match.group(2), match.group(2))
        if kind == "CONCEPT" and label and before.rstrip().endswith(label):
            keep = len(before.rstrip()) - len(label)
            before = before[:keep]
            before_indices = before_indices[:keep]
        chars.extend(before)
        mapping.extend(before_indices)
        start = len(chars)
        chars.extend(label)
        if match.group(3) is None:
            mapping.extend([match.start()] * len(label))
        else:
            mapping.extend(range(match.start(3), match.start(3) + len(label)))
        markers.append(
            {
                "visible_start": start,
                "visible_end": len(chars),
                "raw_start": match.start(),
                "raw_end": match.end(),
                "synthetic": match.group(3) is None,
            }
        )
        cursor = match.end()
    chars.extend(raw[cursor:])
    mapping.extend(range(cursor, len(raw)))
    return "".join(chars), mapping, markers




def build_review_document(content, reference_context=None):
    c = as_dict(content)
    if (c.get("_meta") or {}).get("schema_version") == "4.0":
        from .reader_document import project_reader
        if reference_context is None:
            raise ContractError("v4 review requires frozen reference_catalog")
        reader = project_reader(content, reference_context)
        return ReviewDocumentV1(**{**reader.as_dict(), "schema_version": "ReviewDocumentV1", "reader_document_hash": reader.digest})
    blocks = []
    context = dict(reference_context or {})
    labels = _labels(context)
    for row in c.get("explainables") or []:
        if isinstance(row, dict):
            labels[str(row.get("id"))] = str(
                row.get("name") or row.get("title") or row.get("id")
            )
    context["reference_labels"] = labels

    def walk(value, pointer="", identity=""):
        if isinstance(value, str):
            if not value.strip():
                return
            visible, mapping, markers = _projection(value, context)
            if visible.strip():
                blocks.append(
                    {
                        "block_id": content_digest(
                            {"unit": c.get("unit_id"), "identity": identity or pointer}
                        ),
                        "owner_id": identity.rsplit("/", 1)[0].lstrip("/")
                        or c.get("unit_id"),
                        "json_pointer": pointer,
                        "raw_text": value,
                        "visible_text": visible,
                        "source_map": mapping,
                        "marker_spans": markers,
                    }
                )
        elif isinstance(value, dict):
            for key, child in value.items():
                if key not in _METADATA:
                    walk(
                        child,
                        pointer + "/" + key.replace("~", "~0").replace("/", "~1"),
                        identity + "/" + key,
                    )
        elif isinstance(value, list):
            for index, child in enumerate(value):
                owner = (
                    str(
                        child.get("id")
                        or child.get("example_id")
                        or child.get("step_id")
                        or index
                    )
                    if isinstance(child, dict)
                    else str(index)
                )
                walk(child, pointer + "/" + str(index), identity + "/" + owner)

    walk(c)
    return ReviewDocumentV1(
        schema_version="ReviewDocumentV1",
        unit_id=c.get("unit_id"),
        content_hash=content_digest(c),
        blocks=blocks,
        required_block_ids=[b["block_id"] for b in blocks],
    )
