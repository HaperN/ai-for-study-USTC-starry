"""Linear-algebra notation discovery hints for parsing, never author quality gates.

Migrated without changing the parser's observed gap output. These hints do not
claim a textbook is complete and are not applied as cross-discipline obligations.
"""
import re
from dataclasses import dataclass

@dataclass(frozen=True)
class Notation:
    """一个高风险记号：命中 `patterns` 就算用到了它，`hint` 是"最小定义该长什么样"。"""

    key: str                      # 人话名字（报错时给读者/模型看）
    patterns: tuple[str, ...]     # 正则；命中任一即算"用到了"
    hint: str                     # 最小定义该怎么写
    weak: bool = False

WATCHLIST: tuple[Notation, ...] = (
    Notation("行列式", (r"\\det", r"行列式"),
             "二阶行列式要给出算法：$\\begin{vmatrix}a&b\\\\c&d\\end{vmatrix}=ad-bc$；"
             "高阶的至少说明它由上一章的定义给出"),
    # `迹` 的子串误伤：单字环视不够（实测残留：奇迹/足迹/遗迹/迹象/绝迹/劣迹/墨迹/笔迹）。
    # 这类"迹"都不是矩阵的迹 → 用**否定多字前缀**列全，并配上"的迹/迹是/迹 ="这类句式。
    Notation("迹", (r"\\operatorname\{Tr\}", r"\\mathrm\{Tr\}", r"\\operatorname\{tr\}",
                   # 定长环视要**并列在同一个位置**（分开写成多条会被前一条先命中，
                   # 排除就形同虚设 —— 实测「有理数域」就是这么漏过去的）。
                   # 「迹象」要在**后面**排，所以用否定前视。
                   r"(?<![轨痕事奇足遗绝劣墨笔])迹(?!象)"),
             "要说明「迹就是主对角线上那些数相加」"),
    Notation("秩", (r"\\operatorname\{rank\}", r"\\operatorname\{r\}", r"矩阵的秩"),
             "要说明「秩 = 化成阶梯形后非零行的行数」"),
    # ⚠️ `数域` 必须排掉「复数域」「实数域」：它们**包含**「数域」两个字，
    #    第一版就在 §6.3.3 把「这里的相似变换允许在复数域上进行」报成了「数域没定义」。
    # 残留误伤（实测）：有理数域 / 整数域 / 函数域 —— 都是**别的东西**的名字。
    # ⚠️ 排除的是**紧挨着「数」的那一个字**：复数域→复、实数域→实、有理数域→理、
    #    整数域→整、函数域→函。（第一版把「有理数」「整数」当成整词写进环视，
    #    位置算错了 → 根本没排掉。）
    Notation("数域", (r"(?<![复实理整函有])数域",),
             "要说明「数域就是允许取值的数的集合（如实数域 $\\mathbb{R}$、复数域 $\\mathbb{C}$）」"
             "，并说明本节在哪个数域上讨论"),
    Notation("转置", (r"转置", r"A\^\{?\\mathrm\{T\}"),
             "要说明「转置就是把行和列互换」"),
    # ⚠️ `逆矩阵` 会被**「可逆矩阵」**子串命中 —— 而"可逆矩阵"是本节天天用的词，
    #    等于每条都假警报（实测抓出）。所以排除"可逆"，只认真在讲逆矩阵的地方。
    Notation("逆矩阵", (r"(?<!可)逆矩阵", r"A\^\{-1\}"),
             "要说明「$A^{-1}$ 是那个与 $A$ 相乘得到单位阵的矩阵」"),
    Notation("单位阵", (r"单位阵", r"单位矩阵", r"\\boldsymbol\{I\}", r"\bI_n\b"),
             "要说明「单位阵就是主对角线上全是 1、其余全是 0 的方阵」", weak=True),
    Notation("余子式", (r"(?<!代数)余子式",),
             "要说明「划掉某元素所在的行和列，剩下元素构成的行列式」"),
    Notation("代数余子式", (r"代数余子式",),
             "要说明「代数余子式 = 余子式前面乘 $(-1)^{i+j}$」"),
    Notation("特征多项式", (r"特征多项式",),
             "要说明「特征多项式就是 $\\det(\\lambda I-A)$」"),
    Notation("可对角化", (r"可对角化",),
             "要说明「可对角化 = 相似于一个对角阵」", weak=True),
    Notation("线性无关", (r"线性无关",),
             "要说明「线性无关 = 只有全取 0 的系数才能把它们凑成零向量」", weak=True),
)

def _hits(text: str, notation: Notation) -> bool:
    return any(re.search(pattern, text) for pattern in notation.patterns)

def notation_gaps(unit_text: str, rows: list[dict]) -> list[dict]:
    blob = " ".join(str(row.get("name") or "")+" "+str(row.get("formal") or "") for row in rows)
    return [{"key":item.key,"hint":item.hint,"weak":bool(item.weak)}
            for item in WATCHLIST if _hits(unit_text,item) and item.key and item.key not in blob]
