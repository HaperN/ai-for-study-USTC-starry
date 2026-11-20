"""PDF 抽取（OCR 路线）：给"文字层坏掉"的 PDF 用。

为什么需要它（实测）：
  某些 LaTeX 生成的中文 PDF，字体是**子集化 CID 字体且缺 ToUnicode 映射**，
  `pymupdf` / `pdftotext` / `pdfminer` 抽出来**汉字占比 0.00%**（全是乱码，且反解不回来）。
  这类 PDF 只能走 OCR。

与旧项目的差异：
  旧链 OCR 依赖系统 `tesseract`（要 sudo 装）。这里用 `rapidocr-onnxruntime`
  —— 纯 pip、自带中文模型、不需要系统包。

输出（与旧链保持同一种"document.md"格式，便于复用下游）：
  <out>/document.md      每页一段 `[第 N 页]` + OCR 文本行
  <out>/ocr.json         每页的文字块（含坐标与置信度），供排查
  <out>/extract.json     抽取元信息（引擎、页数、耗时、平均置信度、原 PDF sha256）

缓存在 <cache_root>/<pdf sha256>/：命中就直接复用，不重复 OCR。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

ENGINE = "rapidocr-onnxruntime"
SCHEMA_VERSION = "1.1"


@dataclass
class PageResult:
    page: int
    lines: list[str]
    boxes: list[dict]
    mean_score: float


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def _sort_key(box) -> tuple[float, float]:
    """按"先上后下、再左到右"排序；用行的中心 y 做容差。"""
    ys = [point[1] for point in box]
    xs = [point[0] for point in box]
    return (round(min(ys) / 12.0), min(xs))


def render_and_ocr(pdf: Path, *, scale: float = 2.0, engine=None,
                   page_limit: int | None = None, progress=None) -> list[PageResult]:
    import pymupdf

    engine = engine or _load_engine()
    results: list[PageResult] = []
    with pymupdf.open(pdf) as document:
        total = len(document) if page_limit is None else min(page_limit, len(document))
        for index in range(total):
            page = document[index]
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            image = pixmap.tobytes("png")
            raw, _elapsed = engine(image)
            raw = raw or []
            raw = sorted(raw, key=lambda row: _sort_key(row[0]))
            boxes, lines, scores = [], [], []
            for box, text, score in raw:
                text = (text or "").strip()
                if not text:
                    continue
                boxes.append({"text": text, "score": round(float(score), 4),
                              "box": [[round(float(x), 1), round(float(y), 1)] for x, y in box]})
                lines.append(text)
                scores.append(float(score))
            results.append(PageResult(
                page=index + 1, lines=lines, boxes=boxes,
                mean_score=(sum(scores) / len(scores)) if scores else 0.0))
            if progress is not None:
                progress(index + 1, total)
    return results


def build_document_markdown(pages: list[PageResult]) -> str:
    out: list[str] = []
    for page in pages:
        out.append(f"[第 {page.page} 页]")
        out.extend(page.lines)
        out.append("")
    return "\n".join(out).strip() + "\n"


def extract_pdf(pdf: str | Path, *, cache_root: str | Path,
                scale: float = 2.0, page_limit: int | None = None,
                force: bool = False, progress=None) -> dict:
    """抽取 PDF → document.md。按 PDF 内容哈希缓存。"""
    pdf = Path(pdf).resolve(strict=True)
    sha = _sha256_file(pdf)
    key = sha if page_limit is None else f"{sha}-first{page_limit}"
    out_dir = Path(cache_root) / key
    document_path = out_dir / "document.md"
    meta_path = out_dir / "extract.json"
    if not force and document_path.is_file() and meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("source_sha256") == sha and meta.get("engine") == ENGINE:
            meta["cached"] = True
            meta["document"] = str(document_path)
            return meta

    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    pages = render_and_ocr(pdf, scale=scale, page_limit=page_limit, progress=progress)
    document_path.write_text(build_document_markdown(pages), encoding="utf-8")
    (out_dir / "ocr.json").write_text(
        json.dumps({"pages": [{"page": p.page, "mean_score": round(p.mean_score, 4),
                               "boxes": p.boxes} for p in pages]},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    scored = [p.mean_score for p in pages if p.boxes]
    meta = {
        "_meta": {
            "file_type": "extract",
            "schema_version": SCHEMA_VERSION,
            "generator": "aiforstudy.parse.extract.ocr_pdf",
            "source_sha256": sha,
            "field_notes": {
                "pages_with_text": "OCR 出至少一行文字的页数",
                "mean_score": "全篇平均置信度（0~1），低于 0.8 建议人工抽查",
            },
        },
        "engine": ENGINE,
        "source_pdf": str(pdf),
        "source_sha256": sha,
        "scale": scale,
        "pages": len(pages),
        "pages_with_text": sum(1 for p in pages if p.boxes),
        "mean_score": round(sum(scored) / len(scored), 4) if scored else 0.0,
        "seconds": round(time.time() - started, 1),
        "cached": False,
        "document": str(document_path),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta


__all__ = ["extract_pdf", "render_and_ocr", "build_document_markdown",
           "PageResult", "ENGINE"]
