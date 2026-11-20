"""MinerU 适配块的"块类型"与"分块合并"测试（全离线）。

这两块都是**全本跑出来才暴露**的：

* 前 40 页只有 text/title/interline_equation/image 四种块；整本 289 页多出
  `index`（目录清单）、`table`、`chart`、`list`、`ref_text`。
  `index` 漏进正文会让整份目录变成章节结构；`table`/`chart` 的内容是**图片**，
  按文本处理会渲染成空行 —— 静默丢内容。
* 云 API 单文件上限 200 页，289 页要切块；而**分块返回的 `page_idx` 是从 0 开始的**
  （实测：要第 201-202 页，回来是 `[0, 1]`），合并时必须按起始页加偏移，
  否则页码全错、`[第 N 页]` 标记和插图页码全错。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "blocks" / "parse" / "mineru"))
sys.path.insert(0, str(ROOT / "lib"))

import adapt as A          # noqa: E402
import fetch as F          # noqa: E402
import preflight as PF     # noqa: E402

from test_mineru_adapt import block, layout, page, text, title, write_layout   # noqa: E402


# ── ① index（目录）整块丢 ──────────────────────────────────────────────
def test_index_blocks_are_dropped_like_the_toc(tmp_path):
    """pipeline 后端把目录清单标成 `index`（hybrid 标成 text）—— 不认它目录就漏进正文。"""
    toc = {"type": "index", "bbox": [0, 0, 10, 10], "index": 0, "lines": [{"spans": [
        {"type": "text", "content": "第一章 向量 7\n1.1 向量的线性运算 7\n1.1.1 向量及其表示 7"}]}]}
    body = page(1, title("第一章 向量"), text("向量是既有大小又有方向的量。"))
    result = A.adapt(write_layout(tmp_path, [page(0, title("目录"), toc), body]), tmp_path / "out")
    assert "第一章 向量 7" not in result.document, "目录清单漏进正文了"
    assert "第一章 向量" in result.document, "真标题被误伤"
    assert any(item["type"] == "index" for item in result.audit["discarded"]), "index 没记账"


# ── ② table / chart 按图处理（它们的内容是图片）────────────────────────
def test_table_and_chart_blocks_become_figures(tmp_path):
    def body_block(kind, body_type, name, caption):
        return {"type": kind, "bbox": [10, 10, 200, 120], "index": 0, "lines": [], "blocks": [
            {"type": body_type, "lines": [{"spans": [
                {"type": kind, "image_path": f"https://cdn.example/{name}.jpg"}]}]},
            {"type": "table_caption", "lines": [{"spans": [
                {"type": "text", "content": caption}]}]},
        ]}

    mineru = write_layout(tmp_path, [page(0, body_block("table", "table_body", "t1", "表 1.1 矩阵"),
                                             body_block("chart", "chart_body", "c1", "图 1.2 曲线"))])
    (mineru / "images").mkdir(exist_ok=True)
    for name in ("t1", "c1"):
        (mineru / "images" / f"{name}.jpg").write_bytes(b"x")

    result = A.adapt(mineru, tmp_path / "out")
    figures = result.figures["images"]
    assert len(figures) == 2, "表格/图被打成空文本丢掉了"
    assert [f["caption"] for f in figures] == ["表 1.1 矩阵", "图 1.2 曲线"]
    assert A.image_body_path({"blocks": [{"type": "table_body", "lines": [
        {"spans": [{"type": "table", "image_path": "x.jpg"}]}]}]}) == "x.jpg"


def test_table_without_content_is_not_silently_lost(tmp_path):
    """表格没有图体也没文字时，至少不能静默消失：要么出图，要么进 audit 的记账。"""
    empty = {"type": "table", "bbox": [0, 0, 10, 10], "index": 0, "lines": [], "blocks": []}
    result = A.adapt(write_layout(tmp_path, [page(0, empty)]), tmp_path / "out")
    assert result.counts["figures"] == 0
    assert any(item["type"] == "table" for item in result.audit["discarded"]) or \
        result.audit["warnings"], "空表格既没出图也没留痕"


# ── ③ 体检：没见过的块类型要报警 ───────────────────────────────────────
def test_preflight_flags_unknown_block_types(tmp_path):
    weird = {"type": "footnote", "bbox": [0, 0, 10, 10], "index": 0,
             "lines": [{"spans": [{"type": "text", "content": "脚注内容"}]}]}
    report = PF.preflight(write_layout(tmp_path, [page(0, text("正文"), weird)]))
    assert "F12" in report["warnings"], report["warnings"]
    assert "footnote" in str(report["warnings"]) or "footnote" in str(
        [c for c in report["checks"] if c["id"] == "F12"])
    assert report["passed"] is True, "没见过的类型只该提醒，不该拦"


def test_preflight_census_lists_how_each_type_is_handled(tmp_path):
    report = PF.preflight(write_layout(tmp_path, [page(0, title("§1 一"), text("正文"))]))
    census = next(c for c in report["checks"] if c["id"] == "F13")
    assert "当标题" in census["观察"] and "当文本" in census["观察"]


# ── ④ 分块合并：page_idx 必须加偏移（实测最容易错的一步）────────────────
def _chunk(directory: Path, pages_idx: list[int], images: dict[str, bytes]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "layout.json").write_text(json.dumps({
        "_version_name": "3.4.4", "_backend": "hybrid",
        "pdf_info": [{"page_idx": i, "page_size": [595, 842],
                      "para_blocks": [], "preproc_blocks": [], "discarded_blocks": []}
                     for i in pages_idx]}, ensure_ascii=False), encoding="utf-8")
    (directory / "abc_content_list.json").write_text(json.dumps(
        [{"type": "text", "text": f"p{i}", "page_idx": i} for i in pages_idx],
        ensure_ascii=False), encoding="utf-8")
    (directory / "full.md").write_text(f"chunk-{pages_idx[0]}\n", encoding="utf-8")
    if images:
        (directory / "images").mkdir(parents=True, exist_ok=True)
        for name, payload in images.items():
            (directory / "images" / name).write_bytes(payload)
    return directory


def test_merge_chunks_offsets_page_idx_and_merges_assets(tmp_path):
    first = _chunk(tmp_path / "c1", [0, 1], {"a.jpg": b"A"})
    second = _chunk(tmp_path / "c2", [0, 1], {"b.jpg": b"B"})
    merged = F.merge_chunks([first, second], [0, 200], tmp_path / "mineru")

    payload = json.loads((tmp_path / "mineru" / "layout.json").read_text(encoding="utf-8"))
    assert [p["page_idx"] for p in payload["pdf_info"]] == [0, 1, 200, 201]
    assert payload["_version_name"] == "3.4.4" and payload["_backend"] == "hybrid"
    content = json.loads((tmp_path / "mineru" / "content_list.json").read_text(encoding="utf-8"))
    assert [i["page_idx"] for i in content] == [0, 1, 200, 201]
    assert (tmp_path / "mineru" / "images" / "a.jpg").is_file()
    assert (tmp_path / "mineru" / "images" / "b.jpg").is_file()
    assert "chunk-0" in (tmp_path / "mineru" / "full.md").read_text(encoding="utf-8")
    assert merged["pages"] == 4 and merged["images"] == 2


def test_merge_chunks_refuses_conflicting_images(tmp_path):
    first = _chunk(tmp_path / "c1", [0], {"same.jpg": b"A"})
    second = _chunk(tmp_path / "c2", [0], {"same.jpg": b"B"})
    try:
        F.merge_chunks([first, second], [0, 200], tmp_path / "mineru")
    except F.MinerUFetchError as exc:
        assert "同名" in str(exc)
    else:
        raise AssertionError("同名不同内容的图应该拒绝合并，不能悄悄覆盖")


def test_plan_chunks_splits_over_the_api_limit():
    assert F.plan_chunks(289) == [(1, 200), (201, 289)]
    assert F.plan_chunks(200) == [(None, None)]
    assert F.plan_chunks(401) == [(1, 200), (201, 400), (401, 401)]
    assert F.plan_chunks(None) == [(None, None)], "数不出页数时先不切"
