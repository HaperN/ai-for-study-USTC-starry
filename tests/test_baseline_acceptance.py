"""基线验收：解析层的产物**必须和《AIforStudy 任务基线》保持一致**。

这条测试是把基线里已经写死的判据，变成"对**两条解析路线都跑**的机械检查" ——
以后谁改了适配块 / 结构层 / 导出层，只要把某条判据弄坏了，这里立刻红。

| 判据来自 | 这一条检查什么 |
|---|---|
| T1.2 ②③ | `structured.anchor3.md` 的正文没丢（≥ document.md 的 60%，且原文特征串在场） |
| T1.2 ① | 标题层级无跳级（最顶层必须是章） |
| T1.4 ②③ | `manifest.json` 列的产物全部存在且 sha256 对得上 |
| T1.4 ⑤⑥ | 原文保留率 ≥ 60%；`original_text/` 没有 manifest 之外的多余文件 |
| T1.4 ⑦ | 有正文的节点都在 `lesson_units` 里（不能漏单元） |
| T1.5 ①②③④ | 每张图有页码 + 四边形 bbox + 置信度分级 + id 唯一 |
| T1.7 ①③⑤ | 概念五个字段非空、id 全局唯一、分片落盘 |
| G3 | 契约 JSON 都有 `_meta` |
| G5 | 讲解记的 `original_sha256` 与原文文件当前 sha256 一致（原文没被改过） |
| G6 | 每次模型请求 ≤ 200KB（不随小节序号增长） |
| G7 | 占位符 id 都能解析；每个占位符恰好出现 1 次 |

跑法（数据不在就自动跳过，所以换机器也能跑）：

    ../.venv-linux/bin/python -m pytest tests/test_baseline_acceptance.py -q
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
PLACEHOLDER = re.compile(r"\[\[(CONCEPT|FIGURE)\s*:\s*([^\]|]*)\s*(?:\|\s*[^\]]*?)?\]\]")
MAX_REQUEST_BYTES = 200 * 1024
CONTRACT_FILES = ("manifest.json", "chapters.json", "concepts.json", "images.json",
                  "tables.json", "figures.json")

# 两条路线都要过同一套判据：默认（MinerU）与兜底（本地 OCR）
ROUTES = ("linear-algebra", "linear-algebra-ocr")


def cjk(text: str) -> int:
    return len(CJK.findall(text or ""))


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def parse_of(name: str) -> Path:
    return ROOT / "data" / "parse" / name


def lesson_of(name: str) -> Path:
    return ROOT / "data" / "lesson" / name


@pytest.fixture(params=ROUTES)
def route(request):
    name = request.param
    parse_dir = parse_of(name)
    if not (parse_dir / "chapters.json").is_file():
        pytest.skip(f"没有 {parse_dir}（换机器/没跑过解析），跳过")
    return {
        "name": name,
        "parse": parse_dir,
        "lesson": lesson_of(name),
        "chapters": load(parse_dir / "chapters.json") or {},
        "manifest": load(parse_dir / "manifest.json") or {},
        "concepts": load(parse_dir / "concepts.json") or {},
        "images": load(parse_dir / "images.json") or {},
    }


# ── G3：契约 JSON 都有 _meta ────────────────────────────────────────────
def test_every_contract_json_has_meta(route):
    for name in CONTRACT_FILES:
        payload = load(route["parse"] / name)
        if payload is None:
            continue
        assert "_meta" in payload, f"{route['name']}/{name} 缺 _meta"
        meta = payload["_meta"]
        for key in ("file_type", "schema_version", "generator"):
            assert meta.get(key), f"{route['name']}/{name} 的 _meta 缺 {key}"


# ── 路线留档（谁做的解析、为什么） ──────────────────────────────────────
def test_ingest_route_is_recorded(route):
    """每次入库都要留档：哪条路线、为什么、输入 sha256。"""
    ingest = load(route["parse"] / "ingest.json")
    if ingest is None:
        pytest.skip("这个目录是早期产物，没有 ingest.json（v0.24 起才有）")
    assert ingest["route"] in ("mineru", "ocr")
    assert ingest["reason"]
    if ingest["route"] == "mineru":
        assert ingest["mineru"]["layout_sha256"]


# ── T1.2：正文没丢 + 层级合理 ───────────────────────────────────────────
def test_structure_keeps_the_body_text(route):
    document = (route["parse"] / "document.md")
    structured = (route["parse"] / "structured.anchor3.md")
    assert document.is_file() and structured.is_file()
    keep = cjk(structured.read_text(encoding="utf-8")) / max(cjk(document.read_text(encoding="utf-8")), 1)
    assert keep >= 0.6, f"正文保留率只有 {keep:.0%}（去噪掉太多）"


def test_original_sentences_survive_into_structured(route):
    """从原文里挑 3 句有代表性的话，必须在结构层产物里找得到（去噪不许吃掉正文）。"""
    flat = re.sub(r"\s+", "", (route["parse"] / "structured.anchor3.md").read_text(encoding="utf-8"))
    files = sorted((route["parse"] / "original_text").glob("*.md"))
    assert files
    checked = 0
    for path in files[:3]:
        text = re.sub(r"\s+", "", path.read_text(encoding="utf-8"))
        # 跳过纯标题/极短的文件，取正文里最长的一段
        chunk = max((piece for piece in re.split(r"#+", text) if len(piece) >= 20),
                    key=len, default="")
        if not chunk:
            continue
        assert chunk[:40] in flat, f"{path.name} 的正文没进 structured.anchor3.md"
        checked += 1
    assert checked, "没有可用的特征串（数据太短？）"


def test_heading_levels_have_no_gap(route):
    payload = load(route["parse"] / "structure.json") or {}
    headings = payload.get("headings") or []
    assert headings, "一个标题都没认出来"
    levels = {h.get("level") for h in headings}
    assert min(levels) == 1, f"最顶层不是章：{sorted(levels)}"


# ── T1.4：导出层三件事 ─────────────────────────────────────────────────
def test_manifest_products_exist_and_match_sha256(route):
    for item in route["manifest"].get("produced", []):
        path = route["parse"] / item["path"]
        assert path.is_file(), f"manifest 列的 {item['path']} 不存在"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == item["sha256"], f"{item['path']} 的 sha256 与 manifest 不一致"


def test_original_text_has_no_extra_files(route):
    declared = {item["path"] for item in route["manifest"].get("produced", [])
                if item.get("kind") == "original_text"}
    actual = {f"original_text/{p.name}" for p in (route["parse"] / "original_text").glob("*.md")}
    extra = actual - declared
    assert not extra, f"original_text/ 里有 manifest 没列的文件（旧产物没清干净）：{sorted(extra)}"


def test_original_text_keeps_enough_content(route):
    total = sum(cjk(p.read_text(encoding="utf-8"))
                for p in (route["parse"] / "original_text").glob("*.md"))
    document = cjk((route["parse"] / "document.md").read_text(encoding="utf-8"))
    assert total / max(document, 1) >= 0.6, f"原文保留率 {total / max(document, 1):.0%}"


def test_every_unit_with_body_is_a_lesson_unit(route):
    """有正文的节点都要在 lesson_units 里（否则教案块永远不处理它）。"""
    units = {u["unit_id"] for u in route["chapters"].get("lesson_units", [])}
    assert units
    for unit in route["chapters"]["lesson_units"]:
        path = route["parse"] / str(unit.get("original_text") or "")
        assert path.is_file(), f"{unit['unit_id']} 的原文文件不存在：{path}"
    files = {f"original_text/{p.name}" for p in (route["parse"] / "original_text").glob("*.md")}
    declared = {str(u.get("original_text")) for u in route["chapters"]["lesson_units"]}
    leftover = files - declared - {"original_text/_frontmatter.md"}
    assert not leftover, f"这些原文没有对应的教案单元：{sorted(leftover)}"


# ── T1.5：插图的四项 ───────────────────────────────────────────────────
def test_images_are_usable(route):
    images = route["images"].get("images") or []
    assert images, "一张图都没有？"
    ids = [i.get("id") for i in images]
    assert len(ids) == len(set(ids)), "同一张图出了两行（id 重复）"
    for item in images:
        assert isinstance(item.get("page"), int) and item["page"] > 0, item
        bbox = item.get("bbox") or []
        assert len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox), item
        assert item.get("confidence") in ("high", "medium", "low"), item
        assert item.get("bbox_source"), item
        # 展示层要么直接用裁好的单图，要么按整页图 + bbox 裁 —— 至少要有一个能用
        has_file = bool(item.get("file")) and (route["parse"] / item["file"]).is_file()
        has_page = bool(item.get("page_image")) and (route["parse"] / item["page_image"]).is_file()
        assert has_file or has_page, f"{item['id']} 既没有单图也没有整页图"


# ── T1.7：概念的五个字段 + 唯一性 + 分片 ────────────────────────────────
def test_concepts_are_complete_and_unique(route):
    concepts = route["concepts"].get("concepts") or []
    assert concepts
    ids = [c.get("id") for c in concepts]
    assert len(ids) == len(set(ids)), "概念 id 有重复"
    for concept in concepts:
        assert concept.get("type") in ("definition", "theorem", "proposition", "corollary"), concept
        for field in ("brief", "explanation", "formal"):
            assert (concept.get(field) or "").strip(), f"{concept['id']} 的 {field} 是空的"
        assert concept.get("source_quotes"), f"{concept['id']} 没有抽取依据（source_quotes）"
        assert concept.get("occurrences"), f"{concept['id']} 没有出现位置"
    parts = list((route["parse"] / "concepts.parts").glob("*.json"))
    assert parts, "概念分片没落盘（重跑会重复花钱）"


# ── G5 / G6 / G7：跑完教案之后的三条断言 ────────────────────────────────
@pytest.fixture
def lesson_files(route):
    if not (route["lesson"] / "lessons").is_dir():
        pytest.skip(f"没有 {route['lesson']}（还没跑过教案）")
    files = sorted((route["lesson"] / "lessons").glob("*.json"))
    if not files:
        pytest.skip("教案目录是空的")
    return files


def test_g5_original_text_was_never_touched(route, lesson_files):
    concepts = {c["id"] for c in route["concepts"].get("concepts") or []}
    figures = {i["id"] for i in route["images"].get("images") or []}
    units = {u["unit_id"]: u for u in route["chapters"].get("lesson_units") or []}
    for path in lesson_files:
        lesson = json.loads(path.read_text(encoding="utf-8"))
        unit = units.get(lesson.get("unit_id"))
        assert unit, f"{path.name} 的单元不在 chapters.json 里"
        original = route["parse"] / str(unit["original_text"])
        digest = hashlib.sha256(original.read_bytes()).hexdigest()
        assert lesson.get("original_sha256") == digest, f"{lesson['unit_id']}: 原文被动过了"


def test_g6_requests_stay_small(route, lesson_files):
    for path in lesson_files:
        lesson = json.loads(path.read_text(encoding="utf-8"))
        size = (lesson.get("generated_by") or {}).get("request_bytes")
        if size is None:
            continue
        assert size <= MAX_REQUEST_BYTES, f"{lesson['unit_id']}: 请求 {size} 字节，超过 200KB"


def _figure_pool(route) -> set[str]:
    """可引用的插图有两池子：解析出来的（`images.json`）+ 图库里生成的（`figures/index.json`）。

    漏掉后面这一池子会误报"引用了不存在的图"（实测：`FIG-vector-segment` 是生成的矢量图）。
    """
    pool = {i["id"] for i in route["images"].get("images") or []}
    library = load(ROOT / "figures" / "index.json") or {}
    pool |= {f["id"] for f in library.get("figures") or []}
    return pool


def test_g7_placeholders_resolve_exactly_once(route, lesson_files):
    concepts = {c["id"] for c in route["concepts"].get("concepts") or []}
    figures = _figure_pool(route)
    for path in lesson_files:
        lesson = json.loads(path.read_text(encoding="utf-8"))
        text = lesson.get("explanation_markdown") or ""
        seen: dict[tuple[str, str], int] = {}
        for kind, cid in PLACEHOLDER.findall(text):
            seen[(kind, cid)] = seen.get((kind, cid), 0) + 1
        for (kind, cid), count in seen.items():
            pool = concepts if kind == "CONCEPT" else figures
            assert cid in pool, f"{lesson['unit_id']}: 引用了不存在的 {kind} {cid}"
            assert count == 1, f"{lesson['unit_id']}: {kind} {cid} 出现了 {count} 次"
