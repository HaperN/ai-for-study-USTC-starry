"""扫描小工具：**把"注释/文档字符串里提到"和"代码里真的用了"分开**。

这是本项目里反复出现的一类假阳性，值得单独一个模块：

    "生产代码里还有旧入口引用吗？" —— 如果连文档字符串里的一句
    "请用 tools/run_book.py 起跑"都算引用，那 M9 的"活动引用为 0"永远达不到，
    或者逼着人删掉有价值的注释；反过来，如果真调用被当成注释忽略，门就失效了。

第一次做 `retire_status.py` 时得到 115 处"生产引用"，逐行看回去发现绝大多数是注释；
把口径改成只认代码之后降到 37 处 —— 而且那 37 处确实是真调用。
M1 的出口检查又踩了同一个坑（`sys.path.insert` 出现在模块**文档字符串**里被算成使用），
所以口径上收到这里，只有这一份。

判定方式：三引号奇偶性近似判断文档字符串，`#` 开头判注释。近似带来的偏差是**保守**
的（可能把代码判成文档，不会把文档判成代码），因为"漏报一句注释"远好过"误报一处依赖"。
"""
from __future__ import annotations

import re
from pathlib import Path

TRIPLE_QUOTES = ('"""', "'''")

CODE = "code"
COMMENT = "comment"
DOC = "doc"


def line_kinds(text: str) -> list[str]:
    """给每一行判 `code` / `comment` / `doc`。"""
    kinds: list[str] = []
    in_doc = False
    for line in text.splitlines():
        stripped = line.strip()
        starts_in_doc = in_doc
        for quote in TRIPLE_QUOTES:
            count = line.count(quote)
            if count and count % 2 == 1:
                in_doc = not in_doc
        if stripped.startswith("#"):
            kinds.append(COMMENT)
        elif starts_in_doc:
            kinds.append(DOC)
        elif stripped.startswith(TRIPLE_QUOTES):
            # ⚠️ 文档字符串的**起始行**原先被当成代码 —— 实测抓到一例：
            #    `src/aiforstudy/blocks/interaction/__init__.py` 的模块文档第一行
            #    提到 `blocks/model`（那是在解释改名），结果被算成"生产代码引用旧入口"。
            #    以三引号开头就是文档，不是代码。
            kinds.append(DOC)
        else:
            kinds.append(CODE)
    return kinds


def code_lines(path: Path) -> list[tuple[int, str]]:
    """只返回**代码行**（行号从 1 起）。读不了就返回空。"""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    kinds = line_kinds(text)
    return [(i, line) for i, line in enumerate(text.splitlines(), 1)
            if i - 1 < len(kinds) and kinds[i - 1] == CODE]


def code_hits(path: Path, patterns: list[re.Pattern] | list[str]) -> list[tuple[int, str]]:
    """在**代码行**里找命中。`patterns` 可以是编译好的正则，也可以是普通子串。"""
    compiled = [p if isinstance(p, re.Pattern) else re.compile(re.escape(p))
                for p in patterns]
    out: list[tuple[int, str]] = []
    for lineno, line in code_lines(path):
        if any(p.search(line) for p in compiled):
            out.append((lineno, line.strip()[:200]))
    return out


def iter_sources(root: Path, suffixes: tuple[str, ...] = (".py", ".js", ".mjs")):
    """遍历源文件，跳过缓存与第三方目录。"""
    skip = {"__pycache__", "node_modules", "vendor", ".cache", ".git"}
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if any(part in skip for part in path.parts):
            continue
        yield path


__all__ = ["CODE", "COMMENT", "DOC", "line_kinds", "code_lines", "code_hits", "iter_sources"]
