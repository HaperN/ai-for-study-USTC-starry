"""测试套件自身的卫生判据（不测产品，测"这套测试还能不能可靠地跑"）。

## 为什么需要这个文件

2026-10-07 实测踩到一次**只在跑全套时才出现**的故障：

* T06 有 `tests/fixtures/visual/builders.py`；
* T08 新增了 `tests/fixtures/frame_review/builders.py`；
* 两个模块**同名**（都叫 `builders`）。单独跑文件时各自正常，
  但 `pytest tests/` 时 `sys.modules["builders"]` 先到先得 ——
  后导入的那个拿到的是**另一份**，于是 `AttributeError: module 'builders' has no attribute 'build'`，
  **一次性红了 20 条**。

这类故障最坏的地方不是它难修（改个名就好），而是它**只在整套运行时出现**：
单跑文件全绿，很容易被当成"偶发"或"环境问题"，从而被忽略或误归因。

所以这里把它变成一条**结构性判据**：凡是"同名且被 import"的测试辅助模块，直接判红，
并告诉作者改成唯一名字（就像当时那样）。判据本身不需要跑整套，秒级完成。
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent

#: 这些名字是 pytest 约定/标准库式入口，天然允许重名（不会被 `import <name>` 直接引用）
ALLOWED_DUPLICATES = {"__init__.py", "conftest.py"}

IMPORT_LINE = re.compile(r"^\s*(?:from|import)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)", re.M)


def _python_files() -> list[Path]:
    return [p for p in sorted(TESTS.rglob("*.py")) if "__pycache__" not in p.parts]


def _imported_basenames() -> set[str]:
    """测试代码里**按顶层名字 import 过**的模块名。

    只看 `import x` / `from x import ...` 这种顶层形式 —— 那才是会走
    `sys.modules[x]` 的写法。`from . import x` 与包内相对导入不受影响。
    """
    names: set[str] = set()
    for path in _python_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in IMPORT_LINE.finditer(text):
            name = match.group("name")
            if name not in {"__future__"}:
                names.add(name)
    return names


def test_no_duplicate_imported_module_basenames_under_tests():
    """同名且被 import 的测试辅助模块会**只在整套运行时**撞 `sys.modules`。

    这条判据的存在理由见模块 docstring：它抓到过一次 20 条同因红的真实故障。
    """
    by_name: dict[str, list[Path]] = defaultdict(list)
    for path in _python_files():
        if path.name in ALLOWED_DUPLICATES:
            continue
        by_name[path.stem].append(path)

    imported = _imported_basenames()
    offenders = {name: paths for name, paths in by_name.items()
                 if len(paths) > 1 and name in imported}

    assert not offenders, (
        "这些模块**重名且被按名字 import** —— 整套运行时 `sys.modules` 会先到先得，"
        "后导入的那个会拿到另一份，症状是**只有跑全套才红**：\n"
        + "\n".join(f"  {name}: " + ", ".join(str(p.relative_to(TESTS)) for p in paths)
                    for name, paths in sorted(offenders.items()))
        + "\n请给其中一个改成唯一名字（例如 `frame_review_builders.py`）。")


def test_duplicate_but_unimported_helper_scripts_are_still_reported():
    """重名但**没被 import** 的脚本目前是安全的（多为手工生成夹具的入口）。

    这条不断言"必须唯一"，只把现状**显式记录**出来：如果哪天有人开始 import 它们，
    上一条测试会立刻变红。这样"现在安全"与"以后安全"是两件说得清的事。
    """
    by_name: dict[str, list[Path]] = defaultdict(list)
    for path in _python_files():
        if path.name in ALLOWED_DUPLICATES:
            continue
        by_name[path.stem].append(path)

    imported = _imported_basenames()
    unimported_duplicates = {name: paths for name, paths in by_name.items()
                             if len(paths) > 1 and name not in imported}
    for name, paths in unimported_duplicates.items():
        assert len(paths) >= 2
        # 至少确认它们真的是**独立脚本**（不是包的一部分）
        for path in paths:
            assert not (path.parent / "__init__.py").is_file(), (
                f"{path} 在包里还重名 —— 那条路和顶层 import 不一样，需要单独看")

# 说明：这里本来还有第三条"测试文件名不要与已加载的包同名"。
# **它被删掉了**，因为它是个**假阳性生成器**：`conftest` 是 pytest 自己的约定，
# 而 `tests/support/offline_guard.py` 本来就是项目自己的支撑模块（它在"与自己同名"）。
# 一条会误报的守卫最终只会被人以糟糕的方式"修好"（放宽或加白名单），
# 不如只留下两条判据明确的。**删掉它本身也是记录**：这条规则不成立。


# ────────────────────────────────────────── 同名重复定义（后一个静默胜出）

def _top_level_definitions(path: Path) -> dict[str, int]:
    """模块内**顶层** `def`／`class` 的名字 → 行号（同名的取最后一次出现）。"""
    import ast as _ast

    found: dict[str, int] = {}
    try:
        tree = _ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return found
    for node in tree.body:
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
            found[node.name] = node.lineno
    return found


def _duplicate_top_level_definitions(path: Path) -> dict[str, list[int]]:
    import ast as _ast

    seen: dict[str, list[int]] = defaultdict(list)
    try:
        tree = _ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return {}
    for node in tree.body:
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
            seen[node.name].append(node.lineno)
    return {name: lines for name, lines in seen.items() if len(lines) > 1}


def test_no_module_defines_the_same_top_level_name_twice():
    """同一个模块里**顶层同名定义出现两次**时，Python 让后一个静默胜出 —— 前一个是**死代码**。

    这条判据来自一次真实踩坑（2026-10-07，台账 §9.45）：
    `tests/integration/test_m10_a01_fresh_env.py` 里 `_provision` **被定义了两次**
    （后面那个胜出），我第一版改动正好落在**死代码**里 —— 改完毫无效果，
    还会让人以为"改了却没生效"是环境问题。**静默胜出比报错难查得多。**

    为什么算"卫生"而不是"洁癖"：读到第一个定义的人（包括当时的我）
    会以为那就是真实行为，于是**对错的那一份做修改与验证**。
    """
    offenders: dict[str, dict[str, list[int]]] = {}
    for path in _python_files():
        duplicates = _duplicate_top_level_definitions(path)
        if duplicates:
            offenders[str(path.relative_to(TESTS))] = duplicates

    assert not offenders, (
        "这些模块里**顶层同名定义出现了两次**，后一个会静默胜出、前一个是死代码：\n"
        + "\n".join(f"  {name}: " + ", ".join(f"{key} 在行 {lines}" for key, lines in sorted(dups.items()))
                    for name, dups in sorted(offenders.items()))
        + "\n请删掉不再使用的那一份（或改名）。读到第一个定义的人会按它行动。")
