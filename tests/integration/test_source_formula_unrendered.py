"""原书资料里的坏公式必须按原文保留、不渲染，且不得钉死浏览器验收。

对应真实失败：run-20261011-125024 的原书"习题参考答案"里 `\left[` 缺配对的 `\right]`，
那段原文必须逐字保留，于是 `.katex-error` 把整章验收钉成 failed。
"""
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_source_formula_unrendered_dom_contracts():
    node = shutil.which("node")
    if not node or not (ROOT / "node_modules/jsdom").is_dir():
        import pytest
        pytest.skip("Node/jsdom unavailable")
    result = subprocess.run([node, "--test", "tests/ui/source_formula_unrendered.test.cjs"],
                            cwd=ROOT, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
