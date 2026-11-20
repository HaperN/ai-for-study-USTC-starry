"""M10 概念覆盖度：`normalize_name` **不能把名字归成空串**。

## 这个 bug 的代价（实测）

`normalize_name` 原先把名字按"先砍类型词、再砍编号"归一：
`定理1` → `1` → **`''`**。而 `_covers()` 开头就是 `if not wanted: return False` ——
于是**凡是「类型词 + 编号」形式的名字（定理1、性质5、推论1、引理2）永远被判成"没覆盖"**，
而它们恰恰是数学教材里最常见的形状。

后果不是"某个分片报错"，而是**整条流水线的门永久关着**：

| 事实 | 数值 |
|---|---|
| 真实书 `blocks_freeze` 的分片 | **28 / 80** |
| 为此花的重抽钱 | **669,453 token**（重抽当然修不好 —— 模型本来就把它们抽出来了，是尺子看不见） |
| 重抽的副作用 | 概念数 528 → **519**（**变差了**） |
| 用修好的尺子**重算**（不重抽） | 翻转 **38** 个分片，花费 **0**，`frozen: False → True`，概念数**仍是 528** |

所以这个文件守的是一条**数量级**级别的教训：
**一个卡住整条流水线的门，先要确认那把尺子是准的。**

## 判据

1. `定理1` 这类名字**不许**归成空串（否则 `_covers` 永远判没覆盖）；
2. 原意**不能丢**：`定理 1.1（柯西不等式）` 仍要归成 `柯西不等式`；
3. `_covers` 对"确实有的"判 True、对"确实没有的"判 False（两边都要钉，
   否则"全判 True"也能让测试变绿）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LEGACY_CONCEPTS = ROOT / "blocks" / "parse" / "concepts"


@pytest.fixture(scope="module")
def concepts():
    if str(LEGACY_CONCEPTS) not in sys.path:
        sys.path.insert(0, str(LEGACY_CONCEPTS))
    import extract_concepts
    return extract_concepts


# ────────────────────────────────────────── 判据 1：不许归成空串

@pytest.mark.parametrize("name", [
    "定理1", "定理 1", "性质5", "推论1", "推论2", "引理1", "引理2",
    "命题1", "公式3", "定义1", "定理1.1", "性质 12",
])
def test_a_type_word_plus_number_never_normalises_to_empty(concepts, name):
    """**这一条是根因**：`定理1` 归一成 `''` 会让 `_covers` 永远判"没覆盖"。"""
    key = concepts.normalize_name(name)
    assert key, f"{name!r} 归一成了空串 —— `_covers` 会永远判它没被覆盖"
    assert key == concepts.normalize_name(name), "归一必须稳定（同输入同输出）"


# ────────────────────────────────────────── 判据 2：原意不能丢

@pytest.mark.parametrize("name,expected", [
    ("定理 1.1（柯西不等式）", "柯西不等式"),   # 类型词与编号是噪声，括号里才是名字
    ("定义 2.3（解析函数）", "解析函数"),
    ("幂级数", "幂级数"),
    ("拉普拉斯方程", "拉普拉斯方程"),
    ("C-R 方程", "c-r方程"),                  # 归一化会小写并去空格
])
def test_the_original_intent_of_the_normaliser_is_preserved(concepts, name, expected):
    """修复**不能**顺手把原本正确的行为改坏 —— 这条防止"为了不返空而干脆不归一"。"""
    assert concepts.normalize_name(name) == expected


# ────────────────────────────────────────── 判据 3：两边都要钉

def test_covers_says_true_for_present_and_false_for_absent(concepts):
    """`_covers` 两边都要钉：只钉一边的话，"全判 True" 也能让测试变绿。"""
    rows = [{"name": "定理1"}, {"name": "调和函数"}]
    assert concepts._covers("定理1", rows) is True
    assert concepts._covers("调和函数", rows) is True
    assert concepts._covers("定理3", rows) is False, "确实没有的必须判 False"
    assert concepts._covers("柯西积分定理", rows) is False


def test_covers_handles_the_type_word_with_number_inside_a_longer_name(concepts):
    """编号条目与带说明的名字要能互相认出（`定理1.1` vs `定理 1.1（柯西不等式）`）。"""
    rows = [{"name": "定理 1.1（柯西不等式）"}]
    assert concepts._covers("柯西不等式", rows) is True
    assert concepts._covers("柯西不等式", [{"name": "柯西不等式"}]) is True


# ────────────────────────────────────────── 判据 4：端到端（真实形态）

def test_a_part_full_of_numbered_theorems_is_not_reported_as_missing(concepts):
    """端到端：一个只含编号定理的分片，**不该**被判成"有漏项"。

    这正是真实书上那 28 个分片的形状 —— 它们全都被误判了。
    """
    named = ["定理1", "定理2", "性质5", "推论1"]
    rows = [{"name": n} for n in named]
    missing = [n for n in named if not concepts._covers(n, rows)]
    assert missing == [], f"抽到的编号条目却被判成漏掉：{missing}"
