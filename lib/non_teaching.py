"""**非正文**判据（前言/目录/附录/习题集/答案集）—— **全程序唯一一份**。

## 为什么要下沉到这里（D-19，2026-09-29）

原来有**两套**各写各的：

* `blocks/parse/mineru/adapt.py` 的 `FRONT_TITLE` + `EXERCISE_TITLE` → 决定 `toc.json` 里
  `chapters` / `chapters_numbered`（**体检"章数对账"的基线**）；
* `blocks/parse/structure/structure.py` 的 `NON_TEACHING_TITLE` + `EXERCISE_MARK` → 决定
  目录节点的 `kind`（解析侧）。

英文判据只加在了解析侧，于是《Basic Analysis》的 `titlepage` / `index` / `listofnotation`
在**解析侧**被正确判成非正文，而**基线那 12 章里还算着它们** ——
体检报"目录说 12 章 / 解析出 9 章"，一条**假警报**。

这份模块是两边判据的**并集**（中文前后置 + 英文前后置 + 习题集/答案集；
习题集同时认中文数字与阿拉伯数字、以及 `第8章综合习题` 这种写法）。

## 怎么用

```python
from lib.non_teaching import is_non_teaching      # 或 is_book_matter（同义，历史名字）
```

⚠️ 本仓的 CLI 有**两种导入风格**（`python -m blocks.parse.mineru.cli` 与
`from preflight import ...` 这种顶层模块方式），所以调用方要么用包路径导入、
要么带一个 `try/except` 兜底（见两个调用点的写法）。
"""
from __future__ import annotations

import re

#: `第N章`/`第N篇` 前缀 —— 判"是不是章"时要先剥掉它再比对（`第8章综合习题` → `综合习题`）
CHAPTER_PREFIX = re.compile(r"^\s*第\s*[一二三四五六七八九十百零〇\d]+\s*[章篇]\s*")

#: 中文书的前置/后置材料。`绪论`/`引言` **故意不列** —— 它们是正文的一部分
#: （《电磁学》的绪论章就是正文，实测被误排除过一次，见 D-13）。
FRONT_TITLE = re.compile(
    r"^\s*(?:第?\s*[一二三四五六七八九十百零〇\d]*\s*版?\s*前言|前言|序言|序|编者的话|"
    r"内容简介|图书在版编目.*|目录|目次|总序|编写说明|使用说明|出版说明|修订说明|"
    r"参考文献|参考书目|索引|后记|跋|致谢|附录.*|附表.*)\s*$")

#: 英文书的前置/后置材料（`Introduction` **不列** —— 很多书里它是真章）。
FRONT_TITLE_EN = re.compile(
    r"^\s*(?:title\s*page|titlepage|cover|half[-\s]?title|copyright(?:\s+page)?|"
    r"contents|table\s+of\s+contents|list\s+of\s+\w+|preface|foreword|"
    r"acknowledg\w*|dedication|abstract|bibliography|references|index|glossary|"
    r"appendix\b.*|annex\b.*|colophon|about\s+the\s+author\w*|epilogue|prologue|"
    r"notation|list\s+of\s+symbols)\s*$", re.I)

#: 习题集/答案集（**不是章**）：`习题 8.1`、`第 8 章综合习题`、`习题参考答案`、`练习1.2`…
#: ⚠️ 为什么必须单独判（2026-09-27 实测《数分讲义第二册》）：它把每章末尾的
#: `第 8 章综合习题` 排成与章同级的条目 —— 那不是章；它带 `第N章` 编号，
#: 还会把**书签位移量**带偏（真值 shift=0 被算成 1，真章名被降级）。
EXERCISE_TITLE = re.compile(
    r"^\s*(?:综合习题|习题|练习|复习题|总习题|习题解答|习题参考答案|参考答案|答案)"
    r"\s*[一二三四五六七八九十百零〇\d.．\-—]*\s*$")


def is_non_teaching(text: str) -> bool:
    """这条目录条目是不是**非正文**（前言/目录/附录/习题集/答案集）。

    只看标题本身、不做任何猜测；`第N章` 前缀会先剥掉（`第8章综合习题` → `综合习题`）。
    """
    stripped = str(text or "").strip()
    if not stripped:
        return False
    if FRONT_TITLE.match(stripped) or FRONT_TITLE_EN.match(stripped):
        return True
    return bool(EXERCISE_TITLE.match(CHAPTER_PREFIX.sub("", stripped)))


#: 历史名字（`adapt.py` 里一直叫 `is_book_matter`）—— 同义词，别再多一份实现。
is_book_matter = is_non_teaching

__all__ = ["CHAPTER_PREFIX", "EXERCISE_TITLE", "FRONT_TITLE", "FRONT_TITLE_EN",
           "is_book_matter", "is_non_teaching"]
