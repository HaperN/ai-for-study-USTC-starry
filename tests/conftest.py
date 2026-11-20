"""测试公共装置。

**只做一件事**：把 `src/` 放到 `sys.path` 上，好让测试能在**未安装**的环境里
`import aiforstudy`。为什么需要它而不是要求"先 pip install -e ."：

* A01 要求"全新虚拟环境从锁定来源安装…打乱模块导入顺序" —— 那种环境里包是装好的；
* 但绝大多数日常回归、以及别人 clone 下来第一件事就是 `pytest`，那时还没装。
  要求先装才能跑测试，等于给"跑一下回归"加了一道无意义的前置动作。

⚠️ 顺带说清为什么这里**不做**别的事：不加 `testpaths`、不改 `import-mode`、不注册
奇特的 fixture。现有 37 个 test_*.py 用 `sys.path` 相对导入，任何"顺手统一一下"
的改动都会让它们静默少跑或收集失败。

## 第二件事：会话级离线守卫（T00）

原来"断网"是**每个测试文件自己记得装**的 tripwire；忘装一次就等于真的能出网、真的能花钱。
现在在这里装一次：`tests/support/offline_guard.py` 拦本进程的公网 socket／DNS／urllib，
放行回环、AF_UNIX 与所有替身传输。它**不改 fixture、不收集测试**，只包一层网络原语，
所以不会让既有测试静默少跑。子进程的网络不归它管 —— 那是 T07 bubblewrap 的职责。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SUPPORT = ROOT / "tests" / "support"

if SRC.is_dir() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# `tests/support` 也要在路径上：`-p offline_guard` 与下面的直接导入都靠它。
if SUPPORT.is_dir() and str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))

try:  # 守卫缺失是环境问题，要**显式喊出来**，不静默放行
    import offline_guard

    offline_guard.install()
except Exception as _exc:  # noqa: BLE001 - 故意宽：装不上必须刺眼
    print(f"[conftest] ⚠️ 离线守卫未装上（{_exc!r}）：本次运行的外网**不受保护**。",
          file=sys.stderr)

# Existing contract tests also exercise the restorable full-review mode.
# JSON-only tests opt into their own run context explicitly.
import pytest


@pytest.fixture(autouse=True)
def full_review_contracts():
    from aiforstudy.core.review_mode import review_mode
    with review_mode('full'):
        yield
