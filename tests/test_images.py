"""图片提取回归测试（全部离线，不打模型）。

锁住的是任务基线 T1.5 的关键行为，以及实测踩过的两个坑：
  * 页边距曾被当成插图（40 页报出 84 个假候选）
  * "相邻空白带合并"的阈值大于正文行距，把整页连成一条带
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "blocks" / "parse" / "images"))

import extract_images as X  # noqa: E402


def _box(x0, y0, x1, y1, text=""):
    return {"text": text, "score": 0.99,
            "box": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}


# ── 重叠度 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("a,b,expected", [
    ([0, 0, 10, 10], [0, 0, 10, 10], 1.0),          # 完全重合
    ([0, 0, 10, 10], [20, 20, 30, 30], 0.0),        # 不相交
    ([0, 0, 10, 10], [0, 0, 5, 10], 1.0),           # 一个包住另一个 → 小框被完全覆盖
    ([0, 0, 10, 10], [5, 0, 15, 10], 0.5),          # 各占一半
])
def test_overlap_ratio(a, b, expected):
    assert X._overlap_ratio(a, b) == pytest.approx(expected)


def test_overlap_ratio_rejects_bad_input():
    assert X._overlap_ratio([0, 0, 1], [0, 0, 1, 1]) == 0.0
    assert X._overlap_ratio(None, [0, 0, 1, 1]) == 0.0


# ── 文字行归并 ──────────────────────────────────────────────────────────
def test_text_lines_groups_boxes_on_the_same_row():
    boxes = [_box(100, 300, 200, 330, "左"), _box(210, 305, 400, 335, "右"),
             _box(100, 400, 300, 430, "下一行")]
    lines = X._text_lines(boxes, 1684)
    assert len(lines) == 2
    assert lines[0][2] == ["左", "右"]              # y 有重叠 → 同一行
    assert lines[1][2] == ["下一行"]


# ── 候选区：必须排除页边距 ──────────────────────────────────────────────
def test_blank_band_found_between_text_lines():
    # 一行在 y=100，一行在 y=900，中间是 800px 的大空白 → 一个候选
    boxes = [_box(100, 100, 900, 130, "上"), _box(100, 900, 900, 930, "下")]
    bands = X.find_blank_bands(boxes, width=1000, height=1000)
    assert len(bands) == 1
    x0, y0, x1, y1 = bands[0]
    assert 130 <= y0 <= 140 and 890 <= y1 <= 900
    assert (x0, x1) == (100, 900)                   # 横向取正文列


def test_page_margins_are_not_figures():
    """顶部/底部的大空白是页边距，不是插图 —— 这是实测踩过的坑。"""
    boxes = [_box(100, 100, 900, 130, "唯一一行")]
    bands = X.find_blank_bands(boxes, width=1000, height=1000)
    assert bands == []


def test_small_gap_is_not_a_figure():
    boxes = [_box(100, 100, 900, 130, "上"), _box(100, 180, 900, 210, "下")]
    assert X.find_blank_bands(boxes, width=1000, height=1000) == []


def test_gap_does_not_cascade_merge_into_whole_page():
    """正文行距很近时，不能把所有间隙连成一条覆盖整页的带（实测踩过的坑）。"""
    boxes = []
    y = 100
    for _ in range(20):                             # 20 行紧密文字
        boxes.append(_box(100, y, 900, y + 30, "行"))
        y += 40
    zones = X.find_blank_bands(boxes, width=1000, height=1000)
    for _, y0, _, y1 in zones:
        assert (y1 - y0) < 1000 * 0.5, "出现了覆盖半页以上的假带"


def test_caption_line_is_absorbed_into_the_band():
    """图题那一行紧跟在图下面，应该被并进候选区（裁出来自带说明）。"""
    boxes = [_box(100, 100, 900, 130, "上"),
             _box(100, 900, 500, 930, "图1.1 向量的加法"),
             _box(100, 1000, 900, 1030, "下")]
    bands = X.find_blank_bands(boxes, width=1000, height=1200)
    assert len(bands) == 1
    assert bands[0][3] >= 930                       # 框到了图题的底部


# ── 图题识别：必须按框判断，不能全页搜 ──────────────────────────────────
def test_caption_only_when_box_starts_with_it():
    boxes = [_box(100, 100, 900, 130, "如图1.1所示，两个向量共线"),   # 正文引用
             _box(100, 200, 500, 230, "图1.1 向量的加法")]           # 真正的图题
    captions = X.captions_on_page(boxes)
    assert captions == ["图1.1 向量的加法"]


def test_caption_rejects_long_or_punctuated_text():
    boxes = [_box(1, 1, 9, 9, "图1.2三向量共面条件这一句话非常长" + "很长" * 20),
             _box(1, 1, 9, 9, "图1.3，然后继续说话。")]
    assert X.captions_on_page(boxes) == []


# ── 整页渲染 ────────────────────────────────────────────────────────────
@pytest.fixture
def tiny_pdf(tmp_path):
    import pymupdf
    document = pymupdf.open()
    for index in range(2):
        page = document.new_page()
        page.insert_text((72, 100), f"page {index + 1}", fontsize=20)
    path = tmp_path / "tiny.pdf"
    document.save(path)
    document.close()
    return path


def test_render_pages_writes_one_file_per_page(tiny_pdf, tmp_path):
    renders = X.render_pages(tiny_pdf, tmp_path, scale=1.0)
    assert [r.page for r in renders] == [1, 2]
    for render in renders:
        file = tmp_path / render.file
        assert file.is_file()
        assert render.width > 0 and render.height > 0
        import hashlib
        assert hashlib.sha256(file.read_bytes()).hexdigest() == render.sha256


# ── 双来源合并 + 置信度 ─────────────────────────────────────────────────
class _FakeClient:
    """假模型：按页返回预置的 bbox，不打网络。"""

    def __init__(self, replies):
        self.replies = replies
        self.session_usage = type("U", (), {"as_dict": staticmethod(lambda: {})})()

    def chat_json(self, messages, *, tag="", images=None, use_cache=None):
        page = int(tag.rsplit("-", 1)[-1])
        return {"data": self.replies.get(page, {"has_figure": False, "figures": []}),
                "usage": {}, "cached": True}


def test_model_only_detection_is_medium_and_flagged(tiny_pdf, tmp_path):
    """本地找不到候选（行内插图）时靠模型补，标成 medium + 待复核 —— 实测第 8 页就是这样。"""
    client = _FakeClient({1: {"has_figure": True, "figures": [
        {"index": 1, "kind": "diagram", "caption": "图1.1 测试图",
         "bbox": [60, 40, 140, 60]}]}})          # 0~1000 归一化
    result = X.extract_images(tiny_pdf, tmp_path, scale=1.0, client=client)
    payload = json.loads(Path(result["figures"]).read_text(encoding="utf-8"))
    # 没有 ocr.json 时筛不出候选页 → 所有页都送模型复核（否则 --model 单独跑等于没跑）
    assert payload["pages_reviewed"] == 2
    assert len(payload["images"]) == 1
    entry = payload["images"][0]
    assert entry["bbox_source"] == "model"
    assert entry["confidence"] == "medium"
    assert entry["needs_review"] is True
    assert entry["caption"] == "图1.1 测试图"
    # bbox 已从 0~1000 归一化换算成像素
    assert entry["bbox"][2] > entry["bbox"][0]


def test_local_and_model_agreeing_marks_high(tmp_path):
    """本地几何与模型框重合 → high，不要求人工复核。"""
    import pymupdf
    document = pymupdf.open()
    page = document.new_page(width=400, height=400)
    page.insert_text((50, 50), "top line", fontsize=12)
    page.insert_text((50, 350), "bottom line", fontsize=12)
    pdf = tmp_path / "gap.pdf"
    document.save(pdf)
    document.close()

    # 本地：中间 50~350 是空白带 → 会成为一个候选
    # 模型：给一个和它高度重合的框（归一化 0~1000 的 100~900）
    ocr = tmp_path / "ocr.json"
    ocr.write_text(json.dumps({"pages": [{"page": 1, "boxes": [
        _box(50, 40, 350, 60, "top line"),
        _box(50, 340, 350, 360, "bottom line")]}]}, ensure_ascii=False),
        encoding="utf-8")
    client = _FakeClient({1: {"has_figure": True, "figures": [
        {"index": 1, "kind": "diagram", "caption": "图1.1 重合",
         "bbox": [100, 100, 900, 900]}]}})

    result = X.extract_images(pdf, tmp_path, scale=1.0, ocr_json=ocr, client=client)
    payload = json.loads(Path(result["figures"]).read_text(encoding="utf-8"))
    assert payload["pages_reviewed"] == 1
    entry = payload["images"][0]
    assert entry["bbox_source"] == "both", payload["images"]
    assert entry["confidence"] == "high"
    assert entry["needs_review"] is False


def test_without_model_only_local_candidates(tmp_path, tiny_pdf):
    result = X.extract_images(tiny_pdf, tmp_path, scale=1.0)
    payload = json.loads(Path(result["figures"]).read_text(encoding="utf-8"))
    assert payload["pages_reviewed"] == 0
    assert payload["_meta"]["field_notes"]["images[].bbox"]
    for entry in payload["images"]:
        assert entry["bbox_source"] == "local"
        assert entry["confidence"] == "medium"


def test_figures_json_has_meta(tiny_pdf, tmp_path):
    result = X.extract_images(tiny_pdf, tmp_path, scale=1.0)
    payload = json.loads(Path(result["figures"]).read_text(encoding="utf-8"))
    meta = payload["_meta"]
    assert meta["file_type"] == "figures"
    assert meta["schema_version"]
    assert meta["generator"] == X.GENERATOR
    assert meta["field_notes"]


# ── 图区下边界：不许切掉图内标注，要把图题并进来 ────────────────────────
def test_band_reaches_through_vertex_labels_to_the_caption():
    """核心坑：插图自己的顶点标注（A/D/O）被 OCR 当成正文行，候选区在标注处
    就截断了，图题也没并进来 —— 实测第 10 页裁出来的图缺下半截。"""
    boxes = [
        _box(100, 50, 900, 80, "第一章 向量"),
        _box(400, 300, 420, 320, "A"),         # 插图顶点标注
        _box(300, 305, 320, 325, "D"),
        _box(250, 302, 270, 322, "O"),
        _box(350, 350, 650, 380, "图1.2 三向量共面条件"),
        _box(100, 400, 900, 430, "下面这一段是正文，不能被并进来"),
    ]
    bands = X.find_blank_bands(boxes, width=1000, height=1000)
    assert len(bands) == 1, bands
    band = bands[0]
    assert band[3] >= 380, band             # 图题被并进来了
    assert band[3] < 400, band              # 但没吃到下面那行正文


def test_band_stops_at_body_text_without_caption():
    """中间隔着正文就不要往后硬并 —— 宁可少框，不能把正文当插图。"""
    boxes = [
        _box(100, 50, 900, 80, "第一章 向量"),
        _box(350, 350, 650, 380, "例1.1.1 证明下列结论"),
    ]
    bands = X.find_blank_bands(boxes, width=1000, height=1000)
    assert len(bands) == 1, bands
    assert bands[0][3] <= 350, bands


def test_captions_in_band_only_counts_captions_inside():
    boxes = [_box(350, 350, 650, 380, "图1.2 三向量共面条件"),
             _box(350, 900, 650, 930, "图1.3 别处的图")]
    assert X.captions_in_band(boxes, [0, 300, 1000, 400]) == ["图1.2 三向量共面条件"]
    assert X.captions_in_band(boxes, [0, 300, 1000, 950]) == [
        "图1.2 三向量共面条件", "图1.3 别处的图"]


# ── 同一张图只出一行 ────────────────────────────────────────────────────
def test_same_figure_matches_caption_ignoring_spaces():
    """核心坑：本地抄到的图题是 `图1.5`，模型给的是 `图 1.5`，字面不等
    就被当成两张图，同一张图出了两行。"""
    group = {"items": [{"kind": "local", "bbox": [0, 0, 10, 10], "caption": "图1.5"}]}
    item = {"kind": "model", "bbox": [900, 900, 950, 950], "caption": "图 1.5"}
    assert X._same_figure(group, item, slack=8) is True


def test_same_figure_rejects_different_captions():
    group = {"items": [{"kind": "local", "bbox": [0, 0, 100, 100], "caption": "图1.14 差集"}]}
    item = {"kind": "model", "bbox": [0, 0, 100, 100], "caption": "图1.15 补集"}
    assert X._same_figure(group, item, slack=8) is False


def test_same_kind_boxes_are_not_merged_by_proximity():
    """同页并排两张图，本地会框成一条带；两个本地框不能靠"挨得近"合并。"""
    group = {"items": [{"kind": "local", "bbox": [0, 0, 100, 100], "caption": ""}]}
    item = {"kind": "local", "bbox": [0, 105, 100, 200], "caption": ""}
    assert X._same_figure(group, item, slack=8) is False


# ── 主框怎么挑 ──────────────────────────────────────────────────────────
def test_primary_prefers_local_when_model_box_has_no_caption():
    """模型有时把页眉当插图（第 10 页就是这样），它框里一个图题都没有 → 用本地框。"""
    group = {"local_bbox": [141, 220, 953, 469], "model_bbox": [405, 118, 786, 269],
             "local_captions": 1, "model_captions": 0, "slack": 33}
    bbox, source, confidence = X._pick_primary(group)
    assert bbox == [141, 220, 953, 469]
    assert source == "both"
    assert confidence == "low"          # 两个来源差得远 → 标出来让人看一眼


def test_primary_prefers_the_box_holding_exactly_one_caption():
    """并排两张图：本地横向连成一片、框住两个图题，模型只框住一个 → 用模型的框，
    随后由 `_extend_to_caption` 把它贴到自己的图题上。实测第 30 页。"""
    group = {"local_bbox": [140, 1152, 956, 1419], "model_bbox": [226, 1145, 536, 1389],
             "local_captions": 2, "model_captions": 1, "slack": 33}
    bbox, source, confidence = X._pick_primary(group)
    assert bbox == [226, 1145, 536, 1389]
    assert source == "both"
    assert confidence == "high"         # 模型框落在本地带里 → 两个来源互相印证


def test_primary_takes_the_tighter_box_when_both_are_clean():
    group = {"local_bbox": [100, 100, 400, 400], "model_bbox": [120, 130, 380, 390],
             "local_captions": 1, "model_captions": 1, "slack": 20}
    bbox, source, confidence = X._pick_primary(group)
    assert bbox == [120, 130, 380, 390]   # 都只框住一个图题 → 取更紧的
    assert confidence == "high"


# ── 收尾：把框贴到自己的图题 ────────────────────────────────────────────
def test_extend_to_caption_absorbs_the_caption_line():
    """模型只框到图形、图题被切掉半行（实测第 29 页）→ 向下贴到图题结束。"""
    boxes = [_box(350, 590, 650, 620, "图 1.11 集合的包含")]
    bbox = X._extend_to_caption([257, 396, 453, 581], "图1.11集合的包含",
                                boxes, line_height=30, page_height=1000)
    assert bbox == [257, 396, 650, 620]


def test_extend_to_caption_leaves_far_away_caption_alone():
    """图题离得远（隔着正文）就不贴 —— 不能为了凑图题去吃正文。"""
    boxes = [_box(350, 900, 650, 930, "图1.11 集合的包含")]
    bbox = X._extend_to_caption([257, 396, 453, 581], "图1.11 集合的包含",
                                boxes, line_height=30, page_height=1000)
    assert bbox == [257, 396, 453, 581]


def test_no_duplicate_captions_in_real_run_shape(tmp_path):
    """同一页并排三张图 + 一张本地带 → 输出里不该出现两张图共用一个图题。"""
    import pymupdf
    document = pymupdf.open()
    page = document.new_page(width=400, height=400)
    page.insert_text((50, 50), "top line", fontsize=12)
    page.insert_text((50, 350), "bottom line", fontsize=12)
    pdf = tmp_path / "three.pdf"
    document.save(pdf)
    document.close()

    ocr = tmp_path / "ocr.json"
    ocr.write_text(json.dumps({"pages": [{"page": 1, "boxes": [
        _box(50, 40, 350, 60, "top line"),
        _box(50, 300, 120, 315, "图1.11 包含"),
        _box(160, 300, 230, 315, "图1.12 交"),
        _box(270, 300, 340, 315, "图1.13 并"),
        _box(50, 340, 350, 360, "bottom line")]}]}, ensure_ascii=False), encoding="utf-8")
    client = _FakeClient({1: {"has_figure": True, "figures": [
        {"index": 1, "kind": "venn", "caption": "图1.11 包含", "bbox": [100, 120, 280, 720]},
        {"index": 2, "kind": "venn", "caption": "图1.12 交", "bbox": [320, 120, 500, 720]},
        {"index": 3, "kind": "venn", "caption": "图1.13 并", "bbox": [560, 120, 740, 720]}]}})

    result = X.extract_images(pdf, tmp_path, scale=1.0, ocr_json=ocr, client=client)
    payload = json.loads(Path(result["figures"]).read_text(encoding="utf-8"))
    captions = [entry["caption"] for entry in payload["images"]]
    assert len(captions) == len(set(captions)), captions
    assert len(captions) == 3, payload["images"]
    for entry in payload["images"]:
        assert entry["bbox_picked"] in {"local", "model", "mixed"}
