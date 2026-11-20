"""目录层回归测试 —— **三源抽取**（M1 的前半）。

这些用例锁的是**实测踩过的坑**，不是理论边界。每个坑都在注释里写了
"哪本书、什么现象、如果不修会怎样"。

跑法：
    ../.venv-linux/bin/python -m pytest tests/test_toc_sources.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blocks.parse.toc import sources as S  # noqa: E402

#: 四本回归用的书（PDF 与产物都在仓库里；缺了就让用例 skip，不让测试变成环境依赖）
BOOKS = {
    "numerical-methods": "data/raw/数值计算方法与算法.pdf",
    "math-analysis-2": "data/raw/数分讲义第二册.pdf",
}


# ── 编号解析（唯一一处） ────────────────────────────────────────────────
@pytest.mark.parametrize("text,number,title,depth", [
    ("第一章 向量", "第一章", "向量", 1),
    ("第 1 章 向量", "第1章", "向量", 1),
    ("10 章 多变量函数的重积分", "10章", "多变量函数的重积分", 1),
    ("1.1 向量的线性运算", "1.1", "向量的线性运算", 2),
    ("§8.1 向量与坐标系", "§8.1", "向量与坐标系", 2),
    ("*1.3 Hermite插值", "*1.3", "Hermite插值", 2),
    ("8.1.2 向量的共线和共面", "8.1.2", "向量的共线和共面", 3),
    ("一、完成的工作", "一", "完成的工作", 2),
    ("绪论", "", "绪论", 0),
])
def test_parse_title(text, number, title, depth):
    assert S.parse_title(text) == (number, title, depth)


def test_bare_integer_is_not_a_heading():
    """裸整数不是标题：实测《数值计算方法》正文里 `3 位和 5 位有效位数.`
    被当成"第 3 章"（假章）。裸编号在目录里是页码、在正文里是正文。
    """
    assert S.parse_title("3 位和 5 位有效位数.")[2] == 0
    assert S.parse_title("5")[2] == 0


# ── 身份键：两个 §/* 前缀的坑 ─────────────────────────────────────────
@pytest.mark.parametrize("left,right", [
    ("§8.1 向量与坐标系", "向量与坐标系"),
    ("8.1.2 向量的共线和共面", "向量的共线和共面"),
    ("*1.3 Hermite插值", "1.3 Hermite插值"),
    ("Gauss－Seidel迭代", "Gauss-Seidel 迭代"),
])
def test_identity_folds_prefix_and_punctuation(left, right):
    """编号可有可无、全角连接号折平 —— 否则《数分讲义第二册》
    （书签不带编号、印刷目录带编号）两源一条都对不上（实测只对上 37/155）。
    """
    assert S.identity(left, "") == S.identity(right, "") or \
        S.identity(*S.parse_title(left)[:2]) == S.identity(*S.parse_title(right)[:2])


# ── 印刷目录行 ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("row,number,printed,level", [
    ("1.1 复数 …… (3)", "1.1", "3", 2),
    ("第1章 复数和平面点集 …… 1", "第1章", "1", 1),
    ("第1章 复数和平面点集 …… (Ⅰ)", "第1章", "1", 1),
    ("8.1.2 向量的共线和共面   27", "8.1.2", "27", 3),
])
def test_parse_printed_line(row, number, printed, level):
    candidate, why = S.parse_printed_line(row)
    assert why is None
    assert (candidate.number, candidate.printed, candidate.level) == (number, printed, level)


def test_printed_line_without_page_is_unresolved_not_dropped():
    """没有页码的行**不丢**：进 unresolved（R-b）。"""
    candidate, why = S.parse_printed_line("第1章 复数和平面点集")
    assert candidate is None
    assert "页码" in why["why"]


def test_extract_printed_keeps_unparsable_rows():
    result = S.extract_printed(["1.1 复数 …… 3", "这是一行正文，不是目录条目"])
    assert len(result.candidates) == 1
    assert len(result.unresolved) == 1
    assert result.unresolved[0]["raw"].startswith("这是一行正文")


def test_roman_page_number(  # 《复变函数》第 1 章那 5 行全是罗马页码
):
    assert S.roman_to_int("Ⅰ") == "1"
    assert S.roman_to_int("Ⅻ") == "12"
    assert S.roman_to_int("7") == "7"
    assert S.roman_to_int("？") == "？"          # 不认的**原样返回**，不猜


# ── 书签层级位移（determine_shift） ────────────────────────────────────
def _printed(rows):
    return S.extract_printed(rows).candidates


def test_shift_is_learned_from_printed_toc():
    """《数值计算方法》：书签根是 `目录`（章在 lv2、节在 lv3），印刷目录章在 lv1 → 位移 1。

    关键是 `绪论`：**它不带编号**（印刷目录里也只是 `绪论 …… 1`），
    所以层级完全靠书签，形状投票投不了它的票。
    """
    bookmarks = [
        {"text": "目录", "level": 1, "pdf_page": 10},
        {"text": "绪论", "level": 3, "pdf_page": 14},
        {"text": "0.1数值计算方法与算法", "level": 3, "pdf_page": 14},
        {"text": "0.2误差与有效数字", "level": 3, "pdf_page": 15},
    ]
    printed = _printed(["绪论 …… 1", "0.1 数值计算方法与算法 …… 1",
                        "0.2 误差与有效数字 …… 2"])
    shift, detail = S.determine_shift(bookmarks, printed)
    assert shift == 1
    assert detail["method"] == "printed-toc"
    assert detail["support"] >= 2


def test_shift_falls_back_to_bookmark_shape_when_printed_is_unusable():
    """印刷目录用不了时，**按书签自己的形状兜底**，不再把整本书签作废（D-3）。

    用户 2026-09-28 定案（方案 A / Q1）：**印刷目录坏 ≠ 书签坏**。
    实测《电磁学》：目录页是多栏排布，印刷目录抽出来基本是废的（护栏拦掉 293 字的假条目后
    只剩 3 条）→ 旧实现走 `unresolved`，**把 238 条书签全部作废**，
    `toc.v2.json` 只剩 **4 个节点**；改成形状兜底后同一本书 **240 个节点**。

    ⚠️ 但兜底必须**保守**：位移量只许 0 或 1 层，且记 `method: bookmark-shape`
    （调用方据此把整本 confidence 降一档，等模型视觉复核）。
    """
    bookmarks = [
        {"text": "完全不同的东西甲", "level": 1, "pdf_page": 1},
        {"text": "完全不同的东西乙", "level": 2, "pdf_page": 2},
    ]
    printed = _printed(["1.1 复数 …… 3", "1.2 平面点集 …… 5"])
    shift, detail = S.determine_shift(bookmarks, printed)
    assert detail["method"] == "bookmark-shape"
    assert shift in (0, 1), "兜底位移量必须封顶 1 层"
    assert "印刷目录" in str(detail.get("why") or ""), "要说清为什么走了兜底"


def test_shape_fallback_never_moves_more_than_one_level():
    """兜底位移量**封顶 1 层**：书签顶层是 3 时也只减 1（不猜"整本移两层"）。"""
    bookmarks = [{"text": "甲", "level": 3, "pdf_page": 1},
                 {"text": "乙", "level": 4, "pdf_page": 2}]
    shift, detail = S.shape_shift(bookmarks)
    assert detail["method"] == "bookmark-shape"
    assert shift == 1, "顶层是 3 也只许减 1 层（封顶），否则整棵树会错位"


def test_wrapper_top_level_is_dropped_by_shape_fallback():
    """顶层是包装（`目录`/`封面`）时，形状兜底要**把它减掉**（实测三本都是 shift=1）。"""
    bookmarks = [{"text": "目录", "level": 1, "pdf_page": 1},
                 {"text": "第1章 静电场", "level": 2, "pdf_page": 7},
                 {"text": "1.1 电荷守恒", "level": 3, "pdf_page": 7}]
    shift, detail = S.shape_shift(bookmarks)
    assert shift == 1 and detail["majority_wrapper"] is True
    assert detail["top_count"] == 1 and detail["wrapper_count"] == 1


# ── 两源对齐（子序列 + 不许假配） ──────────────────────────────────────
def test_align_does_not_mispair_chapter_with_its_own_exercise_set():
    """**实测抓出的假配**：《数分讲义第二册》第 8 章叫 `空间解析几何`，
    章末的 `第 8 章综合习题` 折平后同名 —— 按层顺序补齐会把章配到习题集上，
    后面整段错位（实测凭空算出 36 条假冲突）。
    """
    bookmarks = [
        {"text": "空间解析几何", "level": 1, "pdf_page": 7},
        {"text": "向量与坐标系", "level": 2, "pdf_page": 7},
        {"text": "第 8 章综合习题", "level": 2, "pdf_page": 40},
        {"text": "重积分", "level": 1, "pdf_page": 138},
    ]
    printed = _printed(["第8章 空间解析几何 …… 1", "§8.1 向量与坐标系 …… 1",
                        "第8章综合习题 …… 40", "第9章 重积分 …… 138"])
    pairs, unmatched_bm, unmatched_pr = S.align_by_key(bookmarks, printed)
    paired = {bookmarks[i]["text"]: printed[j].text for i, j in pairs}
    assert paired.get("空间解析几何") == "第8章 空间解析几何"
    assert paired.get("第 8 章综合习题") == "第8章综合习题"
    assert paired.get("重积分") == "第9章 重积分"
    assert not unmatched_pr


def test_align_is_order_preserving():
    bookmarks = [{"text": "1.1 甲", "level": 2, "pdf_page": 1},
                 {"text": "1.2 乙", "level": 2, "pdf_page": 2}]
    printed = _printed(["1.2 乙 …… 2", "1.1 甲 …… 1"])       # 印刷目录行序被打乱
    pairs, _, _ = S.align_by_key(bookmarks, printed)
    assert len(pairs) == 1                    # 只按顺序配，能配的少但**不会配错**


# ── 页码映射（PageMap） ────────────────────────────────────────────────
def test_page_map_learns_offset_from_bookmarks():
    bookmarks = [{"text": f"{i}.1 第{i}节", "level": 2, "pdf_page": 10 + i}
                 for i in range(1, 8)]
    printed = _printed([f"{i}.1 第{i}节 …… {i}" for i in range(1, 8)])
    page_map = S.PageMap()
    page_map.learn(bookmarks, printed)
    assert page_map.offset == 10
    assert page_map.to_pdf("3") == 13
    assert page_map.to_printed(13) == "3"


def test_page_map_refuses_offset_without_enough_evidence():
    """证据不足就**留空**（不猜偏移量）。实测《数分讲义第二册》的噪声条目
    曾把众数从 6 拉到 4 —— 这种时候宁可 A2 空着。"""
    bookmarks = [{"text": "1.1 甲", "level": 2, "pdf_page": 100}]
    printed = _printed(["1.1 甲 …… 1"])
    page_map = S.PageMap()
    page_map.learn(bookmarks, printed)
    assert page_map.offset is None
    assert page_map.conflicts[0]["kind"] == "page-offset"


def test_page_map_accepts_a_clean_majority():
    bookmarks = [{"text": f"{i}.1 第{i}节", "level": 2, "pdf_page": 10 + i}
                 for i in range(1, 8)]
    printed = _printed([f"{i}.1 第{i}节 …… {i}" for i in range(1, 8)])
    page_map = S.PageMap()
    page_map.learn(bookmarks, printed)
    assert page_map.offset == 10
    assert not page_map.conflicts


def test_page_map_refuses_when_votes_are_split():
    """样本里夹一条异常落点 → **整本留空**，不硬给一个"多数派但不多的"数。

    实测《数分讲义第二册》：8 个样本夹 1 条 `习题 10.4`（书签页码落在第 40 页），
    众数被从 6 拉到 4。页码偏移差一层，全书 PDF 页全错 —— 这种时候宁可 A2 空着。
    """
    bookmarks = [{"text": f"{i}.1 第{i}节", "level": 2, "pdf_page": 10 + i}
                 for i in range(1, 8)]
    bookmarks.append({"text": "9.1 落点异常的", "level": 2, "pdf_page": 3})
    printed = _printed([f"{i}.1 第{i}节 …… {i}" for i in range(1, 8)]
                       + ["9.1 落点异常的 …… 9"])
    page_map = S.PageMap()
    page_map.learn(bookmarks, printed)
    assert page_map.offset is None
    assert any(row["kind"] == "page-offset" for row in page_map.conflicts)


# ── 真实书籍的冒烟（产物在仓库里才跑） ────────────────────────────────
@pytest.mark.parametrize("slug,pdf", sorted(BOOKS.items()))
def test_real_book_sources_are_sane(slug, pdf):
    """真书冒烟：书签能读出、位移量能定、页码偏移能学出来。

    这不是"验证解析正确"（那要人看），只是**保证三源在这本书上不是空的**。
    """
    pdf_path = ROOT / pdf
    parse_dir = ROOT / "data" / "parse" / slug
    if not pdf_path.is_file() or not (parse_dir / "toc.json").is_file():
        pytest.skip(f"{slug} 的 PDF 或产物不在仓库里")
    import json
    old = json.loads((parse_dir / "toc.json").read_text(encoding="utf-8"))
    printed = S.extract_printed(old.get("printed_entries") or [])
    raw = S.read_raw_bookmarks(pdf_path)
    bookmarks = S.extract_bookmarks(pdf_path, printed=printed.candidates)
    assert raw, "这本书应当有 PDF 书签"
    assert bookmarks.candidates, "书签应当能换算成候选"
    assert bookmarks.detail["shift"] in (0, 1), "位移量只该是 0 或 1 层"
    page_map = S.PageMap()
    page_map.learn(raw, printed.candidates, shift=bookmarks.detail["shift"])
    assert page_map.offset is not None, "有书签的书应当能学出页码偏移"
