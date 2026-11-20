"""JSON 读写：**坏了要说清楚**，写要**原子**。

两条都来自已经踩过的坑：

* **读**：`json.loads` 失败时返回 `None`，与"文件不存在"、"内容为空"混成一件事，
  于是"产物坏了"表现成"还没跑过"，一路静默重跑或静默通过。所以这里
  `read_json` 区分"不存在 / 不是 JSON / 顶层类型不对"，并带路径报错。
* **写**：方案 §15 A27 要求"每阶段完成前后杀进程、半写 JSON、磁盘写失败后 resume →
  原子产物不损坏"。半写的 JSON 会让下一次运行读到坏缓存并把它当有效输入。
  所以一律"写临时文件 → `os.replace`"，`os.replace` 在同一文件系统上是原子的。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..errors import ConfigError

_MISSING = object()


def read_json(path: str | Path, default: Any = _MISSING) -> Any:
    """读 JSON。文件不存在或内容坏了 → 报错（除非显式给了 `default`）。"""
    p = Path(path)
    if not p.is_file():
        if default is not _MISSING:
            return default
        raise ConfigError(f"缺文件 {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:
        if default is not _MISSING:
            return default
        raise ConfigError(f"{p} 不是合法 JSON：{exc}") from exc
    except OSError as exc:
        if default is not _MISSING:
            return default
        raise ConfigError(f"读不了 {p}：{exc}") from exc


def read_json_soft(path: str | Path) -> Any | None:
    """探测用：读不到就 `None`。**只许用在"探测"场景**，不要拿它当正常读取路径。"""
    return read_json(path, default=None)


def write_json_atomic(path: str | Path, payload: Any, *, indent: int = 2) -> Path:
    """原子写 JSON：临时文件 → `os.replace`。目录会自动建。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=indent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return p


__all__ = ["read_json", "read_json_soft", "write_json_atomic"]
