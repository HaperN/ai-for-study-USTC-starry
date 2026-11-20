"""AIforStudy 统一引擎。

方案 §3：生产模块只使用**包内导入**；移除散落的 `sys.path.insert`、按路径加载本项目模块，
以及 `__file__.parents[N]` 猜工作区。工作区、书籍和产物版本由参数传入。

对外只有两个入口：

* `python -m aiforstudy`（=`aiforstudy.__main__`）
* 安装后的 `aifs` 命令（=`aiforstudy.cli:main`）

⚠️ 迁移期说明：现有 `blocks/`、`lib/`、`tools/` 下的实现**还没有搬进来**（那是 M3–M9 的工作）。
本包在 M1 只落地"契约 + 书籍注册 + 工作区解析 + 服务适配"，其余按里程碑逐个迁入。
不在这里放空占位目录——空占位会让人以为能力已经接好了（方案 §13 明确要删掉两处空占位）。
"""
from __future__ import annotations

__all__ = ["__version__"]
__version__ = "0.1.0"
