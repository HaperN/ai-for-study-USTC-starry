"""审核器适配：把本地 vendor 当成**带来源与许可证的依赖制品**固定下来。

方案 §3：

> 第三方审核器当前需要本地 vendor 与上游依赖。先把正在使用的 vendor 版本作为独立、
> 带来源和许可证信息的依赖制品固定，验证一致性，再移除路径注入。

现状（M1 核对）：vendor 是仓库内的一份目录副本 `blocks/audit/vendor/hbep_controller_v2/`，
由 `blocks/audit/audit_adapter.py` 用 `sys.path.insert` 注入。那正是方案 §3 要移除的东西。

迁移期的做法：**把路径注入收进这一个模块**。别的模块不许自己再插一次 `sys.path`——
路径注入本身不可怕，散在 N 处、每处一份版本假设才可怕。等 vendor 被固定成真正的依赖制品
（M9），`load()` 改一行即可，调用点不动。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

from ..core.workspace import Workspace
from ..errors import CapabilityError
from .artifacts import Artifact, SourceLock, payload_digest

VENDOR_REL = Path("blocks") / "audit" / "vendor"
VENDOR_PACKAGE = "hbep_controller_v2"


def vendor_root(workspace: Workspace) -> Path:
    return workspace.root / VENDOR_REL / VENDOR_PACKAGE


def inspect(workspace: Workspace) -> Artifact:
    root = vendor_root(workspace)
    if not root.is_dir():
        return Artifact(kind="vendor-dir", name=VENDOR_PACKAGE,
                        notes=(f"vendor 不在位：{root}",))
    payload, files = payload_digest(root)
    license_text = ""
    for candidate in ("LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING"):
        path = root / candidate
        if path.is_file():
            license_text = f"{candidate}"
            break
    notes = []
    if not license_text:
        notes.append("vendor 目录里没有找到 LICENSE/COPYING 文件 —— "
                     "方案 §3 要求带许可证信息，必须在 M9 前补齐来源与许可证记录")
    return Artifact(kind="vendor-dir", name=VENDOR_PACKAGE,
                    path=root, payload=payload, payload_files=files,
                    license=license_text or "（未记录）",
                    origin=str(root),
                    notes=tuple(notes))


def verify(workspace: Workspace, lock: SourceLock | None = None) -> tuple[Artifact, list[str]]:
    lock = lock or SourceLock.load(workspace.root / "vendor.lock.json")
    art = inspect(workspace)
    problems: list[str] = []
    expected = lock.expected("auditor_vendor")
    if expected:
        want = expected.get("payload")
        if want and want != art.payload:
            problems.append(
                f"审核器 vendor 的内容指纹与锁不一致：锁 {want} ≠ 现场 {art.payload}"
                f"（{art.payload_files} 个文件）")
    else:
        problems.append("vendor.lock.json 里没有 auditor_vendor 条目 —— 尚无可核对的锁定来源")
    if "LICENSE" in art.license.upper() or "COPYING" in art.license.upper():
        pass
    else:
        problems.append("审核器 vendor 没有许可证记录（方案 §3 要求带来源和许可证信息）")
    return art, problems


_LOADED = None


def load(workspace: Workspace):
    """import vendor 包。**路径注入只在这一处发生。**"""
    global _LOADED
    if _LOADED is not None:
        return _LOADED
    root = vendor_root(workspace)
    if not root.is_dir():
        raise CapabilityError(
            f"审核器 vendor 不在位：{root}。方案 §13 明确**不许**仅因没有主入口调用就整目录删除，"
            f"但也要求把来源与许可证固定下来。")
    as_str = str(root)
    if as_str not in sys.path:
        sys.path.insert(0, as_str)
    try:
        _LOADED = importlib.import_module(VENDOR_PACKAGE)
    except ImportError as exc:
        raise CapabilityError(
            f"vendor 在盘上但 import 失败：{type(exc).__name__}: {exc}。"
            f"这通常意味着它的上游依赖没装齐（不是'没有审核能力'）。") from exc
    return _LOADED


__all__ = ["VENDOR_REL", "VENDOR_PACKAGE", "vendor_root", "inspect", "verify", "load"]
