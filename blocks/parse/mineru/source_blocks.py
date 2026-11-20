"""Lossless MinerU provenance, explicit attachment links and cache fingerprints.

Coordinates are the unscaled layout PDF points. Only document line ranges are
derived; raw_content is a deep copy of a source block, never a rendered version.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

SOURCE_PROTOCOL_VERSION = "mineru-source-v1"
SOURCE_SCHEMA_VERSION = "1.0"
OUTPUT_FILES = ("document.md", "figures.json", "mineru.audit.json", "extract.json", "headings.json", "toc.json")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def adapter_fingerprint() -> str:
    digest = hashlib.sha256()
    for name in ("adapt.py", "source_blocks.py"):
        digest.update(name.encode())
        digest.update(Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()


def payload_fingerprint(payload: dict) -> str:
    body = {k: v for k, v in payload.items() if k != "_meta"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_layout(payload: dict) -> None:
    """Reject incompatible structures before any adaptation output is written."""
    pages = payload.get("pdf_info")
    if not isinstance(pages, list):
        raise ValueError("MinerU pdf_info 必须是页面数组，不能猜测未支持的原始结构")

    def validate_node(node, location, children):
        if not isinstance(node, dict):
            raise ValueError(f"MinerU {location} 必须是对象")
        for key, nested in children:
            values = node.get(key, [])
            if values is None:
                continue
            if not isinstance(values, list):
                raise ValueError(f"MinerU {location}.{key} 必须是数组")
            for i, value in enumerate(values):
                validate_node(value, f"{location}.{key}[{i}]", nested)

    span_children = []
    line_children = [("spans", span_children)]
    block_children = [("lines", line_children)]
    block_children.append(("blocks", block_children))
    seen_pages = set()
    for i, page in enumerate(pages):
        validate_node(page, f"pdf_info[{i}]", [(key, block_children) for key in
                      ("para_blocks", "preproc_blocks", "discarded_blocks")])
        idx = page.get("page_idx")
        if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0 or idx in seen_pages:
            raise ValueError(f"MinerU pdf_info[{i}].page_idx 必须是唯一的非负整数")
        seen_pages.add(idx)


def page_blocks(page: dict) -> list[dict]:
    return page.get("para_blocks") or page.get("preproc_blocks") or []


def _nodes(node):
    yield node
    for key in ("blocks", "lines", "spans"):
        for child in node.get(key) or []:
            yield from _nodes(child)


def raw_text(block: dict) -> str:
    return "\n".join(str(node["content"]) for node in _nodes(block) if node.get("content"))


def running_headers(pages: list[dict]) -> set[int]:
    """Use a page's discarded header/page-number band, never global title reuse.

    A title/text block must be short, near the page edge, in the same horizontal
    band as an explicit discarded header or page number, and have other body
    material away from that band. Repeated words by themselves prove nothing.
    """
    result = set()
    for page in pages:
        size = page.get("page_size") or []
        if len(size) != 2 or not size[1]:
            continue
        height = float(size[1])
        bands = [b.get("bbox") for b in page.get("discarded_blocks") or []
                 if b.get("type") in ("header", "footer", "page_number") and len(b.get("bbox") or []) == 4]
        for b in page_blocks(page):
            box = b.get("bbox") or []
            text = raw_text(b).strip()
            if b.get("type") not in ("title", "text") or len(box) != 4 or not text or len(text) > 120:
                continue
            y0, y1 = float(box[1]), float(box[3])
            if not (0 <= y0 < y1 <= height * .16 or height * .86 <= y0 < y1 <= height):
                continue
            mid = (y0 + y1) / 2
            if not any(len(other.get("bbox") or []) == 4 and
                       abs((float(other["bbox"][1]) + float(other["bbox"][3])) / 2 - mid) > 20
                       for other in page_blocks(page) if other is not b):
                continue
            for band in bands:
                bmid = (float(band[1]) + float(band[3])) / 2
                if abs(mid - bmid) <= max(4, min(y1 - y0, float(band[3]) - float(band[1])) * .6):
                    result.add(id(b))
                    break
    return result


class SourceBlocks:
    def __init__(self, pages, layout_path: Path):
        self.layout_path = layout_path
        self.rows = []
        self.by_identity = {}
        self.assets = []
        self.content_path = layout_path.parent / "content_list.json"
        self.content = []
        if self.content_path.is_file():
            self.content = json.loads(self.content_path.read_text(encoding="utf-8"))
            if not isinstance(self.content, list) or not all(isinstance(x, dict) for x in self.content):
                raise ValueError("MinerU content_list.json 必须是对象数组")
        for page in sorted(pages, key=lambda p: p["page_idx"]):
            for group, blocks in (("b", page_blocks(page)), ("d", page.get("discarded_blocks") or [])):
                for order, block in enumerate(blocks):
                    row = {"block_id": f"p{page['page_idx'] + 1:05d}-{group}{order:04d}",
                           "page": page["page_idx"] + 1, "page_idx": page["page_idx"],
                           "reading_order": len(self.rows), "page_order": order,
                           "source_group": "discarded_blocks" if group == "d" else
                               ("para_blocks" if page.get("para_blocks") else "preproc_blocks"),
                           "original_type": block.get("type"), "bbox": copy.deepcopy(block.get("bbox")),
                           "coordinate_system": "pdf_points", "page_size": copy.deepcopy(page.get("page_size")),
                           "raw_content": copy.deepcopy(block), "raw_text": raw_text(block),
                           "source_lines": None, "disposition": "discarded" if group == "d" else "empty",
                           "reason": "mineru_discarded" if group == "d" else "not_rendered",
                           "region": None, "asset_ids": [], "rendered_text": ""}
                    self.rows.append(row)
                    self.by_identity[id(block)] = row

    def mark(self, block, *, reason=None, disposition=None, region=None, lines=None, first_line=None):
        row = self.by_identity[id(block)]
        if reason is not None:
            row["reason"] = reason
        if disposition is not None:
            row["disposition"] = disposition
        if region is not None:
            row["region"] = region
        if lines is not None and first_line is not None and len(lines) >= first_line:
            row.update(source_lines=[first_line, len(lines)], rendered_text="\n".join(lines[first_line - 1:]),
                       disposition="document", reason="rendered")
        return row

    def prepare_assets(self, base_dir, out_dir, resolve_image, *, copy_images=True, dry_run=False):
        for row in self.rows:
            block = row["raw_content"]
            kind = block.get("type")
            if kind not in ("image", "table", "chart"):
                continue
            paths = list(dict.fromkeys(str(n["image_path"]) for n in _nodes(block) if n.get("image_path")))
            htmls = [str(n.get("html") or n.get("table_body")) for n in _nodes(block)
                     if n.get("html") or n.get("table_body")]
            # An HTML-only table is still an asset, even if MinerU omitted its crop.
            for index, original in enumerate(paths or ([None] if htmls else []), 1):
                asset_id = f"ASSET-{row['block_id']}-{index:02d}"
                related = [c for c in self.content if original and c.get("page_idx") == row["page_idx"]
                           and c.get("type") == kind and Path(str(c.get("img_path", ""))).name == Path(original).name]
                table_htmls = list(dict.fromkeys(htmls + [str(c["table_body"]) for c in related if c.get("table_body")]))
                table_html = table_htmls[0] if table_htmls else None
                resolved = resolve_image(base_dir, original, row["page_idx"]) if original else None
                local = None
                if resolved:
                    target = out_dir / "images" / f"mineru-{asset_id}{resolved.suffix.lower()}"
                    if copy_images:
                        if not dry_run:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(resolved, target)
                        local = str(target.relative_to(out_dir))
                    else:
                        local = str(resolved.resolve())
                asset = {"id": asset_id, "kind": kind, "source_block_ids": [row["block_id"]],
                         "page": row["page"], "page_idx": row["page_idx"], "bbox": copy.deepcopy(row["bbox"]),
                         "coordinate_system": "pdf_points", "page_size": copy.deepcopy(row["page_size"]),
                         "original_path": original, "local_path": local,
                         "source_path": str(resolved.resolve()) if resolved else None,
                         "source_relative_path": str(resolved.relative_to(base_dir)) if resolved else None,
                         "sha256": sha256(resolved) if resolved else None, "table_html": table_html,
                         "table_html_variants": table_htmls,
                         "content_list_entries": copy.deepcopy(related),
                         "status": "available" if resolved or table_html else "missing",
                         "needs_review": bool(original and not resolved) or len(table_htmls) > 1}
                self.assets.append(asset)
                row["asset_ids"].append(asset_id)

    def asset_lines(self, block, *, caption_present=False):
        row = self.by_identity[id(block)]
        out = []
        for asset in self.assets:
            if asset["id"] not in row["asset_ids"]:
                continue
            if asset["table_html"]:
                out.extend(asset["table_html"].splitlines())
            elif not caption_present:
                # A missing crop remains addressable and triggers coverage review.
                out.append(f"![{asset['kind']}]({asset['local_path'] or asset['original_path']})")
        return out

    def payload(self, document):
        payload = {"_meta": {"file_type": "source-blocks", "schema_version": SOURCE_SCHEMA_VERSION,
                          "protocol_version": SOURCE_PROTOCOL_VERSION,
                          "document_sha256": hashlib.sha256(document.encode("utf-8")).hexdigest(),
                          "layout_sha256": sha256(self.layout_path), "layout_path": str(self.layout_path.resolve()),
                          "adapter_sha256": adapter_fingerprint(),
                          "content_list_sha256": sha256(self.content_path) if self.content_path.is_file() else None},
                "blocks": self.rows, "assets": self.assets}
        payload["_meta"]["payload_sha256"] = payload_fingerprint(payload)
        return payload


def adaptation_is_current(out_dir, mineru_dir=None) -> bool:
    """Only reuse a complete adaptation with matching code, inputs and assets."""
    out = Path(out_dir)
    try:
        source = json.loads((out / "source.blocks.json").read_text(encoding="utf-8"))
        meta = source["_meta"]
        if (meta["protocol_version"] != SOURCE_PROTOCOL_VERSION or
                meta["schema_version"] != SOURCE_SCHEMA_VERSION or
                meta["payload_sha256"] != payload_fingerprint(source) or
                meta["adapter_sha256"] != adapter_fingerprint() or
                meta["document_sha256"] != sha256(out / "document.md")):
            return False
        layout = Path(meta["layout_path"])
        if mineru_dir is not None:
            raw = Path(mineru_dir)
            layout = raw if raw.is_file() else raw / layout.name
        if sha256(layout) != meta["layout_sha256"]:
            return False
        content = layout.parent / "content_list.json"
        if (sha256(content) if content.is_file() else None) != meta.get("content_list_sha256"):
            return False
        for name in OUTPUT_FILES:
            if sha256(out / name) != meta["output_sha256"][name]:
                return False
        for asset in source["assets"]:
            if not asset.get("sha256"):
                # Previously missing source images becoming available must rebuild.
                original = asset.get("original_path")
                if original and (layout.parent / "images" / Path(original).name).is_file():
                    return False
                continue
            if sha256(out / asset["local_path"]) != asset["sha256"]:
                return False
            if sha256(layout.parent / asset["source_relative_path"]) != asset["sha256"]:
                return False
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False
