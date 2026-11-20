"""图形块（方案 §3 的 `blocks/figures/`）。

M5 落地两件：

* `request.py` —— 图请求闭环：请求 → 匹配/规格/绘制/校验/挂载的**终态**，以及终审的
  "挂图与服务对象一致"检查（§8）。**只有 fulfilled 与经认可的 not_applicable 算完成**；
* `safe_eval.py` —— plot 表达式的 **AST 白名单求值**。

`safe_eval` 是本次实测修掉的一个真问题：旧 `backends/plot.py` 用
`eval(expr, {"__builtins__": {}}, allowed)`，注释写着"我们自己的规格文件，不是用户输入"——
而方案 §8 明确允许"新增规格生成可以使用模型"。规格一旦来自模型，`expr` 就是模型输出，
清空 builtins **不是沙箱**（属性链逃逸）。所以这里完全不调 `eval`。
"""
from __future__ import annotations

from .request import (BACKENDS, FigureLedger, FigureRequest, FigureResource, FigureResult,
                      spec_digest, choose_backend)
from .safe_eval import SAFE_FUNCTIONS, compile_expression, evaluate

# T08：显式动画请求 → `AnimationResult` 的分流。**只被显式请求触发**；
# 绘图（SVG）请求已不走 `declarative.fulfill()`（该模块已删除，D10 清扫）：
# 现在统一由"模型写代码"产出，见 `pipeline/media_stages.py:136` / `:804`。
from . import animation

__all__ = ["BACKENDS", "FigureLedger", "FigureRequest", "FigureResource", "FigureResult",
           "spec_digest", "choose_backend", "SAFE_FUNCTIONS", "compile_expression",
           "evaluate", "animation"]
