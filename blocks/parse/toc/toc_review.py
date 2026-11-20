"""模型视觉复核（M3，★ 全方案的核心，P1/P2/P7 定案）。

## 为什么是核心

用户原话："**这是第一步也是最重要的一步**"。理由很实在：一本书的目录错了，
后面全白跑；而目录是全书**最小**的一份产物（几百行文字 + 几页截图），
在这里花钱做复核，是整条链上性价比最高的一次模型调用（设计稿 §2.2）。

## 三步模式（**照抄 `structure/review.py`，一点不能省**）

```
① 规则挑"不确定项"（0 token）：suspects()
      源间不一致 / 只有单源 / 编号跳跃 / 落不了页 / 条目形状可疑
② 只把这些 + 目录页截图送模型：review_toc()
      client.chat_json(messages, images=[截图...], effort=low)
      模型只做两件事：**裁决冲突**（P7：以它为准）、**捞回没抽到的条目**
      → toc.review.json（**建议稿**，逐条带 reason）
③ apply_review()：写进 toc.json，每条改动记 revisions
      （改了什么 / 依据什么 / 模型怎么说）—— 支持 dry-run
```

## 边界（沿用 `review.py` 已验证的那套，一条不能松）

* **绝不改原文一个字节**：复核只产出"结论 + 依据"，`apply` 只改目录产物本身；
  产物的 `revisions` 里逐条留底。跑完要能验证 PDF/document.md 的 sha256 没变。
* **拿不准怎么办**（Q-a 定案 α）：**保留在 `nodes` 里标 `confirmed: false`**，
  进 `counts.unconfirmed`，**不删、不整本判失败**。
* **送图预算**（Q-b 定案）：目录页**优先全送**，设上限 N（默认 8）；
  超限**降级**为"只送不确定项所在页"，**并在产物里记下"降级了"**。

## 一条对成本很实在的设计

复核结果**逐片缓存**（`lib/model.py` 自带磁盘缓存，按请求指纹命中），
所以同一本书重跑一次复核**不重复花钱**（G8 单节可重跑）。
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

from .contract import GENERATOR, ROOT, SCHEMA_VERSION, sha256_text
from .sources import identity

HERE = Path(__file__).resolve().parent
PROMPTS = HERE / "prompts"

#: 一次送模型最多多少条可疑项（太多会淹没在提示词里；结构层用的是 50，这里更小
#: 因为还带图，含图的上下文对条数更敏感）
CHUNK_SIZE = 30
ACTIONS = {"conflict", "recover", "unsure"}
DEFAULT_MAX_IMAGES = 8
# Vector pages have no intrinsic pixels and necessarily require rasterization.
VECTOR_RASTER_DPI = 300
MAX_COMPOSITE_PIXELS = 64_000_000  # allocation guard only; never resize original scans
#: 一次送进提示词的冲突条数上限（每条都要花钱；模型只需要值得裁决的那几条）
DISAGREEMENT_LIMIT = 40


def make_client(*, effort: str = "low"):
    """建模型客户端（图像走 data URL，`lib/model.py` 负责；密钥从环境/`.secrets` 读）。

    为什么 `effort="low"`：这一步的任务是"看图对字"，不是推理题；
    结构层复核实测也是低 effort 够用（设计稿 §2.2 第 ③ 步）。
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if str(ROOT / "lib") not in sys.path:
        sys.path.insert(0, str(ROOT / "lib"))
    import model as model_mod                              # noqa: PLC0415
    return model_mod.DeepSeekClient(effort=effort)


# ── ① 规则挑"不确定项"（0 token） ──────────────────────────────────────
def suspects(toc: dict, parse_dir: str | Path | None = None) -> list[dict]:
    """挑出需要模型看的条目（**只挑不判**）。

    五条规则，每条都能说清"为什么挑它"：

    | 规则 | 挑什么 |
    |---|---|
    | 源间不一致 | `disagreements` 里 `arbiter: pending` 的那些 |
    | 只有单源 | `evidence.from` 只有一个源的条目 |
    | 层级可疑 | 目录的级层与编号形状深度不符 |
    | 落不了页 | `page.printed` 与 `page.pdf` 都空 |
    | 条目形状可疑 | `suspicious` 里列的（编号跳跃、层级跳变） |
    """
    nodes = toc.get("nodes") or []
    by_id = {n["toc_id"]: n for n in nodes}
    flagged: dict[str, dict] = {}

    def flag(node: dict, why: str) -> None:
        row = flagged.setdefault(node["toc_id"], {
            "toc_id": node["toc_id"], "line": node.get("order") or 0,
            "level": node.get("level"), "number": node.get("number"),
            "title": node.get("title"), "kind": node.get("kind"),
            "sources": (node.get("evidence") or {}).get("from") or [],
            "page": node.get("page") or {}, "reasons": [],
            "bookmarks": None, "printed": None, "body": None,
        })
        if why not in row["reasons"]:
            row["reasons"].append(why)

    for row in toc.get("disagreements") or []:
        node = by_id.get(row.get("toc_id"))
        if node is None:
            continue
        flag(node, f"源间冲突：{row.get('why') or row.get('kind')}")
        for key in ("bookmarks", "printed", "body"):
            if row.get(key):
                flagged[node["toc_id"]][key] = row[key]
    for row in toc.get("suspicious") or []:
        node = by_id.get(row.get("toc_id"))
        if node is not None:
            flag(node, f"形状可疑：{row.get('why')}")
    missing_title = _title_missing_in_body(nodes, parse_dir)
    for node in nodes:
        sources = (node.get("evidence") or {}).get("from") or []
        if len(sources) == 1:
            flag(node, f"只有单源（{sources[0]}）")
        if not (node.get("page") or {}).get("printed") and not (node.get("page") or {}).get("pdf"):
            flag(node, "落不了页（印刷页与 PDF 页都空）")
        number_depth = _shape_depth(str(node.get("number") or ""))
        if number_depth and number_depth != int(node.get("level") or 0):
            flag(node, f"层级与编号不符：编号形状是 {number_depth} 级，"
                       f"目录说 {int(node.get('level') or 0)} 级")
        if not node.get("confirmed", True):
            flag(node, "confirmed:false（目录层自己就不确定）")
        if node["toc_id"] in missing_title:
            flag(node, missing_title[node["toc_id"]])

    return [flagged[key] for key in sorted(flagged)]


#: 复核的**预算**：一本书最多挑这么多条送模型（保护花费；超了就在产物里记下来）。
#: 设计稿 §11.2（Q-b）只给了"送图页数上限"，条数上限同理：宁可少问几条，也不要失控。
SUSPECT_BUDGET = 60


def _is_cjk(char: str) -> bool:
    return "\u4e00" <= char <= "\u9fff"


def _title_missing_in_body(nodes: list[dict], parse_dir: Path | None) -> dict[str, str]:
    """挑出"**目录说这个标题，正文里却没有**"的条目 —— OCR 错字的**免费探测器**。

    ## 为什么用它（实测，2026-09-28）

    这次重构的靶子之一：《复变函数》目录页的 `5.2 基级数`（真值 `幂级数`）。
    它**页有着落、也只有一个源**（那本书没有书签），所以
    "单源 / 落不了页 / 编号跳跃 / 形状可疑"**一条规则都抓不到它**。

    但换个角度一抓就中：**书名是全书正文的标题**，`幂级数` 在正文里出现了 16 次，
    而 `基级数` **一次都没有**。实测这本书靠这条只挑出 **5 条**，其中 3 条是真 OCR 错字
    （`基级数`←`幂级数`、`狭利克雷`←`狄利克雷`、`Γ函数` 的写法差异），另外 2 条是附表
    —— **精度极高、候选极少**，正是"规则挑可疑、模型只判可疑"想要的那种筛子。

    ⚠️ 没有 `document.md` 的书（还没跑 MinerU）这一条用不了，返回空 —— 不猜。
    标题为空的条目也跳过（非正文条目在目录里可能本来就只有标题）。
    """
    if parse_dir is None:
        return {}
    doc = Path(parse_dir) / "document.md"
    if not doc.is_file():
        return {}
    try:
        text = doc.read_text(encoding="utf-8")
    except OSError:
        return {}
    from ..structure.structure import title_key
    # 比之前先把正文也按**同一套标题口径**折平：全角连字符、空格、LaTeX 写法差异
    # 在真书里满地都是（实测《数值计算方法》：正文写 `Gauss-Seidel`、
    # 目录写 `Gauss－Seidel`，逐字比会挑出 31 条"找不到"——全是假警报）。
    haystack = title_key(text)
    out: dict[str, str] = {}
    for node in nodes:
        title = str(node.get("title") or "").strip()
        if len(title) < 3 or not any(_is_cjk(char) for char in title):
            continue
        needle = title_key(title)
        if len(needle) < 3:
            continue
        if needle not in haystack:
            out[node["toc_id"]] = (f"目录里写「{title}」，但**正文里找不到这个标题**"
                                   f"（疑似 OCR 错字；已按标题口径折平后仍找不到）")
    return out


def _shape_depth(number: str) -> int:
    from .sources import number_depth
    return number_depth(number)


# ── 截图（fitz 渲染；**不用无头浏览器**，见实施方案 §7.5） ──────────────
def _original_page_image(doc, page, images):
    """Extract only a directly painted, unmodified full-page scan."""
    if (len(images) != 1 or page.rotation or page.cropbox != page.mediabox
            or list(page.annots()) or list(page.widgets()) or page.get_xobjects()):
        return None
    for drawing in page.get_drawings(extended=True):
        items = drawing.get('items') or []
        # A standard full-page rectangular clip does not change source pixels.
        if drawing.get('type') != 'clip' or len(items) != 1 or items[0][0] != 're':
            return None
        clip = items[0][1]
        if (clip.x0 > page.rect.x0 or clip.y0 > page.rect.y0
                or clip.x1 < page.rect.x1 or clip.y1 < page.rect.y1):
            return None
    image = images[0]
    a, b, c, d, _x, _y = image['transform']
    if (not image.get('xref') or image.get('has-mask') or a <= 0 or d <= 0
            or abs(b) > 1e-6 or abs(c) > 1e-6
            or any(abs(x-y) > .01 for x,y in zip(image['bbox'], page.rect))):
        return None
    # Invisible OCR is harmless. Any visible text/shape or graphics state needs
    # full-page composition, otherwise extraction could silently omit content.
    visible = [kind for kind, *_ in page.get_bboxlog() if kind != 'ignore-text']
    if visible != ['fill-image']:
        return None
    for xref, key in ((page.xref,'Group'), (page.xref,'Resources/ExtGState'),
                      (image['xref'],'SMask'), (image['xref'],'Mask'),
                      (image['xref'],'Decode')):
        if doc.xref_get_key(xref,key)[0] != 'null':
            return None
    original = doc.extract_image(image['xref'])
    if not original or original.get('smask'):
        return None
    return original


def render_toc_pages(pdf: str | Path, pages: list[int], out_dir: str | Path) -> list[dict]:
    """Preserve full-page scan pixels; never impose DPI or resize those images.

    Mixed pages are composed at their source images' pixel density to retain
    overlays, crop and orientation. Pure vector pages have no original pixels;
    only that fallback uses an explicit raster density, recorded in evidence.
    """
    import pymupdf
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    shots: list[dict] = []
    with pymupdf.open(str(pdf)) as doc:
        for page_no in pages:
            if not 1 <= page_no <= doc.page_count:
                continue
            page = doc[page_no - 1]
            images = page.get_image_info(xrefs=True)
            metadata = {'source_images': [
                {key:image.get(key) for key in ('xref','width','height')} for image in images]}
            original = _original_page_image(doc, page, images)
            if original is not None:
                width, height = original['width'], original['height']
                ext = original['ext']
                if ext in ('png','jpeg','jpg','webp'):
                    path = out_dir / f'page-{page_no:04d}.{ext}'
                    path.write_bytes(original['image'])
                    metadata['render_policy'] = 'embedded-original-v1'
                else:
                    # Convert unsupported codecs losslessly, without resizing.
                    pixmap = pymupdf.Pixmap(doc, images[0]['xref'])
                    if pixmap.colorspace.n not in (1,3):
                        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
                    path = out_dir / f'page-{page_no:04d}.png'
                    pixmap.save(str(path))
                    metadata['render_policy'] = 'embedded-pixels-png-v1'
            else:
                densities = []
                for image in images:
                    a,b,c,d,_x,_y = image['transform']
                    for pixels, extent in ((image['width'],math.hypot(a,b)),
                                           (image['height'],math.hypot(c,d))):
                        if extent > 0:
                            densities.append(pixels/extent)
                scale = max(densities) if densities else VECTOR_RASTER_DPI/72
                pixels = math.ceil(page.rect.width*scale)*math.ceil(page.rect.height*scale)
                if pixels > MAX_COMPOSITE_PIXELS:
                    raise ValueError(f'PDF第{page_no}页合成预计{pixels}像素，超过内存保护；'
                                     '停止合成，不自动缩小原图')
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),alpha=False)
                path = out_dir / f'page-{page_no:04d}.png'
                pixmap.save(str(path))
                width, height = pixmap.width, pixmap.height
                metadata.update(render_policy=('page-composite-native-density-v1' if densities
                                               else 'vector-raster-v1'),
                                effective_dpi=round(scale*72,3),
                                fallback_reason='页面不是可直接发送的独立整页扫描图；保留完整可见页面')
            shots.append({'pdf_page':page_no, 'path':str(path), 'width':width,
                          'height':height, 'bytes':path.stat().st_size, **metadata})
    return shots


def _toc_page_numbers(toc: dict, parse_dir: Path) -> list[int]:
    """这份目录涉及哪几页 PDF：优先用预检结果，其次用条目自己的 `page.pdf`。"""
    preflight_path = parse_dir / "toc.preflight.json"
    if preflight_path.is_file():
        try:
            found = json.loads(preflight_path.read_text(encoding="utf-8"))
            pages = [int(p) for p in (found.get("toc_pages") or {}).get("pdf_pages") or []]
            if pages:
                return pages
        except (OSError, ValueError, TypeError):
            pass
    pages = sorted({int(n["page"]["pdf"]) for n in toc.get("nodes") or []
                    if (n.get("page") or {}).get("pdf")})
    return pages


def _image_note(toc: dict, parse_dir: Path, shots: list[dict], mode: str) -> str:
    """给模型说清"这几张图是什么"（哪几页、覆盖哪些条目）。"""
    pages = ", ".join(str(s["pdf_page"]) for s in shots)
    scope = [n for n in toc.get("nodes") or []
             if (n.get("page") or {}).get("pdf") in {s["pdf_page"] for s in shots}]
    lines = [f"共 {len(shots)} 张图，是按顺序排列的 PDF 第 {pages} 页的整页渲染图"
             f"（第 1 张对应上面第一个页码）。"]
    if mode == "downgraded":
        lines.append("⚠️ 这几张是**降级**送的：目录页超过送图上限，"
                     "只送了「不确定项落在的那几页」，所以**覆盖不全** —— "
                     "你只能判断能看见的部分，看不见的请报 `unsure`。")
    if scope:
        lines.append("这些页上大致包含这些目录条目（PDF 页码 → 标题）：")
        for node in scope[:40]:
            lines.append(f"  · P{(node.get('page') or {}).get('pdf')} "
                         f"{node.get('number') or ''} {node.get('title')}".rstrip())
    return "\n".join(lines)


# ── ② 送模型 ───────────────────────────────────────────────────────────
def review_toc(toc: dict, *, parse_dir: str | Path, client=None,
               max_images: int = DEFAULT_MAX_IMAGES, effort: str = "low",
               dry_run: bool = False, progress=None) -> dict:
    """复核 `toc.json`(v2)，返回**建议稿**（不落盘到正式文件）。

    `dry_run=True` 时不调模型、也不截图，只把"会送什么"写进产物。
    """
    parse_dir = Path(parse_dir)
    flagged = suspects(toc, parse_dir)
    if dry_run or client is None:
        return _empty_review(toc, flagged, reason="dry-run：没有调模型",
                             parse_dir=parse_dir)

    pdf = _pdf_path(toc, parse_dir)
    pages = _toc_page_numbers(toc, parse_dir)
    mode = "all-pages"
    if len(pages) > max_images:
        # Q-b 定案：超上限 → 降级为"只送不确定项所在页"，并**记下降级**
        wanted = sorted({int((s.get("page") or {}).get("pdf")) for s in flagged
                         if (s.get("page") or {}).get("pdf")})
        pages = wanted[:max_images]
        mode = "downgraded"
    shot_dir = parse_dir / "toc.pages"
    shots = render_toc_pages(pdf, pages, shot_dir) if (pdf and pages) else []
    if not shots and not flagged:
        return _empty_review(toc, flagged, reason="没有可疑项、也没有截图可送",
                             parse_dir=parse_dir)

    notes = _image_note(toc, parse_dir, shots, mode)
    chunks = _chunk(flagged, CHUNK_SIZE)
    decisions: list[dict] = []
    model_calls = 0
    tokens = {"prompt": 0, "completion": 0, "total": 0}
    for number, part in enumerate(chunks, 1):
        if progress is not None:
            progress(number, len(chunks))
        messages = _messages(toc, part, notes)
        result = client.chat_json(messages, tag=f"toc-review-{number}",
                                  images=[s["path"] for s in shots] or None)
        model_calls += 0 if result.get("cached") else 1
        usage = result.get("usage") or {}
        tokens["prompt"] += int(usage.get("prompt_tokens") or 0)
        tokens["completion"] += int(usage.get("completion_tokens") or 0)
        tokens["total"] += int(usage.get("total_tokens") or 0)
        decisions.extend(_validate(result["data"], flagged))

    return {
        "_meta": {
            "file_type": "toc_review", "schema_version": SCHEMA_VERSION,
            "generator": GENERATOR + ".review",
            # 审的时候**用了哪些来源**（`pdf-bookmarks`/`printed-toc`/`body-heading-shapes`）：
            # 这是判"复核结论还作不作数"的正确指纹（2026-09-30 D-36）。
            # 不能用 nodes 哈希 —— `apply` 本身就会改 nodes，于是每次跑都判"过期"、
            # 每次都重跑一次复核（实测白花 3.5 万 token）。
            "sources_used": ((toc.get("_meta") or {}).get("sources_used") or []),
            "toc_sha256": sha256_text(json.dumps(toc.get("nodes") or [],
                                                 ensure_ascii=False, sort_keys=True)),
            "effort": effort, "images": len(shots), "model_calls": model_calls,
            "tokens": tokens, "downgraded": mode == "downgraded",
            "image_mode": mode,
            "field_notes": {
                "decisions[].action": "conflict=裁决源间冲突 / recover=捞回漏条 / unsure=拿不准",
                "decisions[].title": "只在裁决/捞回时给**截图上真正印的字**；不许润色",
                "decisions[].reason": "模型给的中文依据（人不看截图也能追）",
                "decisions[].confidence": "模型自己的把握程度",
                "images": "实际送了几张目录页截图",
                "downgraded": "目录页超过送图上限时为 true —— 只送了可疑项所在页（Q-b 定案）",
            },
        },
        "suspects": flagged,
        "suspects_total": len(flagged),
        "nodes_total": len(toc.get("nodes") or []),
        "chunks": len(chunks),
        "images_sent": [s["pdf_page"] for s in shots],
        "decisions": decisions,
        "summary": _summarise(decisions),
    }


def _messages(toc: dict, part: list[dict], notes: str) -> list[dict]:
    system = (PROMPTS / "visual_review.md").read_text(encoding="utf-8")
    template = (PROMPTS / "visual_review_user.md").read_text(encoding="utf-8")
    context = {
        "nodes_total": len(toc.get("nodes") or []),
        "counts": (toc.get("_meta") or {}).get("counts"),
        "sources_used": (toc.get("_meta") or {}).get("sources_used"),
        "confidence": (toc.get("_meta") or {}).get("confidence"),
        "book_title": toc.get("book_title"),
    }
    # ⚠️ 冲突清单**要限量**：实测这本书 16 条冲突时，整个请求体涨到 64 万字符
    # （图片的 data URL 也在里面）—— 送进去的每条都要花钱，而模型只需要
    # "值得裁决的那几条"。与 `SUSPECT_BUDGET` 同一个道理：宁可少问，不要失控。
    disagreements = (toc.get("disagreements") or [])[:DISAGREEMENT_LIMIT]
    user = (template
            .replace("{{IMAGE_NOTES}}", notes)
            .replace("{{SUSPECTS}}", json.dumps({"suspects": part}, ensure_ascii=False,
                                                indent=1))
            .replace("{{DISAGREEMENTS}}", json.dumps(
                {"disagreements": disagreements,
                 "note": ("（只列了前 %d 条）" % DISAGREEMENT_LIMIT)
                         if len(toc.get("disagreements") or []) > DISAGREEMENT_LIMIT
                         else ""},
                ensure_ascii=False, indent=1))
            .replace("{{CONTEXT}}", json.dumps(context, ensure_ascii=False, indent=1)))
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _chunk(rows: list[dict], size: int) -> list[list[dict]]:
    return [rows[i:i + size] for i in range(0, len(rows), size)]


def _validate(data: dict, flagged: list[dict]) -> list[dict]:
    """把模型返回的 decisions 逐条校验成**我们能应用**的形状。

    校验是硬边界：`line` 必须在送出去的清单里、`action` 必须在三个里、
    `level` 必须是 1~3、`title` 不许空。不合规的**丢掉并记下**（不猜它的意思）。
    """
    valid_lines = {int(row["line"]) for row in flagged if row.get("line")}
    out: list[dict] = []
    for row in (data or {}).get("decisions") or []:
        action = str(row.get("action") or "").strip()
        if action not in ACTIONS:
            continue
        line = row.get("line")
        if action != "recover":
            try:
                line = int(line)
            except (TypeError, ValueError):
                continue
            if line not in valid_lines:
                continue
        else:
            line = None
        title = str(row.get("title") or "").strip()
        number = str(row.get("number") or "").strip()
        level = row.get("level")
        if action in ("conflict", "recover"):
            if not title:
                continue
            try:
                level = int(level)
            except (TypeError, ValueError):
                level = None
            if level is not None and not 1 <= level <= 4:
                continue
        decision = {
            "line": line, "action": action, "toc_id": None,
            "number": number or None, "title": title or None, "level": level,
            "chosen_source": str(row.get("chosen_source") or "").strip() or None,
            "page": row.get("page"),
            "resolution": str(row.get("resolution") or "").strip()[:400],
            "reason": str(row.get("reason") or "").strip()[:400],
            "confidence": str(row.get("confidence") or "low").strip() or "low",
            "model_said": {"title": title or None, "level": row.get("level"),
                           "number": number or None},
        }
        out.append(decision)
    return out


def _summarise(decisions: list[dict]) -> dict:
    summary: dict[str, int] = {}
    for row in decisions:
        summary[row["action"]] = summary.get(row["action"], 0) + 1
    return summary


def _pdf_path(toc: dict, parse_dir: Path) -> Path | None:
    """复核要用哪份 PDF。

    ⚠️ **必须和 `cli._pdf_for()` 同一个口径**。一开始这里自己写了一份
    （只看 `ingest.json` → 再看 `data/raw/<slug>*.pdf`），结果新书
    （`linear-algebra-v3`，PDF 是 `LinearAlgebra.pdf`、文件名不含 slug）**一张图都送不出去**，
    复核悄悄退化成"纯文字裁决" —— 而视觉复核没有图，就只是模型在猜（实测踩过）。
    现在直接复用 `cli._pdf_for()`，它把四个来源按优先级写在一处（本块不再有第二份）。
    """
    from .cli import _pdf_for                            # noqa: PLC0415
    return _pdf_for(parse_dir)


def _empty_review(toc: dict, flagged: list[dict], *, reason: str,
                  parse_dir: Path) -> dict:
    return {
        "_meta": {"file_type": "toc_review", "schema_version": SCHEMA_VERSION,
                  "generator": GENERATOR + ".review", "images": 0, "model_calls": 0,
                  "tokens": {"prompt": 0, "completion": 0, "total": 0},
                  "downgraded": False, "image_mode": "none",
                  "field_notes": {}},
        "suspects": flagged, "suspects_total": len(flagged),
        "nodes_total": len(toc.get("nodes") or []), "chunks": 0,
        "images_sent": [], "decisions": [], "summary": {}, "note": reason,
    }


# ── ③ 应用建议（可 dry-run，可拒绝） ───────────────────────────────────
def apply_review(toc: dict, review: dict) -> dict:
    """把建议稿应用到目录上，返回**新 payload**（不落盘），并逐条记 `revisions`。

    只做三件事：

    * `conflict`：按模型裁决改 `title` / `level` / `number`，并把裁决写进
      `disagreements[].resolution` + `arbiter: model-vision`（P7：以它为准）；
    * `recover`：把模型捞回来的条目**追加**到 `nodes` 末尾（`confirmed: false`，
      因为位置是我们不知道的 —— 不猜位置，R-c），并记进 `disagreements`；
    * `unsure`：**什么都不改**，只把这一条标成 `confirmed: false`（Q-a 定案 α：
      保留在 nodes 里，不删、不整本判失败）。

    **绝不改原文**：本函数只碰 `toc.json` 的字段，一个字节的 PDF/markdown 都不动。
    """
    nodes = [dict(n) for n in toc.get("nodes") or []]
    by_line = {n.get("order"): n for n in nodes}
    decisions = review.get("decisions") or []
    changes: list[dict] = []
    disagreements = [dict(d) for d in toc.get("disagreements") or []]
    recovered: list[dict] = []

    for decision in decisions:
        action = decision.get("action")
        if action == "unsure":
            node = by_line.get(decision.get("line") or 0)
            if node is not None and node.get("confirmed", True):
                node["confirmed"] = False
                changes.append({"action": "unsure", "toc_id": node["toc_id"],
                                "before": {"confirmed": True},
                                "after": {"confirmed": False},
                                "reason": decision.get("reason"),
                                "model_said": decision.get("model_said")})
            continue
        if action == "conflict":
            node = by_line.get(decision.get("line") or 0)
            if node is None:
                continue
            before = {"title": node.get("title"), "level": node.get("level"),
                      "number": node.get("number")}
            after = dict(before)
            if decision.get("title"):
                number, title = _split_number(decision["title"],
                                              decision.get("number")
                                              or node.get("number"))
                after["title"] = title
                if number:
                    after["number"] = number
            if decision.get("level"):
                after["level"] = int(decision["level"])
            if after == before:
                continue
            node.update({k: v for k, v in after.items() if v is not None})
            node["confirmed"] = True
            _resolve(disagreements, node, decision)
            changes.append({"action": "conflict", "toc_id": node["toc_id"],
                            "before": before, "after": after,
                            "confidence": decision.get("confidence"),
                            "reason": decision.get("reason"),
                            "model_said": decision.get("model_said")})
            continue
        if action == "recover":
            node = _recovered_node(decision, len(nodes) + 1)
            nodes.append(node)
            recovered.append(node)
            disagreements.append({
                "kind": "recovered", "toc_id": node["toc_id"],
                "title": node["title"], "bookmarks": None, "printed": None,
                "body": None,
                "resolution": decision.get("resolution")
                              or "模型从目录页截图里捞回来的条目",
                "arbiter": "model-vision", "confidence": decision.get("confidence", "low"),
                "why": decision.get("reason") or "三个源都漏了它",
            })
            changes.append({"action": "recover", "toc_id": node["toc_id"],
                            "before": None, "after": {"title": node["title"]},
                            "confidence": decision.get("confidence"),
                            "reason": decision.get("reason"),
                            "model_said": decision.get("model_said")})

    nodes, merged_duplicates = _merge_duplicates(nodes)
    for row in merged_duplicates:
        disagreements.append(row)
    # ⚠️ **无条件重挂父子**（D-24，2026-09-29 实测抓出）：
    # 原来只在"合并掉了重复节点"时才重算 —— 但**改层级**同样会让 parent 过期：
    # 实测《线性代数》的「参考文献」被模型判成章级（level 2 → 1），
    # parent 却还指着「第八章」（同为 1 级）→ 契约当场报
    # `T-0109 的 parent=T-0103 层级不比它浅 —— 树是错的`，整个 apply 失败。
    # 重挂是**幂等且便宜**的（按顺序扫一遍层级栈），没理由有条件地做。
    _relink_parents(nodes)

    updated = dict(toc)
    updated["nodes"] = nodes
    updated["disagreements"] = disagreements
    updated["revisions"] = [*(toc.get("revisions") or []), {
        "at": None, "source": "model_toc_visual_review",
        "reviewed": len(decisions), "changed": len(changes),
        "actions": _summarise(decisions),
        "decisions": changes,
    }]
    # 账目**重新算**（教训 §7.1：派生数据算出来，不要复制）
    from .contract import build_toc
    rebuilt = build_toc(
        slug=(toc.get("_meta") or {}).get("slug", ""),
        book_title=toc.get("book_title", ""),
        nodes=updated["nodes"],
        disagreements=updated["disagreements"],
        unresolved=toc.get("unresolved") or [],
        suspicious=toc.get("suspicious") or [],
        sources_used=(toc.get("_meta") or {}).get("sources_used") or [],
        confidence=(toc.get("_meta") or {}).get("confidence", "low"),
        pdf_sha256=(toc.get("_meta") or {}).get("pdf_sha256", ""),
        document_sha256=(toc.get("_meta") or {}).get("document_sha256", ""),
        page_offset=(toc.get("_meta") or {}).get("page_offset"),
    )
    reviewed_by = {
        "kind": "model-vision",
        "images": (review.get("_meta") or {}).get("images", 0),
        "model_calls": (review.get("_meta") or {}).get("model_calls", 0),
        "downgraded": (review.get("_meta") or {}).get("downgraded", False),
        "arbiter": "视觉复核（P7：冲突以它为准）",
    }
    rebuilt["_meta"]["reviewed_by"] = reviewed_by
    rebuilt["revisions"] = updated["revisions"]
    rebuilt["_meta"]["confidence"] = _regrade(rebuilt)
    rebuilt["_meta"]["counts"] = _counts(rebuilt)
    return rebuilt


#: 标题行首的编号（`2.3` / `第2章` / `§4.1` / `*1.3`）
_LEADING_NUMBER = re.compile(
    r"^([*＊]?\s*§?\s*\d+(?:\.\d+)*|第\s*[一二三四五六七八九十百零〇\d]+\s*[章篇])"
    r"\s+(.+)$")


def _split_number(title: str, number: str) -> tuple[str, str]:
    """把模型抄回来的"整行"拆成 `(编号, 标题)`。

    模型经常把**一整行**还给 `title`（`2.3 线性方程组的解空间`），因为那正是它在截图上
    看到的东西。照单全收的话，节点会变成 `number=2.3` +
    `title="2.3 线性方程组的解空间"`，页面就印成"2.3 2.3 线性方程组的解空间"（实测抓出）。

    规则（保守）：
    * 行首能拆出编号、且后面还有标题文字 → 拆开；编号优先用**行首那个**（书上印的），
      没有再退回已有的 `number`；
    * 拆不出就原样返回 —— 不碰真以数字开头的标题。
    """
    text = str(title or "").strip()
    match = _LEADING_NUMBER.match(text)
    if match:
        return match.group(1).replace(" ", ""), match.group(2).strip()
    return str(number or "").strip(), text


def _relink_parents(nodes: list[dict]) -> None:
    """按层级栈重算 `parent`（节点被合并掉之后必须做一次）。

    算法与 `reconcile._attach_parents()` 同一条：栈里只留"比我浅"的最近一个。
    这里不重新编号（`toc_id` 是稳定身份，改了就丢了对账线索）。
    """
    stack: list[dict] = []
    for node in nodes:
        while stack and int(stack[-1]["level"]) >= int(node["level"]):
            stack.pop()
        node["parent"] = stack[-1]["toc_id"] if stack else None
        stack.append(node)


def _merge_duplicates(nodes: list[dict]) -> tuple[list[dict], list[dict]]:
    """把**同一编号 + 同一标题**的重复条目合并成一条，返回 `(节点, 合并记录)`。

    ## 为什么会有重复（实测《线性代数：原理与应用》）

    我的目录页切条器有 6 处没切干净：`2.3 线性方程组的解空间 48` 把下一条的编号和页码
    一起吞了。模型视觉复核照着截图把 6 条**都**纠正成了正确写法
    （`2.3 线性方程组的解空间`）—— 于是它和被吞掉的那条**变成了同一个条目**。
    这时如果两条都留着，目录里就会出现两个"2.3 线性方程组的解空间"，
    下游按编号挂树就会错。

    合并规则（保守、可解释）：
    * 只合并 `(number, title)` 完全相同的**同级**节点；
    * **保留页码/来源更多的那条**（更完整），并记一条 `duplicate-merged` 冲突；
    * 被合并掉的那条**不静默消失**：它在 `disagreements` 里留了名字（R-a 的精神）。
    """
    from .sources import identity
    seen: dict[tuple, dict] = {}
    kept: list[dict] = []
    merged: list[dict] = []
    for node in nodes:
        # ⚠️ **位置也要进 key**（2026-09-30 用户定案）：同名条目在真书里**跨章重复**
        #     （每章末尾一个「习题」、每章一个「小结」）。原来只比 (层级, 编号+标题)，
        #     于是《电路分析基础》7 章各自的「习题」被并成 1 条 —— 骨架少了 6 个节点，
        #     更要命的是**各章的习题区随之消失**，习题里的题号行又被当成正文标题
        #     （实测假小节 `CH02-S06 若节点方程为`）。
        #     加上父节点/页码后：真·重复（同一条被切坏两次，父子页码都一样）照旧合并，
        #     跨章同名条目各留各的。
        key = (int(node.get("level") or 0),
               str(node.get("parent") or ""), str((node.get("page") or {}).get("printed") or ""),
               identity(str(node.get("number") or ""), str(node.get("title") or "")))
        if not all(key):
            kept.append(node)
            continue
        first = seen.get(key)
        if first is None:
            seen[key] = node
            kept.append(node)
            continue
        # 谁更完整谁留下
        score = lambda n: (bool((n.get("page") or {}).get("printed")),
                           bool((n.get("page") or {}).get("pdf")),
                           len((n.get("evidence") or {}).get("from") or []))
        winner, loser = (first, node) if score(first) >= score(node) else (node, first)
        if winner is node:
            kept[kept.index(first)] = node
            seen[key] = node
        merged.append({
            "kind": "duplicate-merged", "toc_id": winner["toc_id"],
            "title": winner.get("title"), "bookmarks": None,
            "printed": loser.get("title"), "body": None,
            "resolution": f"与 {loser['toc_id']} 是同一编号+同一标题，已合并（保留更完整的那条）",
            "arbiter": "rule", "confidence": "medium",
            "why": "目录页切条把下一条的编号/页码吞了，复核纠正后与原本那条重合",
        })
    return kept, merged


def _recovered_node(decision: dict, order: int) -> dict:
    """`recover` 造出来的节点：**位置不确定**，所以标 `confirmed: false`。"""
    level = int(decision.get("level") or 2)
    recovered_number, recovered_title = _split_number(decision.get("title") or "",
                                                      decision.get("number") or "")
    page = decision.get("page")
    printed = str(page) if page not in (None, "") else None
    return {
        "toc_id": f"T-R{order:03d}", "level": level,
        "number": recovered_number, "title": recovered_title,
        "parent": None, "order": 0,
        "page": {"printed": printed, "pdf": None},
        "kind": "section", "confirmed": False,
        "evidence": {"from": ["model-vision"], "agree": True},
    }


def _resolve(disagreements: list[dict], node: dict, decision: dict) -> None:
    """把裁决写进对应的 `disagreements` 行（`arbiter: model-vision`）。

    ⚠️ **不能只按 `toc_id` 匹配**（D-26，2026-09-29 实测抓出）：
    有些分歧行是**两源对账时**记下来的，那会儿 `toc_id` 还是 `None`
    （实测《电路分析基础》章 4 的 `source-title` 就是这样）。只按 toc_id 匹配 →
    匹配不上 → 新追加一条 `model-ruling`，而**原来那条一直挂着 `pending`** ——
    于是"模型早就裁决过了"和"还有 N 条待裁决"同时成立，驱动每次都报待裁决、审计永远过不去。
    兜底判据：**标题/编号指向同一条**（归一后相等，或相似度够高）。
    """
    from ..structure.structure import norm_title, title_similarity

    def _same(row: dict) -> bool:
        if row.get("toc_id") and row.get("toc_id") == node.get("toc_id"):
            return True
        wanted = norm_title(node.get("title"))
        for field in ("title", "printed", "body", "bookmarks"):
            text = norm_title(row.get(field))
            if not text or not wanted:
                continue
            if text == wanted or title_similarity(text, wanted) >= 0.75:
                return True
        return False

    for row in disagreements:
        if row.get("arbiter") in ("pending", None) and _same(row):
            row["resolution"] = decision.get("resolution") or "模型视觉复核裁决"
            row["arbiter"] = "model-vision"
            row["confidence"] = decision.get("confidence", "low")
            row["resolved_by"] = "model-ruling"
            return
    disagreements.append({
        "kind": "model-ruling", "toc_id": node["toc_id"], "title": node.get("title"),
        "bookmarks": None, "printed": node.get("title"), "body": None,
        "resolution": decision.get("resolution") or "模型视觉复核给出的写法",
        "arbiter": "model-vision", "confidence": decision.get("confidence", "low"),
        "why": decision.get("reason") or "复核发现这一条与目录页不符",
    })


def _regrade(payload: dict) -> str:
    from .reconcile import grade
    nodes = payload.get("nodes") or []
    has_bookmarks = any("pdf-bookmarks" in (n.get("evidence") or {}).get("from", [])
                        for n in nodes)
    return grade(nodes, payload.get("disagreements") or [],
                 payload.get("unresolved") or [], payload.get("suspicious") or [],
                 has_bookmarks=has_bookmarks)


def _counts(payload: dict) -> dict:
    from .contract import counts_of
    return counts_of(payload.get("nodes") or [],
                     disagreements=payload.get("disagreements") or [],
                     unresolved=payload.get("unresolved") or [],
                     suspicious=payload.get("suspicious") or [])


__all__ = ["ACTIONS", "CHUNK_SIZE", "DEFAULT_MAX_IMAGES", "apply_review",
           "make_client", "render_toc_pages", "review_toc", "suspects"]
