"""测试夹具包（`tests/` 在 pytest 的 prepend 导入模式下就在 sys.path 上）。

这里不放 `tests/__init__.py` —— 那会把测试模块改成包内名称（`tests.test_x`），
并让 `tests/` 从 sys.path 上消失，可能弄坏现有测试的导入方式（硬约束：不许破坏）。
"""
