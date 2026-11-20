"""Apply verified display errata to a deep-copied ReaderDocumentV1.

This is a presentation projection, never a mutation of a frozen source. The
caller keeps ``study_source_document`` / ``study_textbook_original`` untouched,
then calls ``correct_support_document(document, unit_id)`` on the final reader
projection before assigning reader_document and recomputing reader_layout.
The existing renderer consumes these blocks, and app.js clones the rendered
support DOM for popups, so no second browser-side override is needed.

Only exact verified statements registered below are admitted. Correct uses of
R=1/r, root criteria, explanations of an old error, and unrelated concepts are
not rewritten. The CLI emits separate originals, projections and reports.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / 'src'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from aiforstudy.blocks.content.reader_document import REFERENCE_PATTERN, _anchor, _project_text
from aiforstudy.core.digest import digest_of
from aiforstudy.core.review_mode import review_mode

CORRECTION_ID = 'SUPPORT-C0256-INVERSE-RATIO-V1'
CORRECTION_POLICY = 'VERIFIED-SUPPORT-DISPLAY-ERRATA-V4'
TARGET_ID = 'C-0256'
TARGET_ORIGIN_UNIT = 'CH05-S02'
TARGET_ANCHOR = 'reader-b2d1feb0c361060b4b8d'
BAD_STATEMENT = (
    r'如果$$\lim _ {n \to + \infty} \left| \frac {a _ {n - 1}}{a _ {n}} \right| = r '
    r'\quad \text {或} \quad \lim _ {n \to + \infty} \sqrt [ n ]{| a _ {n} |} = r,$$'
    '则复幂级数(5.5)的收敛半径 R = 1/r.'
)
# The source combines two differently oriented criteria under one r. Split
# them; replacing the final R by r alone would make the root criterion wrong.
CORRECT_STATEMENT = (
    '若相邻系数最终非零，且下列极限存在，则达朗贝尔公式为\n'
    r'$$\lim_{n\to\infty}\left|\frac{a_{n-1}}{a_n}\right|=r'
    r'\quad\Longrightarrow\quad R=r.$$',
    '约定 $r=0$ 时 $R=0$，$r=+\\infty$ 时 $R=+\\infty$。'
    '若用根值极限，则另记为 $L$，柯西公式为\n'
    r'$$\lim_{n\to\infty}\sqrt[n]{|a_n|}=L'
    r'\quad\Longrightarrow\quad R=\frac1L.$$',
    '根值公式约定 $1/0=+\\infty$、$1/(+\\infty)=0$；'
    '相邻系数缺项或比值无定义时，不直接使用相邻比值公式。'
)
CORRECT_STATEMENT = '\n\n'.join(CORRECT_STATEMENT)
ORIGINAL_PREFIX = (
    "由上面讨论，复幂级数(5.5)在 $z - a$ 的幂无间隔的条件下收敛半径可按达朗贝尔(d'Alembert)公式或柯西公式计算："
)
BAD_PARAGRAPH = ORIGINAL_PREFIX + BAD_STATEMENT
CORRECT_PARAGRAPH = ORIGINAL_PREFIX + '\n\n' + CORRECT_STATEMENT

EXACT_BLOCK_CORRECTIONS = [
    {
        'id': 'SUPPORT-CH05-S05-FINITE-PRODUCT-LIMIT-V1', 'finding': 'ch04-06/F06',
        'unit_id': 'CH05-S05', 'group': 'annotations', 'owner_id': 'PITFALL-LIMIT-FINITE',
        'json_pointer': '/annotations/3/markdown', 'anchor': 'reader-883a9fe4d23b5a962319',
        'before': r'在证明 $g/f$ 以 $a$ 为本性奇点时，若反设 $K=g/f$ 的极限有限，记为 $L$，则 $g=fK$ 在 $a$ 的极限是 $f(a)L$。当 $f(a)\ne0$（即 $f$ 在 $a$ 是解析点且不为零）时，这个极限是 $f(a)L\ne0$，不是 $0$；只有 $f(a)=0$（即 $f$ 以 $a$ 为零点，$n\ge1$）时才为 $0$。无论哪种情形，极限都有限，仍由可去奇点判别推论推出 $a$ 是 $g$ 的可去奇点，矛盾。',
        'after': r'在证明 $g/f$ 以 $a$ 为本性奇点时，若反设 $K=g/f$ 的极限有限，记为 $L$，则 $g=fK$ 在 $a$ 的极限是 $f(a)L$。这个极限可能为 $0$，也可能非零；即使 $f(a)\ne0$，仍可能因 $L=0$ 而为 $0$。无论哪种情形，极限都有限，仍由可去奇点判别推论推出 $a$ 是 $g$ 的可去奇点，矛盾。',
        'reason': 'The actual block assumes a finite quotient limit L, which can be zero. Only finiteness of f(a)L is needed for the contradiction.',
    },
    {
        'id': 'SUPPORT-CH05-S05-POLE-ORDER-SIGN-V1', 'finding': 'ch04-06/F07',
        'unit_id': 'CH05-S05', 'group': 'annotations', 'owner_id': 'INTUITION-BALANCE',
        'json_pointer': '/annotations/6/markdown', 'anchor': 'reader-38ce81abd87cce98b33f',
        'before': '罗朗展开的主要部分是负次幂项。没有负次幂项是“没有层”，对应可去奇点；有限多个负次幂项是“有限层”，对应极点，极点阶数等于最低负次幂的幂次；无限多个负次幂项是“无限层”，对应本性奇点。',
        'after': '罗朗展开的主要部分是负次幂项。没有负次幂项是“没有层”，对应可去奇点；有限多个非零负次幂项是“有限层”，对应极点。最低负次幂的指数为 $-m$ 时，极点阶数为正整数 $m$，即该负指数的绝对值；无限多个非零负次幂项是“无限层”，对应本性奇点。',
        'reason': 'For z^(-3), the lowest exponent is -3 while the pole order is the positive integer 3.',
    },
    {
        'id': 'SUPPORT-CH06-S03-OPEN-AXIS-WINDING-V1', 'finding': 'ch04-06/F10',
        'unit_id': 'CH06-S03', 'group': 'annotations', 'owner_id': 'LEFT-HALF-PITFALL',
        'json_pointer': '/annotations/6/markdown', 'anchor': 'reader-825f87b76bd66d6b4b08',
        'before': r'定理7要求多项式在虚轴上无零点。若虚轴上有零点，沿虚轴的辐角变化可能没有定义或需要绕行，定理不能直接使用。另外，$k$ 可以是半整数，因为 $\Delta \arg P(\mathrm{i}y)$ 只要求是 $2\pi$ 的整数倍中的 $2k\pi$，而它可以是 $\pi$ 的奇数倍，所以 $k=\frac12$ 是允许的。',
        'after': r'定理7要求多项式在虚轴上无零点。若虚轴上有零点，沿虚轴的辐角变化可能没有定义或需要绕行，定理不能直接使用。虚轴从 $-\infty$ 到 $+\infty$ 是一条开放路径，因此其像的连续辐角增量不必是 $2\pi$ 的整数倍。写作 $\Delta\arg P(\mathrm iy)=2k\pi$ 时，$k$ 可以是半整数；例如增量为 $\pi$ 就对应 $k=1/2$。与左半大圆的增量合起来，整个闭路的总增量才按整圈计数。',
        'reason': 'The imaginary-axis path is open; only the completed closed contour has an integral winding number.',
    },
]


# Exact canonical signatures verified against the delivered frozen catalog.
CANONICAL_CONCEPT_CORRECTIONS = [{'id': 'SUPPORT-C0175-CANONICAL-V1',
  'concept_id': 'C-0175',
  'origin_unit_id': 'CH02-S05-SS06',
  'label': '双曲函数的解析性',
  'before_variants': ['双曲正弦函数和双曲余弦函数都是全平面的解析函数，而双曲正切函数及双曲余切函数，则是分别除去 $z = n\\pi \\mathrm{i}$ 及 $z = (n '
                      '+ 1 / 2)\\pi \\mathrm{i}, n = 0, \\pm 1, \\pm 2, \\dots$ 的全平面上的解析函数'],
  'after': '双曲正弦 $\\sinh z$ 与双曲余弦 $\\cosh z$ 是整函数。双曲正切 $\\tanh z=\\sinh z/\\cosh z$ 的极点为 '
           '$z=(n+1/2)\\pi\\mathrm i$；双曲余切 $\\coth z=\\cosh z/\\sinh z$ 的极点为 $z=n\\pi\\mathrm i$，其中 '
           '$n\\in\\mathbb Z$。各商在除去各自这组极点的全平面解析。',
  'reason': 'tanh denominator cosh vanishes at (n+1/2)πi; coth denominator sinh vanishes at nπi.'},
 {'id': 'SUPPORT-C0522-CANONICAL-V1',
  'concept_id': 'C-0522',
  'origin_unit_id': 'CH02-S05-SS07',
  'label': '正整数次幂的单值性',
  'before_variants': ['当 $\\alpha$ 为正整数 $n$ 时，$z ^ {n} = \\exp [ n (\\ln | z | + \\operatorname{iarg} '
                      'z) ] = | z | ^ {n} \\exp (\\operatorname{iarg} z)$，与通常幂函数一致，是一个单值函数.'],
  'after': '当 $n$ 为正整数、$z\\ne0$ 时，选任一辐角 $\\theta$，有 $z^n=\\exp[n(\\ln|z|+\\mathrm i\\theta)]=|z|^n '
           'e^{\\mathrm in\\theta}$。将 $\\theta$ 改成 $\\theta+2k\\pi$ 不改变结果，所以与通常整数幂一致，是单值函数；在 $z=0$ 补定义 '
           '$0^n=0$。极式中的角度必须乘以 $n$，例如 $\\mathrm i^2=e^{\\mathrm i\\pi}=-1$。',
  'reason': 'Integer power multiplies the argument by n; i² must equal -1.'},
 {'id': 'SUPPORT-C0219-CANONICAL-V1',
  'concept_id': 'C-0219',
  'origin_unit_id': 'CH03-S05',
  'label': '柯西不等式',
  'before_variants': ['设 $f(z)$ 在区域 $D$ 内解析，以 $D$ 内任一点 $z$ 为圆心，作一个包含在 $D$ 内的圆周 $C: |\\zeta - z| = R$，设 '
                      '$M(R)$ 是 $|f(z)|$ 在 $C$ 上的最大值，则$$\\mid f ^ {(n)} (z) \\mid \\leqslant \\frac {n '
                      '! M (R)}{R ^ {n}}.$$',
                      '设 $f(z)$ 在区域 $D$ 内解析，以 $D$ 内任一点 $z$ 为圆心，作一个包含在 $D$ 内的圆周 $C: |\\zeta - z| = R$，设 '
                      '$M(R)$ 是 $|f(z)|$ 在 $C$ 上的最大值，则\n'
                      '$$\\mid f ^ {(n)} (z) \\mid \\leqslant \\frac {n ! M (R)}{R ^ {n}}.$$'],
  'after': '设 $f$ 在开区域 $D$ 内解析，且以 $a$ 为圆心、半径 $R>0$ 的整个闭圆盘 $\\{\\zeta:|\\zeta-a|\\le R\\}$ 包含于 $D$。记 '
           '$M(R)=\\max_{|\\zeta-a|=R}|f(\\zeta)|$，则对每个非负整数 $n$ 有 '
           '$$|f^{(n)}(a)|\\le\\frac{n!M(R)}{R^n}.$$ 只要求圆周在 $D$ 内不够；闭圆盘内部也不能有函数的奇点。',
  'reason': 'The Cauchy derivative estimate requires the full closed disk inside the analytic domain; a '
            'circle may enclose an excluded singularity.'},
 {'id': 'SUPPORT-C0796-CANONICAL-V1',
  'concept_id': 'C-0796',
  'origin_unit_id': 'CH09-S03-SS03',
  'label': '概率积分（误差函数）',
  'before_variants': ['由积分 $\\frac{2}{\\sqrt{\\pi}}\\int_0^n\\mathrm{e}^{-x^2}\\mathrm{d}x$ '
                      '所定义的函数，也是一类重要的特殊函数，它称为概率积分或误差函数，记为 $\\operatorname {erf}(x)$ ，于是$$\\mathbf {L} ^ '
                      '{- 1} \\left[ \\frac {\\sqrt {a}}{p (p + a)} \\right] = \\operatorname{erf} '
                      '(\\sqrt {a t}) \\quad (\\text {附表2中公式53}).$$'],
  'after': '对实数 $x$，误差函数定义为 $\\operatorname{erf}(x)=\\frac2{\\sqrt\\pi}\\int_0^x e^{-u^2}\\,\\mathrm '
           'du$。当 $a>0$、$t\\ge0$ 时，其拉普拉斯变换关系为 $$\\mathcal L^{-1}\\!\\left[\\frac{\\sqrt '
           'a}{p\\sqrt{p+a}}\\right](t)=\\operatorname{erf}(\\sqrt{at}),\\qquad\\operatorname{Re}p>0.$$ '
           '在右半平面取与正实轴正平方根一致的解析平方根；分母中是 $p\\sqrt{p+a}$，不能写成 $p(p+a)$。',
  'reason': 'L[erf(sqrt(at))]=sqrt(a)/(p sqrt(p+a)), as follows by transforming its derivative.'}]

EXACT_BLOCK_CORRECTIONS.extend([{'id': 'SUPPORT-CH08-S06-POISSON-Y-V1',
  'finding': 'second-pass/verified-legacy',
  'unit_id': 'CH08-S06',
  'group': 'annotations',
  'owner_id': 'EXAMPLE15-COEFFICIENT',
  'json_pointer': '/annotations/5/markdown',
  'anchor': 'reader-20516fd9897631e28cf9',
  'before': '用上半平面狄氏问题公式时，前面的系数是 $1/\\pi$，即\n'
            '$$u(x,y)=\\frac1\\pi\\left[v_2\\int_{-\\infty}^{0}\\frac{\\mathrm{d}t}{(t-x)^2+y^2}+v_1\\int_0^{+\\infty}\\frac{\\mathrm{d}t}{(t-x)^2+y^2}\\right].$$\n'
            '不要把 $1/\\pi$ 误写成 $v/\\pi$ 或丢掉。',
  'after': '用上半平面狄利克雷问题公式时，取 $y>0$，泊松核为 $y/((t-x)^2+y^2)$，归一化系数为 $1/\\pi$。因此 '
           '$$u(x,y)=\\frac1\\pi\\left[v_2\\int_{-\\infty}^{0}\\frac{y\\,\\mathrm '
           'dt}{(t-x)^2+y^2}+v_1\\int_0^{\\infty}\\frac{y\\,\\mathrm dt}{(t-x)^2+y^2}\\right].$$ '
           '不能漏掉核分子中的 $y$；若把 $y$ 提到积分号外，整体系数就写成 $y/\\pi$。例如 $v_1=v_2=1$ 时，积分应给出恒定解 $u=1$。',
  'reason': 'The upper half-plane Poisson kernel must contain y so constant boundary data stays '
            'constant.'},
 {'id': 'SUPPORT-CH08-ANGLE-MODULO-V1',
  'finding': 'second-pass/verified-legacy',
  'unit_id': 'CH08',
  'group': 'annotations',
  'owner_id': 'NOTE-NONZERO-DERIVATIVE',
  'json_pointer': '/annotations/0/markdown',
  'anchor': 'reader-1d3c3a7cc52a7eefa21f',
  'before': "把 $f(z_0+h)$ 写成 $f(z_0)+f'(z_0)h+$ 更高次项之后，决定小尺度形状的是一次项。若 "
            "$f'(z_0)=0$，这一项为零，起作用的变成更高次项，形状会被“张开”或“压扁”，而不是被旋转加均匀放大，局部保角性也就失去了。\n"
            '\n'
            "典型例子是 $f(z)=z^n$（$n\\ge 2$）在 $z_0=0$ 处：$f'(0)=0$。用 $z=r\\mathrm{e}^{\\mathrm{i}\\theta}$ 得 "
            '$z^n=r^n\\mathrm{e}^{\\mathrm{i}n\\theta}$，两条辐角为 $\\theta_1,\\theta_2$ 的射线被映成辐角为 '
            '$n\\theta_1,n\\theta_2$ 的射线，方向角之差由 $\\theta_2-\\theta_1$ 变为 '
            '$n(\\theta_2-\\theta_1)$。由于两条射线的夹角总取不超过 $\\pi$ 的较小角，只有原夹角不超过 $\\pi/n$ 时，像的实际夹角才恰好是原来的 $n$ '
            '倍；原夹角更大时，像的实际夹角要取 $|n(\\theta_2-\\theta_1)|$ 与 $2\\pi-|n(\\theta_2-\\theta_1)|$ '
            '中的较小者，可能反而变小甚至为 0。例如 $n=2$ 时，夹角 $\\frac{\\pi}{4}$ 的两条射线变成夹角 $\\frac{\\pi}{2}$ 的两条射线；而夹角 '
            '$\\pi$ 的两条射线（辐角 $0$ 与 $\\pi$）的像辐角为 $0$ 与 $2\\pi$，实际是同一条射线，夹角为 '
            '$0$。所以讨论整体保形变换时，必须把导数为零的点排除在外。',
  'after': "把 $f(z_0+h)$ 写成 $f(z_0)+f'(z_0)h+$ 更高次项之后，决定小尺度形状的是一次项。若 "
           "$f'(z_0)=0$，这一项为零，起作用的变成更高次项，形状会被“张开”或“压扁”，而不是被旋转加均匀放大，局部保角性也就失去了。\n"
           '\n'
           "典型例子是 $f(z)=z^n$（$n\\ge 2$）在 $z_0=0$ 处：$f'(0)=0$。用 $z=r\\mathrm{e}^{\\mathrm{i}\\theta}$ 得 "
           '$z^n=r^n\\mathrm{e}^{\\mathrm{i}n\\theta}$，两条辐角为 $\\theta_1,\\theta_2$ 的射线被映成辐角为 '
           '$n\\theta_1,n\\theta_2$ 的射线，方向角之差由 $\\theta_2-\\theta_1$ 变为 '
           '$n(\\theta_2-\\theta_1)$。由于两条射线的夹角总取不超过 $\\pi$ 的较小角，只有原夹角不超过 $\\pi/n$ 时，像的实际夹角才恰好是原来的 $n$ '
           '倍；原夹角更大时，要先将方向角之差按 $2\\pi$ 取模：令 $\\delta=n(\\theta_2-\\theta_1)\\bmod '
           '2\\pi\\in[0,2\\pi)$，再取实际夹角 $\\min(\\delta,2\\pi-\\delta)$。这样夹角总在 $[0,\\pi]$ 内，可能反而变小甚至为 '
           '$0$；例如 $n=3$、原方向角差为 $\\pi$ 时，先得 $3\\pi\\bmod2\\pi=\\pi$，实际夹角仍为 $\\pi$，不能得到负角。例如 $n=2$ 时，夹角 '
           '$\\frac{\\pi}{4}$ 的两条射线变成夹角 $\\frac{\\pi}{2}$ 的两条射线；而夹角 $\\pi$ 的两条射线（辐角 $0$ 与 $\\pi$）的像辐角为 '
           '$0$ 与 $2\\pi$，实际是同一条射线，夹角为 $0$。所以讨论整体保形变换时，必须把导数为零的点排除在外。',
  'reason': 'A multiplied angle must be reduced modulo 2π before choosing its smaller nonnegative '
            'angle.'},
 {'id': 'SUPPORT-CH06-ABSOLUTE-TWO-TAILS-V1',
  'finding': 'second-pass/verified-legacy',
  'unit_id': 'CH06-S02-SS02',
  'group': 'annotations',
  'owner_id': 'DERIVE-ABSOLUTE-CONVERGENCE',
  'json_pointer': '/annotations/8/markdown',
  'anchor': 'reader-c241684990503822ff44',
  'before': '当 $\\deg Q\\geqslant\\deg P+2$ 时，$|R(x)|\\leqslant C/x^2$（$|x|$ 充分大）。于是\n'
            '$$\\int_{-\\infty}^{+\\infty}|R(x)|\\,\\mathrm{d}x\\leqslant '
            'C\\int_{1}^{+\\infty}\\frac{\\mathrm{d}x}{x^2}<\\infty,$$\n'
            '所以 $\\int_{-\\infty}^{+\\infty}R(x)\\,\\mathrm{d}x$ 绝对收敛。绝对收敛的广义积分满足\n'
            '$$\\int_{-\\infty}^{+\\infty}R(x)\\,\\mathrm{d}x=\\lim_{R\\to\\infty}\\int_{-R}^{R}R(x)\\,\\mathrm{d}x,$$\n'
            '因为左右两侧的无穷积分各自收敛，可以把对称区间拆成 $[-R,0]$ 和 $[0,R]$ 分别取极限。',
  'after': '设有理函数 $R=P/Q$ 在实轴上无极点，且 $\\deg Q\\ge\\deg P+2$。存在 $A\\ge1$、$C>0$，使 $|x|\\ge A$ 时 '
           '$|R(x)|\\le C/x^2$；在紧区间 $[-A,A]$ 上，$R$ 连续，故其中绝对值积分有限。因此 '
           '$$\\begin{aligned}\\int_{-\\infty}^{\\infty}|R(x)|\\,\\mathrm '
           'dx&\\le\\int_{-A}^{A}|R(x)|\\,\\mathrm dx+2C\\int_A^{\\infty}\\frac{\\mathrm '
           'dx}{x^2}\\\\&=\\int_{-A}^{A}|R(x)|\\,\\mathrm dx+\\frac{2C}{A}<\\infty.\\end{aligned}$$ '
           '这份估计分别保留中央有限区间和左右两条尾部。由左右无穷积分各自收敛，才能用对称截断表示普通广义积分：$$\\int_{-\\infty}^{\\infty}R(x)\\,\\mathrm '
           'dx=\\lim_{T\\to\\infty}\\int_{-T}^{T}R(x)\\,\\mathrm dx.$$',
  'reason': 'The large-|x| bound controls two tails only; the finite middle interval must be included.'}])

# Incremental final active-support corrections, verified against source S.
CANONICAL_CONCEPT_CORRECTIONS.extend([{'id': 'SUPPORT-C0048-ACTIVE-V4',
  'concept_id': 'C-0048',
  'origin_unit_id': 'CH01-S01-SS04',
  'label': '复数的乘方',
  'before_variants': ['设 $n$ 是正整数，$z^n$ 表示 $n$ 个 $z$ 的乘积。当 $z = 0$ 时，$z^n = 0$。当 $z \\neq 0$ 时，设 $z = r '
                      'e^{i\\varphi}$，由乘法规则，得$$z ^ {n} = r ^ {n} \\mathrm{e} ^ {\\mathrm{i} m \\varphi} = r '
                      '^ {n} (\\cos n \\varphi + \\mathrm{i} \\sin n \\varphi).\\tag{1.4}$$显然，上式对 $n = 0$ '
                      '（定义 $z^{\\circ} = 1$）时也成立.如果定义$$z ^ {- n} = \\frac {1}{z ^ {n}},$$ $$z ^ {- n} = r ^ '
                      '{- n} [ \\cos (- n \\varphi) + \\mathrm{i} \\sin (- n \\varphi) ] = r ^ {- n} '
                      '\\mathrm{e} ^ {- i n \\varphi}.\\tag{1.5}$$'],
  'after': '对正整数 $n$，$z^n$ 表示 $n$ 个 $z$ 的乘积，$0^n=0$。对 $z\\ne0$，写 $z=re^{\\mathrm i\\varphi}$、$r>0$，则对任意整数 '
           '$n$ 有 $$z^n=r^ne^{\\mathrm in\\varphi}=r^n[\\cos(n\\varphi)+\\mathrm '
           'i\\sin(n\\varphi)]\\tag{1.4}$$ 其中 '
           '$z^0=1$，$z^{-n}=1/z^n$（$n>0$）；非正整数幂要求底数非零。指数中的整数必须与幂次相同，零次幂的上标为数字 $0$。对正整数 $n$ 和非零 '
           '$z=re^{\\mathrm i\\varphi}$，负幂的等价极式为 $$z^{-n}=r^{-n}[\\cos(-n\\varphi)+\\mathrm '
           'i\\sin(-n\\varphi)]=r^{-n}e^{-\\mathrm in\\varphi}.\\tag{1.5}$$',
  'reason': 'Incremental verified active-support correction; exact canonical identity and complete old text '
            'required.'},
 {'id': 'SUPPORT-C0288-ACTIVE-V4',
  'concept_id': 'C-0288',
  'origin_unit_id': 'CH06-S02-SS01',
  'label': '泊松积分',
  'before_variants': ['$$\\int_ {0} ^ {2 \\pi} \\frac {\\mathrm{d} \\theta}{1 - 2 p \\cos \\theta + p ^ {2}} '
                      '= \\frac {2 \\pi}{1 - p ^ {2}}.$$'],
  'after': '若参数 $p$ 为实数且 $0<p<1$，则 $$\\int_0^{2\\pi}\\frac{\\mathrm '
           'd\\theta}{1-2p\\cos\\theta+p^2}=\\frac{2\\pi}{1-p^2}.$$ '
           '这是本题参数范围内的泊松型积分。参数范围保证分母在积分区间不为零，并确定代换后哪些极点在单位圆内；不能把同一右式无条件用于任意 $p$。',
  'reason': 'Incremental verified active-support correction; exact canonical identity and complete old text '
            'required.'},
 {'id': 'SUPPORT-C0580-ACTIVE-V4',
  'concept_id': 'C-0580',
  'origin_unit_id': 'CH06-S02-SS02',
  'label': '一般公式',
  'before_variants': ['$$\\int_ {- \\infty} ^ {+ \\infty} R (x) \\mathrm{d} x = 2 \\pi \\mathrm{i} \\sum_ {k '
                      '= 1} ^ {n} \\operatorname{Res} [ R (z), a _ {k} ].$$'],
  'after': '设有理函数 $R=P/Q$ 在实轴上无极点，且 $\\deg Q\\ge\\deg P+2$。令 $a_k$ 遍历严格上半平面的全部极点，则普通广义积分绝对收敛，且 '
           '$$\\int_{-\\infty}^{\\infty}R(x)\\,\\mathrm dx=2\\pi\\mathrm '
           'i\\sum_{\\operatorname{Im}a_k>0}\\operatorname{Res}(R,a_k).$$ 实轴上的极点不能直接计入此式，次数条件用于保证大半圆弧积分趋零。',
  'reason': 'Incremental verified active-support correction; exact canonical identity and complete old text '
            'required.'},
 {'id': 'SUPPORT-C0788-ACTIVE-V4',
  'concept_id': 'C-0788',
  'origin_unit_id': 'CH06-S02-SS03',
  'label': '公式(6.10)',
  'before_variants': ['$$\\mathrm{v.p.} \\int_ {- \\infty} ^ {+ \\infty} R (x) \\mathrm{e} ^ {\\mathrm{i} m '
                      'x} \\mathrm{d} x = 2 \\pi \\mathrm{i} \\sum_ {k = 1} ^ {n} \\operatorname{Res} [ R '
                      '(z) \\mathrm{e} ^ {\\mathrm{i} m z}, a _ {k} ] + \\pi \\mathrm{i} \\sum_ {k = 1} ^ '
                      '{l} \\operatorname{Res} [ R (z) \\mathrm{e} ^ {\\mathrm{i} m z}, x _ {k} '
                      '].\\tag{6.10}$$'],
  'after': '设 $m>0$ 为实数，$R=P/Q$ 为有理函数，$\\deg Q\\ge\\deg P+1$，实轴上至多有有限个单极点 $x_j$。记 $F(z)=R(z)e^{\\mathrm '
           'imz}$，$a_k$ 为严格上半平面的全部极点，则 $$\\operatorname{v.p.}\\int_{-\\infty}^{\\infty}R(x)e^{\\mathrm '
           'imx}\\,\\mathrm dx=2\\pi\\mathrm '
           'i\\sum_{\\operatorname{Im}a_k>0}\\operatorname{Res}(F,a_k)+\\pi\\mathrm '
           'i\\sum_j\\operatorname{Res}(F,x_j)\\tag{6.10}$$ 实轴小弧从上方顺时针挖去，移项后给第二个和；无实轴极点时该和为空。$m>0$ '
           '决定指数在上半平面衰减；负频率须改选半平面并重新核对方向。',
  'reason': 'Incremental verified active-support correction; exact canonical identity and complete old text '
            'required.'},
 {'id': 'SUPPORT-C0437-ACTIVE-V4',
  'concept_id': 'C-0437',
  'origin_unit_id': 'CH09-S03-SS02',
  'label': '拉氏变换的反演公式',
  'before_variants': ['$$f (t) = \\frac {1}{2 \\pi \\mathrm{i}} \\int_ {\\sigma - \\mathrm{i} m} ^ {\\sigma '
                      '+ \\mathrm{i} m} F (p) \\mathrm{e} ^ {p t} \\mathrm{d} '
                      'p.\\tag{9.10}$$上式右端的积分是沿自下而上的直线 $\\mathrm{Re}p = \\sigma$ 进行的.',
                      '$$f (t) = \\frac {1} {2 \\pi \\mathrm{i}} \\int_ {\\sigma - \\mathrm{i} m} ^ {\\sigma '
                      '+ \\mathrm{i} m} F (p) \\mathrm{e} ^ {p t} \\mathrm{d} p.\\tag{9.10}$$'],
  'after': '设因果函数 $f$ 在有限区间逐段光滑，且满足指数界 $|f(t)|\\le Ke^{ct}$，$F=\\mathcal L[f]$。取 $\\sigma>c$，沿竖线自下而上，在 $t>0$ '
           '的连续点有 $$f(t)=\\frac1{2\\pi\\mathrm i}\\lim_{T\\to\\infty}\\int_{\\sigma-\\mathrm '
           'iT}^{\\sigma+\\mathrm iT}F(p)e^{pt}\\,\\mathrm dp\\tag{9.10}$$ '
           '这里先作有限对称截断再取极限；跳跃点恢复左右极限的平均值。有限的上下端点本身不能给出精确反演。',
  'reason': 'Incremental verified active-support correction; exact canonical identity and complete old text '
            'required.'},
 {'id': 'SUPPORT-C0438-ACTIVE-V4',
  'concept_id': 'C-0438',
  'origin_unit_id': 'CH09-S03-SS02',
  'label': '傅立叶-梅林公式',
  'before_variants': ['这条定理所给出的拉氏变换的反演公式，称为傅立叶-梅林(Fourier-Mellin)公式.$$f (t) = \\frac {1}{2 \\pi \\mathrm{i}} '
                      '\\int_ {\\sigma - \\mathrm{i} m} ^ {\\sigma + \\mathrm{i} m} F (p) \\mathrm{e} ^ {p '
                      't} \\mathrm{d} p.\\tag{9.10}$$'],
  'after': '傅立叶–梅林公式是拉氏变换的竖线反演公式。设因果函数 $f$ 在有限区间逐段光滑，且满足指数界 $|f(t)|\\le Ke^{ct}$，$F=\\mathcal L[f]$。取 '
           '$\\sigma>c$，沿竖线自下而上，在 $t>0$ 的连续点有 $$f(t)=\\frac1{2\\pi\\mathrm '
           'i}\\lim_{T\\to\\infty}\\int_{\\sigma-\\mathrm iT}^{\\sigma+\\mathrm iT}F(p)e^{pt}\\,\\mathrm '
           'dp\\tag{9.10}$$ 这里先作有限对称截断再取极限；跳跃点恢复左右极限的平均值。有限的上下端点本身不能给出精确反演。',
  'reason': 'Incremental verified active-support correction; exact canonical identity and complete old text '
            'required.'}])
EXACT_BLOCK_CORRECTIONS.extend([{'id': 'SUPPORT-LAURENT-EQUAL-RADII-V4',
  'finding': 'incremental-final-active-support',
  'unit_id': 'CH05-S04',
  'group': 'explainables',
  'owner_id': 'CONVERGENCE-ANNULUS',
  'json_pointer': '/explainables/0/markdown',
  'anchor': 'reader-d2f581b7becfd12884bb',
  'before': '若 $R\\le r$，两部分没有公共收敛点，罗朗级数不收敛。收敛域不包含点 $a$ 本身，因为负幂项在 $z=a$ 处无定义。',
  'after': '负幂部分在 $|z-a|>r$ 内收敛，非负幂部分在 $|z-a|<R$ 内收敛。当 $R>r$，共同的开收敛域是圆环 $r<|z-a|<R$；若 $R<r$，连共同收敛点也没有；若 '
           '$R=r$，没有非空的共同开圆环，但圆周 $|z-a|=R$ 上仍可能有两部分同时收敛的点，必须分别判别边界级数。例如 '
           '$\\sum_{n\\ge1}(z-a)^n/n^2+\\sum_{n\\ge1}(z-a)^{-n}/n^2$ 在 $|z-a|=1$ 上两部分均绝对收敛。若有非零负幂项，中心 $a$ '
           '不能直接代入这些项。',
  'reason': 'Open annuli and boundary convergence points must be distinguished; R=r can have a common '
            'circle.'},
 {'id': 'SUPPORT-E17-PRODUCT-FINITE-DUPLICATE-V4',
  'finding': 'incremental-final-active-support',
  'unit_id': 'CH05-S05',
  'group': 'examples',
  'owner_id': 'E17',
  'json_pointer': '/examples/2/pitfalls/3',
  'anchor': 'reader-bb5724e931546e5d3bce',
  'before': '在 $g/f$ 的论证中，若反设 $K=g/f$ 的极限有限，不能由 $f\\to0$ 推出 $g=fK$ 的极限必为 $0$；当 $f(a)\\ne0$ 时极限是 '
            '$f(a)L$。具体见[[NOTE:PITFALL-LIMIT-FINITE|K 极限有限时 g 的极限未必是 0]]。',
  'after': '在 $g/f$ 的论证中，若反设 $K=g/f\\to L\\in\\mathbb C$，且 $f$ 在 $a$ 解析，则 $g=fK\\to f(a)L$，因此极限总是有限。特别地，若 '
           '$f\\to0$，乘积必趋于 $0$；若 $f(a)\\ne0$，乘积极限仍可能因 $L=0$ 而为 '
           '$0$。反证只需有限性，不需要非零性。具体见[[NOTE:PITFALL-LIMIT-FINITE|有限乘积极限的正确用法]]。',
  'reason': 'Correct the example-local duplicate without editing deliberate historical quotations.'},
 {'id': 'SUPPORT-REAL-QUADRATIC-POLYNOMIAL-NOTE-V4',
  'finding': 'incremental-final-active-support',
  'unit_id': 'CH09-S03-SS01',
  'group': 'annotations',
  'owner_id': 'NOTE-REAL-DECOMP',
  'json_pointer': '/annotations/3/markdown',
  'anchor': 'reader-e1ef09fa8becc1d767ab',
  'before': '当 $A(p)$、$B(p)$ 都是实系数多项式时，可把 $B(p)$ 在实数范围内分解成 $(p-a)^{m}$ 与 '
            '$(p^{2}+bp+c)^{n}$（$b^{2}-4c<0$）的乘积，再把 $F(p)$ 拆成 $\\dfrac{A}{(p-a)^{s}}$ 与 '
            '$\\dfrac{Bp+C}{(p^{2}+bp+c)^{s}}$ 两类项，所有系数都是实数。每一类项反演出来分别是 '
            '$\\dfrac{t^{s-1}}{(s-1)!}\\mathrm{e}^{at}$ 和 $\\mathrm{e}^{\\alpha t}\\cos\\beta '
            't$、$\\mathrm{e}^{\\alpha t}\\sin\\beta t$ 的组合（把 $p^{2}+bp+c$ 配方成 '
            '$(p-\\alpha)^{2}+\\beta^{2}$，再用位移定理），所以本函数是实值函数，这一点可以用来检验结果。用复数范围分解当然也可以，只是系数会成共轭对出现，把共轭的两项配对相加同样得到实值表达式；两种写法算出的是同一个本函数，但实数范围分解的计算量一般更小。',
  'after': '当 $A(p)$、$B(p)$ 都是实系数多项式时，可把 $B(p)$ 在实数范围内分解成 $(p-a)^{m}$ 与 '
           '$(p^{2}+bp+c)^{n}$（$b^{2}-4c<0$）的乘积，再把 $F(p)$ 拆成 $\\dfrac{A}{(p-a)^{s}}$ 与 '
           '$\\dfrac{Bp+C}{(p^{2}+bp+c)^{s}}$ 两类项，所有系数都是实数。每一类项反演出来分别是 '
           '$\\dfrac{t^{s-1}}{(s-1)!}\\mathrm{e}^{at}$ 和 $e^{\\alpha t}[P_{s-1}(t)\\cos(\\beta '
           't)+Q_{s-1}(t)\\sin(\\beta t)]$，其中 $P_{s-1},Q_{s-1}$ 为次数至多 $s-1$ 的实多项式；仅 $s=1$ 时才退化为常系数三角组合（把 '
           '$p^{2}+bp+c$ 配方成 '
           '$(p-\\alpha)^{2}+\\beta^{2}$，再用位移定理），所以本函数是实值函数，这一点可以用来检验结果。用复数范围分解当然也可以，只是系数会成共轭对出现，把共轭的两项配对相加同样得到实值表达式；两种写法算出的是同一个本函数，但实数范围分解的计算量一般更小。',
  'reason': 'Repeated conjugate poles produce time polynomials of degree at most s-1.'},
 {'id': 'SUPPORT-REAL-QUADRATIC-POLYNOMIAL-EXPLAIN-V4',
  'finding': 'incremental-final-active-support',
  'unit_id': 'CH09-S03-SS01',
  'group': 'explainables',
  'owner_id': 'EXP-PARTIAL-FRACTION',
  'json_pointer': '/explainables/0/markdown',
  'anchor': 'reader-54861aef0deca8652465',
  'before': '**为什么存在。** 记 $N=\\deg B=\\sum_k m_k$。第一步，令 $c_{k}=A_{k m_k}=\\lim_{p\\to '
            'a_k}(p-a_k)^{m_k}F(p)$：由于 $F(p)$ 在 $a_k$ 处至多有 $m_k$ 阶极点，$(p-a_k)^{m_k}F(p)$ 在 $a_k$ '
            '的某个去心邻域内有界，这个极限存在且有限。第二步，把 $F_1(p)=F(p)-\\sum_{k=1}^{m}\\dfrac{c_k}{(p-a_k)^{m_k}}$ 看作新的函数：它在每个 '
            '$a_k$ 处的极点阶数至多为 $m_k-1$，其余点处仍无极点，并且仍是真分式（把右端通分，分母可取 $B(p)$，分子次数仍小于 $\\deg B$）。对 $F_1$ '
            '重复同样的操作，每做一轮，各极点的阶数都至少降低 $1$，总共有限轮之后得到的函数在复平面上处处无极点，是一个多项式；另一方面它又是 $F$ 与有限个趋于 $0$ 的项之差，故 '
            '$p\\to\\infty$ 时趋于 $0$，所以这个多项式恒为 $0$。这说明分解式成立，并且一共给出 $\\sum_k m_k=\\deg B$ '
            '个常数。唯一性并不来自“常数的个数与方程个数相等”这种计数理由：个数相等既不能排除无解，也不能排除多解。这里唯一性来自上述构造本身——每一步最高阶的系数 $c_k=\\lim_{p\\to '
            'a_k}(p-a_k)^{m_k}F(p)$ 由 $F(p)$ 唯一确定，减去这些已确定的项后，新函数又随之唯一确定，下一步的系数再由它的极限唯一确定；照此逐层进行，每个 $A_{ks}$ '
            '都被唯一确定，因此分解式唯一。\n'
            '\n'
            '**系数怎么求。** 除了上述“逐次取极限再相减”的递推办法，常用的还有两种等价做法。\n'
            '\n'
            '1. 消去分母比较系数：把分解式两边乘以 $B(p)$，得到关于 $p$ 的多项式恒等式，再令同次幂的系数相等，解出 $A_{ks}$。例15、例16 用的就是这一种（例16 先代入 '
            '$p=1$ 减少未知量）。\n'
            '2. 对重零点更方便的公式：把 $(p-a_k)^{m_k}F(p)$ 在 $a_k$ 处作泰勒展开，可得 $$A_{ks}=\\frac{1}{(m_k-s)!}\\lim_{p\\to '
            'a_k}\\frac{\\mathrm{d}^{m_k-s}}{\\mathrm{d}p^{m_k-s}}\\Big[(p-a_k)^{m_k}F(p)\\Big],\\qquad '
            's=1,\\dots,m_k.$$ 当 $s=m_k$ 时它就是前面用过的极限 $\\lim_{p\\to a_k}(p-a_k)^{m_k}F(p)$。\n'
            '\n'
            '**实系数情形的差别。** 若 $A(p)$、$B(p)$ 都是实系数多项式，也可以在实数范围内把 $B(p)$ 分解成 $(p-a)^{m}$ 与 '
            '$(p^{2}+bp+c)^{n}$（$b^{2}-4c<0$）的乘积，此时分解式的每一项形如 $\\dfrac{A}{(p-a)^{s}}$ 或 '
            '$\\dfrac{Bp+C}{(p^{2}+bp+c)^{s}}$，系数全为实数，反演出来的是 $\\dfrac{t^{s-1}}{(s-1)!}\\mathrm{e}^{at}$ 与 '
            '$\\mathrm{e}^{\\alpha t}\\cos\\beta t$、$\\mathrm{e}^{\\alpha t}\\sin\\beta t$ '
            '的组合，结果自然是实值函数。若改用复数范围分解，系数会成共轭对出现，把共轭的两项配对相加，虚部抵消，同样回到实值表达式。两种写法能算出同一个本函数，但实数范围分解在计算上通常更省。',
  'after': '**为什么存在。** 记 $N=\\deg B=\\sum_k m_k$。第一步，令 $c_{k}=A_{k m_k}=\\lim_{p\\to '
           'a_k}(p-a_k)^{m_k}F(p)$：由于 $F(p)$ 在 $a_k$ 处至多有 $m_k$ 阶极点，$(p-a_k)^{m_k}F(p)$ 在 $a_k$ '
           '的某个去心邻域内有界，这个极限存在且有限。第二步，把 $F_1(p)=F(p)-\\sum_{k=1}^{m}\\dfrac{c_k}{(p-a_k)^{m_k}}$ 看作新的函数：它在每个 '
           '$a_k$ 处的极点阶数至多为 $m_k-1$，其余点处仍无极点，并且仍是真分式（把右端通分，分母可取 $B(p)$，分子次数仍小于 $\\deg B$）。对 $F_1$ '
           '重复同样的操作，每做一轮，各极点的阶数都至少降低 $1$，总共有限轮之后得到的函数在复平面上处处无极点，是一个多项式；另一方面它又是 $F$ 与有限个趋于 $0$ 的项之差，故 '
           '$p\\to\\infty$ 时趋于 $0$，所以这个多项式恒为 $0$。这说明分解式成立，并且一共给出 $\\sum_k m_k=\\deg B$ '
           '个常数。唯一性并不来自“常数的个数与方程个数相等”这种计数理由：个数相等既不能排除无解，也不能排除多解。这里唯一性来自上述构造本身——每一步最高阶的系数 $c_k=\\lim_{p\\to '
           'a_k}(p-a_k)^{m_k}F(p)$ 由 $F(p)$ 唯一确定，减去这些已确定的项后，新函数又随之唯一确定，下一步的系数再由它的极限唯一确定；照此逐层进行，每个 $A_{ks}$ '
           '都被唯一确定，因此分解式唯一。\n'
           '\n'
           '**系数怎么求。** 除了上述“逐次取极限再相减”的递推办法，常用的还有两种等价做法。\n'
           '\n'
           '1. 消去分母比较系数：把分解式两边乘以 $B(p)$，得到关于 $p$ 的多项式恒等式，再令同次幂的系数相等，解出 $A_{ks}$。例15、例16 用的就是这一种（例16 先代入 '
           '$p=1$ 减少未知量）。\n'
           '2. 对重零点更方便的公式：把 $(p-a_k)^{m_k}F(p)$ 在 $a_k$ 处作泰勒展开，可得 $$A_{ks}=\\frac{1}{(m_k-s)!}\\lim_{p\\to '
           'a_k}\\frac{\\mathrm{d}^{m_k-s}}{\\mathrm{d}p^{m_k-s}}\\Big[(p-a_k)^{m_k}F(p)\\Big],\\qquad '
           's=1,\\dots,m_k.$$ 当 $s=m_k$ 时它就是前面用过的极限 $\\lim_{p\\to a_k}(p-a_k)^{m_k}F(p)$。\n'
           '\n'
           '**实系数情形的差别。** 若 $A(p)$、$B(p)$ 都是实系数多项式，也可以在实数范围内把 $B(p)$ 分解成 $(p-a)^{m}$ 与 '
           '$(p^{2}+bp+c)^{n}$（$b^{2}-4c<0$）的乘积，此时分解式的每一项形如 $\\dfrac{A}{(p-a)^{s}}$ 或 '
           '$\\dfrac{Bp+C}{(p^{2}+bp+c)^{s}}$，系数全为实数，反演出来的是 $\\dfrac{t^{s-1}}{(s-1)!}\\mathrm{e}^{at}$ 与 '
           '$e^{\\alpha t}[P_{s-1}(t)\\cos(\\beta t)+Q_{s-1}(t)\\sin(\\beta t)]$，其中 $P_{s-1},Q_{s-1}$ 为次数至多 '
           '$s-1$ 的实多项式；仅 $s=1$ '
           '时才退化为常系数三角组合，结果自然是实值函数。若改用复数范围分解，系数会成共轭对出现，把共轭的两项配对相加，虚部抵消，同样回到实值表达式。两种写法能算出同一个本函数，但实数范围分解在计算上通常更省。',
  'reason': 'Synchronize the explanation duplicate of the real repeated-quadratic inversion rule.'},
 {'id': 'SUPPORT-ZHUKOVSKY-EX8-BRANCH-STEP4-V4',
  'finding': 'incremental-final-active-support',
  'unit_id': 'CH08-S04-SS03',
  'group': 'examples',
  'owner_id': 'EX-ZHUKOVSKY-8',
  'json_pointer': '/examples/0/steps/3/markdown',
  'anchor': 'reader-9829042aa45203a81879',
  'before': '取根式函数 $w = \\sqrt{w_3}$，以正实轴为支割线，并取 $\\sqrt{1}=1$ 的一支。它把去掉正实轴的平面 $D_3$ 变为上半 $w$ 平面。',
  'after': '取 $w=\\sqrt{w_3}$，明确在 $w_3$ 平面沿正实轴割开，连续辐角取 $0<\\arg w_3<2\\pi$，并定义 $w=|w_3|^{1/2}e^{\\mathrm '
           'i\\arg(w_3)/2}$。它把这个割平面单叶地映到上半平面；“$\\sqrt1=1$”在此只指从辐角 $0$ 一岸趋近 $1$ 的边界极限，并不是在被排除的割线上定义函数。',
  'reason': 'Fix the branch in the composed q plane and interpret sqrt(1) as a boundary limit.'},
 {'id': 'SUPPORT-ZHUKOVSKY-EX8-BRANCH-BASIS4-V4',
  'finding': 'incremental-final-active-support',
  'unit_id': 'CH08-S04-SS03',
  'group': 'examples',
  'owner_id': 'EX-ZHUKOVSKY-8',
  'json_pointer': '/examples/0/steps/3/basis',
  'anchor': 'reader-9829042aa45203a81879',
  'before': '平方根函数的主支把割去正实轴的平面单叶保形地映为上半平面；条件 $\\sqrt{1}=1$ 固定了所需分支。',
  'after': '在 $w_3=\\rho e^{\\mathrm i\\theta}$、$0<\\theta<2\\pi$ 的连续角支上，平方根的像角为 '
           '$\\theta/2\\in(0,\\pi)$，所以像是上半平面。这是指定角区间的分支，不应无说明地称为通常沿负实轴割开的主平方根。',
  'reason': 'Name the exact branch rather than using an ambiguous main-branch label.'},
 {'id': 'SUPPORT-ZHUKOVSKY-EX8-BRANCH-MERGED-STEP-V4',
  'finding': 'incremental-final-active-support',
  'unit_id': 'CH08-S04-SS03',
  'group': 'examples',
  'owner_id': 'EX-ZHUKOVSKY-8',
  'json_pointer': '/examples/0/steps/4/markdown',
  'anchor': 'reader-9829042aa45203a81879',
  'before': '合并以上各步。由 $w_1=\\frac{z^2+1}{2z}$，得 $w_1^2=\\frac{(z^2+1)^2}{4z^2}$，于是\n'
            '$$w_3 = 1 + \\frac{9}{16w_1^2} = 1 + \\frac{9z^2}{4(z^2+1)^2} = '
            '\\frac{4z^4+17z^2+4}{4(z^2+1)^2}.$$\n'
            '在所选分支下开方，得\n'
            '$$w = \\frac{\\sqrt{4z^4+17z^2+4}}{2(z^2+1)}.$$',
  'after': '写 $q(z)=1+9z^2/[4(z^2+1)^2]$。先在 $q$ 平面取 $q=\\rho e^{\\mathrm i\\theta}$、$0<\\theta<2\\pi$，定义 '
           '$w(z)=\\sqrt\\rho\\,e^{\\mathrm i\\theta/2}$。合并式满足 $$q(z)=\\frac{4z^4+17z^2+4}{4(z^2+1)^2}.$$ '
           '若写作 $w=N(z)/[2(z^2+1)]$，必须定义 $N(z)=2(z^2+1)w(z)$，于是 '
           '$N(z)^2=4z^4+17z^2+4$。分子根号因此继承整条复合映照的解析支，不能另取一个独立标准平方根。',
  'reason': 'Splitting sqrt(P/[4(z²+1)²]) requires correlated branches.'},
 {'id': 'SUPPORT-ZHUKOVSKY-EX8-BRANCH-BASIS5-V4',
  'finding': 'incremental-final-active-support',
  'unit_id': 'CH08-S04-SS03',
  'group': 'examples',
  'owner_id': 'EX-ZHUKOVSKY-8',
  'json_pointer': '/examples/0/steps/4/basis',
  'anchor': 'reader-9829042aa45203a81879',
  'before': '代入并化简；开方时取正实轴为支割线且 $\\sqrt{1}=1$ 的一支，故分母取 $2(z^2+1)$。',
  'after': '合并分式是代数恒等式，独立拆平方根却不是无条件恒等式。先由 $0<\\arg q<2\\pi$ 定义 $w=\\sqrt q$，再令 $N=2(z^2+1)w$，才可把分子写成 '
           '$\\sqrt{4z^4+17z^2+4}$ 的相容解析支。分母 $2(z^2+1)$ 来自这一关联定义，不能仅凭“标准根支”推出。',
  'reason': 'Reject independent numerator root branch; retain correlated numerator definition.'},
 {'id': 'SUPPORT-ZHUKOVSKY-EX8-BRANCH-CONCLUSION-V4',
  'finding': 'incremental-final-active-support',
  'unit_id': 'CH08-S04-SS03',
  'group': 'examples',
  'owner_id': 'EX-ZHUKOVSKY-8',
  'json_pointer': '/examples/0/conclusion',
  'anchor': 'reader-9829042aa45203a81879',
  'before': '所求保形变换为 $w = \\frac{\\sqrt{4z^4+17z^2+4}}{2(z^2+1)}$，其中根式取正实轴为支割线且 $\\sqrt{1}=1$ 的一支。',
  'after': '写 $q(z)=1+9z^2/[4(z^2+1)^2]$。先在 $q$ 平面取 $q=\\rho e^{\\mathrm i\\theta}$、$0<\\theta<2\\pi$，定义 '
           '$w(z)=\\sqrt\\rho\\,e^{\\mathrm i\\theta/2}$。这给出原域到上半平面的保形变换。等价地可写 '
           '$$w(z)=\\frac{N(z)}{2(z^2+1)},\\qquad N(z)^2=4z^4+17z^2+4,$$ 但 $N=2(z^2+1)\\sqrt{q(z)}$ '
           '必须继承刚才的复合支，而不是给分子根号独立指定标准支。',
  'reason': 'Final formula retains the same composed branch as the construction.'},
 {'id': 'SUPPORT-ZHUKOVSKY-EX8-BRANCH-KEYPOINT-V4',
  'finding': 'incremental-final-active-support',
  'unit_id': 'CH08-S04-SS03',
  'group': 'examples',
  'owner_id': 'EX-ZHUKOVSKY-8',
  'json_pointer': '/examples/0/key_points/2',
  'anchor': 'reader-9829042aa45203a81879',
  'before': '分支条件（如 $\\sqrt{1}=1$）决定最终区域是上半平面还是下半平面，必须与支割线一起写明。',
  'after': '分支必须在最后一个复合变量 $q$ 上说明：$0<\\arg q<2\\pi$ 给出 $0<\\arg\\sqrt '
           'q<\\pi$。合并为分子、分母的根式时，两者的支要保持关联；不能分别采用两个看似相同的标准根支。',
  'reason': 'Keep the same branch warning in the example review copy.'}])

# Independently verified exact CH03/slit additions delegated by the root task.
EXACT_BLOCK_CORRECTIONS.extend([{'id': 'SUPPORT-CH03-PARAMETER-INTEGRAL-OUTER-I-V1',
  'finding': 'ch01-03/L05',
  'unit_id': 'CH03-S01',
  'group': 'annotations',
  'owner_id': 'EX2-PITFALL',
  'json_pointer': '/annotations/2/markdown',
  'anchor': 'reader-8a5eed04f579b7cabd2c',
  'before': '在例2中，$\\int_0^{2\\pi} e^{\\mathrm{i}(1-n)\\theta}\\,\\mathrm{d}\\theta$ 当 $1-n$ 是非零整数时等于 '
            '$0$，因为原函数在一个完整周期后回到同一值。不要把它写成 $2\\pi\\mathrm{i}/(1-n)$；只有 $n=1$ 时指数为 $1$，积分才留下 '
            '$2\\pi\\mathrm{i}$。',
  'after': '须把实参数积分与原来的圆周积分分开。对整数 $n$，令\n'
           '$$J_n=\\int_0^{2\\pi}e^{i(1-n)\\theta}\\,d\\theta.$$\n'
           '当 $n\\ne1$ 时，$1-n$ 是非零整数，指数函数的原函数走完一周期回到同值，故 $J_n=0$；当 $n=1$ 时，被积函数恒为 $1$，所以 $J_1=2\\pi$，这里没有额外的 '
           '$i$。若 $C$ 是圆周 $z=a+Re^{i\\theta}$、$R>0$，按 $0\\le\\theta\\le2\\pi$ 逆时针走，则原积分为\n'
           '$$I_n=\\oint_C\\frac{dz}{(z-a)^n}=iR^{1-n}J_n.$$\n'
           '因此 $I_1=2\\pi i$，$n\\ne1$ 时 $I_n=0$。外面的 $i$ 来自微分 $dz=iRe^{i\\theta}d\\theta$，不能提前塞进 '
           '$J_n$；也不能把非零频率的一圈积分写成 $2\\pi i/(1-n)$。',
  'reason': '原块明确把所谈对象写为实参数积分Jn，却在n=1时给出原围道积分的2πi，漏分外层微分因子；n=1时“指数为1”也应改为“被积函数恒为1”（指数本身为0）。',
  'source_conditions': {'n': 'n∈Z',
                        'circle': 'C:z=a+Re^{iθ}, R>0, θ从0增到2π',
                        'orientation': '逆时针',
                        'parameter_integral': 'Jn的积分变量是实数θ，外层i尚未相乘'},
  'verified_counterexample': 'n=1时Jn=∫₀^{2π}1 dθ=2π；原围道积分I1=iJn=2πi，两数不同。',
  'source_evidence': [{'source_path': 'data/parse/complex-analysis-v2/original_text/ch03_s01.md',
                       'lines': [33, 37],
                       'note': '教材例2先参数化，明确外层i/R^(n-1)与内层实参数积分。'}],
  'related_canonical_ids': [],
  'exact_match_verification': {'fbook_raw_text_sha256': 'sha256:0c1d2ab871ae0237431f51061a6cef0034f64b096d5b12975c364d7af383a870',
                               'source_raw_text_equal': True,
                               'source_block_id': 'sha256:ce27560c6cfbbc0ee31ba1b4ae49c95a7c36d7b960de38890f48875aaa2480e3',
                               'fbook_block_id': 'sha256:ce27560c6cfbbc0ee31ba1b4ae49c95a7c36d7b960de38890f48875aaa2480e3',
                               'anchor_equal': True}},
 {'id': 'SUPPORT-CH01-SLIT-SIMPLE-CONNECTIVITY-V1',
  'finding': 'ch01-03/L06',
  'unit_id': 'CH01-S02-SS02',
  'group': 'annotations',
  'owner_id': 'MULTIPLY-CONNECTED-EXAMPLES',
  'json_pointer': '/annotations/5/markdown',
  'anchor': 'reader-92e4341fccc151bd2338',
  'before': '圆环、去掉线段的带域、去心邻域都是多连通区域。一般地，把一个单连通区域挖去若干个点或去掉一条割痕，连通性可能仍保持，但单连通性被破坏。多连通区域的边界通常由若干条闭曲线以及割痕或点组成。',
  'after': '圆环、去心邻域，以及从带域内部挖去一条不接边界的闭线段，都是多连通区域：剩余区域中可以画出闭曲线，围住已经删去的点或线段。但“去掉割痕”不一定破坏单连通。比如沿负实轴割开的平面 '
           '$\\mathbb C\\setminus(-\\infty,0]$ 仍是单连通域；单位圆盘沿半径割开所得的 $\\{z:|z|<1\\}\\setminus[0,1)$ '
           '也仍单连通。前一个域可用连续辐角 $-\\pi<\\theta<\\pi$，后一个域可用 '
           '$0<\\theta<2\\pi$；对数分别把它们一一连续地映成凸的水平带或半带，说明这种接通边界的切口与内部空洞不同。判断时先确认剩余点集仍是区域，再检查其中每条简单闭曲线的内部是否全部留在区域内，不能只凭“割痕”这个名称下结论。',
  'reason': '“挖去孤立内点”与“沿连接边界的支割线割开”拓扑作用不同。原教材列举的是带域内不接边界的有限线段，旧注释却推广成去掉任何割痕都破坏单连通，与后续割平面选根/对数支相矛盾。',
  'source_conditions': {'ambient_space': '本项均指有限复平面',
                        'internal_slit_example': '带域内部的一条闭线段，端点不接区域外边界',
                        'radial_slit_example': '单位圆盘去掉[0,1)，包括原点，射线在圆盘边界止步',
                        'criterion': '所得集合先须保持非空、开、连通；然后按其中闭曲线的内部判断单连通'},
  'verified_counterexample': 'C\\(-∞,0]在主对数Log下与R×(-π,π)同胚，后者凸，故仍单连通。开圆盘去掉[0,1)在0<Arg<2π的Log支下与(-∞,0)×(0,2π)同胚，也单连通。',
  'source_evidence': [{'source_path': 'data/parse/complex-analysis-v2/original_text/ch01_s02_ss02.md',
                       'lines': [12, 15],
                       'note': '教材的有限内部线段与去心邻域具体例子，不含任何割痕必坏的普遍命题。'},
                      {'source_path': 'data/parse/complex-analysis-v2/original_text/ch02_s05_ss02.md',
                       'lines': [31, 39],
                       'note': '教材从0到∞取射线作为支割线，构造连续根分支，正是接通边界的割开。'}],
  'related_canonical_ids': ['C-0778', 'C-0087', 'C-0126'],
  'exact_match_verification': {'fbook_raw_text_sha256': 'sha256:7e27993d484e210e484420b0cb4c30e4d6cee3efb2270343544ead5427be6870',
                               'source_raw_text_equal': True,
                               'source_block_id': 'sha256:544f526351e1e641ebfd0d38b557284d3b241075adbb32c28bfc682241278766',
                               'fbook_block_id': 'sha256:544f526351e1e641ebfd0d38b557284d3b241075adbb32c28bfc682241278766',
                               'anchor_equal': True}},
 {'id': 'SUPPORT-CH03-NL-PRIMITIVE-CONDITION-V1',
  'finding': 'ch01-03/L07',
  'unit_id': 'CH03-S03',
  'group': 'annotations',
  'owner_id': 'PITFALL-NL-CONDITION',
  'json_pointer': '/annotations/1/markdown',
  'anchor': 'reader-3422a132406d41eab25e',
  'before': '牛顿－莱布尼兹公式要求 $f$ 在单连通区域 $D$ 内解析，并且积分路径全在 $D$ 内。以 $f(z)=1/z$ 为例：它在 $\\mathbb{C}\\setminus\\{0\\}$ '
            '内解析，但这个区域不是单连通的（$0$ 被挖去），并且 $1/z$ 在这个区域上没有单值原函数。若按公式取 $\\ln z$，沿单位圆的积分却是 '
            '$$\\oint_{|z|=1}\\frac{\\mathrm{d}z}{z}=\\int_0^{2\\pi}\\frac{\\mathrm{i}\\mathrm{e}^{\\mathrm{i}\\theta}}{\\mathrm{e}^{\\mathrm{i}\\theta}}\\mathrm{d}\\theta=2\\pi\\mathrm{i}\\neq0,$$ '
            '说明“用原函数作差”必然失效。错误不在公式本身，而在于忽略了“单连通”以及“路径不得绕洞”这两个条件。',
  'after': "使用牛顿—莱布尼兹公式，关键是在包含整条路径 $C$ 的开区域内，存在同一个单值原函数 $H$，且 $H'=f$。若路径从 $a$ 到 $b$，便有\n"
           '$$\\int_Cf(z)\\,dz=H(b)-H(a).$$\n'
           '$f$ 在单连通域内解析，是保证这种原函数存在的一组充分条件；单连通本身不是对每个具体函数都必要，路径也不因绕洞就自动失去公式。例如圆环 $1<|z|<2$ 虽非单连通，$f(z)=2z$ '
           '仍有单值原函数 $H(z)=z^2$，沿圆环内任何路径都可作端点差。反过来，$1/z$ 在整个穿孔平面 $\\mathbb C\\setminus\\{0\\}$ 没有全局单值原函数，因为\n'
           '$$\\oint_{|z|=1}\\frac{dz}{z}=2\\pi i\\ne0.$$\n'
           '若在允许取一支对数的割平面上计算，须使用那一支作端点差；不能将主值 $\\ln z$ 跨过其切口、沿所有绕零路径统一使用。错误在于没有同一个单值原函数覆盖全路，而非“绕洞”这件事本身。',
  'reason': '单连通解析是一组充分保证，不是牛顿—莱布尼兹针对某个给定f的必要域条件；1/z的反例证明缺少全局原函数会失败，不能证明所有绕洞路径对所有函数都不可用。',
  'source_conditions': {'path': '有向逐段光滑路径C，整体包含在某开区域中',
                        'actual_requirement': '存在同一个单值H，且在覆盖全路的开区域内H′=f',
                        'sufficient_package': 'f在单连通区域解析可保证H存在',
                        'logarithm_caution': '对数原函数必须固定一支且路径不跨该支切口'},
  'verified_counterexample': '在D={1<|z|<2}上取f=2z、H=z²。路径z=(3/2)e^{it},0≤t≤5π/2，在圆环中多绕一周后由3/2走到3i/2，积分仍为H(3i/2)-H(3/2)=-9/2。',
  'source_evidence': [{'source_path': 'data/parse/complex-analysis-v2/original_text/ch03_s03.md',
                       'lines': [1, 3],
                       'note': '原函数按区域内H′=f定义。'},
                      {'source_path': 'data/parse/complex-analysis-v2/original_text/ch03_s03.md',
                       'lines': [21, 30],
                       'note': '教材指出变上限求导实质只用路径无关，并把牛顿—莱布尼兹写为任一原函数的端点差。'},
                      {'source_path': 'data/parse/complex-analysis-v2/original_text/ch03_s03.md',
                       'lines': [33, 43],
                       'note': '多连通区域有些路径仍可比较；对1/z再取割平面上的对数支。'}],
  'related_canonical_ids': ['C-0212', 'C-0213', 'C-0536', 'C-0537', 'C-0251'],
  'exact_match_verification': {'fbook_raw_text_sha256': 'sha256:eb82d20adb4ed679fc6bb8b8bada51c76af8e6329952f756f9be4fd596c7e992',
                               'source_raw_text_equal': True,
                               'source_block_id': 'sha256:56661ac3d98a8980242d7f297a84df7f6f6887c25633c3b2ec230d1c14968170',
                               'fbook_block_id': 'sha256:56661ac3d98a8980242d7f297a84df7f6f6887c25633c3b2ec230d1c14968170',
                               'anchor_equal': True}},
 {'id': 'SUPPORT-CH03-NL-EXAMPLE-KEYPOINT-V1',
  'finding': 'ch01-03/L07',
  'unit_id': 'CH03-S03',
  'group': 'examples',
  'owner_id': 'EX-CALC-INTEGRALS',
  'json_pointer': '/examples/0/key_points/0',
  'anchor': 'reader-5eccf0b02e531c0cd2a5',
  'before': '使用公式前先确认两件事：区域单连通、被积函数在该区域内解析。',
  'after': "用端点差计算前，先确认整条路径都在同一个单值原函数 $H$ 的定义域内，且 $H'=f$。若尚未构造原函数，$f$ "
           '在单连通域内解析是一组方便的存在性充分条件，但不是每个具体被积函数的必要条件。',
  'reason': '这条可独立打开的解后要点把充分条件写成唯一检查清单，需与同节的原函数条件一致。',
  'source_conditions': {'formula': '积分等于同一个H在终点与起点的差', 'scope': '必须覆盖整条路径，不只在端点能写H'},
  'verified_counterexample': '圆环内的2z有全局单值原函数z²，故不满足“区域单连通”仍可使用公式。',
  'source_evidence': [{'source_path': 'data/parse/complex-analysis-v2/original_text/ch03_s03.md',
                       'lines': [21, 30],
                       'note': '任一原函数的端点差，不须把单连通写成必要条件。'}],
  'related_canonical_ids': ['C-0212', 'C-0213', 'C-0537'],
  'exact_match_verification': {'fbook_raw_text_sha256': 'sha256:fdcccf06da374eba4a00092aa1add7675b5369c31668d3d91d4dc15558a715b1',
                               'source_raw_text_equal': True,
                               'source_block_id': 'sha256:dd832d081a2a8caef5ac1f30adf22cc4f73f72ca15fc8bab3a346e7488674dbd',
                               'fbook_block_id': 'sha256:dd832d081a2a8caef5ac1f30adf22cc4f73f72ca15fc8bab3a346e7488674dbd',
                               'anchor_equal': True}},
 {'id': 'SUPPORT-CH03-MULTIVALUED-CONCLUSION-CONDITION-V1',
  'finding': 'ch01-03/L07',
  'unit_id': 'CH03-S03',
  'group': 'examples',
  'owner_id': 'EX-MULTIVALUED-INTEGRAL',
  'json_pointer': '/examples/1/conclusion',
  'anchor': 'reader-9f4e41e646713848d9a7',
  'before': '在非单连通区域 $\\mathbb{C}\\setminus\\{0\\}$ 上，$1/z$ 的变上限积分依赖于路径，等于对数 $\\mathrm{Ln}\\,z=\\ln '
            'z+2k\\pi\\mathrm{i}$（$k=0,\\pm1,\\pm2,\\dots$），其中 $k$ '
            '由路径绕原点的净圈数决定。这也说明“变上限积分是原函数”与牛顿－莱布尼兹公式中“单连通”这一条件不能去掉。',
  'after': '在穿孔平面 $\\mathbb C\\setminus\\{0\\}$，$1/z$ 的积分确实依赖路径。固定一条从 $1$ 到 $z\\ne0$ 的参考路径 $L_0$，另一条路径 $L$ 与 '
           '$L_0$ 的反向组成闭曲线；若它绕原点的净圈数为整数 $k$，则\n'
           '$$\\int_L\\frac{d\\zeta}{\\zeta}=\\int_{L_0}\\frac{d\\zeta}{\\zeta}+2k\\pi i.$$\n'
           '不限制路径类别时，全部可能值组成 '
           '$\\operatorname{Ln}z$；在固定割平面中，则可用同一对数支的端点差计算。这个例子说明：单有解析性，不能在任意区域保证变上限积分单值或保证全局原函数存在。单连通加解析给出一般的充分保证；若对某个具体函数已经存在单值原函数，即使所在区域多连通，牛顿—莱布尼兹公式仍成立。',
  'reason': '原例前半1/z的结论正确；末句若指“一概保证所有解析函数都有原函数”可以成立，但易被读成给定函数使用N-L公式的必要条件。修正精确说明反例的逻辑范围，而不否定单连通充分性定理。另把开放路径的净绕数明确为与固定参考路径组成闭曲线的绕数。',
  'source_conditions': {'reference_path': '先固定从1到同一终点z的参考路径L0，两条路径均避开0',
                        'winding_number': 'k为闭曲线L−L0的整数净绕数，不直接给开放路径赋无参考绕数',
                        'logical_scope': '1/z否定仅凭解析即可全域保证原函数的普遍断言；不推出每个具体函数都须单连通'},
  'verified_counterexample': '同域C\\{0}上，1/z没有全局单值原函数，但2z有原函数z²。因此不能把1/z的失败推广为所有解析函数在多连通区域都无法用端点差。',
  'source_evidence': [{'source_path': 'data/parse/complex-analysis-v2/original_text/ch03_s03.md',
                       'lines': [21, 30],
                       'note': '路径无关是构造所需条件；NL使用任一原函数。'},
                      {'source_path': 'data/parse/complex-analysis-v2/original_text/ch03_s03.md',
                       'lines': [40, 50],
                       'note': '固定对数分支与绕行增加2πi的具体讨论。'}],
  'related_canonical_ids': ['C-0212', 'C-0213', 'C-0537', 'C-0251'],
  'exact_match_verification': {'fbook_raw_text_sha256': 'sha256:b5795f00a9d2a8b8c822320ee8ffd2ed0249df5ee428d4921559fd31513f48f2',
                               'source_raw_text_equal': True,
                               'source_block_id': 'sha256:4ce52c921fb7755305069ae0553e1cef07095a003cc46ae54e795f6d41e0c530',
                               'fbook_block_id': 'sha256:4ce52c921fb7755305069ae0553e1cef07095a003cc46ae54e795f6d41e0c530',
                               'anchor_equal': True}}])

def _digest(value):
    return digest_of(value, check=False)


def _text_hash(text):
    return 'sha256:' + hashlib.sha256(text.encode()).hexdigest()


def _references(document):
    """Recover the existing reference labels/anchors, not a new anchor scheme."""
    refs = {}
    blocks = document.get('blocks', [])
    owner_anchors = {}
    for block in blocks:
        owner_anchors.setdefault((block.get('group'), block.get('owner_id')), block.get('anchor'))
    for entry in document.get('reference_catalog', {}).get('entries', []):
        if entry.get('kind') in ('CONCEPT', 'UNIT'):
            refs[(entry['kind'], entry['id'])] = {
                'label': entry.get('label', ''),
                'anchor': owner_anchors.get(('references', entry['id'])) or _anchor('references', entry['id']),
            }
    local_groups = {'NOTE': 'annotations', 'ANNOTATION': 'annotations',
                    'EXPLAIN': 'explainables', 'EXAMPLE': 'examples'}
    for block in blocks:
        raw = block.get('raw_text', '')
        visible = block.get('visible_text', '')
        spans = {(s.get('raw_start'), s.get('raw_end')): s for s in block.get('marker_spans', [])}
        for match in REFERENCE_PATTERN.finditer(raw):
            kind, ident = match[1], match[2].strip()
            if (kind, ident) in refs:
                continue
            anchor = owner_anchors.get((local_groups.get(kind), ident))
            if not anchor:
                continue
            span = spans.get(match.span())
            label = visible[span['visible_start']:span['visible_end']] if span else match[3]
            if label is None:
                # Exact identity lookup only, no label inference from prose.
                title = next((b for b in blocks if b.get('owner_id') == ident and
                              b.get('group') == local_groups.get(kind) and
                              re.search(r'/(title|name|label)$', b.get('json_pointer', ''))), None)
                label = (title or {}).get('raw_text', ident)
            refs[(kind, ident)] = {'label': label, 'anchor': anchor}
    return refs


def _reproject_block(block, raw, refs):
    """Reset all text-derived offsets while keeping logical IDs and anchors."""
    with review_mode('balanced'):
        visible, mapping, spans, markdown = _project_text(raw, refs)
    block.update(raw_text=raw, visible_text=visible, source_map=mapping,
                 marker_spans=spans, markdown=markdown)
    assert len(mapping) == len(visible)
    assert all(0 <= offset < len(raw) for offset in mapping)
    for span in spans:
        assert 0 <= span['raw_start'] <= span['raw_end'] <= len(raw)
        assert 0 <= span['visible_start'] <= span['visible_end'] <= len(visible)


def correct_support_document(document, unit_id):
    """Return (corrected deep copy, audit report), preserving the input exactly.

    A report with ``signature_mismatches`` requires author investigation: this
    function deliberately declines to guess at another spelling of the error.
    Block IDs are logical field identities in project_reader, so retain them.
    Content/catalog/version hashes change only when their display text changes.
    """
    original_digest = _digest(document)
    projected = deepcopy(document)
    report = {
        'schema_version': 'SupportDisplayCorrectionReportV1', 'unit_id': unit_id,
        'correction_policy': CORRECTION_POLICY, 'status': 'not_applicable',
        'input_document_sha256': original_digest,
        'source_content_hash': document.get('content_hash'),
        'source_catalog_hash': document.get('reference_catalog_hash'),
        'changes': [], 'signature_mismatches': [],
        'snapshot_policy': 'Caller retains the unmodified input in study_source_document; CLI saves snapshots separately.',
    }
    if document.get('unit_id') not in (None, unit_id):
        raise ValueError('Reader document unit identity mismatch: ' + unit_id)
    if not isinstance(projected.get('blocks'), list):
        raise ValueError('ReaderDocumentV1.blocks must be a list')
    entries = projected.get('reference_catalog', {}).get('entries', [])
    matching = [(i, entry) for i, entry in enumerate(entries) if
                entry.get('kind') == 'CONCEPT' and entry.get('id') == TARGET_ID and
                entry.get('unit_id') == TARGET_ORIGIN_UNIT and entry.get('label') == '达朗贝尔公式']
    if len(matching) > 1:
        raise ValueError('Ambiguous frozen C-0256 identity')
    already_corrected = False
    for index, entry in matching:
        before = entry.get('markdown', '')
        if before == CORRECT_STATEMENT:
            already_corrected = True
            continue
        if before != BAD_STATEMENT:
            report['signature_mismatches'].append({'pointer': f'/reference_catalog/entries/{index}/markdown',
                                                    'expected_sha256': _text_hash(BAD_STATEMENT),
                                                    'actual_sha256': _text_hash(before)})
            continue
        old_version = entry.get('version')
        entry['markdown'] = CORRECT_STATEMENT
        entry['version'] = _digest({'correction_id': CORRECTION_ID, 'source_version': old_version,
                                    'kind': 'CONCEPT', 'id': TARGET_ID, 'markdown': CORRECT_STATEMENT})
        report['changes'].append({'kind': 'catalog_entry', 'correction_id': CORRECTION_ID, 'pointer': f'/reference_catalog/entries/{index}/markdown',
                                  'owner_id': TARGET_ID, 'before': before, 'after': CORRECT_STATEMENT,
                                  'source_version': old_version, 'display_version': entry['version']})

    for index, entry in enumerate(entries):
        rule = next((rule for rule in CANONICAL_CONCEPT_CORRECTIONS if
                     entry.get('kind') == 'CONCEPT' and entry.get('id') == rule['concept_id'] and
                     entry.get('unit_id') == rule['origin_unit_id'] and entry.get('label') == rule['label']), None)
        if not rule:
            continue
        before = entry.get('markdown', '')
        if before == rule['after']:
            already_corrected = True
        elif before in rule['before_variants']:
            old_version = entry.get('version')
            entry['markdown'] = rule['after']
            entry['version'] = _digest({'correction_id': rule['id'], 'source_version': old_version,
                                        'id': rule['concept_id'], 'markdown': rule['after']})
            report['changes'].append({'kind': 'catalog_entry', 'correction_id': rule['id'],
                                      'pointer': f'/reference_catalog/entries/{index}/markdown',
                                      'owner_id': entry['id'], 'before': before, 'after': rule['after'],
                                      'source_version': old_version, 'display_version': entry['version']})
        else:
            report['signature_mismatches'].append({'pointer': f'/reference_catalog/entries/{index}/markdown',
                                                    'correction_id': rule['id'], 'owner_id': entry['id'],
                                                    'actual_sha256': _text_hash(before)})

    refs = _references(projected)
    for index, block in enumerate(projected['blocks']):
        pointer = block.get('json_pointer', '')
        raw = block.get('raw_text', block.get('markdown', ''))
        replacement = None
        rule_id = CORRECTION_ID
        exact_rule = next((rule for rule in EXACT_BLOCK_CORRECTIONS if
                           rule['unit_id'] == unit_id and rule['group'] == block.get('group') and
                           rule['owner_id'] == block.get('owner_id') and rule['json_pointer'] == pointer and
                           rule['anchor'] == block.get('anchor')), None)
        if exact_rule:
            rule_id = exact_rule['id']
            if raw == exact_rule['before']:
                replacement = exact_rule['after']
            elif raw == exact_rule['after']:
                already_corrected = True
            else:
                report['signature_mismatches'].append({'pointer': pointer, 'correction_id': rule_id,
                                                        'owner_id': block.get('owner_id'),
                                                        'actual_sha256': _text_hash(raw)})
        concept_rule = next((rule for rule in CANONICAL_CONCEPT_CORRECTIONS if
                             block.get('group') == 'references' and block.get('owner_id') == rule['concept_id'] and
                             re.fullmatch(r'/reference_catalog/entries/\d+/markdown', pointer)), None)
        if concept_rule:
            rule_id = concept_rule['id']
            if raw in concept_rule['before_variants']:
                replacement = concept_rule['after']
            elif raw == concept_rule['after']:
                already_corrected = True
            else:
                report['signature_mismatches'].append({'pointer': pointer, 'correction_id': rule_id,
                                                        'owner_id': block.get('owner_id'),
                                                        'actual_sha256': _text_hash(raw)})
        if (block.get('group') == 'references' and block.get('owner_id') == TARGET_ID and
                re.fullmatch(r'/reference_catalog/entries/\d+/markdown', pointer)):
            if raw == BAD_STATEMENT:
                replacement = CORRECT_STATEMENT
            elif raw == CORRECT_STATEMENT:
                already_corrected = True
            else:
                report['signature_mismatches'].append({'pointer': pointer, 'owner_id': TARGET_ID,
                                                        'actual_sha256': _text_hash(raw)})
        elif (unit_id == TARGET_ORIGIN_UNIT and block.get('group') == 'corrections' and
              block.get('owner_id') == 'CORR-C0256-DALEMBERT' and pointer == '/corrections/0/original'):
            if raw == BAD_PARAGRAPH:
                replacement = CORRECT_PARAGRAPH
            elif raw == CORRECT_PARAGRAPH:
                already_corrected = True
            else:
                report['signature_mismatches'].append({'pointer': pointer, 'owner_id': block.get('owner_id'),
                                                        'actual_sha256': _text_hash(raw)})
        elif (unit_id == TARGET_ORIGIN_UNIT and block.get('group') == 'body_markdown' and
              pointer == '/body_markdown' and BAD_PARAGRAPH in raw):
            if raw.count(BAD_PARAGRAPH) != 1:
                report['signature_mismatches'].append({'pointer': pointer, 'reason': 'ambiguous exact paragraph'})
            else:
                replacement = raw.replace(BAD_PARAGRAPH, CORRECT_PARAGRAPH, 1)
        if replacement is None:
            continue
        # A known raw signature must agree with its rendered field. A new,
        # independently edited markdown field is not overwritten silently.
        old_visible, old_mapping, old_spans, old_markdown = _project_text_frozen(raw, refs)
        if block.get('markdown', old_markdown) != old_markdown:
            report['signature_mismatches'].append({'pointer': pointer, 'reason': 'raw/markdown projection differs'})
            continue
        _reproject_block(block, replacement, refs)
        if pointer == '/corrections/0/original':
            block['field_label'] = '显示订正（原文快照另存）'
        report['changes'].append({'kind': 'reader_block', 'correction_id': rule_id, 'pointer': f'/blocks/{index}',
                                  'json_pointer': pointer, 'owner_id': block.get('owner_id'),
                                  'anchor': block.get('anchor'), 'block_id': block.get('block_id'),
                                  'before': raw, 'after': replacement,
                                  'derived_fields_rebuilt': ['raw_text', 'visible_text', 'source_map', 'marker_spans', 'markdown']})

    if report['changes']:
        if any(change['kind'] == 'catalog_entry' for change in report['changes']):
            projected['reference_catalog_hash'] = _digest(projected['reference_catalog'])
        # Bind the real displayed blocks and catalog, rather than merely naming
        # the old content revision; do not alter any historical source hash.
        projected['content_hash'] = _digest({'source_content_hash': document.get('content_hash'),
                                             'correction_policy': CORRECTION_POLICY,
                                             'blocks': projected['blocks'],
                                             'reference_catalog': projected.get('reference_catalog')})
        if 'required_block_ids' in projected:
            projected['required_block_ids'] = [block['block_id'] for block in projected['blocks']]
        report['status'] = 'corrected' if not report['signature_mismatches'] else 'partially_corrected'
    elif already_corrected:
        report['status'] = 'already_corrected'
    elif report['signature_mismatches']:
        report['status'] = 'signature_mismatch'
    report.update(display_content_hash=projected.get('content_hash'),
                  correction_ids=sorted({c['correction_id'] for c in report['changes']}),
                  display_catalog_hash=projected.get('reference_catalog_hash'),
                  output_document_sha256=_digest(projected),
                  anchors_preserved=[b.get('anchor') for b in projected['blocks']] == [b.get('anchor') for b in document['blocks']],
                  block_ids_preserved=[b.get('block_id') for b in projected['blocks']] == [b.get('block_id') for b in document['blocks']],
                  owner_ids_preserved=[b.get('owner_id') for b in projected['blocks']] == [b.get('owner_id') for b in document['blocks']],
                  input_unchanged=_digest(document) == original_digest)
    assert report['anchors_preserved'] and report['block_ids_preserved'] and report['owner_ids_preserved'] and report['input_unchanged']
    return projected, report


def _project_text_frozen(raw, refs):
    with review_mode('balanced'):
        return _project_text(raw, refs)


def correction_map():
    return {'schema_version': 'SupportDisplayCorrectionMapV1', 'policy': CORRECTION_POLICY, 'corrections': [{
        'id': CORRECTION_ID, 'origin_unit_id': TARGET_ORIGIN_UNIT,
        'concept_id': TARGET_ID, 'anchor': TARGET_ANCHOR,
        'source': 'data/parse/complex-analysis-v2/original_text/ch05_s02.md',
        'exact_original_markdown': BAD_STATEMENT,
        'corrected_markdown': CORRECT_STATEMENT,
        'reason': 'Inverse adjacent coefficient ratio gives R=r, while the root criterion gives R=1/L; these cannot share one conclusion R=1/r.',
        'verified_counterexample': 'a_n=1/n!: |a_(n-1)/a_n|=n→∞, so R=∞; old reciprocal would give R=0.',
        'selector': 'CONCEPT/C-0256 with origin CH05-S02 and exact text signature; related projected blocks by logical owner and pointer.',
    }] + deepcopy(CANONICAL_CONCEPT_CORRECTIONS) + deepcopy(EXACT_BLOCK_CORRECTIONS)}


def correct_global_concepts(concepts):
    """Correct the exact same canonical statements in data.concepts mirrors.

    The renderer's global table is a dictionary keyed by concept ID, with
    id/name/type/brief/explanation/formal/occurrences fields. Current delivered
    readers have an empty global table, but parse/concepts.json supplies the
    real formal/occurrences shape. Match identity + name + exact display text;
    keep occurrences, source_quotes and all other provenance fields unchanged.
    Never interpret or rewrite arbitrary strings outside the registered fields.
    """
    if not isinstance(concepts, dict):
        raise TypeError('data.concepts must be a dictionary keyed by concept ID')
    result = deepcopy(concepts)
    before_hash = _digest(concepts)
    report = {'schema_version': 'GlobalConceptDisplayCorrectionReportV1',
              'correction_policy': CORRECTION_POLICY, 'source_sha256': before_hash,
              'changes': [], 'signature_mismatches': [],
              'source_fields_preserved': ['id', 'name', 'type', 'occurrences', 'source_quotes']}
    ratio_formal = (r'如果 $\lim _ {n \to + \infty} \left| \frac {a _ {n - 1}}{a _ {n}} \right| = r$，'
                    '则复幂级数(5.5)的收敛半径 $R = 1/r$.')
    rules = [{'id': CORRECTION_ID, 'concept_id': TARGET_ID, 'label': '达朗贝尔公式',
              'before_variants': [BAD_STATEMENT, ratio_formal], 'after': CORRECT_STATEMENT}] + CANONICAL_CONCEPT_CORRECTIONS
    for rule in rules:
        ident = rule['concept_id']
        entry = result.get(ident)
        if not isinstance(entry, dict) or entry.get('id', ident) != ident or entry.get('name') != rule['label']:
            continue
        changed = False
        for field in ('formal', 'markdown', 'brief', 'explanation'):
            value = entry.get(field)
            if not isinstance(value, str) or not value or value == rule['after']:
                continue
            if value in rule['before_variants']:
                entry[field] = rule['after']
                changed = True
                report['changes'].append({'correction_id': rule['id'], 'concept_id': ident,
                                          'pointer': '/' + ident + '/' + field,
                                          'before': value, 'after': rule['after']})
            elif any(bad in value for bad in rule['before_variants']):
                # A longer authored explanation may be quoting an error on
                # purpose. Flag it for review instead of rewriting context.
                report['signature_mismatches'].append({'concept_id': ident, 'field': field,
                                                        'reason': 'known bad statement embedded in unverified context'})
        if changed and 'version' in entry:
            entry['version'] = _digest({'source_version': concepts[ident]['version'],
                                        'correction_id': rule['id'], 'entry': entry})
    report.update(display_sha256=_digest(result), input_unchanged=_digest(concepts) == before_hash,
                  status='not_applicable_empty_table' if not concepts else ('corrected' if report['changes'] else 'unchanged'),
                  actual_scope_status='N/A: global table is empty' if not concepts else 'evaluated exact registered identities')
    assert report['input_unchanged']
    return result, report


def prepare(source, output, units=None):
    from tools.upgrade_study_reader import load_reader_data
    source, output = Path(source).resolve(), Path(output).resolve()
    input_file = source / 'data/data.js' if source.is_dir() else source
    if (output == source or output == input_file or output in input_file.parents or
            (source.is_dir() and output.is_relative_to(source))):
        raise ValueError('Output must be an independent directory, not an original reader location')
    data = load_reader_data(source)
    selected = units if units is not None else list(data['units'])
    output.mkdir(parents=True, exist_ok=True)
    reports = []; snapshots = []; affected = []
    for uid in selected:
        unit = data['units'][uid]
        # Inspect the actual displayed document; retain it byte-for-byte as a
        # logical JSON snapshot. The original source snapshot remains untouched.
        original = unit['reader_document']
        projected, report = correct_support_document(original, uid)
        reports.append(report)
        if not report['changes']:
            continue
        affected.append(uid)
        for directory, value in [('snapshots', original), ('projections', projected)]:
            destination = output / directory / (uid + '.json')
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        snapshots.append({'unit_id': uid, 'snapshot': f'snapshots/{uid}.json',
                          'logical_sha256': _digest(original), 'projection': f'projections/{uid}.json'})
    global_source = data.get('concepts', {})
    global_projected, global_report = correct_global_concepts(global_source)
    (output / 'global-concepts.original.json').write_text(json.dumps(global_source, ensure_ascii=False, indent=2) + '\n')
    (output / 'global-concepts.corrected.json').write_text(json.dumps(global_projected, ensure_ascii=False, indent=2) + '\n')
    result = {'schema_version': 'PreparedSupportCorrectionsV1', 'source': str(input_file),
              'source_file_sha256': 'sha256:' + hashlib.sha256(input_file.read_bytes()).hexdigest(),
              'affected_units': affected, 'snapshots': snapshots, 'unit_reports': reports,
              'global_concepts_report': global_report,
              'global_concepts_integration': 'data["concepts"], report = correct_global_concepts(data.get("concepts", {})); retain the original table separately.',
              'integration': 'Run after authored body replacement and before assigning reader_document / layout_from_reader_media. Preserve study_source_document and study_textbook_original verbatim.',
              'popup_path': 'render-core.readerDocumentHtml renders corrected references blocks; app.copyReaderContent clones that support DOM including corrected math. No JS hook required.'}
    (output / 'support-corrections-map.json').write_text(json.dumps(correction_map(), ensure_ascii=False, indent=2) + '\n')
    (output / 'support-corrections-report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--units', nargs='+')
    args = parser.parse_args()
    result = prepare(args.source, args.output, args.units)
    print(json.dumps({'affected_units': result['affected_units'],
                      'signature_mismatches': sum(len(x['signature_mismatches']) for x in result['unit_reports'])}, ensure_ascii=False))
