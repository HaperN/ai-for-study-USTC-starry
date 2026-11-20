"""受限表达式求值：**AST 白名单**，不是 `eval` 加个空 builtins。

方案 §8 的原话：

> 新增规格生成可以使用模型，但只返回受限 JSON；**不得执行模型生成的 Python、
> JavaScript 或任意 TeX**。plot 表达式改为 **AST 白名单求值**，限制函数、变量、
> 节点深度、采样量与有限数值……

## 为什么必须重写（本次实测到的真问题）

旧 `blocks/figures/backends/plot.py` 是这么算曲线的：

```python
def _safe_eval(expr, x, params):
    allowed = {"np": np, ..., "x": x}
    return eval(expr, {"__builtins__": {}}, allowed)      # ← 这里
```

它旁边的注释写着"我们自己的规格文件，不是用户输入"。**但这正是会变的那条假设**：
方案 §8 明确允许"新增规格生成可以使用模型"。规格一旦来自模型，`expr` 就是模型输出，
而 `eval(expr, {"__builtins__": {}}, allowed)` **不是沙箱** ——
清空 `__builtins__` 挡不住经典的属性链逃逸
（`().__class__.__bases__[0].__subclasses__()` 一类），拿到任意类之后就是任意代码执行。

所以本模块**完全不调用 `eval`**，而是把表达式解析成 AST 之后**逐节点按白名单放行**：
只认算术运算符、只认显式列出的函数名、**根本不允许属性访问与下标**（`np.sin` 这种写法
在规格里要写成 `sin`）。白名单之外的节点一律拒绝，并说明拒绝的是哪一类。

限制一共五档（对应 §8 点名的五项）：

| 限制 | 值 | 防的是 |
|---|---|---|
| 节点深度 | ≤ 24 | 用深层嵌套把求值器拖死 |
| 节点总数 | ≤ 400 | 超长表达式 |
| 允许的函数 | 显式列表 | 任意调用 |
| 变量 | 规格里声明的参数 + `x`/`i` | 读取求值环境里的别的东西 |
| 结果 | 必须是**有限**实数 | `inf`/`nan` 让下游图形/断言失效 |
"""
from __future__ import annotations

import ast
import math
from dataclasses import dataclass
from typing import Any

from ...errors import ContractError

#: 允许调用的函数（**只按名字**，规格里写 `sin(x)`，不写 `np.sin(x)`）
SAFE_FUNCTIONS: dict[str, Any] = {
    "abs": abs, "min": min, "max": max, "round": round, "pow": pow,
    "sqrt": math.sqrt, "exp": math.exp, "log": math.log, "log10": math.log10,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "floor": math.floor, "ceil": math.ceil, "copysign": math.copysign,
    "pi": math.pi, "e": math.e,
}

#: 允许的**二元**运算符
SAFE_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
#: 允许的一元运算符
SAFE_UNOPS = (ast.UAdd, ast.USub)

MAX_DEPTH = 24
MAX_NODES = 400
MAX_POW_EXPONENT = 64.0          # 防 `x ** 1e9` 把内存吃光
MAX_ABS_VALUE = 1e12             # 结果必须落在这个范围内（有限且不至于溢出）


@dataclass(frozen=True)
class CompiledExpression:
    """编译好的表达式 + 它用到的变量名。"""
    source: str
    tree: ast.Expression
    names: frozenset[str]
    nodes: int
    depth: int

    def evaluate(self, variables: dict[str, Any],
                 functions: dict[str, Any] | None = None) -> Any:
        """求值。`functions` 可覆盖/扩充白名单函数（例如用 numpy 版本支持数组入参）。

        ⚠️ 为什么要显式传而不是改模块级 `SAFE_FUNCTIONS`：后者是**进程级可变状态**，
        并行 worker 会互相看见对方的改动（一个把 `sin` 换成 numpy 版，另一个就跟着变）。
        求值器不该有跨调用的隐藏状态。
        """
        table = {**SAFE_FUNCTIONS, **(functions or {})}
        missing = self.names - set(variables) - set(table)
        if missing:
            raise ContractError(f"表达式 {self.source!r} 用到了未声明的变量：{sorted(missing)}")
        value = _eval(self.tree.body, variables, table)
        return _check_finite(value, self.source)


def _check_finite(value, source: str):
    """结果必须是有限实数；数组则逐个检查。"""
    try:
        import numpy as np
        if isinstance(value, np.ndarray):
            if not np.all(np.isfinite(value)):
                bad = value[~np.isfinite(value)][:3]
                raise ContractError(f"表达式 {source!r} 产生了非有限值（如 {list(bad)}）")
            if np.max(np.abs(value)) > MAX_ABS_VALUE:
                raise ContractError(f"表达式 {source!r} 的取值过大（> {MAX_ABS_VALUE:g}）")
            return value
    except ImportError:
        pass
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ContractError(f"表达式 {source!r} 求值得到非有限值 {value!r}")
        if abs(value) > MAX_ABS_VALUE:
            raise ContractError(f"表达式 {source!r} 求值得到过大的值 {value!r}")
    return value


def _eval(node: ast.AST, variables: dict[str, Any],
          functions: dict[str, Any] | None = None):
    table = functions if functions is not None else SAFE_FUNCTIONS
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ContractError(f"只允许数字常量，实际是 {node.value!r}")
        return node.value
    if isinstance(node, ast.Name):
        if node.id in variables:
            return variables[node.id]
        if node.id in table:                        # 常量 pi / e
            return table[node.id]
        raise ContractError(f"未知名字 {node.id!r}（可用变量由规格声明；函数见白名单）")
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, SAFE_BINOPS):
            raise ContractError(f"不允许的运算符 {type(node.op).__name__}")
        left = _eval(node.left, variables, table)
        right = _eval(node.right, variables, table)
        if isinstance(node.op, ast.Pow):
            _guard_exponent(right)
        if isinstance(node.op, ast.Div) or isinstance(node.op, ast.FloorDiv) or \
                isinstance(node.op, ast.Mod):
            _guard_divisor(right)
        op = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
              ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
              ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b,
              ast.Pow: lambda a, b: a ** b}[type(node.op)]
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, SAFE_UNOPS):
            raise ContractError(f"不允许的一元运算符 {type(node.op).__name__}")
        operand = _eval(node.operand, variables, table)
        return +operand if isinstance(node.op, ast.UAdd) else -operand
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ContractError(
                "只允许直接调用白名单里的函数名（不允许属性访问，例如 `np.sin` 要写成 `sin`）")
        name = node.func.id
        if name not in table or not callable(table[name]):
            raise ContractError(f"函数 {name!r} 不在白名单里：{sorted(table)}")
        if node.keywords:
            raise ContractError("不允许关键字参数")
        args = [_eval(a, variables, table) for a in node.args]
        return table[name](*args)
    if isinstance(node, ast.Attribute):
        raise ContractError(
            "**不允许属性访问**。这是把 `eval` 换成 AST 白名单的主要目的之一："
            "`().__class__.__bases__[0].__subclasses__()` 这类逃逸全靠属性链，"
            "直接禁掉整类语法比事后过滤属性名可靠得多。")
    raise ContractError(
        f"表达式里出现了不允许的语法：{type(node).__name__}"
        f"（只允许 数字 / 变量 / 算术运算 / 白名单函数调用）")


def _guard_exponent(value) -> None:
    try:
        if abs(float(value)) > MAX_POW_EXPONENT:
            raise ContractError(f"指数过大（|{value}| > {MAX_POW_EXPONENT:g}）—— 防内存爆炸")
    except (TypeError, ValueError):
        return


def _guard_divisor(value) -> None:
    try:
        if float(value) == 0.0:
            raise ContractError("除数为 0")
    except (TypeError, ValueError):
        return


def compile_expression(source: str) -> CompiledExpression:
    """把表达式编译成受检的 AST。**不执行任何用户/模型代码。**"""
    text = str(source or "").strip()
    if not text:
        raise ContractError("表达式不能为空")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ContractError(f"表达式 {text!r} 语法不对：{exc}") from exc

    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        raise ContractError(f"表达式 {text!r} 的节点太多（{len(nodes)} > {MAX_NODES}）")

    def depth(node: ast.AST) -> int:
        children = list(ast.iter_child_nodes(node))
        return 1 + (max(depth(c) for c in children) if children else 0)

    d = depth(tree)
    if d > MAX_DEPTH:
        raise ContractError(f"表达式 {text!r} 嵌套太深（{d} > {MAX_DEPTH}）")

    names = frozenset(n.id for n in nodes if isinstance(n, ast.Name))
    # ── 静态放行检查：**编译期就把不允许的东西拒掉**，不等到求值
    #    ⚠️ 这一条是实测补的：`__import__("os")` 与 `open("/etc/passwd")` 在 `_eval`
    #    里会被"函数不在白名单"挡住，但**编译期**当时是放行的。只调用
    #    `compile_expression` 做规格校验的地方（以及将来别处的复用）就漏了 ——
    #    "两道口子只关一道"是这类沙箱最常见的失效方式，所以两道都关。
    for node in nodes:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ContractError(
                    f"表达式 {text!r} 里只允许数字常量，实际是 {node.value!r}"
                    f"（字符串/字节/None 一律不许 —— 它们是拼接出危险调用的原材料）")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ContractError(
                    f"表达式 {text!r} 里的调用不是直接函数名（不允许属性访问）")
            if node.func.id not in SAFE_FUNCTIONS or not callable(SAFE_FUNCTIONS[node.func.id]):
                raise ContractError(
                    f"表达式 {text!r} 调用了白名单之外的函数 {node.func.id!r}")
            if node.keywords:
                raise ContractError(f"表达式 {text!r} 使用了关键字参数（不允许）")
    for node in nodes:
        if isinstance(node, (ast.Attribute, ast.Subscript, ast.Lambda, ast.ListComp,
                             ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.Await,
                             ast.Yield, ast.NamedExpr, ast.IfExp, ast.BoolOp, ast.Compare,
                             ast.Dict, ast.List, ast.Set, ast.Tuple, ast.Starred,
                             ast.JoinedStr, ast.FormattedValue)):
            raise ContractError(
                f"表达式 {text!r} 里出现了不允许的语法：{type(node).__name__}")
    return CompiledExpression(source=text, tree=tree, names=names, nodes=len(nodes), depth=d)


def evaluate(source: str, variables: dict[str, Any],
             functions: dict[str, Any] | None = None):
    """编译并求值。规格里 `expr` 的入口就是它。"""
    return compile_expression(source).evaluate(variables, functions)


def numpy_functions() -> dict[str, Any]:
    """用 numpy 同名函数覆盖白名单 —— 曲线的入参是**数组**，`math.*` 收不了。

    只在需要数组求值的地方显式传进去（`evaluate(..., numpy_functions())`），
    不动全局表。
    """
    try:
        import numpy as np
    except ImportError:                                  # pragma: no cover
        return {}
    return {name: getattr(np, name) for name in SAFE_FUNCTIONS
            if callable(getattr(np, name, None))}


__all__ = ["SAFE_FUNCTIONS", "SAFE_BINOPS", "SAFE_UNOPS", "MAX_DEPTH", "MAX_NODES",
           "MAX_POW_EXPONENT", "MAX_ABS_VALUE", "CompiledExpression", "compile_expression",
           "evaluate", "numpy_functions"]
