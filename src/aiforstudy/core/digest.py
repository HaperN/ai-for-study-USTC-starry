"""规范化指纹（digest）。

方案 §11 对 digest 的要求，逐条对应到本模块的做法：

> digest 使用规范化字段顺序与逻辑身份，不包含运行时间、run_id 或绝对工作目录；
> 书籍/单元身份和实际消费内容必须包含。移动工作区或改变并发不应无故失去缓存，
> 跨书同名 CH 单元也不能串缓存归属。

所以：

* `canonical_json` 固定 **键序**（`sort_keys`）与分隔符 —— 同一份内容在两次运行里必须得到同一串；
* `digest_bytes` / `digest_file` 是对**内容**取 sha256，不是对路径；
* `digest_of` 只吃"逻辑身份"，**调用方负责不要把 run_id / 时间 / 绝对路径放进去**。
  这条光靠注释约束不住，所以 `FORBIDDEN_KEYS` 让它在运行期**直接报错**——
  静默算出一个"每次都不同"的指纹，比算不出来更坏。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

#: 不允许进入指纹的键：它们每次运行都不同，混进去会让缓存永远命中不了。
FORBIDDEN_KEYS = frozenset({
    "run_id", "attempt_id", "created_at", "started_at", "finished_at", "timestamp",
    "duration", "elapsed", "wall_clock", "mtime", "mtime_ns", "workspace", "cwd",
    "hostname", "pid", "log", "logs",
})


class DigestError(ValueError):
    """指纹输入里有"每次都不同"的字段 —— 那是缓存失效的根源，必须当场报错。"""


def _check(obj: Any, path: str = "") -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key) in FORBIDDEN_KEYS:
                where = f"{path}.{key}" if path else str(key)
                raise DigestError(
                    f"指纹输入里出现了 {where!r}：{sorted(FORBIDDEN_KEYS)} 这类字段每次运行都不同，"
                    f"混进指纹会让缓存永远命中不了。请把它从被哈希的结构里移出去。")
            _check(value, f"{path}.{key}" if path else str(key))
    elif isinstance(obj, (list, tuple)):
        for i, item in enumerate(obj):
            _check(item, f"{path}[{i}]")


def canonical_json(obj: Any) -> str:
    """规范化 JSON 文本：键序固定、分隔符固定、非 ASCII 不转义（人可读）。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=str)


def digest_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def digest_text(text: str) -> str:
    return digest_bytes(text.encode("utf-8"))


def digest_of(obj: Any, *, check: bool = True) -> str:
    """对**逻辑身份**取指纹。`check=True` 时会拒绝 run_id/时间/绝对路径一类的键。"""
    from .review_mode import reviews_frozen
    if check and not reviews_frozen():
        _check(obj)
    return digest_text(canonical_json(obj))


def digest_file(path: str | Path, chunk: int = 1 << 20) -> str:
    """对文件**内容**取指纹（不掺进路径、大小、mtime）。"""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return "sha256:" + h.hexdigest()


__all__ = ["FORBIDDEN_KEYS", "DigestError", "canonical_json", "digest_bytes", "digest_text",
           "digest_of", "digest_file"]
