"""Shared reader shell and live resource helpers.

The retired bare-lesson build() path was removed under D10. Production and
preview both consume immutable envelopes through build_reader_snapshot.
HTML/Markdown escaping belongs to the reader assets; delivery does not repeat it.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "lib"))

VENDOR = HERE / "vendor"
ASSETS = HERE / "assets"
TEMPLATES = HERE / "templates"


def inline_svg(svg_text: str, prefix: str) -> str:
    """把 SVG 变成能内联进页面的片段。

    两件事：
      * 去掉 XML 声明和 DOCTYPE（内联时不需要，留着是噪音）；
      * **给所有 id 加前缀**：两张图内联在同一页时，matplotlib 生成的内部 id
        （`p9d6356621f` 这种）有可能撞名，撞了就会串图。加前缀一劳永逸。
    """
    body = re.sub(r"<\?xml[^>]*\?>", "", svg_text)
    body = re.sub(r"<!DOCTYPE[^>]*>", "", body)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    ids = set(re.findall(r'id="([^"]+)"', body))
    # 长的先替换，避免 A 是 B 的前缀时把 B 也改坏
    for name in sorted(ids, key=len, reverse=True):
        new = f"{prefix}-{name}"
        body = body.replace(f'id="{name}"', f'id="{new}"')
        body = body.replace(f'href="#{name}"', f'href="#{new}"')   # xlink:href 也含这段子串
        body = body.replace(f"url(#{name})", f"url(#{new})")
    return body.strip()


#: 页面里那份"概念表副本"会带上的字段（改这个就要连着改 data.js 的消费方）
CONCEPT_COPY_FIELDS = ("id", "name", "type", "brief", "explanation", "formal", "occurrences")


def concept_copy_fingerprint(concepts_out: dict) -> dict:
    """页面里那份**概念表副本**的指纹：`{"fingerprint": …, "ids": […]}`。

    保留供 tools/doctor.py 检查历史页面；当前快照使用完整成员字节指纹。
    """
    canonical = {}
    for concept_id in sorted(concepts_out or {}):
        concept = concepts_out[concept_id]
        canonical[concept_id] = {field: concept.get(field) for field in CONCEPT_COPY_FIELDS}
    blob = json.dumps(canonical, ensure_ascii=False, sort_keys=True)
    return {"fingerprint": hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16],
            "ids": sorted(canonical)}


# ── 插图裁剪 ────────────────────────────────────────────────────────────
def crop_figure(parse_dir: Path, item: dict, target: Path, pad: int = 8) -> bool:
    """按 bbox 从整页图裁出这张插图；已经是独立文件的就直接复制。"""
    if item.get("file"):
        source = parse_dir / item["file"]
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            return True
    rel = item.get("page_image")
    bbox = item.get("bbox")
    if not rel or not bbox:
        return False
    page = parse_dir / rel
    if not page.is_file():
        return False
    from PIL import Image

    with Image.open(page) as image:
        width, height = image.size
        x0, y0, x1, y1 = (int(v) for v in bbox)
        box = (max(0, x0 - pad), max(0, y0 - pad),
               min(width, x1 + pad), min(height, y1 + pad))
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        image.crop(box).save(target)
    return True


# ── 共享页面壳 ──────────────────────────────────────────────────────────


def reader_site_files(data: dict, *, has_scene: bool = False) -> dict[str, Path]:
    """Actual copied inputs, including required paths when a runtime is absent."""
    files = {"assets/" + name: ASSETS / name for name in
             ("styles.css", "media-surface.css", "reader-book.css", "reader-layout.js", "app.js", "render-core.js", "reader-markdown.js", "models.js",
              # 修改计划 S：批注模块与固定版式随每个入口打包。
              "notes.js", "notes.css", "study-reader.css", "study-visuals.js",
              "study-foundation-scenes.js", "study-special-scenes.js", "study-guides.js",
              "study-math.js", "study-lesson.js", "study-light.css", "animations.js",
              "study-text-led-figures.js", "study-text-led-figures.css",
              "course-library.js", "course-library.css", "video-evidence.js",
              "audible-audio.js", "media-purpose.js", "media-purpose.css")}
    files['assets/text-led-multiplication.js'] = HERE.parents[1] / 'examples/text-led-multiplication/multiplication.js'
    # The study explorer is a local mathematical scene, independent of whether
    # a historical lesson happened to include a legacy MathBox model.
    files["assets/three.min.js"] = VENDOR / "mathbox" / "three.min.js"
    if data.get("animations"):
        files["assets/animations.js"] = ASSETS / "animations.js"
    files.update({"assets/" + name: VENDOR / name for name in ("katex.min.js", "katex.min.css", "marked.min.js")})
    for root, target in ((VENDOR / "fonts", "assets/fonts"),):
        if root.is_dir():
            files.update({target + "/" + str(p.relative_to(root)): p for p in root.rglob("*") if p.is_file()})
    if has_scene:
        files.update({"assets/" + name: VENDOR / "mathbox" / name for name in ("three.min.js", "OrbitControls.js")})
    if data.get("counts", {}).get("canvas_models", 0):
        root = VENDOR / "mathbox"
        if root.is_dir():
            files.update({"assets/mathbox/" + str(p.relative_to(root)): p for p in root.rglob("*") if p.is_file()})
        files.update({"assets/mathbox/" + name: root / name for name in
                      ("three.min.js", "OrbitControls.js", "mathbox.min.js", "mathbox.css")})
    return files


def reader_site_inputs(data: dict, *, has_scene: bool = False) -> dict[str, Path]:
    """Shared shell inputs for immutable diagnostics and other identity consumers."""
    return {**reader_site_files(data, has_scene=has_scene), "template/index.html": TEMPLATES / "index.html"}


def write_reader_site(data: dict, out_dir: Path, *, book: str, has_scene: bool = False, notes: list | None = None) -> None:
    """One static page shell for approved publication and labeled diagnostics.

    This function copies reader assets and serializes a preassembled view. It
    does not make or change approval decisions; callers own their typed boundary.
    """
    # Keep the registry book id intact while presenting the human title.
    data = {**data, 'display_title': book, 'study_reader': {
        **data.get('study_reader', {}), 'visual_policy':'authored', 'theme':'light'}}
    out_dir = Path(out_dir)
    notes = notes if notes is not None else []
    # 写产物
    (out_dir / "assets").mkdir(parents=True, exist_ok=True)
    (out_dir / "data").mkdir(parents=True, exist_ok=True)
    for relative, source in reader_site_files(data, has_scene=has_scene).items():
        if not source.is_file() and (relative.startswith("assets/mathbox/") or
                relative in ("assets/three.min.js", "assets/OrbitControls.js")):
            notes.append(f"运行资源缺失：{relative}；对应画布不可用")
            continue
        target = out_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        pending=target.with_name(target.name+'.pending')
        shutil.copy2(source, pending)
        pending.replace(target)
    pending_data=out_dir/'data/data.js.pending'
    pending_data.write_text(
        "/* 本文件由 blocks/render/build.py 生成，别手改 */\n"
        "window.__DATA__ = " + json.dumps(data, ensure_ascii=False) + ";\n",
        encoding="utf-8")
    pending_data.replace(out_dir/'data/data.js')
    # 有 canvas 模型才把 MathBox 的样式与脚本注入页面（1.1MB，用不到就别加载）
    # 只注入样式；脚本交给 models.js 按需拉。
    # ⚠️ 实测踩过：这里若也静态注入一遍，就会和 models.js 的按需加载**重复加载**，
    # 控制台报 "Multiple instances of Three.js"，canvas 模型永远等不到就绪。
    head_extra = ""
    if data.get("counts", {}).get("canvas_models", 0):
        head_extra = '<link rel="stylesheet" href="assets/mathbox/mathbox.css">'
    page = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    from aiforstudy.blocks.render.display_policy import reader_display_css
    page = page.replace("<!--__READER_DISPLAY_POLICY__-->", "<style>" + reader_display_css() + "</style>")
    from html import escape
    page = page.replace("__BOOK__", escape(book))
    page = page.replace("<!--__MATHBOX_CSS__-->", head_extra)
    # A new delivery must not reuse an earlier reader script with the same URL.
    # Queries remain local and are also valid on file:// pages.
    def versioned(match):
        relative=match.group(2)
        path=out_dir/relative
        if not path.is_file():return match.group(0)
        identity=hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        return match.group(1)+relative+'?v='+identity+match.group(3)
    page=re.sub(r'((?:src|href)=[\"\'])(assets/[^\"\']+\.(?:js|css)|data/data\.js)([\"\'])',versioned,page)
    pending_index=out_dir/'index.html.pending'
    pending_index.write_text(page, encoding="utf-8")
    pending_index.replace(out_dir/'index.html')


__all__ = ["inline_svg", "crop_figure", "concept_copy_fingerprint", "CONCEPT_COPY_FIELDS",
           "reader_site_files", "reader_site_inputs", "write_reader_site", "VENDOR", "ASSETS", "TEMPLATES"]
