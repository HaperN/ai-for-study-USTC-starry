"""`ArtifactRef` —— 产物的**带版本引用**。

方案 §6：

> | ArtifactRef | book_id、kind、schema_version、logical key、内容/依赖摘要、path、
>   producer stage/version；**不能只给一个"latest"路径** |

为什么"不能只给 latest"值得单独写进契约：本项目最贵的一类 bug 就是"存了一份会过期的副本"
或"文件在就算新鲜"。一个只有路径的引用无法回答"这份产物是哪次、用什么输入、由谁生成的"，
于是下游只能靠"文件存在"判断，而方案 §11 明确：

> 文件存在不是缓存有效证据。

所以 `ArtifactRef` 必须同时带上 **内容摘要** 与 **依赖摘要**，并且提供 `verify()`：
拿磁盘上的真实内容重算一遍，对不上就报错（缓存损坏必须失效，而不是接着用）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core.digest import digest_file, digest_of
from ..errors import ContractError


@dataclass(frozen=True)
class ArtifactRef:
    book_id: str
    kind: str                       # 例：parse.catalog / lesson.unit / figure.svg / release.manifest
    logical_key: str                # 书内稳定的逻辑键（例：unit:CH03-S01、figure:FIG-x）
    path: Path
    schema_version: str = "1.0"
    content_digest: str | None = None      # sha256:...（文件内容）
    dependency_digest: str | None = None   # sha256:...（实际消费的输入指纹）
    producer_stage: str = ""
    producer_version: str = ""
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("book_id", "kind", "logical_key"):
            if not str(getattr(self, name) or "").strip():
                raise ContractError(f"ArtifactRef.{name} 必填（不能只给一个路径）")
        object.__setattr__(self, "path", Path(self.path))

    # ── 校验
    def verify(self, *, require_digest: bool = True) -> "ArtifactRef":
        """拿磁盘上的真实内容核对摘要。对不上 → 报错（缓存损坏必须失效）。"""
        if require_digest and not self.content_digest:
            raise ContractError(
                f"{self.kind}:{self.logical_key} 没有 content_digest——"
                f"只有路径的引用无法回答'这份产物是不是那次生成的'（方案 §6）")
        if not self.path.is_file():
            raise ContractError(f"{self.kind}:{self.logical_key} 的产物不在盘上：{self.path}")
        if self.content_digest:
            actual = digest_file(self.path)
            if actual != self.content_digest:
                raise ContractError(
                    f"{self.kind}:{self.logical_key} 的内容指纹对不上："
                    f"记录 {self.content_digest} ≠ 实际 {actual}（{self.path}）")
        return self

    def with_content(self) -> "ArtifactRef":
        """按当前磁盘内容补上 `content_digest`（写产物之后调用）。"""
        if not self.path.is_file():
            raise ContractError(f"产物不在盘上，无法取指纹：{self.path}")
        return self.replace(content_digest=digest_file(self.path))

    def replace(self, **changes) -> "ArtifactRef":
        from dataclasses import replace
        return replace(self, **changes)

    def identity(self) -> str:
        """逻辑身份（不含路径与时间），用于缓存归属与跨书隔离。"""
        return digest_of({"book_id": self.book_id, "kind": self.kind,
                          "logical_key": self.logical_key,
                          "schema_version": self.schema_version})

    def as_dict(self) -> dict:
        return {"book_id": self.book_id, "kind": self.kind, "logical_key": self.logical_key,
                "path": str(self.path), "schema_version": self.schema_version,
                "content_digest": self.content_digest,
                "dependency_digest": self.dependency_digest,
                "producer_stage": self.producer_stage,
                "producer_version": self.producer_version,
                "meta": dict(self.meta)}

    @classmethod
    def from_dict(cls, payload: dict) -> "ArtifactRef":
        return cls(book_id=str(payload.get("book_id") or ""),
                   kind=str(payload.get("kind") or ""),
                   logical_key=str(payload.get("logical_key") or ""),
                   path=Path(payload.get("path") or ""),
                   schema_version=str(payload.get("schema_version") or "1.0"),
                   content_digest=payload.get("content_digest"),
                   dependency_digest=payload.get("dependency_digest"),
                   producer_stage=str(payload.get("producer_stage") or ""),
                   producer_version=str(payload.get("producer_version") or ""),
                   meta=dict(payload.get("meta") or {}))


__all__ = ["ArtifactRef"]
