"""交付仅支持章节 HTML，负责资源与快照成员一致性。

净化责任在渲染层（逐处转义），交付层不重复净化。
新增任何绕过渲染层的 HTML 来源时，必须在此层补净化。
交付层不做组合级复验：检查快照哈希和成员字节，不独立重推导排版或挂载。
旧单 HTML 载荷装配与重复净化模块随未实现格式一并删除（D8/D11/D12）。
"""
from __future__ import annotations

from .publish import (ChapterRef, PublishResult, assert_release_validated, build_release,
                      member_hashes, read_current, rollback, switch_current)
from .resources import INLINE_KINDS, KINDS, Resource, ResourceManifest

__all__ = ["ChapterRef", "PublishResult",
           "assert_release_validated", "build_release", "member_hashes", "read_current",
           "rollback", "switch_current", "INLINE_KINDS", "KINDS", "Resource",
           "ResourceManifest"]
