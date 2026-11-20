"""清洗块回归测试（全部离线，不打模型）。

锁住的核心命题：**"只准动公式"是能机械判定的**，不是靠模型自觉。
所以测试里既要证明"改公式"能过，也要证明"偷偷改写正文"一定被抓住。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "blocks" / "clean"))
sys.path.insert(0, str(ROOT / "lib"))

import clean_text as C          # noqa: E402
from files import sha256_file   # noqa: E402


# ── 切段 ────────────────────────────────────────────────────────────────
def test_split_paragraphs_keeps_inner_newlines():
    text = "### 1.1.1 标题\n\n第一段第一行\n第二行还在这一段\n\n第二段\n"
    blocks = C.split_paragraphs(text)
    assert len(blocks) == 3
    assert blocks[1] == "第一段第一行\n第二行还在这一段"


def test_flatten_ignores_whitespace_so_rewrapping_is_free():
    assert C.flatten("向量\n的 模") == C.flatten("向量的模")


# ── 公式定位 ────────────────────────────────────────────────────────────
def test_math_spans_finds_inline_and_display():
    flat = "看$x$和$$y$$这"
    spans = C.math_spans(flat)
    assert [flat[a:b] for a, b in spans] == ["$x$", "$$y$$"]


# ── 核心：只改公式 vs 改写正文 ──────────────────────────────────────────
def test_fixing_a_formula_is_allowed():
    source = "向量DA与OB所夹的角ZAOB称为它们之间的夹角."
    cleaned = "向量$\\overrightarrow{OA}$与$\\overrightarrow{OB}$所夹的角$\\angle AOB$称为它们之间的夹角."
    report = C.diff_inside_math(source, cleaned)
    assert report["ok"] is True, report
    assert report["changed_math"] >= 3


def test_missing_absolute_bars_is_allowed():
    report = C.diff_inside_math("向量a的模用a表示", "向量$\\boldsymbol{a}$的模用$|\\boldsymbol{a}|$表示")
    assert report["ok"] is True, report


def test_rewriting_prose_is_caught():
    """核心坑：模型容易顺手润色。润色出来的字在公式外 → 必须抓住。"""
    source = "向量的长度也称为向量的模"
    cleaned = "向量的长度也就是它的大小，称为模"
    report = C.diff_inside_math(source, cleaned)
    assert report["ok"] is False
    assert report["inserted_outside"], report


def test_deleting_a_sentence_is_caught():
    """把整句删掉也必须在公式外删除预算内被抓住（不是只看"新增"）。"""
    source = "第一句话在这里。第二句话被整句删掉了。第三句话还在。"
    cleaned = "第一句话在这里。第三句话还在。"
    report = C.diff_inside_math(source, cleaned)
    assert report["ok"] is False, report
    assert report["deleted_outside_chars"] > report["budget"]


def test_adding_explanation_is_caught():
    report = C.diff_inside_math("向量是既有大小又有方向的量。",
                                "向量是既有大小又有方向的量（这一点很重要）。")
    assert report["ok"] is False, report


def test_fixing_typo_is_caught():
    """连错别字都不许改 —— 改动发生在公式外就算违规。"""
    report = C.diff_inside_math("仿射坐标系的原点", "仿射坐标系的原点。")
    assert report["ok"] is False or report["inserted_outside"] == ["。"], report


def test_two_inline_formulas_across_a_newline_are_not_a_display_formula():
    """核心坑：`$a$` 换行 `$b$` 在"删掉空白"之后会粘成 `$$`，被当成显示公式
    定界符 → 误报"在公式以外改了字"。实测 1.1.3 节就这样被误判成 needs_review。"""
    source = "所以只要证明方程组\n$+\\mu+\\nu=0$\n$-\\mu+2\\nu=0$\n有不全为零的解."
    cleaned = "所以只要证明方程组\n$+\\mu+\\nu=0$\n$-\\mu+2\\nu=0$\n有不全为零的解."
    report = C.diff_inside_math(source, cleaned)
    assert report["ok"] is True, report
    # 同一段里公式数量要数对（不是被 `$$` 吃掉一段）
    assert len(C.math_bodies(cleaned)) == 2
    assert len(C.math_bodies(C.flatten(cleaned))) <= 2


def test_merging_hard_wrapped_lines_is_allowed():
    source = "很多物理量不仅有大小，而且有方向，例如速度、\n位移、力等等."
    cleaned = "很多物理量不仅有大小，而且有方向，例如速度、位移、力等等."
    report = C.diff_inside_math(source, cleaned)
    assert report["ok"] is True, report


# ── 逐字引用与覆盖 ──────────────────────────────────────────────────────
def test_coverage_requires_verbatim_quotes():
    original = "第一段内容。\n\n第二段内容。"
    ratio, missing = C.coverage(original, ["第一段内容。", "第二段被改写过了"])
    assert missing == ["第二段被改写过了"]
    assert ratio == pytest.approx(len("第一段内容。") / len(C.flatten(original)))


def test_coverage_detects_missing_paragraph():
    original = "第一段。\n\n第二段。\n\n第三段。"
    ratio, missing = C.coverage(original, ["第一段。", "第三段。"])
    assert missing == []
    assert ratio < C.COVERAGE_MIN


def test_coverage_of_out_of_order_quotes_fails():
    original = "第一段。\n\n第二段。"
    _ratio, missing = C.coverage(original, ["第二段。", "第一段。"])
    assert missing, "顺序错了必须报出来"


# ── 整体校验 ────────────────────────────────────────────────────────────
def _payload(paragraphs, sha="a" * 64):
    return {"unit_id": "CH01-S01", "paragraphs": paragraphs, "source_sha256": sha}


def test_validate_rejects_unbalanced_dollar():
    original = "一条公式 $a+b$。"
    payload = _payload([{"source_quote": original, "text_md": "一条公式 $a+b。"}])
    report = C.validate_clean(payload, original=original, original_sha="a" * 64)
    assert any("不成对" in p for p in report["problems"])


def test_validate_rejects_fabricated_quote():
    original = "原文只有这一句。"
    payload = _payload([{"source_quote": "原文里根本没有这句", "text_md": "原文里根本没有这句"}])
    report = C.validate_clean(payload, original=original, original_sha="a" * 64)
    assert any("找不到" in p for p in report["problems"])


def test_validate_rejects_dropped_heading():
    """核心坑：模型把 `### 1.1.1 向量及其表示` 整行丢掉，95% 覆盖率照样放过。
    改成逐段逐字对齐后必须抓住。"""
    original = "### 1.1.1 向量及其表示\n\n正文一段。"
    payload = _payload([{"source_quote": "正文一段。", "text_md": "正文一段。"}])
    report = C.validate_clean(payload, original=original, original_sha="a" * 64)
    assert any("段落数不对" in p for p in report["problems"]), report


def test_validate_rejects_a_dropped_clause_in_the_quote():
    """引用里**漏掉半句**必须拦住（1 个字符的规范化可以容忍，漏句子不行）。"""
    original = "### 标题\n\n模为零的向量称为零向量，记作零向量."
    payload = _payload([
        {"source_quote": "### 标题", "text_md": "### 标题"},
        {"source_quote": "称为零向量.", "text_md": "称为$\\boldsymbol{0}$."},
    ])
    report = C.validate_clean(payload, original=original, original_sha="a" * 64)
    assert any("逐字照抄" in p for p in report["problems"]), report


def test_validate_rejects_changed_original():
    original = "一句话。"
    payload = _payload([{"source_quote": original, "text_md": original}], sha="b" * 64)
    report = C.validate_clean(payload, original=original, original_sha="a" * 64)
    assert any("原文被动过了" in p for p in report["problems"])


def test_validate_passes_a_clean_result():
    original = "向量a的模用a表示.\n\n向量a与b平行记作a//b."
    payload = _payload([
        {"source_quote": "向量a的模用a表示.",
         "text_md": "向量$\\boldsymbol{a}$的模用$|\\boldsymbol{a}|$表示."},
        {"source_quote": "向量a与b平行记作a//b.",
         "text_md": "向量$\\boldsymbol{a}$与$\\boldsymbol{b}$平行记作"
                    "$\\boldsymbol{a}\\parallel\\boldsymbol{b}$."},
    ])
    report = C.validate_clean(payload, original=original, original_sha="a" * 64)
    assert report["problems"] == [], report
    assert report["stats"]["覆盖率"] == 1.0
    assert report["stats"]["修正公式处数"] >= 2
    assert report["stats"]["公式片段数"] >= 2


# ── 跑一遍（假模型） ────────────────────────────────────────────────────
class _FakeClient:
    model = "fake"
    effort = "low"

    def __init__(self, paragraphs=None):
        self.calls = 0
        self.paragraphs = paragraphs

    def chat_json(self, messages, tag="", **_):
        self.calls += 1
        original = messages[-1]["content"].split("本节原文（逐字，不要修改任何非公式内容）：\n")[-1]
        paragraphs = self.paragraphs
        if paragraphs is None:
            paragraphs = [{"source_quote": block,
                           "text_md": block.replace("a表示", "$|\\boldsymbol{a}|$表示"),
                           "changes": []}
                          for block in C.split_paragraphs(original)]
        return {"data": {"paragraphs": paragraphs}, "usage": {}, "cached": False}


@pytest.fixture
def book(tmp_path):
    parse = tmp_path / "parse"
    (parse / "original_text").mkdir(parents=True)
    (parse / "original_text/ch01_s01.md").write_text(
        "### 1.1.1 向量\n\n向量a的模用a表示.\n\n向量a与b平行记作a//b.\n", encoding="utf-8")
    (parse / "chapters.json").write_text(json.dumps({
        "_meta": {"file_type": "chapters"}, "book": {"title": "测试教材"}, "chapters": [],
        "lesson_units": [{"unit_id": "CH01-S01", "level": "section", "chapter": "CH01",
                          "section": "CH01-S01", "title": "向量", "anchor": "1.1",
                          "original_text": "original_text/ch01_s01.md"}],
    }, ensure_ascii=False), encoding="utf-8")
    return {"parse": parse}


def test_run_writes_json_md_and_index(book):
    client = _FakeClient()
    outcome = C.run(book["parse"], client=client, log=lambda *_: None)
    assert client.calls == 1
    out = book["parse"] / "latex"
    payload = json.loads((out / "CH01-S01.json").read_text(encoding="utf-8"))
    assert payload["_meta"]["file_type"] == "clean_text"
    assert payload["source"] == "original_text/ch01_s01.md"
    assert payload["checks"]["only_math_changed"] is True
    assert (out / "CH01-S01.md").is_file()
    index = json.loads((out / "latex.index.json").read_text(encoding="utf-8"))
    assert index["totals"]["done"] == 1
    assert index["original_text_unchanged"] is True
    assert index["max_request_bytes"] > 0
    assert outcome["model_calls"] == 1


def test_original_text_untouched(book):
    before = (book["parse"] / "original_text/ch01_s01.md").read_bytes()
    C.run(book["parse"], client=_FakeClient(), log=lambda *_: None)
    assert (book["parse"] / "original_text/ch01_s01.md").read_bytes() == before
    assert sha256_file(book["parse"] / "original_text/ch01_s01.md")


def test_prose_rewrite_is_flagged_needs_review(book):
    """模型改写正文 → 校验不过 → 重跑 → 仍不过 → 标 needs_review，不静默通过。"""
    original = (book["parse"] / "original_text/ch01_s01.md").read_text(encoding="utf-8")
    blocks = C.split_paragraphs(original)
    bad = [{"source_quote": blocks[0], "text_md": "### 第 1.1.1 节 向量（整理版）", "changes": []},
           {"source_quote": blocks[1], "text_md": "向量的长度叫做模。", "changes": []},
           {"source_quote": blocks[2], "text_md": "两个向量方向相同或相反就叫平行。", "changes": []}]
    client = _FakeClient(paragraphs=bad)
    C.run(book["parse"], client=client, retries=1, log=lambda *_: None)
    assert client.calls == 2, "校验不过应该重跑一次"
    index = json.loads((book["parse"] / "latex/latex.index.json").read_text(encoding="utf-8"))
    row = index["units"][0]
    assert row["status"] == "needs_review"
    assert row["only_math_changed"] is False
    assert row["problems"], row


def test_second_run_skips_and_force_reruns(book):
    client = _FakeClient()
    C.run(book["parse"], client=client, log=lambda *_: None)
    C.run(book["parse"], client=client, log=lambda *_: None)
    assert client.calls == 1
    C.run(book["parse"], client=client, force=True, log=lambda *_: None)
    assert client.calls == 2


def test_only_one_unit_calls_model_once(book):
    client = _FakeClient()
    C.run(book["parse"], only=["CH01-S01"], client=client, log=lambda *_: None)
    assert client.calls == 1


def test_dry_run_does_not_call_model(book):
    client = _FakeClient()
    outcome = C.run(book["parse"], client=client, dry_run=True, log=lambda *_: None)
    assert client.calls == 0
    assert outcome["todo"] == ["CH01-S01"]


def test_lesson_corrections_are_passed_as_hints(book, tmp_path):
    lesson_dir = tmp_path / "lesson/lessons"
    lesson_dir.mkdir(parents=True)
    (lesson_dir / "CH01-S01.json").write_text(json.dumps({
        "unit_id": "CH01-S01",
        "corrections": [{"原文": "a//b", "讲解": "$\\boldsymbol{a}\\parallel\\boldsymbol{b}$",
                         "理由": "OCR"}],
    }, ensure_ascii=False), encoding="utf-8")
    seen = {}

    class _Recorder(_FakeClient):
        def chat_json(self, messages, tag="", **kwargs):
            seen["prompt"] = messages[-1]["content"]
            return super().chat_json(messages, tag=tag, **kwargs)

    C.run(book["parse"], client=_Recorder(), log=lambda *_: None)
    assert "上一道工序" in seen["prompt"]
    assert "a//b" in seen["prompt"]


def test_request_size_stays_under_g6_limit(book):
    original = (book["parse"] / "original_text/ch01_s01.md")
    original.write_text("正文。" * 4000, encoding="utf-8")
    client = _FakeClient()
    C.run(book["parse"], client=client, log=lambda *_: None)
    index = json.loads((book["parse"] / "latex/latex.index.json").read_text(encoding="utf-8"))
    assert 0 < index["max_request_bytes"] <= C.MAX_REQUEST_BYTES


def test_whitespace_around_a_math_span_is_not_a_prose_change():
    """核心坑：difflib 给的片段**会带上紧邻的空白**（`' $\\boldsymbol{'` 落在
    [47,61)，而公式区间是 [48,78)），"是否在公式内"就会判成否 →
    纯公式改动被误报成"在公式以外改了字"。实测这条会系统性地把大量节标成待复核。"""
    source = "设a=(x1,x2),b=(y1,y2)."
    cleaned = "设$\\boldsymbol{a}=(x_1,x_2)$, $\\boldsymbol{b}=(y_1,y_2)$."
    report = C.diff_inside_math(source, cleaned)
    assert report["ok"] is True, report
    assert report["inserted_outside"] == [], report


def test_whitespace_only_insertion_is_ignored():
    report = C.diff_inside_math("甲乙丙", "甲 乙 丙")
    assert report["ok"] is True and report["inserted_outside"] == []


def test_tiny_quote_difference_is_tolerated_but_a_dropped_clause_is_not():
    """source_quote 差一两个字符（标点规范化）→ 放行；漏掉半句 → 拦住。
    实测严格相等会让 60%+ 的节重跑，成本翻到 1.75 倍。"""
    original = "设两个向量 a=(a1,a2,a3)与 b=(b1,b2,b3) 共线，则存在实数 k 使 a=kb."
    payload = {
        "unit_id": "X",
        "paragraphs": [
            {"source_quote": "设两个向量 a=(a1,a2,a3)与 b=(b1,b2,b3) 共线,则存在实数 k 使 a=kb.",
             "text_md": "设两个向量 $a=(a_1,a_2,a_3)$ 与 $b=(b_1,b_2,b_3)$ 共线,则存在实数 $k$ 使 $a=kb$."},
        ],
    }
    report = C.validate_clean(payload, original=original, original_sha="a" * 64)
    assert not [p for p in report["problems"] if "逐字照抄" in p], report["problems"]
    assert any("极小差异" in w for w in report["warnings"]), report["warnings"]

    payload["paragraphs"][0]["source_quote"] = "设两个向量 a=(a1,a2,a3) 共线."
    report = C.validate_clean(payload, original=original, original_sha="a" * 64)
    assert [p for p in report["problems"] if "逐字照抄" in p], report["problems"]


def test_lone_punctuation_deletion_is_not_prose_loss():
    """核心坑：删掉一个孤立的 `（` / `：` 被判"把正文删掉了" → 纯误报、白重跑。
    只看两个信号：连续 3 个汉字，或有汉字且有句读。"""
    for junk in ("（", "）", "：", "Aa1+A2a2", "a上b", "//"):
        assert C._looks_like_prose(junk) is None, junk
    for prose in ("第二句话被整句删掉了。", "模为零的向量", "（柯西不等式）"):
        assert C._looks_like_prose(prose) is not None, prose
