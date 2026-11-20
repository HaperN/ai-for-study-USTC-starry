"""图片提取（任务 T1.5）。

## 做法：整页图 + 位置框（推荐方案）

为什么不直接"按图裁剪"：
  数学教材的插图常和公式、文字混排（有些图本身就是带标注的坐标系），
  自动裁切容易切掉东西、或者把公式切进图里。整页渲染**绝不失真**。

但整页图展示时噪音大，所以额外记录**这张图在页面上的位置框（bbox）**：
  * 数据层 = 整页图 + bbox  → 简单、可验证、永不损坏
  * 展示层 = 按 bbox 裁剪显示 → 看起来就是"只有那张图"

## 位置框怎么来（两层，先免费后花钱）

1. **本地几何**（免费、确定性）：
   * 从 OCR 文字框里统计"整行都没有文字"的横向空白带 → 候选图区
   * 从正文里匹配图题（`图1.1 …`）→ 说明文字与页码
2. **模型复核**（可选，`--model`）：把候选页的整页图交给 `deepseek-flash`
   （它支持图片输入），确认"这页到底有没有插图、说明文字是什么、属于哪一类"。
   只对**候选页**调用，不逐页烧钱。

产出 `figures.json`：每页一条，含整页图文件、哈希、以及该页的插图候选（含 bbox）。
导出层会把这份东西合并进最终的 `images.json`。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = "1.0"
GENERATOR = "aiforstudy.parse.images"
CAPTION = re.compile(r"图\s*(\d+(?:\.\d+)*)\s*([^\n]{0,60})")
CAPTION_LOOKAHEAD = 3               # 图区下方最多再看几行去找图题
SPARSE_MAX_CHARS = 12               # 插图自己的标注（顶点 A/B/C 之类）算"稀疏行"
MIN_FIGURE_HEIGHT_RATIO = 0.10      # 空白带高度至少占页面 10% 才算候选
MIN_FIGURE_WIDTH_RATIO = 0.45       # 且横向至少占 45%
TEXT_ROW_RATIO = 0.05               # 一行文字总宽低于页面 5% 才算"这一行没字"
FIGURE_GAP_RATIO = 0.02             # 上下间隔小于页高 2% 就当作同一张图


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _overlap_ratio(a: list[int], b: list[int]) -> float:
    """两个框的交集面积 ÷ 较小框面积（0~1）。"""
    if not (isinstance(a, list) and isinstance(b, list) and len(a) == len(b) == 4):
        return 0.0
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    area_a = max(1, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(1, (b[2] - b[0]) * (b[3] - b[1]))
    return inter / min(area_a, area_b)


# ── 第一步：整页渲染 ────────────────────────────────────────────────────
@dataclass
class PageRender:
    page: int
    file: str
    sha256: str
    width: int
    height: int


def render_pages(pdf: Path, out_dir: Path, *, scale: float = 2.0,
                 page_limit: int | None = None) -> list[PageRender]:
    import pymupdf

    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    renders: list[PageRender] = []
    with pymupdf.open(pdf) as document:
        total = len(document) if page_limit is None else min(page_limit, len(document))
        for index in range(total):
            pixmap = document[index].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            data = pixmap.tobytes("png")
            name = f"page-{index + 1:05d}.png"
            (images_dir / name).write_bytes(data)
            renders.append(PageRender(page=index + 1, file=f"images/{name}",
                                      sha256=_sha256_bytes(data),
                                      width=pixmap.width, height=pixmap.height))
    return renders


# ── 第二步：本地找候选图区（免费） ──────────────────────────────────────
def _text_lines(boxes: list[dict], height: int) -> list[list]:
    """把文字框按 y 归并成"行"，返回 [[y0, y1, [文字...]], ...]（按 y 排序）。"""
    rows = sorted(
        (int(min(point[1] for point in item["box"])),
         int(max(point[1] for point in item["box"])),
         str(item.get("text", "")))
        for item in boxes)
    lines: list[list] = []
    for y0, y1, text in rows:
        if lines and y0 <= lines[-1][1] + 5:          # 同一行（y 有重叠）
            lines[-1][1] = max(lines[-1][1], y1)
            lines[-1][2].append(text)
        else:
            lines.append([y0, y1, [text]])
    return lines


def _is_sparse(texts: list[str]) -> bool:
    """这一行是不是"没几个字"——插图内部的标注常被 OCR 读成这种短行。"""
    return sum(len(str(t).strip()) for t in texts) <= SPARSE_MAX_CHARS


def _band_bottom(lines: list[list], index: int, y0: int, y1: int) -> int:
    """决定候选图区的下边界。

    * 空隙下面那一行就是图题（`图N.M` 开头）→ 并进来，裁出来自带说明文字。
    * 否则往后最多看 CAPTION_LOOKAHEAD 行：只要中间都是稀疏行（插图自己的
      顶点标注 A/B/C 之类），而且能找到图题，就把这一串连同图题一起并进来。
    * 都不是 → 到下面那一行的**上沿**为止，绝不吃正文。

    踩过的坑：第 10 页插图下方的顶点标注 `A/D/0` 被 OCR 当成正文行，候选区
    在这些标注处就截断了，图题也没并进来，裁出来的图缺了下半截。
    """
    if any(CAPTION.match(str(t).strip()) for t in lines[index][2]):
        return y1
    for offset in range(1, CAPTION_LOOKAHEAD + 1):
        nxt = index + offset
        if nxt >= len(lines):
            break
        _ny0, ny1, ntexts = lines[nxt]
        if any(CAPTION.match(str(t).strip()) for t in ntexts):
            return ny1
        if not _is_sparse(ntexts):
            break
    return y0


def find_blank_bands(boxes: list[dict], width: int, height: int) -> list[list[int]]:
    """找出插图候选区，返回**四值像素框** `[x0, y0, x1, y1]`。

    做法：把文字框归并成行，看**相邻两行之间的空隙**。
    空隙足够高（≥ 页面 10%）就是候选图区；下边界怎么定见 `_band_bottom`。

    踩过的坑：一开始用"相邻空白带合并"的写法，合并阈值比正文行距还大，
    结果全页被连成一条带 —— 所以这里改成基于"行间隙"，不做级联合并。
    """
    if not boxes:
        return []
    lines = _text_lines(boxes, height)
    xs = [point[0] for item in boxes for point in item["box"]]
    left, right = int(min(xs)), int(max(xs))

    bands: list[list[int]] = []
    previous_bottom = 0
    for index, (y0, y1, _texts) in enumerate(lines):
        gap = y0 - previous_bottom
        if gap >= height * MIN_FIGURE_HEIGHT_RATIO:
            bands.append([left, previous_bottom, right, _band_bottom(lines, index, y0, y1)])
        previous_bottom = max(previous_bottom, y1)
    if height - previous_bottom >= height * MIN_FIGURE_HEIGHT_RATIO:
        bands.append([left, previous_bottom, right, height])

    # 只保留"上下都被文字夹住"的带。
    # 页面上下的空白（页边距）不是插图 —— 之前把它们也当候选，40 页报出 84 个。
    return [band for band in bands
            if band[1] > height * 0.02 and band[3] < height * 0.98]


def captions_in_band(boxes: list[dict], band: list[int]) -> list[str]:
    """落在候选区里的图题（按文字框的 y 是否在框内判断）。"""
    found: list[str] = []
    for item in boxes:
        text = str(item.get("text", "")).strip()
        match = CAPTION.match(text)
        if not match:
            continue
        ys = [point[1] for point in item["box"]]
        if int(min(ys)) >= band[1] - 4 and int(max(ys)) <= band[3] + 4:
            caption = match.group(0).strip()
            if caption and caption not in found:
                found.append(caption)
    return found


def captions_on_page(boxes_or_text) -> list[str]:
    """抽图题。

    图题的特征是"**一个文字框以 `图N.M` 开头，而且很短**、不以句读结尾"。
    直接在整页文本里搜会把正文里的引用（"如图1.1所示…"）也抓进来，所以必须按框判断。
    """
    boxes = boxes_or_text
    if isinstance(boxes_or_text, str):
        boxes = [{"text": line} for line in boxes_or_text.splitlines()]
    found: list[str] = []
    for item in boxes:
        text = str(item.get("text", "")).strip()
        match = CAPTION.match(text)              # 必须从框首开始
        if not match:
            continue
        caption = match.group(0).strip()
        if len(caption) > 40 or re.search(r"[。；，,;]$", caption):
            continue
        if caption not in found:
            found.append(caption)
    return found


# ── 第三步：模型复核（可选） ────────────────────────────────────────────
FIGURE_SCHEMA_HINT = """只输出 JSON：
{"has_figure": true/false,
 "figures": [{"index": 1, "kind": "diagram|chart|photo|table|formula",
              "caption": "这一页上该图的说明文字（原样抄，没有就写空串）",
              "bbox": [x0, y0, x1, y1]}]}
坐标用 0~1000 的整数，左上角是 [0,0]，右下角是 [1000,1000]，框住插图本身（不含页眉页码）。"""


def model_review_page(client, page_image: Path, *, page: int,
                      candidates: list[list[int]], captions: list[str],
                      width: int, height: int) -> dict:
    """把一个候选页交给模型确认。只对候选页调用。"""
    hint_lines = [f"本页第 {page} 页。"]
    if candidates:
        hint_lines.append("本地几何检测到的空白带（像素坐标）：" +
                          json.dumps(candidates, ensure_ascii=False))
    if captions:
        hint_lines.append("本页正文里出现的图题：" + "；".join(captions))
    hint_lines.append(FIGURE_SCHEMA_HINT)
    result = client.chat_json(
        [{"role": "system", "content": "你是教材排版助手，只做客观识别，不添加原文没有的内容。"},
         {"role": "user", "content": "\n".join(hint_lines)}],
        images=[str(page_image)], tag=f"figure-page-{page}")
    data = result["data"]
    figures = []
    for row in data.get("figures", []) or []:
        bbox = row.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) == 4):
            continue
        # 0~1000 归一化 → 像素
        x0, y0, x1, y1 = [int(round(float(v) / 1000 * size))
                          for v, size in zip(bbox, (width, height, width, height))]
        figures.append({"bbox": [x0, y0, x1, y1],
                        "kind": str(row.get("kind") or "diagram"),
                        "caption": str(row.get("caption") or "").strip()})
    return {"page": page, "has_figure": bool(data.get("has_figure")), "figures": figures}


# ── 编排 ────────────────────────────────────────────────────────────────
def _caption_key(text: str) -> str:
    """图题比较用的键：把空格去掉再比。

    踩过的坑：本地从正文抄到的图题是 `图1.5`，模型给的是 `图 1.5`，
    字面不等就被当成两张图，同一张图出了两行。
    """
    return re.sub(r"\s+", "", str(text or ""))


def _same_figure(group: dict, item: dict, slack: int) -> bool:
    """item 是不是和 group 里已有的框指同一张图。"""
    for member in group["items"]:
        other = _caption_key(member["caption"])
        mine = _caption_key(item["caption"])
        if other and mine:
            if other != mine:
                continue          # 图题不一样，肯定不是同一张图
            return True
        if member["kind"] == item["kind"]:
            continue              # 同源的两个框（比如同页两张并排的图）不靠"挨得近"合并
        a, b = member["bbox"], item["bbox"]
        if min(a[3], b[3]) >= max(a[1], b[1]) - slack:      # 上下重叠或挨得很近
            return True
    return False


def _figure_groups(row: dict, boxes: list[dict]) -> list[dict]:
    """把同一张图的"本地框"和"模型框"配成一组 —— 一张图只出一行。

    判据：图题一样，或者一本地一模型的框上下重叠/挨得很近。

    踩过的坑：第 10 页本地框和模型框差得远（一个框住图的上半、一个框住下半
    加正文），被当成两张图各出一行，同一张图在清单里出现两次。
    """
    height = int(row.get("height") or 0)
    slack = max(8, int(height * FIGURE_GAP_RATIO))
    items: list[dict] = []
    for cand in row["candidates"]:
        bbox = [int(v) for v in cand["bbox"]]
        found = captions_in_band(boxes, bbox)
        items.append({"kind": "local", "bbox": bbox,
                      "caption": found[0] if found else "", "figure": {}})
    for figure in row["figures"]:
        items.append({"kind": "model", "bbox": [int(v) for v in figure["bbox"]],
                      "caption": (figure.get("caption") or "").strip(),
                      "figure": figure})

    groups: list[dict] = []
    for item in items:
        for group in groups:
            if _same_figure(group, item, slack):
                group["items"].append(item)
                break
        else:
            groups.append({"items": [item]})

    out: list[dict] = []
    for group in groups:
        local = next((i for i in group["items"] if i["kind"] == "local"), None)
        model = next((i for i in group["items"] if i["kind"] == "model"), None)
        caption = next((i["caption"] for i in group["items"] if i["caption"]), "")
        out.append({
            "local_bbox": local["bbox"] if local else None,
            "model_bbox": model["bbox"] if model else None,
            "local_captions": len(captions_in_band(boxes, local["bbox"])) if local else 0,
            "model_captions": len(captions_in_band(boxes, model["bbox"])) if model else 0,
            "slack": slack,
            "figure": model["figure"] if model else {},
            "caption": caption,
        })
    return out


def _median_line_height(boxes: list[dict]) -> int:
    heights = sorted(int(max(p[1] for p in item["box"])) - int(min(p[1] for p in item["box"]))
                     for item in boxes)
    if not heights:
        return 0
    return heights[len(heights) // 2]


def _extend_to_caption(bbox: list[int], caption: str, boxes: list[dict],
                       line_height: int, page_height: int) -> list[int]:
    """把框向下/向左右"贴"到它自己的图题上，让裁出来的图自带说明文字。

    只在图题就在眼前（已经落在框里、或紧贴框下方一行多一点）时才贴，绝不为了
    找图题去吃正文。

    踩过的坑：模型给第 29 页那张图的框只到图形，图题被切掉半行。
    """
    key = _caption_key(caption)
    if not key or not boxes:
        return bbox
    reach = bbox[3] + max(int(line_height * 1.5), 12)
    best: list[int] | None = None
    for item in boxes:
        text = str(item.get("text", "")).strip()
        if _caption_key(text) != key:
            continue
        ys = [int(point[1]) for point in item["box"]]
        xs = [int(point[0]) for point in item["box"]]
        if int(min(ys)) <= reach:
            candidate = [int(min(xs)), int(max(xs)), int(max(ys))]
            if best is None or candidate[2] > best[2]:
                best = candidate
    if best is None:
        return bbox
    return [min(bbox[0], best[0]), bbox[1], max(bbox[2], best[1]),
            min(page_height, max(bbox[3], best[2]))]


def _pick_primary(group: dict) -> tuple[list[int] | None, str, str]:
    """同一张图有两个框时，选一个当主框，并给出置信度。

    看哪个框"正好框住一个图题"：框住两个说明它把并排的两张图连成一片了
    （本地几何就会这样），一个都没框住说明它框错了地方（模型偶尔会把页眉
    当插图）。都一样时取面积小的那个（更贴图形）。

    被选中之后还会走 `_extend_to_caption`：图题紧贴着框下方时把它并进来，
    所以"模型框比图题高一点点"这种切掉图题的情况能自动补上。

    踩过的坑：一开始一律用本地框，第 30 页并排的"差集/补集"被当成一张。
    """
    local_bbox = group["local_bbox"]
    model_bbox = group["model_bbox"]
    if local_bbox and not model_bbox:
        return local_bbox, "local", "medium"
    if model_bbox and not local_bbox:
        return model_bbox, "model", "medium"

    ratio = _overlap_ratio(local_bbox, model_bbox)
    confidence = "high" if ratio >= 0.5 else "low"

    def score(bbox: list[int], captions: int) -> tuple[int, int]:
        return (0 if captions == 1 else 1, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))

    mine = score(local_bbox, group["local_captions"])
    theirs = score(model_bbox, group["model_captions"])
    if mine <= theirs:
        return local_bbox, "both", confidence
    return model_bbox, "both", confidence


def extract_images(pdf: str | Path, out_dir: str | Path, *, scale: float = 2.0,
                   page_limit: int | None = None, ocr_json: str | Path | None = None,
                   client=None, model_pages: int | None = None,
                   only_pages: list[int] | None = None) -> dict:
    """渲染整页图 + 记录插图位置框，产出 `figures.json`。

    client 不为 None 时启用模型复核，且**只对候选页**调用；
    model_pages 限制最多复核多少页（成本护栏）。
    """
    pdf = Path(pdf).resolve(strict=True)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    source_sha = _sha256_bytes(pdf.read_bytes())

    renders = render_pages(pdf, out_dir, scale=scale, page_limit=page_limit)
    by_page = {r.page: r for r in renders}

    # OCR 文字框（有就用，没有就退化成"整页都是候选"）
    boxes_by_page: dict[int, list[dict]] = {}
    page_text: dict[int, str] = {}
    if ocr_json and Path(ocr_json).is_file():
        payload = json.loads(Path(ocr_json).read_text(encoding="utf-8"))
        for row in payload.get("pages", []):
            page = int(row.get("page", 0))
            boxes_by_page[page] = row.get("boxes", [])
            page_text[page] = "\n".join(b.get("text", "") for b in row.get("boxes", []))

    pages: list[dict] = []
    for render in renders:
        boxes = boxes_by_page.get(render.page, [])
        captions = captions_on_page(boxes)
        candidates = find_blank_bands(boxes, render.width, render.height) if boxes else []
        pages.append({
            "page": render.page,
            "image": render.file,
            "sha256": render.sha256,
            "width": render.width,
            "height": render.height,
            "captions": captions,
            "candidates": [{"bbox": band, "reason": "text_free_band"} for band in candidates],
            "reviewed": False,
            "figures": [],
        })

    # 模型复核：只挑"有图题或有空白带"的页
    reviewed = 0
    if client is not None:
        # 有 OCR 数据时用"有图题或有空白带"筛候选页（省钱）；
        # 没有 OCR 数据时筛不出来，就所有页都算候选（否则 --model 单独跑等于没跑）。
        has_ocr = bool(boxes_by_page)
        pending = [p for p in pages
                   if not has_ocr or p["captions"] or p["candidates"]]
        if only_pages:
            pending = [p for p in pending if p["page"] in set(only_pages)]
        if model_pages is not None:
            pending = pending[:model_pages]
        for row in pending:
            outcome = model_review_page(
                client, out_dir / row["image"], page=row["page"],
                candidates=[c["bbox"] for c in row["candidates"]],
                captions=row["captions"], width=row["width"], height=row["height"])
            row["reviewed"] = True
            row["figures"] = outcome["figures"]
            reviewed += 1

    # 汇总成图片索引（figures.json）
    #
    # 实测结论（两个来源各有胜负，所以都留着并给出置信度）：
    #   * 本地几何：框得住图 + 图题，而且不会吃正文（边界由真实文字行决定），
    #               但图内标注离得远时可能切到标注
    #   * 模型复核：对**行内插图**很准（实测第 8 页本地一个候选都没有，模型框得正好），
    #               但它有时会把图题和下方正文一起框进来
    # 所以：同一张图的两种框合成一行，高置信 = 两者重叠；中置信 = 只有一个来源；
    #       低置信 = 两者差得远 → 标记待人工抽查，两种框都留在 candidates 里
    images: list[dict] = []
    for row in pages:
        for group in _figure_groups(row, boxes_by_page.get(row["page"], [])):
            local_bbox = group["local_bbox"]
            model_bbox = group["model_bbox"]
            figure = group["figure"]
            # 主框：见 _pick_primary（"正好框住一个图题"优先，其次取面积小的）
            bbox, source, confidence = _pick_primary(group)
            picked = "local" if bbox == local_bbox else "model"
            caption = group["caption"] or (row["captions"][0] if row["captions"] else "")
            page_boxes = boxes_by_page.get(row["page"], [])
            bbox = _extend_to_caption(bbox, caption, page_boxes,
                                      _median_line_height(page_boxes), row["height"])
            candidates: list[dict] = []
            if local_bbox:
                candidates.append({"source": "local", "bbox": local_bbox})
            if model_bbox:
                candidates.append({"source": "model", "bbox": model_bbox})
            images.append({
                "id": f"IMG-{len(images) + 1:04d}",
                "page": row["page"],
                "page_image": row["image"],
                "page_image_sha256": row["sha256"],
                "bbox": bbox,
                "bbox_source": source,       # local | model | both
                "bbox_picked": picked,
                "confidence": confidence,    # high | medium | low
                "needs_review": confidence != "high",
                "local_bbox": local_bbox,
                "model_bbox": model_bbox,
                "candidates": candidates,    # 每个来源给的原框，人工抽查时两个都能看
                "kind": figure.get("kind") or "unreviewed",
                "caption": caption,
                "file": None,          # 尚未裁出单图；展示层可按 bbox 从整页图裁
                "sha256": None,
                "concepts": [],
                "occurrences": [],
            })

    payload = {
        "_meta": {
            "file_type": "figures",
            "schema_version": SCHEMA_VERSION,
            "generator": GENERATOR,
            "source_sha256": source_sha,
            "field_notes": {
                "pages[].image": "整页渲染图（相对 data/parse/ 的路径），绝不失真",
                "pages[].candidates[]": "本地几何检测出的空白带（免费），作为插图候选区",
                "pages[].reviewed": "该页是否经模型复核",
                "images[].bbox": "插图在整页图上的像素框 [x0,y0,x1,y1]；展示层按它裁剪",
                "images[].bbox_source": "local=本地几何；model=模型复核；both=两个来源都框到了同一张图",
                "images[].bbox_picked": "主框最后取自哪个来源（贴图题之后可能和上面两个框都不完全一样）",
                "images[].confidence": "high=两个来源重叠；medium=只有一个来源；low=两个来源差得远，建议人工看",
                "images[].candidates[]": "这张图的各个来源原框，人工抽查时两个都能看",
                "images[].file": "裁出的单图文件；为 null 表示按 bbox 从整页图裁即可",
                "images[].caption": "图题（优先用图区里匹配到的，其次用模型给的）",
            },
        },
        "pdf": str(pdf),
        "scale": scale,
        "pages": len(pages),
        "pages_with_candidates": sum(1 for p in pages if p["candidates"]),
        "pages_reviewed": reviewed,
        "confidence_counts": {
            level: sum(1 for item in images if item["confidence"] == level)
            for level in ("high", "medium", "low")},
        "images": images,
        "page_index": pages,
    }
    (out_dir / "figures.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"figures": str(out_dir / "figures.json"), "pages": len(pages),
            "pages_with_candidates": payload["pages_with_candidates"],
            "pages_reviewed": reviewed, "images": len(images),
            "confidence_counts": payload["confidence_counts"]}


__all__ = ["extract_images", "render_pages", "find_blank_bands", "captions_on_page",
           "captions_in_band", "_text_lines", "_overlap_ratio", "_band_bottom",
           "_extend_to_caption", "_median_line_height", "_pick_primary", "_figure_groups",
           "model_review_page", "PageRender", "SCHEMA_VERSION"]
