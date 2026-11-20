"""中文文本切分的小工具（跨块共用）。

断句规则：。！？；和换行都算。**故意做得简单** —— 它只服务于
"长度统计"和"太长就报出来"，不需要处理引号里的句号这种细节。
"""
from __future__ import annotations

import re


def sentences(text: str) -> list[str]:
    parts = re.split(r"[。！？；\n]+", text or "")
    return [part.strip() for part in parts if part.strip()]


def paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", (text or "").strip()) if part.strip()]


MATH = re.compile(r"\$\$[^$]+\$\$|\$[^$]+\$")
CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def strip_math(text: str) -> str:
    """去掉 `$...$` / `$$...$$` 里的公式，只留文字。

    为什么需要它：换 MinerU 之后原文里本来就带着公式源码，做"有没有整段照抄原文"
    这类**逐字比对**时，两边的同一段公式必然字面相同 —— 那不是抄袭，那是应该的
    （实测误报：`ldsymbol{c}=\boldsym…`）。所以比对前先把公式去掉。
    """
    return MATH.sub("", text or "")


def cjk_count(text: str) -> int:
    """汉字个数（跟 lib 里其它工具一样：只做计数，不做判断）。"""
    return len(CJK.findall(text or ""))


def visual_length(text: str, math_chars: int = 2) -> int:
    r"""估算"这句话读起来多长"：**公式按 2 个字算**，不按 LaTeX 源码长度算。

    实测踩过：`$\boldsymbol{a}^{\circ}=\dfrac{\boldsymbol{a}}{|\boldsymbol{a}|}$`
    源码 50+ 字符，渲染出来只是一个短公式。按源码长度算会把句长算虚高，
    于是模型被要求"拆句"，拆了也还是超 —— 白花一次调用。
    """
    return len(MATH.sub("□" * math_chars, text or ""))


def no_placeholders(text: str) -> str:
    """去掉 [[CONCEPT:...]] / [[FIGURE:...]] 这类标记（它们不该算进字数）。"""
    return re.sub(r"\[\[[^\]]*\]\]", "", text or "")


__all__ = ["sentences", "paragraphs", "no_placeholders", "visual_length",
           "strip_math", "cjk_count"]


# ── 引文/原句的"在不在正文里"（容差版，2026-09-26）────────────────────────────
# 为什么容差：目的是"**这句话确实存在**"，不是"逐字一模一样"。实测反复踩：
# 复核/结论里抄成半角标点、多一个空格、`\mathbf x` vs `\mathbf{x}` → 被判"找不到" →
# 一条真发现被丢掉、或一个已经改对的工单被降级成 partly（然后就冻在那儿）。
# ⚠️ 边界：容差只在**格式**（空白/全角半角/标点）上，不在**措辞**上 ——
#    "意思相近就算"会把别处一句相似的话当成落点，那是"改错地方"。
_PUNCT_TABLE = str.maketrans({"，": ",", "。": ".", "；": ";", "：": ":", "（": "(", "）": ")",
                              "、": ",", "！": "!", "？": "?", "“": '"', "”": '"',
                              "‘": "'", "’": "'", "　": " ", "％": "%", "－": "-", "—": "-"})


def normalize_for_match(text) -> str:
    """抹掉空白、统一标点 —— 只用于"找得到吗"的判断，不用于展示。"""
    return "".join(ch.translate(_PUNCT_TABLE) for ch in str(text or "") if not ch.isspace())


def locates_in(quote: str, text: str) -> bool:
    """这句话能不能在 `text` 里**定位到**（逐字不行就按归一化再试）。"""
    if not quote:
        return False
    return quote in text or normalize_for_match(quote) in normalize_for_match(text)
