"""工作区解析：**不靠 `__file__.parents[N]` 猜**。

方案 §3：

> 生产模块只使用包内导入，移除散落的 `sys.path.insert`、按路径加载本项目模块，
> 以及 `__file__.parents[N]` 猜工作区。工作区、书籍和产物版本由参数传入。

但"由参数传入"需要一个**明确的**来源顺序，否则每个命令又会各写一份默认值
（本项目 119 条错误里最常见的那一类）。所以顺序写死在这里，只有这一处：

1. 显式参数（`--workspace`）；
2. 环境变量 `AIFORSTUDY_WORKSPACE`；
3. 从**当前工作目录**逐级向上找 `config/books.json`。

三步都找不到 → **报错**，不退回任何"看起来像"的目录。

⚠️ **故意没有"从包所在位置向上找"这一步。** 那正是方案 §3 点名要删掉的
`__file__.parents[N]` 猜工作区：包装在哪里，跟"这次要处理哪个工作区"没有关系。
实测过一次它的后果——在 `/tmp` 里执行、什么也没指定，它**安静地**用了安装包所在仓库的
工作区并输出结果（退出码 0）。"跑在别的工作区"和"跑在本仓库"长得一模一样，
正是那个默认值族错误的形态。现在它会以退出码 2 明确失败。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from ..errors import ConfigError

ENV_WORKSPACE = "AIFORSTUDY_WORKSPACE"
REGISTRY_REL = Path("config") / "books.json"
MARKER = REGISTRY_REL


def _looks_like_workspace(path: Path) -> bool:
    return (path / MARKER).is_file()


def _walk_up(start: Path, limit: int = 12) -> Path | None:
    cur = start.resolve()
    for _ in range(limit):
        if _looks_like_workspace(cur):
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def find_workspace(explicit: str | Path | None = None) -> Path:
    """按上面 1→3 的顺序定位工作区；失败即报错（不猜）。"""
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not _looks_like_workspace(path):
            raise ConfigError(f"--workspace {path} 下没有 {MARKER}；这不像一个 AIforStudy 工作区")
        return path

    env = os.environ.get(ENV_WORKSPACE)
    if env:
        path = Path(env).expanduser().resolve()
        if not _looks_like_workspace(path):
            raise ConfigError(
                f"环境变量 {ENV_WORKSPACE}={env} 下没有 {MARKER}；这不像一个 AIforStudy 工作区")
        return path

    found = _walk_up(Path.cwd())
    if found is not None:
        return found

    raise ConfigError(
        f"找不到工作区：{MARKER} 不存在。三种给法（**故意不提供「猜」的第四种**）：\n"
        f"  1) --workspace <目录>\n"
        f"  2) 环境变量 {ENV_WORKSPACE}=<目录>\n"
        f"  3) 在含 {MARKER} 的目录（或其子目录）下运行\n"
        f"当前工作目录：{Path.cwd()}")


@dataclass(frozen=True)
class Workspace:
    """工作区内的固定位置。所有路径**一律相对 root 解析**。"""
    root: Path

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def registry_path(self) -> Path:
        return self.root / REGISTRY_REL

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def delivery(self) -> Path:
        return self.root / "delivery"

    @property
    def cache(self) -> Path:
        return self.root / ".cache"

    @property
    def figures(self) -> Path:
        return self.root / "figures"

    @property
    def tests(self) -> Path:
        return self.root / "tests"

    def path(self, rel: str | Path) -> Path:
        """相对路径按 root 解析；**绝对路径原样返回**（方案 §3：外部绝对路径逐项核对）。"""
        p = Path(rel)
        return p if p.is_absolute() else (self.root / p)

    def config_json(self, name: str) -> dict:
        path = self.config / name
        if not path.is_file():
            raise ConfigError(f"缺配置文件 {path}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ConfigError(f"配置文件 {path} 不是合法 JSON：{exc}") from exc

    def as_dict(self) -> dict:
        return {"root": str(self.root)}


def open_workspace(explicit: str | Path | None = None) -> Workspace:
    """定位工作区并包成 `Workspace` —— 调用方（CLI/UI）应该用这个，不要直接用 Path。"""
    return Workspace(root=find_workspace(explicit))


__all__ = ["ENV_WORKSPACE", "REGISTRY_REL", "Workspace", "find_workspace", "open_workspace"]
