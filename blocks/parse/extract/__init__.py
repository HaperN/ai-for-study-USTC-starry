"""抽取层：PDF → document.md（文字层可用就用文字层，坏了走 OCR）。"""
from .ocr_pdf import extract_pdf, render_and_ocr, build_document_markdown, PageResult, ENGINE

__all__ = ["extract_pdf", "render_and_ocr", "build_document_markdown", "PageResult", "ENGINE"]
