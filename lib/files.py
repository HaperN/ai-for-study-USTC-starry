"""跨块共用的小文件工具（lib 层）。

只放"跟业务无关"的东西：读 JSON、写 JSON（原子写）、算文件 sha256。
各块自己的校验规则、schema 不放在这里。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

# 仓库根（`AIforStudy/`）。放这里是为了让 `display_path` 不依赖任何上层模块。
ROOT = Path(__file__).resolve().parents[1]


def display_path(path: str | Path, base: str | Path | None = None) -> str:
    """给人看的路径：**在仓库里就给相对路径，在仓库外就给绝对路径**。

    ⚠️ 为什么必须收成这一个实现（2026-09-26 生产版 A6 实测抓出）：
    `run_book.py` 原来直接写 `worker["log"].relative_to(ROOT)`，而
    `--lessons /tmp/a6/serial/lesson/a6` 这种"**产物在仓库外**"的用法
    （隔离树做串行/并行比对，正是 A6 要用的）会让 `relative_to` 抛
    `ValueError: … is not in the subpath of …` → **整个整本跑当场死掉**。

    教训不是"给这一行包个 try"，而是：**"给人看的路径"不该因为路径在不在
    仓库里而变成一个异常**。凡是拿**用户传进来的路径**（`--lessons`/`--parse`/
    `--out-root`）去 `relative_to(ROOT)` 的地方，都是同一个坑。

    有测试全局守着：`tests/test_wiring.py` 会扫 `tools/`、`lib/`，
    发现裸的 `.relative_to(ROOT)` 就报错。
    """
    path = Path(path)
    base = Path(base).resolve() if base else ROOT
    try:
        return str(path.resolve().relative_to(base))
    except ValueError:
        return str(path)


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: str | Path):
    """文件不存在就返回 None（调用方自己决定这是不是错误）。"""
    path = Path(path)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: str | Path, payload, *, mode: int = 0o644) -> None:
    """原子写：先写同目录临时文件再替换，避免中途失败留下半个 JSON。

    `mode` 要显式 chmod：`mkstemp` 建的文件是 0600，替换过去之后产物会变成
    "只有自己能读"，跟别的产物权限不一致（实测交付目录里就出现过一个 600 的文件）。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


__all__ = ["ROOT", "display_path", "sha256_file", "sha256_bytes", "load_json", "write_json"]
