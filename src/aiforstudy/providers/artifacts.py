"""依赖制品指纹：把"到底跑的哪一版"变成可比对的数字。

方案 §3：

> 第三方审核器当前需要本地 vendor 与上游依赖。先把正在使用的 vendor 版本作为独立、
> 带来源和许可证信息的**依赖制品**固定，验证一致性，再移除路径注入；
> **不允许迁移时静默换成环境中另一版同名包。**

这句话在实践中比听起来严重：`pip` 的版本号**不能**证明字节一致。本次 M1 核对就抓到
一起真实漂移——安装好的 `undergraduate-fitted-exam-system 0.15.0` 与
`CourseForgeV2/安装包/` 里同名同版本的 wheel **有 27 个 .py 文件内容不同**
（详见 `文档/02-记录与报告/M1-包化与上下文隔离-20261005.md`）。
版本号一样、内容不一样，正是"静默换成另一版同名包"的典型形态。

所以这里给出两个不同的指纹，**分别记录、绝不互相顶替**：

* `payload_digest`：**磁盘上实际会被 import 的那批文件**的内容指纹（生产真正跑的东西）；
* `source_hash`：来源制品（wheel/目录）本身的哈希。

两者不一致就叫 **drift**，必须显式记录与裁决，不允许自动"以最新为准"。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..core.digest import digest_file, digest_of

#: 参与指纹的文件后缀。二进制/缓存不参与（它们随解释器版本变，不代表源码同一版）。
TEXT_SUFFIXES = (".py", ".pyi", ".txt", ".md", ".json", ".toml", ".cfg", ".ini")
SKIP_DIR_NAMES = {"__pycache__", ".git", ".mypy_cache", ".pytest_cache"}


def payload_digest(root: Path, *, suffixes: tuple[str, ...] = TEXT_SUFFIXES,
                   normalize_newlines: bool = False) -> tuple[str, int]:
    """目录内容的**顺序无关**指纹：按相对路径排序后逐文件取 sha256，再整体哈希。

    返回 `(指纹, 参与文件数)`。顺序无关很重要——同一份包在不同机器上解包的目录遍历顺序
    可能不同，若把顺序算进指纹，就会得到"每台机器都不同"的假漂移。

    `normalize_newlines=True` 时先把 CRLF 归一成 LF 再取指纹。**为什么需要这个开关**
    （2026-10-05 实测）：同一份 `stem_runner` 在 wheel 里是 LF、装到 site-packages 后
    变成 CRLF，27 个 .py 里 20 个的差异纯粹来自换行符。不归一化的话，每次"wheel ↔ 安装"
    比对都会报 20 处假漂移，真漂移（另外 7 个文件里被删掉的审核模块）就淹在里面了。
    """
    root = Path(root)
    rows: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if suffixes and path.suffix not in suffixes:
            continue
        rel = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        if normalize_newlines:
            raw = raw.replace(b"\r\n", b"\n")
        rows.append(f"{rel}\0sha256:{hashlib.sha256(raw).hexdigest()}")
    blob = "\n".join(rows).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest(), len(rows)


def wheel_payload_digest(wheel: Path, prefix: str = "",
                         suffixes: tuple[str, ...] = TEXT_SUFFIXES,
                         normalize_newlines: bool = True) -> tuple[str, int]:
    """同一个指纹口径，但取自 wheel 内部 —— 用来回答"盘上这份目录是不是那个 wheel 解出来的"。"""
    import zipfile
    rows: list[str] = []
    with zipfile.ZipFile(wheel) as zf:
        for name in sorted(zf.namelist()):
            if name.endswith("/"):
                continue
            rel = name
            if prefix:
                if not name.startswith(prefix):
                    continue
                rel = name[len(prefix):]
            if any(part in SKIP_DIR_NAMES for part in rel.split("/")):
                continue
            if suffixes and Path(rel).suffix not in suffixes:
                continue
            raw = zf.read(name)
            if normalize_newlines:
                raw = raw.replace(b"\r\n", b"\n")
            rows.append(f"{rel}\0sha256:{hashlib.sha256(raw).hexdigest()}")
    blob = "\n".join(rows).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest(), len(rows)


@dataclass(frozen=True)
class Artifact:
    """一个依赖制品的现场状态。"""
    kind: str                       # python-dist | vendor-dir | wheel
    name: str
    version: str = ""
    path: Path | None = None
    payload: str = ""               # 实际会被 import 的那批文件的内容指纹
    payload_files: int = 0
    source_hash: str = ""           # 来源制品本身的哈希
    recorded_source_hash: str = ""  # 安装时记录下来的来源哈希（direct_url.json）
    license: str = ""
    origin: str = ""
    notes: tuple[str, ...] = ()

    @property
    def drift(self) -> bool:
        """来源哈希与安装时记录的来源哈希不一致 —— **同版本不同字节**。"""
        if not self.recorded_source_hash or not self.source_hash:
            return False
        return self.recorded_source_hash != self.source_hash

    def as_dict(self) -> dict:
        return {"kind": self.kind, "name": self.name, "version": self.version,
                "path": (str(self.path) if self.path else None),
                "payload": self.payload, "payload_files": self.payload_files,
                "source_hash": self.source_hash,
                "recorded_source_hash": self.recorded_source_hash,
                "drift": self.drift, "license": self.license, "origin": self.origin,
                "notes": list(self.notes)}

    def identity(self) -> str:
        return digest_of({"kind": self.kind, "name": self.name, "version": self.version,
                          "payload": self.payload})


@dataclass
class SourceLock:
    """`vendor.lock.json` 的内容：每个依赖制品"应当是哪一份"。"""
    path: Path
    artifacts: dict[str, dict] = field(default_factory=dict)
    accepted_drift: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "SourceLock":
        if not path.is_file():
            return cls(path=path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(path=path,
                   artifacts=dict(payload.get("artifacts") or {}),
                   accepted_drift=dict(payload.get("accepted_drift") or {}))

    def expected(self, key: str) -> dict | None:
        return self.artifacts.get(key)

    def as_dict(self) -> dict:
        return {"artifacts": self.artifacts, "accepted_drift": self.accepted_drift,
                "_meta": {"file_type": "vendor_lock", "schema_version": "1.0",
                          "generator": "aiforstudy.providers.lock",
                          "field_notes": {
                              "artifacts[].payload": "实际会被 import 的那批文件的内容指纹（生产真正跑的）",
                              "artifacts[].source_hash": "来源制品（wheel/目录）本身的哈希",
                              "artifacts[].recorded_source_hash": "安装时记下的来源哈希（direct_url.json）",
                              "accepted_drift": "键=制品 key，值=为什么接受这处漂移。**不写就等于不接受**（校验会失败）",
                          }}}


__all__ = ["TEXT_SUFFIXES", "SKIP_DIR_NAMES", "payload_digest", "wheel_payload_digest",
           "Artifact", "SourceLock"]
