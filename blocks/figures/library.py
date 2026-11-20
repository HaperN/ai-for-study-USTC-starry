"""兼容垫片：`library` 这个名字被 `cli.py`、`tools/run_book.py`、`tests/test_figures.py` 用着，
所以重构（规格 ↔ 渲染 分家）时**保留这个名字**，只做转发。

新代码请直接用：
  * `blocks.figures.spec`         —— 图规格与图库清单（画什么）
  * `blocks.figures.backends.*`   —— 各后端渲染（怎么画）
"""
from __future__ import annotations

try:                                    # 包内 import（正常用法）
    from .spec import ALL_FIGURES, DEFAULT_LIMITS, LIBRARY, by_name, figure   # noqa: F401
    from .spec import any_by_name                                            # noqa: F401
    from .backends import render as _render                                  # noqa: F401
    from .backends.vector import _to_current_color, draw_all                 # noqa: F401
except ImportError:                     # 被当成**顶层模块** import（tests/test_figures.py 就是这么用的：
    from spec import ALL_FIGURES, DEFAULT_LIMITS, LIBRARY, by_name, figure   # noqa: F401
    from spec import any_by_name                                             # noqa: F401
    from backends import render as _render                                   # noqa: F401
    from backends.vector import _to_current_color, draw_all                  # noqa: F401


def draw(spec, out_dir, **kwargs):
    """**一处入口**：按 `spec["backend"]` 派发到对应后端（向量图行为不变）。"""
    return _render(spec, out_dir, **kwargs)

__all__ = ["LIBRARY", "ALL_FIGURES", "DEFAULT_LIMITS", "figure", "by_name",
           "any_by_name", "draw", "draw_all", "_to_current_color"]
