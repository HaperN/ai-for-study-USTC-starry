"""图片提取：整页渲染 + 插图位置框（bbox），产出 figures.json。"""
from .extract_images import extract_images, render_pages, find_blank_bands, captions_on_page
__all__ = ["extract_images", "render_pages", "find_blank_bands", "captions_on_page"]
