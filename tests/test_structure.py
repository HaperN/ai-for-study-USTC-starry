"""结构层回归测试。

这些用例锁住的是"实际踩过的坑"，不是理论边界：
每一处都对应一次真实 PDF/OCR 上暴露出来的错误。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "blocks" / "parse" / "structure"))

import structure as S  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "report-document.md"


# ── 中文数字 ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,value", [
    ("一", 1), ("十", 10), ("十一", 11), ("二十", 20), ("二十三", 23),
    ("一百", 100), ("7", 7), ("零", 0), ("", None), ("abc", None),
])
def test_cn_to_int(text, value):
    assert S.cn_to_int(text) == value


# ── 标题识别 ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("line,scheme,number", [
    ("第一章向量", "chapter", "一"),
    ("第 1 章 向量", "chapter", "1"),
    ("第一部分 · 工作总览", "chapter", "一"),
    ("一、完成的工作", "cn", "一"),
    ("4.1 ① 硬件数据如何读取", "num", "4.1"),
    ("1.1.1向量及其表示", "num", "1.1.1"),
    ("§1.2 坐标系", "num", "1.2"),
])
def test_heading_match_accepts(line, scheme, number):
    matched = S.heading_match(line)
    assert matched is not None, line
    assert matched[0] == scheme
    assert matched[1] == number


@pytest.mark.parametrize("line", [
    "- 列表项", "| 表格 |", "```", "$$x$$",
    "1.2",                      # 只有编号没有标题
    "第一章",                    # 同上
    "4.1 2",                    # 标题是纯数字 → 是页码污染
    "1.9 (a))",                 # 公式碎片
    "这段时间，使用 agent 完成了算法的构建，并且下载了真实数据进行优化",  # 太长
])
def test_heading_match_rejects(line):
    assert S.heading_match(line) is None, line


# ── 折行合并 ────────────────────────────────────────────────────────────
def test_unwrap_never_merges_into_heading():
    """曾经的真实 bug：正文被粘到标题后面，导致第一页标题全部丢失。"""
    lines = [S.Line(1, "一、完成的工作", 1),
             S.Line(1, "这段时间，使用 agent 完成了算法的构建，摸索出算法", 2),
             S.Line(1, "适用的边界。", 3)]
    S.detect_headings(lines)
    merged, _ = S.unwrap(lines)
    assert merged[0].text == "一、完成的工作"          # 标题没被粘坏
    assert "适用的边界。" in merged[1].text            # 正文两行合上了


def test_unwrap_keeps_sentence_boundaries():
    lines = [S.Line(1, "第一句结束了。", 1), S.Line(1, "第二句开始。", 2)]
    S.detect_headings(lines)
    merged, _ = S.unwrap(lines)
    assert [l.text for l in merged] == ["第一句结束了。", "第二句开始。"]


# ── 目录区 ──────────────────────────────────────────────────────────────
def test_toc_region_is_dropped_not_kept_as_structure():
    """目录条目和正文标题长得一样，必须整段丢，否则整份目录变成假结构。"""
    text = "\n".join([
        "[第 1 页]", "目录",
        "第一章向量",
        "1.1向量的线性运算", "7",
        "1.1.1向量及其表示.", "7",
        "[第 2 页]", "第一章向量",
        "向量是力学与物理等领域中诸多概念的数学抽象。",
        "1.1向量的线性运算",
        "1.1.1向量及其表示",
        "向量的概念来源于物理学。",
    ])
    result = S.build_structure(text)
    titles = [h.title for h in result.headings]
    assert "向量的线性运算" in titles
    # 目录里的条目不应该产生额外标题（去重后只有正文那一份）
    assert titles.count("向量及其表示") == 1
    assert result.stats.get("dropped_toc_region", 0) > 0


# ── 层级：数字前缀嵌套 ──────────────────────────────────────────────────
def test_numeric_path_nesting():
    """`4.1` 必须挂在 `四、` 下面，而不是和它平级（真实报告里就是这样）。"""
    text = "\n".join([
        "第一部分 · 工作总览", "正文。",
        "一、完成的工作", "正文。",
        "第二部分 · 技术补充", "正文。",
        "四、系统实现", "正文。",
        "4.1 硬件数据读取", "正文。",
        "4.1.1 相机", "正文。",
        "五、验证", "正文。",
        "5.1 合成数据", "正文。",
    ])
    result = S.build_structure(text)
    by_title = {h.title: h for h in result.headings}
    assert by_title["工作总览"].canonical_level == 1
    assert by_title["完成的工作"].canonical_level == 2
    assert by_title["技术补充"].canonical_level == 1
    assert by_title["系统实现"].canonical_level == 2
    assert by_title["硬件数据读取"].canonical_level == 3        # 4.1 → 挂在 四、 下
    assert by_title["相机"].canonical_level == 4                # 4.1.1 → 再下一层
    assert by_title["验证"].canonical_level == 2
    assert by_title["合成数据"].canonical_level == 3


def test_renumber_ignores_broken_source_numbers():
    """原文编号自相矛盾时，**章号跟书走、节号按文档顺序**，并给出警告。

    2026-09-27 改口径（用户定）：章号不再一律重编 ——
    《数分讲义第二册》是第一册的延续（书里是第 8–13 章），重编会让交付的单元编号与教材对不上。
    但书里自相矛盾的**节号**（`四、` 出现在第 2 部分下面）仍然按文档顺序编号 + 报警。
    """
    text = "\n".join(["第二部分 · 技术补充", "正文。", "四、系统实现", "正文。",
                      "4.1 硬件", "正文。"])
    result = S.build_structure(text)
    assert result.headings[0].canonical == "2"           # 书自己写的是「第二部分」
    assert result.headings[-1].canonical == "2.1.1"
    assert any("按文档顺序" in w for w in result.warnings)


def test_chapter_numbers_follow_the_book():
    """章号跟书走：《数分讲义第二册》书里是第 8 章，编号就该是 `CH08`（不是 CH01）。"""
    text = "\n".join([
        "第8章 空间解析几何", "正文。",
        "§8.1 向量与坐标系", "正文。",
        "§8.2 平面与直线", "正文。",
        "第9章 多变量函数的微分学", "正文。",
        "§9.1 多变量函数及其连续性", "正文。",
    ])
    result = S.build_structure(text)
    canon = {h.title: h.canonical for h in result.headings}
    assert canon["空间解析几何"] == "8" and canon["多变量函数的微分学"] == "9"
    assert canon["向量与坐标系"] == "8.1" and canon["多变量函数及其连续性"] == "9.1"
    assert not [w for w in result.warnings if "按文档顺序" in w], "跟书走不该报重编警告"


def test_duplicate_chapter_number_falls_back_to_sequence():
    """书里的章号重了（OCR 出错）→ 退回顺序编号并报警，不许出现两个 CH03。"""
    text = "\n".join(["第3章 甲组", "正文。", "第3章 乙组", "正文。", "第4章 丙组", "正文。"])
    result = S.build_structure(text)
    canon = {h.title: h.canonical for h in result.headings}
    assert canon["甲组"] == "3" and canon["乙组"] == "4" and canon["丙组"] == "5", canon
    assert any("已出现过" in w for w in result.warnings)


# ── OCR 污染的还原 ──────────────────────────────────────────────────────
def test_section_symbol_misread_as_eight_is_repaired():
    """`§` 被 OCR 认成 `8`：`81.2坐标系` 其实是 `1.2坐标系`。"""
    text = "\n".join(["第一章 向量", "正文。", "81.2坐标系", "正文。",
                      "81.2.1仿射坐标系", "正文。"])
    result = S.build_structure(text)
    numbers = {h.raw_number for h in result.headings}
    assert "1.2" in numbers and "1.2.1" in numbers
    assert not any(n.startswith("81") for n in numbers)


def test_identical_heading_twice_is_deduplicated():
    text = "\n".join(["第一章 向量", "正文。", "1.1 线性运算", "正文。",
                      "[第 2 页]", "1.1 线性运算", "正文继续。"])
    result = S.build_structure(text)
    assert [h.title for h in result.headings].count("线性运算") == 1


# ── 渲染 ────────────────────────────────────────────────────────────────
def test_render_emits_canonical_anchor_forms():
    """下游 normalize-source 只认 `第N章` / `§N.M` / `N.M.K` 三种锚点。"""
    text = "\n".join(["第一部分 · 总览", "正文。", "一、工作", "正文。",
                      "1.1 细节", "正文。"])
    result = S.build_structure(text)
    markdown = result.structured_markdown
    assert "# 第1章 总览" in markdown
    assert "## §1.1 工作" in markdown
    assert "### 1.1.1 细节" in markdown


def test_anchor_level_flattens_deeper_levels_to_bold_text():
    text = "\n".join(["第一章 向量", "正文。", "1.1 线性运算", "正文。",
                      "1.1.1 细节", "正文。", "1.1.1.1 更深", "正文。"])
    result = S.build_structure(text)
    lines, _ = S.split_pages(text)
    lines, _ = S._drop_toc_region(lines)
    lines, _w, _s = S.denoise(lines)
    lines, _ = S.join_split_headings(lines)
    S.detect_headings(lines)
    anchored = S.render(result.headings, lines, anchor_level=3)
    assert "#### " not in anchored                     # 不出现第四层标题
    assert "**1.1.1.1 更深**" in anchored              # 降级成粗体正文


# ── 真实文档端到端 ──────────────────────────────────────────────────────
def test_real_report_document_end_to_end():
    """真实 PDF 抽出来的文本：必须能认出章/节/小节，且层级正确。"""
    result = S.build_from_file(FIXTURE)
    assert result.stats["headings_total"] > 20
    by_title = {h.title: h for h in result.headings}
    assert by_title["工作总览"].canonical_level == 1
    assert by_title["系统实现"].canonical_level == 2
    assert by_title["① 硬件数据如何读取"].canonical_level == 3
    assert by_title["相机：海康 MV-CS020-10UC"].canonical_level == 4
    # 至少要能产出下游认得的三种锚点
    # （第一个标题之前是封面/标题页，属正常前件，不算错）
    markdown = result.structured_markdown
    assert "# 第1章 工作总览" in markdown
    assert "# 第2章 技术补充" in markdown
    assert "## §2.1 系统实现" in markdown
    assert "### 2.1.1 ① 硬件数据如何读取" in markdown
    assert "#### 2.1.1.1 相机：海康 MV-CS020-10UC" in markdown
    # 折行合并确实发生了（这份文档 49% 的行是折断的半句）
    assert any("合并了" in w for w in result.warnings)


def test_structure_json_has_meta_header():
    result = S.build_from_file(FIXTURE)
    payload = result.to_json("deadbeef")
    meta = payload["_meta"]
    assert meta["file_type"] == "structure"
    assert meta["source_sha256"] == "deadbeef"
    assert "field_notes" in meta
    assert payload["headings"][0]["numeric"] is not None
    json.dumps(payload)          # 必须可序列化


# ══════════════════════════════════════════════════════════════════════════
# 2026-09-27 用三本真教材（复变函数 / 数值计算方法 / 数分讲义第二册）抓出来的规则
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("a,b", [
    ("52Gauss－Seidel迭代", "5.2 Gauss-Seidel 迭代"),        # 全角连接号 vs 半角 + 空格
    ("*13Hermite插值", "1.3 Hermite插值"),                  # 选学标记 `*`
    ("51简单(Jacobi)迭代", "5.1 简单（Jacobi）迭代"),         # 半角括号 vs 全角
])
def test_norm_title_folds_typography(a, b):
    """同一个节在**书签/印刷目录/正文**里的三种写法必须折成同一个键。

    不折平的后果是**对账误报**：明明对上了，却报"目录有但没解析出来"。
    """
    assert S.norm_title(a) == S.norm_title(b)


@pytest.mark.parametrize("line,ok", [
    ("习题", True), ("习题一", True), ("习题十二", True), ("练习 2", True),
    ("习题 8.1", True), ("习题8.2", True), ("第 8 章综合习题", True),
    ("综合习题", True), ("复习题三", True), ("总习题", True),
    ("§8.1 向量与坐标系", False), ("习题课", False),
])
def test_exercise_mark_forms(line, ok):
    """习题区标记要认「编号挂在后面」的写法。

    《数分讲义第二册》把它写成 `习题 8.1`、章末写成 `第 8 章综合习题` ——
    只认光秃秃的 `习题`/`习题一` 时，那本书**一个习题区都划不出来**（实测抽出 0 条），
    而 `第 8 章综合习题` 还会被当成章（6 个假章）。
    """
    assert bool(S.EXERCISE_MARK.match(line)) is ok


def test_intro_is_chapter_zero():
    """`绪论` = 第 0 章（没有「第N章」编号，但节号是 `0.1`）。

    不认它的后果（《数值计算方法》实测）：绪论里 `0.1 数值计算方法与算法`、
    `0.2 误差与有效数字`、`0.3 矩阵和向量范数` 约 13 页**一个单元都没有**。
    """
    matched = S.heading_match("绪论")
    assert matched is not None and matched[0] == "chapter"
    assert S.numeric_path("chapter", "") == (0,)


def test_intro_does_not_shift_chapter_numbers():
    """绪论拿 0，第 1 章仍是第 1 章（否则全书错一位）。"""
    text = "\n".join([
        "绪论", "正文。",
        "0.1 数值计算方法与算法", "正文。",
        "0.2 误差与有效数字", "正文。",
        "第1章 插值", "正文。",
        "1.1 Lagrange插值多项式", "正文。",
    ])
    result = S.build_structure(text)
    chapters = {h.title: h.canonical for h in result.headings if h.canonical_level == 1}
    assert chapters["绪论"] == "0"
    assert chapters["插值"] == "1", "第 1 章被绪论挤成了第 2 章"
    sections = {h.title: h.canonical for h in result.headings if h.canonical_level == 2}
    assert sections["数值计算方法与算法"] == "0.1"


def test_bare_number_is_not_section_when_book_uses_dotted_numbers():
    """`2. 交换两个方程` 这种裸编号是列表项，不是节。

    实测（《复变函数》）：正文里有 6 处这样的"标题"，全都是段落或题干，
    还把它们底下的真小节（指数函数/对数函数）挂到了错父级。
    """
    text = "\n".join([
        "第1章 插值", "正文。",
        "1.1 线性插值", "正文。",
        "1.1.1 线性插值问题", "正文。",
        "1.2 Newton插值", "正文。",
        "1.2.1 差商", "正文。",
        "1.3 Hermite插值", "正文。",
        "1.3.1 基函数", "正文。",
        "2. 交换两个方程", "正文。",
    ])
    result = S.build_structure(text)
    titles = [h.title for h in result.headings]
    assert "交换两个方程" not in titles, "裸编号的列表项被当成了节"
    assert result.stats["bare_number_seen"] >= 1
    assert result.stats["plain_number_dropped"] == 1


def test_bare_number_exercise_items_still_reach_the_exercise_block():
    """裸编号行不当标题，但它里面的**习题题干**必须照旧交给习题块。

    实测（《复变函数》）：先丢后判，内联题干从 61 条掉到 0 条、习题层少 61 道题。
    正确顺序是**先让 `mark_exercise_items` 挑题干，再清标题树**。
    """
    text = "\n".join([
        "第1章 向量", "正文。",
        "1.1 线性运算", "正文。",
        "1.1.1 定义", "正文。",
        "1.2 方程组", "正文。",
        "1.2.1 解法", "正文。",
        "1.3 行列式", "正文。",
        "1.3.1 性质", "正文。",
        "1. 求下列方程组的解", "2. 证明下列恒等式", "3. 计算下列行列式",
    ])
    result = S.build_structure(text)
    items = [x["title"] for x in result.stats["exercise_items"]]
    assert any("求下列" in t for t in items), "裸编号的习题题干被丢掉了"
    titles = [h.title for h in result.headings]
    assert "求下列方程组的解" not in titles, "题干混进了章节树"


# ── 目录当骨架（2026-09-27 用户定方向：书的目录决定教材分节）──────────────
def _with_toc(tmp_path, entries):
    """造一份带 `toc.json` 的解析目录，返回 `document.md` 的路径。"""
    (tmp_path / "toc.json").write_text(
        json.dumps({"chapters": [e["text"] for e in entries if e["level"] == 1],
                    "entries": entries}, ensure_ascii=False), encoding="utf-8")
    return tmp_path / "document.md"


def test_skeleton_drops_headings_the_book_toc_does_not_have(tmp_path):
    """目录里没有的章/节 → 剔掉（正文排版骗出来的伪标题就是这么治的）。"""
    src = _with_toc(tmp_path, [
        {"text": "第1章 向量", "level": 1},
        {"text": "1.1 线性运算", "level": 2},
        {"text": "1.2 方程组", "level": 2},
    ])
    text = "\n".join([
        "第1章 向量", "正文。",
        "1.1 线性运算", "正文。",
        "1.2 方程组", "正文。",
        "1.3 目录里没有这一节", "正文。",       # 伪节：目录里没有 → 骨架剔掉
        "习题二",                              # 章节末尾的习题标记 → 骨架也剔掉
    ])
    result = S.build_structure(text, source_path=src)
    titles = [h.title for h in result.headings]
    assert "目录里没有这一节" not in titles and "习题二" not in titles
    assert {"线性运算", "方程组"} <= set(titles)
    assert {x["title"] for x in result.stats["skeleton"]["剔除"]} == {"目录里没有这一节", "习题二"}


def test_skeleton_keeps_sections_below_its_depth(tmp_path):
    """目录只列到"节"时，**小节仍然保留**（否则《线性代数》会掉 55 个真小节）。"""
    src = _with_toc(tmp_path, [{"text": "第1章 向量", "level": 1},
                               {"text": "1.1 线性运算", "level": 2}])
    text = "\n".join(["第1章 向量", "正文。", "1.1 线性运算", "正文。",
                      "1.1.1 向量的表示", "正文。", "1.1.2 向量的运算", "正文。"])
    result = S.build_structure(text, source_path=src)
    titles = [h.title for h in result.headings]
    assert {"向量的表示", "向量的运算"} <= set(titles)


def test_skeleton_matches_toc_with_chapter_prefix(tmp_path):
    """正文标题不带编号、目录带 `第N章`：包含匹配要认（否则真章被剔）。"""
    src = _with_toc(tmp_path, [{"text": "第一章 向量", "level": 1},
                               {"text": "1.1 向量的线性运算", "level": 2}])
    text = "\n".join(["第一章 向量", "正文。", "1.1 向量的线性运算", "正文。"])
    result = S.build_structure(text, source_path=src)
    assert result.stats["skeleton"]["剔除"] == []


def test_skeleton_rescues_real_section_when_toc_has_ocr_typo(tmp_path):
    """目录自己有 OCR 错字（`基级数` 其实是 `幂级数`）→ 字面不过、相似度够高要救回来。

    这是**必须**的保守设计：只按字面比会把真节剔掉（复变函数实测 2 处）。
    """
    src = _with_toc(tmp_path, [{"text": "第5章 级数", "level": 1},
                               {"text": "5.2 基级数", "level": 2}])
    text = "\n".join(["第5章 级数", "正文。", "5.2 幂级数", "正文。"])
    result = S.build_structure(text, source_path=src)
    assert "幂级数" in [h.title for h in result.headings]
    # 目录里两条都可能被当成"相似"（`第5章 级数` 是 `幂级数` 的超串）——
    # 这里只要求**救回来了**并且逐条可查，不指定救到哪一条。
    rescued = result.stats["skeleton"]["救回"]
    assert rescued and all("similarity" in x for x in rescued)


def test_skeleton_reports_toc_entries_missing_from_body(tmp_path):
    """目录里有、正文里没找到 → 记账（**不猜位置**，让人看见）。"""
    src = _with_toc(tmp_path, [{"text": "第1章 向量", "level": 1},
                               {"text": "1.1 线性运算", "level": 2},
                               {"text": "1.9 正文里没编号的一节", "level": 2}])
    text = "\n".join(["第1章 向量", "正文。", "1.1 线性运算", "正文。"])
    result = S.build_structure(text, source_path=src)
    missing = [x["text"] for x in result.stats["skeleton"]["目录未配"]]
    assert any("没编号的一节" in t for t in missing)
    assert any("没找到" in w for w in result.warnings)


def test_skeleton_has_the_last_word_over_exercise_rule(tmp_path):
    """目录说了算：**目录里真有的节，不许被"题干"规则吃掉**。

    顺序必须是"先套骨架、再剔题干" —— 反过来的话，题干规则会先把标题删掉，
    骨架子再也看不到它，全书就静悄悄地少一节（这类错最难发现）。
    """
    src = _with_toc(tmp_path, [{"text": "第1章 向量", "level": 1},
                               {"text": "1.1 求下列向量组的秩", "level": 2}])
    # 正文这一节的标题**长着一副题干的样子**（求 + 下列），但书自己的目录里有它
    text = "\n".join(["第1章 向量", "正文。", "1.1 求下列向量组的秩", "正文。"])
    result = S.build_structure(text, source_path=src)
    titles = [h.title for h in result.headings]
    assert "求下列向量组的秩" in titles, "目录里有的节被题干规则吃掉了"
    # 但它仍然算"题干"，要交给习题块（两件事不冲突）
    assert any("求下列向量组的秩" in x["title"] for x in result.stats["exercise_items"])


def test_code_lines_never_become_headings_or_get_merged(tmp_path):
    """代码块的行：**不当标题、不被合并**，但正文里照旧在。

    实测《数值计算方法》：伪代码 `2.2 fx:=fx+tmp*y$_{i}$` 被当成三级标题成了单元。
    """
    (tmp_path / "headings.json").write_text(json.dumps(
        {"headings": [], "page_region": {}, "regions": [], "code_lines": [[3, 4]]},
        ensure_ascii=False), encoding="utf-8")
    text = "\n".join([
        "[第 1 页]",
        "第1章 插值",
        "1.1 线性插值",
        "    2.2 fx:=fx+tmp*y$_{i}$",     # 代码行（行号 4）
        "    step3 输出结果",               # 代码行（行号 5）
        "正文继续，这一句会被折行规则接上",
    ])
    result = S.build_structure(text, source_path=tmp_path / "document.md")
    titles = [h.title for h in result.headings]
    assert not any("fx:=" in t for t in titles), "代码行被当成了标题"
    assert result.stats["code_lines_skipped"] >= 1
    # 代码行没被合并（两项都还在，且各自独立）
    assert "2.2 fx:=fx+tmp*y$_{i}$" in result.structured_markdown
    assert "step3 输出结果" in result.structured_markdown


def test_skeleton_covers_deeper_levels_only_when_toc_does(tmp_path):
    """目录把小节也列全了 → 骨架管到那一层；只列到节 → 小节仍由正文推断。"""
    text = "\n".join([
        "第1章 插值", "正文。",
        "1.1 线性插值", "正文。",
        "1.1.1 线性插值问题", "正文。",
        "1.1.2 二次插值问题", "正文。",
        "1.1.3 目录里没有的伪小节", "正文。",
    ])
    # ① 目录覆盖到 lv3（条目数与正文相当）→ 伪小节被剔
    covered = _with_toc(tmp_path, [
        {"text": "第1章 插值", "level": 1}, {"text": "1.1 线性插值", "level": 2},
        {"text": "1.1.1 线性插值问题", "level": 3}, {"text": "1.1.2 二次插值问题", "level": 3},
        {"text": "1.1.3 线性插值函数", "level": 3}, {"text": "1.1.4 余项", "level": 3},
        {"text": "1.1.5 收敛性", "level": 3},
    ])
    result = S.build_structure(text, source_path=covered)
    assert result.stats["skeleton"]["骨架层级"] == [1, 2, 3]
    assert "目录里没有的伪小节" not in [h.title for h in result.headings]

    # ② 目录只列到 lv2 → 小节不动（否则《线性代数》会掉 55 个真小节）
    shallow = _with_toc(tmp_path, [{"text": "第1章 插值", "level": 1},
                                   {"text": "1.1 线性插值", "level": 2}])
    result2 = S.build_structure(text, source_path=shallow)
    assert result2.stats["skeleton"]["骨架层级"] == [1, 2]
    assert "目录里没有的伪小节" in [h.title for h in result2.headings]


# ══════════════════════════════════════════════════════════════════════
# 2026-09-28 核验后补的守卫：`strict_sections` 只许收紧到 `§` 那一层
# ══════════════════════════════════════════════════════════════════════

def test_strict_sections_only_tightens_the_marked_level():
    """`§` 收紧规则只作用于 `§` 所在的层级，**不许把更深的小节一起抹平**。

    实测教训（《数分讲义第二册》，2026-09-28 核验抓出）：
    原规则是"这本书的节用 `§` ⇒ 不带 `§` 的 num 行一律当正文"。
    而那本书**只有节带 `§`**（`§8.1`），**小节不带**（`8.1.1 …`）
    —— 80 个小节全被判成正文丢掉：`structure.json` 只剩 39 个标题，
    导出层只切出 38 个节级单元，于是新正文层有 **81 个空节**
    （112 个三级目录节点里 81 个绑不上正文）。

    现在按**深度**收紧：只丢"深度 ≤ `§` 那一层"的 num 行。
    """
    text = "\n".join([
        "[第 1 页]",
        "第1章 测试章",
        "§1.1 测试节",
        "节导言一句话.",
        "1.1.1 测试小节一",
        "正文甲.",
        "1.1.2 测试小节二",
        "正文乙.",
        "§1.2 第二节",
        "1.2.1 第二节的小节",
        "正文丙.",
    ])
    result = S.build_structure(text)
    titles = [h.title for h in result.headings]
    assert "测试小节一" in titles, f"小节被抹平了：{titles}"
    assert "测试小节二" in titles
    assert "第二节的小节" in titles
    levels = {h.title: h.canonical_level for h in result.headings}
    assert levels["测试节"] == 2 and levels["测试小节一"] == 3


def test_tightening_still_drops_unmarked_lines_at_the_marked_level():
    """反方向：与 `§` 同级、却不带 `§` 的 num 行**仍然要丢**（那才是它治的东西）。

    正文里的编号列表项（`1. 设…`）和步骤不该变成"节" —— 这条规则不能被上面那条放宽掉。
    """
    text = "\n".join([
        "[第 1 页]",
        "第1章 测试章",
        "§1.1 测试节",
        "1.2 这是一个编号列表项，不是节",     # 与 § 同级、无 § → 丢
        "§1.3 第三节",
        "正文.",
        "§1.4 第四节",                       # 凑够 3 个 §，规则才启用（<3 不启用）
        "正文.",
    ])
    result = S.build_structure(text)
    titles = [h.title for h in result.headings]
    assert "这是一个编号列表项，不是节" not in titles
    assert "测试节" in titles and "第三节" in titles


# ── 视觉恢复的标题行（2026-09-30 用户定案：看图把"目录有、正文缺"的标题插回去）──
def _structure_cli():
    """按**文件路径**加载结构层的 `cli.py`（仓库里好几个块都叫 cli.py，`import cli` 会撞名）。"""
    import importlib.util
    from pathlib import Path
    import blocks.parse.structure as pkg
    path = Path(pkg.__file__).with_name("cli.py")
    spec = importlib.util.spec_from_file_location("structure_cli_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_apply_recovery_inserts_heading_and_shifts_authority_lines():
    """插**一行**标题（不是把锚那行当标题）并把其后 authority 行号 +1。

    为什么是"插一行"：锚是**正文首句**（"半导体三极管又称双极型晶体管…"），
    直接标成标题会把这句话从正文里吃掉 —— 实测《电路分析基础》§2.5 就是这么丢的正文。
    """
    CLI = _structure_cli()
    text = "上一节的正文。\n半导体三极管又称双极型晶体管，它是一种器件。\n后面的正文。\n"
    authority = [{"line": 1, "number": "2.4", "title": "电路的对偶性"},
                 {"line": 3, "number": "3.1", "title": "后一节"}]
    rows = [{"toc_id": "T-0019", "number": "2.5", "level": 2,
             "declared_title": "晶体管的大信号模型",
             "title": "＊§ 2-5 晶体管的大信号模型",
             "first_line_after": "半导体三极管又称双极型晶体管"}]
    warnings: list[str] = []
    new_text, new_auth = CLI.apply_recovery(text, authority, rows, warnings)
    lines = new_text.splitlines()
    assert lines[1].strip() == "§ 2-5 晶体管的大信号模型"     # 标题插在锚**前面**
    assert lines[2].startswith("半导体三极管")                 # 正文首句还在
    assert [a["line"] for a in new_auth] == [1, 2, 4]          # 原第 3 行 → 第 4 行
    inserted = new_auth[1]
    assert inserted["recovered_by"] == "vision"
    assert inserted["title"] == "晶体管的大信号模型"           # 用目录写法，不用 `＊§` 那种原文
    assert inserted["printed_title"] == "＊§ 2-5 晶体管的大信号模型"   # 原文留作证据
    assert any("recovered_by=vision" in w for w in warnings)


def test_apply_recovery_skips_when_anchor_is_missing():
    """锚在正文里找不到 → **不插**（宁可保持"目录有、正文缺"，也不按页码瞎插）。"""
    CLI = _structure_cli()
    text = "上一节的正文。\n下一段正文。\n"
    authority = [{"line": 1, "number": "2.4", "title": "电路的对偶性"}]
    rows = [{"toc_id": "T-0019", "number": "2.5", "level": 2,
             "declared_title": "晶体管的大信号模型", "title": "＊§ 2-5 晶体管的大信号模型",
             "first_line_after": "这一行在正文里根本不存在"}]
    warnings: list[str] = []
    new_text, new_auth = CLI.apply_recovery(text, authority, rows, warnings)
    assert new_text == text and len(new_auth) == 1
    assert any("不插" in w for w in warnings)
