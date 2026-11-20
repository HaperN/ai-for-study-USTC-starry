"""MinerU 适配块回归测试（全部离线：不打模型、不联网、不读真教材）。

锁住的命题：**这个块必须"假装自己是 OCR"**，而且装错的三种方式都要被抓住 ——

1. 独立公式拆成多行 → 结构层的 `denoise` 会把它当跨页页眉**静默删掉**；
2. 标题带上 `#` → `heading_match()` 不认，全书层级塌掉；
3. 一个 span 里的换行不拆开 → audit 记的行号 ≠ 文件里的物理行，
   于是"三条硬规则检查过了"是假过（实测踩过：说 606 行，其实 717 行）。

另外两条是实测撞出来的真问题，一起锁住：
4. MinerU 把整页目录塞进一个 text 块（条目是"标题 + 空格 + 页码"，没有引导点），
   结构层原有的目录规则认不出来 → 直接跑会认出 **71 个标题**（真值 29）；
5. 页眉被 MinerU 认成 title 时，同一行会出现在 3 页以上 → 结构层的
   "跨页重复即页眉、整行删掉"会把**真标题一起删掉**（实测 `§1.7 集合与映射*` 就是这么消失的）。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "blocks" / "parse" / "mineru"))
sys.path.insert(0, str(ROOT / "lib"))

import adapt as A          # noqa: E402


def block(kind, text=None, *, page_spans=None, index=0, lines=None, **extra):
    """造一个 MinerU 风格的块。`page_spans` 是"每个 line 的 span 列表"。"""
    if lines is None:
        rows = page_spans if page_spans is not None else [[{"type": "text", "content": text or ""}]]
        lines = [{"bbox": [0, 0, 10, 10], "spans": row} for row in rows]
    payload = {"type": kind, "bbox": [0, 0, 10, 10], "index": index, "lines": lines}
    payload.update(extra)
    return payload


def layout(pages, *, version="3.4.4", **meta):
    payload = {"pdf_info": pages, "_version_name": version, "_backend": "hybrid",
               "_ocr_enable": True, "_effort": "medium"}
    payload.update(meta)
    return payload


def write_layout(tmp_path, pages, **kwargs):
    mineru = tmp_path / "mineru"
    mineru.mkdir(exist_ok=True)
    (mineru / "layout.json").write_text(
        json.dumps(layout(pages, **kwargs), ensure_ascii=False), encoding="utf-8")
    return mineru


def page(page_idx, *blocks, discarded=()):
    return {"page_idx": page_idx, "page_size": [595, 842], "para_blocks": list(blocks),
            "preproc_blocks": list(blocks), "discarded_blocks": list(discarded)}


def title(text):
    return block("title", text)


def text(text):
    return block("text", text)


def equation(latex):
    return block("interline_equation", "",
                 lines=[{"spans": [{"type": "interline_equation", "content": latex}]}])


# ── ① 独立公式必须单行 ──────────────────────────────────────────────────
def test_display_equation_stays_on_one_line(tmp_path):
    result = A.adapt(write_layout(tmp_path, [page(0, text("上面"), equation("E = mc^2"))]),
                     tmp_path / "out")
    lines = result.document.splitlines()
    assert "$$E = mc^2$$" in lines
    assert "$$" not in lines, "显示公式被拆成了单独一行的 $$ —— denoise 会当页眉删掉"
    assert result.audit["checks"]["hard_rules"] == []


# ── ② 标题必须裸行、不带 # ──────────────────────────────────────────────
def test_title_is_a_bare_line(tmp_path):
    result = A.adapt(write_layout(tmp_path, [page(0, title("§1.1 向量的线性运算"))]), tmp_path / "out")
    assert "§1.1 向量的线性运算" in result.document.splitlines()
    assert not any(line.startswith("#") for line in result.document.splitlines())


# ── ③ span 里的换行必须拆成物理行（否则 audit 行号对不上文件）────────────
def test_embedded_newlines_become_physical_lines(tmp_path):
    listing = block("text", lines=[{"spans": [{"type": "text", "content": "第一行\n第二行\n第三行"}]}])
    result = A.adapt(write_layout(tmp_path, [page(0, listing)]), tmp_path / "out")
    assert result.counts["document_lines"] == len(result.document.splitlines())
    assert len(result.document.splitlines()) >= 4          # 页标记 + 三行


# ── ④ 目录页整页丢，且丢得可查 ──────────────────────────────────────────
def test_toc_page_is_dropped_and_recorded(tmp_path):
    toc = block("text", lines=[{"spans": [{"type": "text",
                "content": "第一章 向量 7\n1.1 向量的线性运算 7\n1.1.1 向量及其表示 7"}]}])
    body = page(1, title("第一章 向量"), text("向量是力学与物理等领域中诸多概念的数学抽象"))
    result = A.adapt(write_layout(tmp_path, [page(0, title("目录"), toc), body]), tmp_path / "out")
    document = result.document
    assert "第一章 向量 7" not in document, "目录条目混进正文了 —— 会被当成章节结构"
    assert "第一章 向量" in document, "真标题被误伤"
    assert result.counts["dropped_toc_blocks"] == 2
    assert any(item["type"] == "toc" for item in result.audit["discarded"])
    # 丢的内容要留档（"丢得可查"是 MinerU 相对我们的优势，照抄）
    assert any(item["text"].startswith("第一章 向量 7") for item in result.audit["discarded"])


def test_short_toc_continuation_page_is_still_dropped(tmp_path):
    """实测第 6 页只剩两行 —— 光靠行的形状判不出来，必须靠 `目录` 标记整页丢。"""
    short = block("text", lines=[{"spans": [{"type": "text", "content": "7.4.3 正交变换 241\n7.5 酉空间*"}]}])
    result = A.adapt(write_layout(tmp_path, [page(0, title("目录"), short)]), tmp_path / "out")
    assert "7.5 酉空间*" not in result.document
    assert result.counts["dropped_toc_blocks"] == 2


# ── ⑤ 通过页眉位置排除重复，不能仅凭标题同名删正文 ──────────────────
def test_repeated_title_keeps_body_and_removes_headers(tmp_path):
    marker = block("page_number", "2", bbox=[500, 95, 515, 107])
    pages = [page(0, block("title", "§1.7 集合与映射*", bbox=[200, 250, 380, 266]), text("正文一")),
             page(1, block("title", "§1.7 集合与映射*", bbox=[100, 94, 280, 108]),
                  text("正文二"), discarded=[marker]),
             page(2, block("text", "§1.7 集合与映射*", bbox=[100, 94, 280, 108]),
                  text("正文三"), discarded=[marker])]
    result = A.adapt(write_layout(tmp_path, pages), tmp_path / "out")
    occurrences = [line for line in result.document.splitlines() if line.strip() == "§1.7 集合与映射*"]
    assert len(occurrences) == 1, "只应保留正文标题，title/text 页眉均须排除"
    assert result.counts["dropped_repeat_titles"] == 1
    assert any(item["type"] == "title-repeat" for item in result.audit["discarded"])


# ── ⑥ 版本门：认不出就报错，且一个文件都不写 ────────────────────────────
def test_unknown_version_stops_before_writing(tmp_path):
    mineru = write_layout(tmp_path, [page(0, text("正文"))], version="9.9.9")
    out = tmp_path / "out"
    with pytest.raises(A.MinerUFormatError) as error:
        A.adapt(mineru, out)
    assert "9.9.9" in str(error.value)
    assert not out.exists(), "报错时不许留下半个产物"


def test_mineru_4_schema_is_rejected_with_a_clear_message(tmp_path):
    mineru = tmp_path / "mineru"
    mineru.mkdir()
    (mineru / "layout.json").write_text(json.dumps(
        {"schema": "docvortex.middle", "schema_version": "2.0", "pages": []}), encoding="utf-8")
    with pytest.raises(A.MinerUFormatError) as error:
        A.adapt(mineru, tmp_path / "out")
    assert "4.0" in str(error.value)


def test_unrelated_json_is_rejected(tmp_path):
    mineru = tmp_path / "mineru"
    mineru.mkdir()
    (mineru / "layout.json").write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    with pytest.raises(A.MinerUFormatError):
        A.adapt(mineru, tmp_path / "out")


# ── ⑦ 图片：进 figures.json，图题进正文（跟 OCR 路径一致）───────────────
def test_image_and_caption(tmp_path):
    mineru = write_layout(tmp_path, [page(0, block(
        "image", lines=[],
        blocks=[{"type": "image_body", "lines": [{"spans": [
                    {"type": "image", "image_path": "https://cdn.example/a/abc123.jpg"}]}]},
                {"type": "image_caption", "lines": [{"spans": [
                    {"type": "text", "content": "图 1.1 向量的加法"}]}]}]))])
    (mineru / "images").mkdir()
    (mineru / "images" / "abc123.jpg").write_bytes(b"fake-jpeg-bytes")

    result = A.adapt(mineru, tmp_path / "out")
    figure = result.figures["images"][0]
    assert figure["id"] == "IMG-0001"
    assert figure["bbox_source"] == "mineru" and figure["confidence"] == "high"
    assert figure["caption"] == "图 1.1 向量的加法"
    assert (tmp_path / "out" / figure["file"]).is_file(), "单图必须落到产物里"
    assert figure["sha256"]
    assert "图 1.1 向量的加法" in result.document.splitlines(), "图题要作为正文一行（OCR 路径就是这样）"


def test_missing_image_is_recorded_not_silently_skipped(tmp_path):
    mineru = write_layout(tmp_path, [page(0, block(
        "image", lines=[],
        blocks=[{"type": "image_body", "lines": [{"spans": [
                    {"type": "image", "image_path": "https://cdn.example/nope.jpg"}]}]}]))])
    result = A.adapt(mineru, tmp_path / "out")
    assert result.counts["figures"] == 0
    assert result.audit["images_missing"], "图找不到必须留痕（否则就是静默丢图）"
    assert result.audit["warnings"]


# ── ⑧ figures.json 的形状要跟图片层一致（导出层照旧读它）────────────────def test_figures_json_shape_matches_the_image_layer(tmp_path):
    result = A.adapt(write_layout(tmp_path, [page(0, text("正文"))]), tmp_path / "out")
    payload = json.loads((tmp_path / "out" / "figures.json").read_text(encoding="utf-8"))
    assert payload["_meta"]["file_type"] == "figures"
    for key in ("pdf", "scale", "pages", "images", "page_index", "confidence_counts"):
        assert key in payload, f"figures.json 少了 {key}，跟图片层的产物形状不一致"
    assert payload["scale"] == A.PAGE_SCALE


# ── ⑨ 丢弃块记账（MinerU 的 discarded_blocks：页码/页眉）────────────────
def test_discarded_blocks_are_accounted(tmp_path):
    discarded = [{"type": "page_number", "lines": [{"spans": [{"type": "text", "content": "11"}]}]},
                 {"type": "header", "lines": [{"spans": [{"type": "text", "content": "第一章 向量"}]}]}]
    result = A.adapt(write_layout(tmp_path, [page(0, text("正文"), discarded=discarded)]), tmp_path / "out")
    assert result.counts["discarded_by_type"] == {"page_number": 1, "header": 1}
    assert "11" not in result.document.splitlines(), "页码不该进正文"
    assert len(result.audit["discarded"]) == 2


# ── ⑩ dry-run 不写文件 ──────────────────────────────────────────────────
def test_dry_run_writes_nothing(tmp_path):
    out = tmp_path / "out"
    result = A.adapt(write_layout(tmp_path, [page(0, text("正文"))]), out, dry_run=True)
    assert result.document
    assert not out.exists()


# ── ⑪ 行号映射要能回指（教案里某句话不对，能说出是第几页第几块）──────────
def test_block_to_line_mapping_is_usable(tmp_path):
    result = A.adapt(write_layout(tmp_path, [page(0, title("§1.1 向量"), text("第一段正文"))]),
                     tmp_path / "out")
    lines = result.document.splitlines()
    for entry in result.audit["blocks"]:
        first, last = entry["lines"]
        assert 1 <= first <= last <= len(lines)
        assert lines[first - 1].strip(), f"第 {entry['page']} 页那块的第一行是空的，映射偏了"
    assert result.audit["blocks"][-1]["preview"].startswith("第一段正文")


# ── ⑫ 整页图题池：并排多图被 MinerU 把图题全挂到最后一块上，要按左右顺序重配 ──
def test_side_by_side_figures_get_their_own_captions(tmp_path):
    """实测第 29 页：三张并排小图，三条图题全挂在最右边那块上。"""
    def body(name):
        return {"type": "image_body", "lines": [{"spans": [
            {"type": "image", "image_path": f"https://cdn.example/{name}.jpg"}]}]}

    merged = {"type": "image", "bbox": [389, 209, 480, 269], "index": 2, "lines": [], "blocks": [
        body("c"),
        {"type": "image_caption", "lines": [{"spans": [{"type": "text", "content": "图 1.11 集合的包含"}]}]},
        {"type": "image_caption", "lines": [{"spans": [{"type": "text", "content": "图 1.12 集合的交"}]}]},
        {"type": "image_caption", "lines": [{"spans": [{"type": "text", "content": "图 1.13 集合的并"}]}]},
    ]}
    left = {"type": "image", "bbox": [145, 206, 211, 269], "index": 0, "lines": [],
            "blocks": [body("a")]}
    middle = {"type": "image", "bbox": [262, 207, 360, 271], "index": 1, "lines": [],
              "blocks": [body("b")]}
    mineru = write_layout(tmp_path, [page(0, left, middle, merged)])
    for name in ("a", "b", "c"):
        (mineru / "images").mkdir(exist_ok=True)
        (mineru / "images" / f"{name}.jpg").write_bytes(b"x")

    figures = A.adapt(mineru, tmp_path / "out").figures["images"]
    assert [f["caption"] for f in figures] == ["图 1.11 集合的包含", "图 1.12 集合的交", "图 1.13 集合的并"]
    assert all(f["caption_from_pool"] for f in figures)


# ── ⑬ 面板标签（(a)/(b)）不许冒充图题 ────────────────────────────────────
def test_panel_labels_are_not_captions(tmp_path):
    def panel(name, caption):
        return {"type": "image", "bbox": [0, 0, 10, 10], "index": 0, "lines": [], "blocks": [
            {"type": "image_body", "lines": [{"spans": [
                {"type": "image", "image_path": f"https://cdn.example/{name}.jpg"}]}]},
            {"type": "image_caption", "lines": [{"spans": [{"type": "text", "content": caption}]}]},
        ]}

    mineru = write_layout(tmp_path, [page(0, panel("a", "(a)"), panel("b", "图 1.9"))])
    (mineru / "images").mkdir(exist_ok=True)
    for name in ("a", "b"):
        (mineru / "images" / f"{name}.jpg").write_bytes(b"x")

    figures = A.adapt(mineru, tmp_path / "out").figures["images"]
    # D6-A 之后：并排的两个面板并成一张，图题只有 `图 1.9`（面板标签不许冒充图题）
    assert len(figures) == 1
    assert figures[0]["caption"] == "图 1.9"
    assert "(a)" not in (figures[0]["caption"] or "")
    assert figures[0]["panel_label"] == "(a)"


# ── ⑭ 只有图题、没有 image_body 的块不许"造"出一张图 ────────────────────
def test_caption_without_image_makes_no_figure(tmp_path):
    orphan = {"type": "image", "bbox": [133, 112, 249, 227], "index": 0, "lines": [], "blocks": [
        {"type": "image_caption", "lines": [{"spans": [{"type": "text", "content": "(b) 左手系"}]}]}]}
    real = {"type": "image", "bbox": [321, 111, 437, 227], "index": 1, "lines": [], "blocks": [
        {"type": "image_body", "lines": [{"spans": [
            {"type": "image", "image_path": "https://cdn.example/real.jpg"}]}]},
        {"type": "image_caption", "lines": [{"spans": [{"type": "text", "content": "图1.5"}]}]}]}
    mineru = write_layout(tmp_path, [page(0, orphan, real)])
    (mineru / "images").mkdir(exist_ok=True)
    (mineru / "images" / "real.jpg").write_bytes(b"x")

    result = A.adapt(mineru, tmp_path / "out")
    assert len(result.figures["images"]) == 1, "没有 image_body 的块被造出了一张假图"
    assert result.figures["images"][0]["caption"] == "图1.5"
    assert result.audit["captions_without_image"] == ["(b) 左手系"]


# ── ⑮ D6-A：同一张图的多个面板并回一张（bbox 取并集）────────────────────
def _panel(name, bbox, caption):
    return {"type": "image", "bbox": bbox, "index": 0, "lines": [], "blocks": [
        {"type": "image_body", "lines": [{"spans": [
            {"type": "image", "image_path": f"https://cdn.example/{name}.jpg"}]}]},
        {"type": "image_caption", "lines": [{"spans": [{"type": "text", "content": caption}]}]},
    ]}


def test_side_by_side_panels_are_merged_into_one_figure(tmp_path):
    """实测第 15 页图 1.5、第 21 页图 1.9：MinerU 拆成两块，我们要并回一张。"""
    mineru = write_layout(tmp_path, [page(0,
        _panel("a", [100, 100, 200, 200], "(a)"),
        _panel("b", [300, 100, 400, 200], "图 1.9"))])
    (mineru / "images").mkdir(exist_ok=True)
    for name in ("a", "b"):
        (mineru / "images" / f"{name}.jpg").write_bytes(b"x")

    result = A.adapt(mineru, tmp_path / "out")
    figures = result.figures["images"]
    assert len(figures) == 1, "并排的两个面板应该并成一张图"
    assert figures[0]["caption"] == "图 1.9"
    assert figures[0]["bbox_pt"] == [100.0, 100.0, 400.0, 200.0]      # 并集
    assert figures[0]["bbox_source"] == "mineru-merged"
    assert len(figures[0]["panels"]) == 2
    assert result.audit["merged_figures"] == 1


def test_stacked_figures_are_not_merged(tmp_path):
    """上下叠放的两个真图不许被误并成一张（几何护栏）。"""
    mineru = write_layout(tmp_path, [page(0,
        _panel("a", [100, 100, 200, 200], ""),
        _panel("b", [100, 400, 200, 500], "图 1.9"))])
    (mineru / "images").mkdir(exist_ok=True)
    for name in ("a", "b"):
        (mineru / "images" / f"{name}.jpg").write_bytes(b"x")

    result = A.adapt(mineru, tmp_path / "out")
    assert len(result.figures["images"]) == 2, "上下叠放的图被误并了"
    assert result.audit["merged_figures"] == 0


# ── ⑯ 相邻行内公式不许拼出 $$（否则渲染器会把中间那段当显示公式吞掉）──────
def test_adjacent_inline_equations_do_not_form_a_fake_display_math(tmp_path):
    """实测：MinerU 把 `\\sigma(1)=2,\\sigma(2)=3,` 和 `\\sigma(3)=1` 拆成两个相邻 span，
    直接拼起来是 `$A$$B$` → 渲染器把 `$$` 当显示公式起点，一处公式渲染失败、
    13 个内部槽位泄漏到页面上。修法是相邻公式之间插一个空格。
    """
    spans = [{"type": "inline_equation", "content": r"\sigma(1)=2,\sigma(2)=3,"},
             {"type": "inline_equation", "content": r"\sigma(3)=1"}]
    result = A.adapt(write_layout(tmp_path, [page(0, block("text", lines=[{"spans": spans}]))]),
                     tmp_path / "out")
    body = [line for line in result.document.splitlines() if "sigma" in line][0]
    assert "$$" not in body, f"两个相邻的行内公式拼出了 $$：{body}"
    assert result.audit["checks"]["hard_rules"] == []


def test_check_document_flags_a_fake_display_math():
    """手写一行 `$A$$B$` 也要被硬规则抓住（不能只在渲染时才发现）。"""
    problems = A.check_document(["前面 $A$$B$ 后面"])
    assert any("$$" in p for p in problems), problems
    assert A.check_document(["$$E = mc^2$$"]) == [], "真正的显示公式行不该被误报"


# ── ⑰ 输入形态体检（preflight）：花钱之前先看"长得对不对" ────────────────
import preflight as PF      # noqa: E402


def _find(report, cid):
    return next(c for c in report["checks"] if c["id"] == cid)


def test_preflight_passes_on_a_clean_product(tmp_path):
    mineru = write_layout(tmp_path, [page(0, title("§1.1 向量"), text("正文"))])
    report = PF.preflight(mineru)
    assert report["passed"] is True
    assert report["blocked"] == []
    assert _find(report, "F1")["状态"] == "通过"
    assert report["_meta"]["file_type"] == "mineru-preflight"      # G3


def test_preflight_blocks_an_unknown_version(tmp_path):
    mineru = write_layout(tmp_path, [page(0, text("正文"))], version="4.0.1")
    report = PF.preflight(mineru)
    assert report["passed"] is False
    assert report["blocked"] == ["F1"]


def test_preflight_downgrades_control_characters_when_adapter_handles_them(tmp_path, monkeypatch):
    """控制字符：**适配层声明会剥并记账时就只提醒，否则照样拦**。

    为什么这样锁（2026-09-29 实测《Basic Analysis》）：那份 PDF 的字体产出 1680 处 U+FFFD，
    老行为是"一律拦" → 整本书卡在门口跑不动；而"剥掉 + 记进 audit"恰恰满足
    "别让脏数据静默流过"这条本意（H7/R-b）。所以判据是**适配层的声明**，不是固定值。
    """
    # ⚠️ 必须 patch **preflight 实际读的那一份模块对象**：本文件是以顶层模块方式
    #    导入 adapt 的（见文件头的 `import adapt as A`），所以要用 `A`；
    #    若在这里另 `from blocks.parse.mineru import adapt`，那会是**另一份副本**，
    #    monkeypatch 打偏（2026-09-29 实测踩过）。
    mineru = write_layout(tmp_path, [page(0, text("正文里有\u0000一个空字符"))])

    # ① 适配层声明会处理 → 只提醒，不拦；但**必须把处数报出来**
    report = PF.preflight(mineru)
    assert report["passed"] is True
    f2 = next(c for c in report["checks"] if c["id"] == "F2")
    assert f2["级别"] == "提醒"
    assert "1 处" in f2["观察"]

    # ② 适配层**没声明**时会怎样 → 直接测判据本身（**不去 patch 全局量**：
    #    本仓里 `adapt` 有 `adapt` / `blocks.parse.mineru.adapt` 两份模块对象，
    #    patch 一个必然打偏 —— 2026-09-29 实测。判据抽成了纯函数就是为了能这么测）。
    assert PF._f2_verdict(handles=False, dirty_count=1680) == ("拦", "拦")
    assert PF._f2_verdict(handles=True, dirty_count=1680) == ("提醒", "通过")
    assert PF._f2_verdict(handles=True, dirty_count=0) == ("拦", "通过")


def test_preflight_blocks_a_missing_page_size(tmp_path):
    broken = {"page_idx": 0, "para_blocks": [text("正文")],
              "preproc_blocks": [text("正文")], "discarded_blocks": []}
    mineru = write_layout(tmp_path, [broken])
    report = PF.preflight(mineru)
    assert report["passed"] is False
    assert "F3" in report["blocked"]


def test_preflight_warns_about_glued_formulas_without_blocking(tmp_path):
    """相邻公式会被适配块插空格修掉 —— 但要报出来（悄悄改了一个字节必须可见）。"""
    spans = [{"type": "inline_equation", "content": r"\sigma(1)=2,"},
             {"type": "inline_equation", "content": r"\sigma(3)=1"}]
    mineru = write_layout(tmp_path, [page(0, block("text", lines=[{"spans": spans}]))])
    report = PF.preflight(mineru)
    assert report["passed"] is True               # 已处理，不该拦
    assert "F11" in report["warnings"]
    assert "1 处" in _find(report, "F11")["观察"]


def test_preflight_warns_about_mismatched_figures_and_repeated_titles(tmp_path):
    panels = [_panel("a", [100, 100, 200, 200], ""), _panel("b", [300, 100, 400, 200], "图 1.9")]
    mineru = write_layout(tmp_path, [page(0, *panels), page(1, title("§1.1 向量"), text("正文")),
                                     page(2, title("§1.1 向量"), text("正文"))])
    report = PF.preflight(mineru)
    assert "F7" in report["warnings"], report["warnings"]     # 2 图 1 题
    assert "F6" in report["warnings"], report["warnings"]     # 重复标题


# ══════════════════════════════════════════════════════════════════════════
# 2026-09-27 用四本真教材（线性代数 / 复变函数 / 数值计算方法 / 数分讲义第二册）抓出来的
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("token,value", [
    ("20", 20), ("Ⅰ", 1), ("Ⅱ", 2), ("Ⅴ", 5), ("Ⅸ", 9), ("Ⅻ", 12),
    ("iv", 4), ("XI", 11), ("XIV", 14), ("？", None), ("", None),
])
def test_page_number_reads_roman(token, value):
    assert A.page_number(token) == value


@pytest.mark.parametrize("row", [
    "第1章 复数和平面点集 …… (Ⅰ)",      # 罗马数字页码（前置材料编页）
    "1.2 平面点集 …… (Ⅴ)",
    "第2章 留数及其应用 …… (107)",       # 括号里的阿拉伯页码
    "第一章 向量 7",                      # 页码前只有一个空格（线性代数）
])
def test_listing_line_accepts_real_toc_rows(row):
    assert A.LISTING_LINE.match(row), row


def test_toc_entry_keeps_roman_label():
    """罗马页码要能转成数、也要留原文（`第1章` 就在罗马数字那段里）。

    实测《复变函数》：这 5 行整行认不出 → 对账基准里少了第 1 章 →
    体检表显示"目录说 8 章 / 解析出 9 章"（**解析是对的、基准错了**）。
    """
    entry = A._toc_entry("1.2 平面点集 …… (Ⅴ)", 9)
    assert entry["text"] == "1.2 平面点集"
    assert entry["toc_page"] == 5 and entry["toc_page_label"] == "Ⅴ"
    assert entry["level"] == 2


@pytest.mark.parametrize("text,matter", [
    ("目录", True), ("第四版前言", True), ("附录2 C语言程序示例", True),
    ("第 8 章综合习题", True), ("习题参考答案", True), ("习题 8.1", True),
    ("绪论", False), ("第1章插值", False), ("空间解析几何", False),
])
def test_book_matter_is_not_a_chapter(text, matter):
    """前言/附录/习题集/答案集都不是"章"，`绪论` 是（它是正文）。"""
    assert A.is_book_matter(text) is matter


def test_normalize_outline_levels_shifts_wrapped_outline():
    """书签比书的结构多一层包装（根节点 `目录`）时要能自动对齐。

    《数值计算方法》：章在 lv2、节 lv3 —— 不归一化的话 `chapters` 只有一个 `目录`。
    """
    wrapped = [{"text": "目录", "level": 1}, {"text": "第1章插值", "level": 2},
               {"text": "1.1 Lagrange插值多项式", "level": 3},
               {"text": "1.1.1 线性插值", "level": 4}]
    out, shift = A.normalize_outline_levels([dict(x) for x in wrapped])
    assert shift == 1
    assert [x["level"] for x in out] == [0, 1, 2, 3]


def test_normalize_outline_levels_keeps_flat_outline():
    """《数分讲义第二册》：章就在 lv1 且**不带**「第N章」；章末的 `第8章综合习题`
    带章号，不能让它把位移量带偏（真值 shift=0，带偏后真章名会被降级、节被升格成章）。"""
    flat = [{"text": "空间解析几何", "level": 1},
            {"text": "向量与坐标系", "level": 2},
            {"text": "第 8 章综合习题", "level": 2},
            {"text": "习题 8.1", "level": 4}]
    out, shift = A.normalize_outline_levels([dict(x) for x in flat])
    assert shift == 0
    assert out[0]["level"] == 1


def test_region_allows_repeated_mark_in_but_not_out(tmp_path):
    """重复标题只能**进入**区域、不能**结束**区域（两个方向都是实测抓出来的）。

    * 每章习题都叫 `习题`（复变函数）→ 重复是正常的，必须还能进入习题区；
    * 页眉仿冒的 `§1.3 行列式`（线性代数里是 `§5.7 子空间运算*`）→ 不许结束习题区，
      否则那一章的习题整段漏抽（实测 37 道真题）。
    """
    pages = [
        page(0, title("第1章 向量"), text("正文。")),
        page(1, title("§1.1 线性运算"), text("正文。")),
        page(2, title("习题"), text("1. 求下列向量组的秩")),
        page(3, title("§1.2 方程组"), text("正文。")),          # 新节标题 → 结束习题区
        page(4, title("习题"), text("1. 求下列方程组的解")),      # 重复标题 → 允许进入
        page(5, title("§1.3 行列式"), text("正文。")),
        page(6, title("习题"), text("1. 计算下列行列式")),        # 重复标题 → 允许进入
        page(7, block("title", "§1.3 行列式", bbox=[100, 94, 280, 108]),
             text("页眉仿冒的重复标题"),
             discarded=[block("page_number", "8", bbox=[500, 95, 515, 107])]),
        page(8, text("还在习题区里")),
    ]
    out = tmp_path / "out"
    A.adapt(write_layout(tmp_path, pages), out)
    regions = json.loads((out / "headings.json").read_text(encoding="utf-8"))["page_region"]
    assert regions["5"] == "body" and regions["6"] == "exercise", \
        "每章都叫「习题」时，重复标题必须还能进入习题区"
    assert regions["8"] == "exercise", "重复标题第二次没能进入习题区"
    assert regions["9"] == "exercise", \
        "页眉仿冒的重复节标题把习题区结束了（第 5 章会漏 37 道题）"


def test_intro_and_section_exercise_marks(tmp_path):
    """`绪论` 进正文（不是章前材料）、`习题 8.1` 划出习题区并在下一节结束。"""
    listing = block("text", lines=[{"spans": [{"type": "text",
               "content": "绪论 1\n0.1 数值计算方法与算法 1"}]}])
    pages = [
        page(0, title("目录"), listing),
        page(1, title("绪论"), text("正文。")),
        page(2, title("0.1 数值计算方法与算法"), text("正文。")),
        page(3, title("第1章 插值"), text("正文。")),
        page(4, title("习题 1.1"), text("1. 求下列插值多项式")),
        page(5, title("§1.2 Newton插值"), text("正文。")),
    ]
    out = tmp_path / "out"
    A.adapt(write_layout(tmp_path, pages), out)
    heads = {h["text"]: h["region"]
             for h in json.loads((out / "headings.json").read_text(encoding="utf-8"))["headings"]}
    assert heads["绪论"] == "body", "绪论被当成章前材料了（里面 13 页内容会全丢）"
    assert heads["0.1 数值计算方法与算法"] == "body"
    assert heads["习题 1.1"] == "exercise"
    assert heads["§1.2 Newton插值"] == "body", "节末习题区没在下一节结束"


# ── 代码/算法块（2026-09-27 实测《数值计算方法》：58 个算法块整块丢内容）────────
def _code_block(lines_text):
    """MinerU 的 `code` 块：内容藏在**嵌套的** `blocks[].lines` 里，顶层 `lines` 是空的。"""
    inner = {"type": "text", "index": 0,
             "lines": [{"bbox": [0, 0, 9, 9], "spans": [{"type": "text", "content": lines_text}]}]}
    return {"type": "code", "sub_type": "algorithm", "index": 1, "bbox": [0, 0, 9, 9],
            "blocks": [inner]}


def test_nested_code_block_content_is_not_lost(tmp_path):
    """算法块的正文在**嵌套字段**里，只渲染顶层就会整块丢内容。"""
    result = A.adapt(write_layout(tmp_path, [page(0, _code_block("step1 输入 x\nstep2 计算 f(x)"))]),
                     tmp_path / "out")
    assert "step1 输入 x" in result.document, "算法块的内容丢了"
    assert "step2 计算 f(x)" in result.document


def test_code_lines_are_indented_and_recorded(tmp_path):
    """代码行**缩进 4 格**（markdown 里就是代码块），并把行号记进 headings.json。"""
    out = tmp_path / "out"
    A.adapt(write_layout(tmp_path, [page(0, _code_block("int i;\n#include <stdio.h>"))]), out)
    document = (out / "document.md").read_text(encoding="utf-8").splitlines()
    code_rows = [row for row in document if row.startswith("    ")]
    assert any("#include" in row for row in code_rows), "C 代码没有缩进 —— 会被当成 markdown 标题"
    code_lines = json.loads((out / "headings.json").read_text(encoding="utf-8"))["code_lines"]
    assert code_lines and code_lines[0][0] >= 1 and code_lines[0][1] >= code_lines[0][0]


def test_hard_rules_skip_code_lines():
    """三条硬规则是给**正文**写的：代码里的 `#include`、不配对的 `$` 不算问题。"""
    lines = ["正文一行", "    #include <stdio.h>", "    x = a $ b"]
    assert A.check_document(lines) != []                       # 不跳过时确实会报
    assert A.check_document(lines, code_lines=[[2, 3]]) == []
