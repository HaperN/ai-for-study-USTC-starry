"""资源清单：**按内容寻址**，重名不覆盖，冲突要么改写要么拒绝。

方案 §12：

> 页面载荷直接从同一个 ScopePlan/ReleaseManifest 装配……不再扫描目录拼章，
> 也不靠字典覆盖解决重复 ID。**重复身份、跨书载荷、同名不同字节资源必须拒绝或
> 按内容哈希重命名并重写引用。**

> 统一 ResourceManifest 枚举正文/图/交互实际需要的 SVG、PNG、scene、字体、CSS、JS
> 及动态依赖。**单 HTML 内联 payload 中的图片与场景，懒加载脚本改从内联资源注册表读取**；
> 不能只处理静态 script/link 标签。

方案 §15 A22 把它变成验收项：

> 两章同资源名不同字节；缺章、重复 ID、跨书 payload →
> **不静默覆盖**；manifest 精确限定 scope，冲突被拒绝或内容寻址改写。

"两章各有一张 `figure.svg`、内容不同"这种事在真实教材里很常见 ——
按文件名放进同一个目录就会互相覆盖，而且**后写的赢、没有任何提示**。
所以这里对每个资源的**逻辑名**做内容寻址：内容不同就得到不同的**物理名**（带内容哈希），
引用在装配时被重写。内容相同则自然合并（省体积）。

跨书载荷单独拦一道：`book_id` 不一致直接拒绝 —— 那说明装配时读错了书。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..core.digest import digest_bytes, digest_file
from ..errors import ContractError
from ..core.resource_files import INVALID_PATH_SEGMENTS
from ..blocks.visual.result import (RESOURCE_KINDS as ANIMATION_KINDS,
                                    REQUIRED_FOR_FULFILLED as ANIMATION_REQUIRED,
                                    STATUSES as ANIMATION_STATUSES)

#: 资源种类（§12 点名的那些）
KINDS = ("svg", "png", "scene", "font", "css", "js", "glb", "data")

#: 内联进单 HTML 的种类（图片、场景、样式、脚本）
INLINE_KINDS = ("svg", "png", "scene", "css", "js", "font", "data")

# ── §5.3 `AnimationResult.resources[]` 的种类（**冻结契约**，不许扩不许改）
#:
#: 资源一律**独立相对路径文件**：MP4／PNG／VTT／文字稿各自成文件。
#: 把 base64 视频塞进单个 HTML 是被计划明确禁止的形态 —— 它会让"资源级缓存"
#: 和"按 Range 取一段给播放器"同时失去意义。

#: 契约要求 `fulfilled` 时必须齐的四种（keyframe 按步骤可以有多个，不在这里要求）

#: 后缀 → MIME。**不靠 mimetypes.guess_type**：本机 /etc/mime.types 里
#: 没有 `.vtt` 与 `.mp4` 的话，视频会被当成 `application/octet-stream` 发出去。
ANIMATION_MIMES = {
    ".wav":"audio/wav",
    ".mp4": "video/mp4",
    ".png": "image/png",
    ".vtt": "text/vtt; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}

#: 分块读取的默认块大小。**不许** `read_bytes()` 整段吞视频。
DEFAULT_CHUNK_BYTES = 1 << 16

#: 播放器允许的媒体来源。只放 `'self'`：
#: 不放 `blob:`（会把任意脚本造的内存对象变成"内容"），不放 `data:`（那是内联视频），
#: 不放 `*`（会允许外部媒体）。
MEDIA_SRC_POLICY = ("'self'",)


def mime_for(relative_path: str | PurePosixPath) -> str:
    """按**后缀**定 MIME（`.vtt`／`.mp4` 在多数机器上 guess 不出来）。"""
    suffix = PurePosixPath(str(relative_path)).suffix.lower()
    if suffix in ANIMATION_MIMES:
        return ANIMATION_MIMES[suffix]
    from mimetypes import guess_type
    return guess_type(str(relative_path))[0] or "application/octet-stream"


def check_relative_path(value: str, *, what: str = "资源路径") -> str:
    """相对路径安全校验（**一处定义**，登记、复制、服务三处共用）。

    拒绝：空、绝对路径、`..`、`.`、盘符、反斜杠、连续斜杠、隐藏段。
    注意：这里只做**字符串**层面的判断；符号链接逃逸要靠 `Path.resolve()`，
    见 `_assert_no_symlink_escape()`。
    """
    text = str(value or "").strip()
    if not text:
        raise ContractError(f"{what}不能为空")
    if text.startswith(("/", "\\")) or ":" in text or "\\" in text:
        raise ContractError(
            f"{what}必须是安全相对路径，实际 {text!r}：绝对路径／盘符／反斜杠一律拒绝"
            f"（§5.3：资源是独立相对路径文件）")
    parts = text.split("/")
    if any(part in INVALID_PATH_SEGMENTS for part in parts):
        raise ContractError(
            f"{what}必须是安全相对路径，实际 {text!r}：`..`／空段／`.` 一律拒绝"
            f"（§5.3：资源是独立相对路径文件）")
    if any(part.startswith(".") for part in parts):
        raise ContractError(f"{what}不允许隐藏段（以 . 开头的路径），实际 {text!r}")
    return text


def assert_contained(path: Path, *, root: Path, what: str) -> Path:
    """落点必须在治理目录内：**先展开符号链接再看包含关系**。

    只查字符串 `..` 是不够的 —— `ln -s /etc delivery/media` 之后，
    `delivery/media/poster.png` 字符串上完全合法，却写到了治理目录之外。
    所以：`path.resolve()` 必须仍在 `root.resolve()` 之内。
    """
    resolved_root = Path(root).resolve()
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ContractError(
            f"{what}解析后落在治理目录之外：{path} → {resolved}（root={resolved_root}）。"
            f"路径穿越／symlink 逃逸必须拒绝。")
    return resolved


def assert_source_is_not_a_symlink(path: Path, *, what: str) -> None:
    """来源不许是符号链接。

    交付要的是"这一件资源的内容"，不是一个指向别处的指针：指针可以在复制之后被改，
    于是清单 hash 记录的是**当时**那一眼的内容，交付出去的是另一样东西。
    """
    if Path(path).is_symlink():
        raise ContractError(
            f"{what}是符号链接（→ {Path(path).resolve()}）：资源来源必须是治理目录内"
            f"的真实文件，不许用 symlink 逃逸。")



def iter_chunks(path: str | Path, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES,
                start: int = 0, length: int | None = None):
    """**分块**读文件（磁盘上的媒体不许整段进内存）。

    `start`／`length` 给 Range 用；不给就是整段，但仍按块 yield。
    """
    if chunk_bytes <= 0:
        raise ContractError("chunk_bytes 必须为正")
    remaining = length
    with Path(path).open("rb") as handle:
        if start:
            handle.seek(start)
        while True:
            want = chunk_bytes if remaining is None else min(chunk_bytes, remaining)
            if want <= 0:
                break
            block = handle.read(want)
            if not block:
                break
            if remaining is not None:
                remaining -= len(block)
            yield block


def stream_digest(path: str | Path, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> str:
    """分块取内容指纹（与 `digest_file` 同料，但块大小可控、便于断言分块行为）。"""
    hasher = hashlib.sha256()
    for block in iter_chunks(path, chunk_bytes=chunk_bytes):
        hasher.update(block)
    return "sha256:" + hasher.hexdigest()


def parse_range_header(value: str | None) -> tuple[int, int | None] | None:
    """解析**单** Range（RFC 9110）。返回 `None` = 按"忽略 Range"处理。

    返回 `(start, end)`：
      * `(0, 9)`       闭区间
      * `(100, None)`  开放尾
      * `(-50, None)`  后缀（最后 50 字节）

    不支持的形态一律 `None`（调用方返回 200 全量）：
      * 多范围 `bytes=0-9,20-29` —— **本层不实现 multipart**，按已声明策略忽略
      * 非 `bytes=` 单位、语法不合法
    """
    if value is None:
        raise TypeError("parse_range_header 只接受「有 Range 头」的情况；无头请先判断")
    text = str(value).strip()
    if not text:
        return None
    if not text.lower().startswith("bytes="):
        return None
    spec = text[len("bytes="):].strip()
    if "," in spec:                     # 多范围 → 忽略（不伪造 206）
        return None
    import re
    match = re.fullmatch(r"(\d*)-(\d*)", spec.replace(" ", ""))
    if not match:
        return None
    left, right = match.group(1), match.group(2)
    if not left and not right:
        return None
    if not left:                        # 后缀：bytes=-N（N=0 不满足）
        size = int(right)
        return (-size, None) if size > 0 else None
    start = int(left)
    if not right:                       # 开放尾
        return (start, None)
    end = int(right)
    if end < start:
        return None
    return (start, end)


def range_for_response(span: tuple[int, int | None], total_bytes: int
                       ) -> tuple[int, int] | None:
    """把解析出的 Range 落到具体字节区间；`None` = 416（不满足）。"""
    start, end = span
    if total_bytes <= 0:
        return None
    if start < 0:                       # 后缀
        want = -start
        if want <= 0:
            return None
        begin = max(0, total_bytes - want)
        return (begin, total_bytes - 1)
    if start >= total_bytes:
        return None
    last = total_bytes - 1 if end is None else min(int(end), total_bytes - 1)
    if last < start:
        return None
    return (start, last)



@dataclass(frozen=True)
class Resource:
    """一件资源。`logical_name` 是它**在书里的名字**，`path` 是物理落点。"""
    logical_name: str
    kind: str
    path: str                      # **交付时的物理名**（相对交付根）
    book_id: str
    sha256: str
    bytes: int = 0
    scope_key: str = ""
    #: 内容**从哪来**。`verify()` 靠它核对内容摘要 ——
    #: 只记 `path` 的话，"清单里那个名字"和"盘上那份内容"之间就断了联系，
    #: 于是核对只能变成"文件在不在"（那正是 §11 禁止的判据）。
    source: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ContractError(f"资源种类必须是 {KINDS} 之一，实际 {self.kind!r}")
        if not self.sha256.startswith("sha256:"):
            raise ContractError(f"{self.logical_name}: 资源必须带内容指纹")

    @property
    def is_inline(self) -> bool:
        return self.kind in INLINE_KINDS

    def as_dict(self) -> dict:
        return {"logical_name": self.logical_name, "kind": self.kind, "path": self.path,
                "book_id": self.book_id, "sha256": self.sha256, "bytes": self.bytes,
                "scope_key": self.scope_key, "source": self.source,
                "inline": self.is_inline}


@dataclass
class ResourceManifest:
    """一次交付涉及的**全部**资源。装配载荷与打包都只认它。"""
    book_id: str
    resources: dict[str, Resource] = field(default_factory=dict)   # path → Resource
    #: 逻辑名 → [物理名, ...]。同名同内容会合并成一个；同名不同内容会有多个。
    by_logical: dict[str, list[str]] = field(default_factory=dict)
    renames: dict[str, str] = field(default_factory=dict)          # 原逻辑名 → 改写后的引用
    conflicts: list[dict] = field(default_factory=list)

    # ── 加
    def add(self, *, logical_name: str, kind: str, source: Path | str, book_id: str,
            scope_key: str = "", content: bytes | None = None) -> Resource:
        """登记一件资源。**同名不同字节会被改写（内容寻址），不覆盖。**"""
        logical = PurePosixPath(logical_name)
        if (not logical_name or logical.is_absolute() or ".." in logical.parts
                or "\\" in logical_name or ":" in logical_name):
            raise ContractError(f"资源路径必须是安全相对路径：{logical_name!r}")
        if book_id != self.book_id:
            raise ContractError(
                f"跨书资源：manifest 属于 {self.book_id!r}，却收到 {book_id!r} 的资源 "
                f"{logical_name!r}。方案 §12：跨书载荷必须拒绝。")
        if content is None:
            path = Path(source)
            if not path.is_file():
                raise ContractError(f"资源不在盘上：{path}")
            content = path.read_bytes()
        sha = digest_bytes(content)
        suffix = Path(logical_name).suffix or f".{kind}"
        stem = Path(logical_name).stem or kind
        existing = self.by_logical.get(logical_name) or []
        # 同内容 → 复用同一个物理名（省体积，也让"两章同一张图"自然合并）
        for physical in existing:
            if self.resources[physical].sha256 == sha:
                return self.resources[physical]
        if not existing:
            physical = logical_name
        else:
            # 同名不同字节 → **内容寻址改名**，并记下引用改写
            physical = str(logical.parent / f"{stem}-{sha.split(':')[1][:8]}{suffix}")
            self.conflicts.append({
                "logical_name": logical_name, "kind": kind,
                "existing": list(existing), "renamed_to": physical,
                "why": "同名不同字节 —— 方案 §12 要求按内容哈希重命名并重写引用，不许字典覆盖"})
        resource = Resource(logical_name=logical_name, kind=kind, path=physical,
                            book_id=book_id, sha256=sha, bytes=len(content),
                            scope_key=scope_key, source=str(source))
        self.resources[physical] = resource
        self.by_logical.setdefault(logical_name, []).append(physical)
        self.renames.setdefault(logical_name, physical)
        return resource

    def add_file(self, *, logical_name: str, kind: str, source: Path, book_id: str,
                 scope_key: str = "") -> Resource:
        return self.add(logical_name=logical_name, kind=kind, source=source,
                        book_id=book_id, scope_key=scope_key)

    # ── 查
    def resolve(self, logical_name: str) -> str:
        """逻辑名 → 本次交付里该用的物理名。"""
        if logical_name in self.resources:
            return logical_name
        physical = self.renames.get(logical_name)
        if physical:
            return physical
        raise ContractError(f"资源清单里没有 {logical_name!r}（清单只含本次 scope 的资源）")

    def verify(self, base_dir: Path | None = None) -> dict:
        """自检：每个资源的内容都对得上（**文件存在不算**）。

        `base_dir` 给定时按 `base_dir/物理名` 核对（交付目录里）；不给就按来源核对。
        """
        problems: list[str] = []
        for resource in self.resources.values():
            if base_dir is not None:
                path = Path(base_dir) / resource.path
            else:
                path = Path(resource.source or resource.path)
            if not path.is_file():
                problems.append(f"资源不在盘上：{path}")
                continue
            if digest_file(path) != resource.sha256:
                problems.append(f"资源内容与清单不一致：{path}")
        return {"ok": not problems, "problems": problems,
                "counts": {"resources": len(self.resources),
                           "logical_names": len(self.by_logical),
                           "conflicts": len(self.conflicts),
                           "inline": sum(1 for r in self.resources.values() if r.is_inline)}}

    def total_bytes(self) -> int:
        return sum(r.bytes for r in self.resources.values())

    def as_dict(self) -> dict:
        return {"_meta": {"file_type": "resource_manifest", "schema_version": "1.0",
                          "generator": "aiforstudy.delivery.resources",
                          "book_id": self.book_id,
                          "field_notes": {
                              "resources": "物理名 → 资源；**按内容寻址**，同名不同字节会被改名而不是覆盖",
                              "by_logical": "逻辑名 → 物理名（同名不同内容会有多个）",
                              "renames": "引用改写表：装配载荷时按它替换引用",
                              "conflicts": "同名不同字节的逐条记录（**不静默覆盖**）",
                          }},
                "book_id": self.book_id,
                "resources": {k: v.as_dict() for k, v in sorted(self.resources.items())},
                "by_logical": {k: sorted(v) for k, v in sorted(self.by_logical.items())},
                "renames": dict(sorted(self.renames.items())),
                "conflicts": list(self.conflicts),
                "verify": self.verify()}


@dataclass(frozen=True)
class AnimationResource:
    """`AnimationResult.resources[]` 的一条（§5.3 冻结字段）。"""
    kind: str
    relative_path: str
    sha256: str
    bytes: int
    mime: str
    request_id: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ANIMATION_KINDS:
            raise ContractError(
                f"资源 kind 必须是 {ANIMATION_KINDS} 之一，实际 {self.kind!r}"
                f"（§5.3 冻结契约，不许扩展）")
        check_relative_path(self.relative_path,
                            what=f"{self.kind} 的 relative_path")
        if not str(self.sha256).startswith("sha256:"):
            raise ContractError(f"{self.relative_path}: 资源必须带内容指纹（sha256:…）")
        if int(self.bytes) < 0:
            raise ContractError(f"{self.relative_path}: bytes 不能为负")
        if not self.mime:
            raise ContractError(f"{self.relative_path}: 资源必须带 mime")

    def as_dict(self) -> dict:
        return {"kind": self.kind, "relative_path": self.relative_path,
                "sha256": self.sha256, "bytes": int(self.bytes), "mime": self.mime,
                "request_id": self.request_id}


@dataclass
class AnimationManifest:
    """一次交付涉及的**动画媒体资源**清单。

    与 `ResourceManifest` 的分工：那一份管"整份交付的页面资源（按内容寻址）"，
    这一份管"§5.3 说的 mp4/poster/keyframe/captions/transcript"，字段与契约一一对应，
    并且**服务端只认它**：没登记的媒体一律不给。
    """
    book_id: str
    unit_id: str
    entries: list[AnimationResource] = field(default_factory=list)
    #: request_id → 资源下标（重复 request_id 直接拒绝，见 `from_result`）
    by_request: dict[str, list[int]] = field(default_factory=dict)
    schema_version: str = "1.0"

    # ── 构造
    @classmethod
    def from_result(cls, result: dict, *, sources: dict[str, Path] | str | Path,
                    book_id: str, unit_id: str,
                    required: tuple[str, ...] | None = None) -> "AnimationManifest":
        """按 §5.3 校验一份 AnimationResult，并核对**盘上内容**的指纹。

        `sources`：kind → 盘上文件（或一个目录，按 `relative_path` 找）。
        `required`：本条结果**必须齐**的种类（`fulfilled` 默认 `ANIMATION_REQUIRED`）。
        """
        if not isinstance(result, dict):
            raise ContractError("AnimationResult 必须是对象")
        schema = str(result.get("schema_version") or "")
        if schema != "1.0":
            raise ContractError(
                f"AnimationResult.schema_version 必须是 '1.0'（冻结契约），实际 {schema!r}")
        request_id = str(result.get("request_id") or "").strip()
        if not request_id:
            raise ContractError("AnimationResult 缺 request_id：清单无法按请求去重")
        status = str(result.get("status") or "")
        allowed = ANIMATION_STATUSES
        if status not in allowed:
            raise ContractError(f"AnimationResult.status 不在 {sorted(allowed)} 内：{status!r}")

        directory = Path(sources) if isinstance(sources, (str, Path)) and Path(sources).is_dir() else None
        source_map = {} if directory is not None else dict(sources or {})

        manifest = cls(book_id=book_id, unit_id=unit_id)
        manifest.by_request[request_id] = []
        rows = result.get("resources")
        if not isinstance(rows, list):
            raise ContractError("AnimationResult.resources 必须是数组（§5.3）")
        for row in rows:
            if not isinstance(row, dict):
                raise ContractError(f"resources[] 的每一项必须是对象，实际 {type(row).__name__}")
            kind = str(row.get("kind") or "")
            if kind not in ANIMATION_KINDS:
                raise ContractError(
                    f"资源 kind 必须是 {ANIMATION_KINDS} 之一，实际 {kind!r}"
                    f"（§5.3 冻结契约，不许扩展）")
            relative = check_relative_path(str(row.get("relative_path") or ""),
                                           what=f"{kind} 的 relative_path")
            declared = str(row.get("sha256") or "")
            if not declared.startswith("sha256:"):
                raise ContractError(f"{relative}: 资源必须带内容指纹（sha256:…）")
            if directory is not None:
                source = directory / relative
            else:
                source = source_map.get(kind) or source_map.get(relative)
                if source is None:
                    raise ContractError(
                        f"资源 {kind}（{relative}）没有对应的盘上来源；"
                        f"清单里写了而盘上没有就是漏项")
            source = Path(source)
            if not source.is_file():
                raise ContractError(f"资源不在盘上：{source}（{kind}）")
            actual = digest_file(source)
            if actual != declared:
                raise ContractError(
                    f"资源 {relative} 的内容与声明指纹不符：清单 {declared}，实际 {actual}"
                    f"（§11：文件存在不是有效证据）")
            size = source.stat().st_size
            declared_size = row.get("bytes")
            if declared_size is not None and int(declared_size) != size:
                raise ContractError(
                    f"资源 {relative} 的 bytes 与盘上不符：清单 {declared_size}，实际 {size}")
            mime = str(row.get("mime") or mime_for(relative))
            entry = AnimationResource(kind=kind, relative_path=relative, sha256=actual,
                                      bytes=size, mime=mime, request_id=request_id)
            manifest.by_request[request_id].append(len(manifest.entries))
            manifest.entries.append(entry)

        if status == "fulfilled":
            need = ANIMATION_REQUIRED if required is None else tuple(required)
            present = {entry.kind for entry in manifest.entries}
            missing = [kind for kind in need if kind not in present]
            if missing:
                raise ContractError(
                    f"AnimationResult 声称 fulfilled，却缺 {missing}："
                    f"（§5.3 fulfilled 必须完成本阶段数学／画面／文件检查）")
        return manifest

    @classmethod
    def from_dict(cls, data: dict) -> "AnimationManifest":
        manifest = cls(book_id=str(data.get("book_id") or ""),
                       unit_id=str(data.get("unit_id") or ""),
                       schema_version=str(data.get("schema_version") or "1.0"))
        for row in data.get("resources") or []:
            entry = AnimationResource(kind=str(row.get("kind") or ""),
                                      relative_path=str(row.get("relative_path") or ""),
                                      sha256=str(row.get("sha256") or ""),
                                      bytes=int(row.get("bytes") or 0),
                                      mime=str(row.get("mime") or ""),
                                      request_id=str(row.get("request_id") or ""))
            manifest.entries.append(entry)
            manifest.by_request.setdefault(entry.request_id, []).append(len(manifest.entries) - 1)
        return manifest

    # ── 查
    def request_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.by_request))

    def resources_for_unit(self, unit_id: str) -> tuple[AnimationResource, ...]:
        if unit_id and unit_id != self.unit_id:
            return ()
        return tuple(self.entries)

    def merge(self, other: tuple["AnimationManifest", ...]) -> "AnimationManifest":
        """把多份清单并进本清单；**重复 request_id 一律拒绝**。"""
        for item in other:
            clash = set(item.by_request) & set(self.by_request)
            if clash:
                raise ContractError(
                    f"重复 request_id {sorted(clash)}：同一请求来了两份结果，"
                    f"谁覆盖谁都是错（§5.3 资源清单必须唯一）")
            if item.book_id and self.book_id and item.book_id != self.book_id:
                raise ContractError(f"跨书资源：{item.book_id!r} vs {self.book_id!r}")
            for entry in item.entries:
                self.by_request.setdefault(entry.request_id, []).append(len(self.entries))
                self.entries.append(entry)
        return self

    def missing_kinds(self, target_root: Path,
                      required: tuple[str, ...] = ANIMATION_REQUIRED) -> list[str]:
        """契约要求但**盘上缺失或对不上**的种类（播放器据此显示错误 + 文字稿）。"""
        present = {entry.kind for entry in self.entries
                   if (Path(target_root) / entry.relative_path).is_file()
                   and stream_digest(Path(target_root) / entry.relative_path) == entry.sha256}
        return [kind for kind in required if kind not in present]

    def verify(self, target_root: Path) -> dict:
        """逐件核对**内容**（文件存在不算）：与 `ResourceManifest.verify` 同一条判据。"""
        problems: list[str] = []
        for entry in self.entries:
            path = Path(target_root) / entry.relative_path
            if not path.is_file():
                problems.append(f"{entry.kind} 不在盘上：{path}")
                continue
            if stream_digest(path) != entry.sha256:
                problems.append(f"{entry.kind} 内容与清单不一致：{path}")
        return {"ok": not problems, "problems": problems,
                "counts": {"resources": len(self.entries),
                           "requests": len(self.by_request),
                           "kinds": sorted({entry.kind for entry in self.entries})}}

    def require_kinds(self, target_root: Path,
                      required: tuple[str, ...] = ANIMATION_REQUIRED) -> None:
        missing = self.missing_kinds(target_root, required=required)
        if missing:
            raise ContractError(
                f"交付缺 {missing}：缺件不许静默交付，也不许用别的资源顶替")

    def inline_base64_bytes(self) -> int:
        """页面载荷里内联的 base64 字节数。**必须是 0**（§5.3 资源是独立文件）。"""
        total = 0
        for entry in self.entries:
            if str(entry.mime).startswith("data:") or "base64" in str(entry.relative_path):
                total += int(entry.bytes)
        return total

    # ── 载荷（播放器只读这一个；**不含任何管理 API 地址**）
    def payload_for(self, unit_id: str) -> dict:
        entries = self.resources_for_unit(unit_id)
        by_kind: dict[str, dict] = {}
        for entry in entries:
            by_kind.setdefault(entry.kind, entry.as_dict())
        return {"schema_version": self.schema_version, "unit_id": self.unit_id,
                "book_id": self.book_id, "request_id": self.request_ids()[0]
                if len(self.by_request) == 1 else "",
                "request_ids": list(self.request_ids()),
                "resources": by_kind,
                "media_src": list(MEDIA_SRC_POLICY),
                "endpoints": {"management_api": None, "origin": None}}

    def as_dict(self) -> dict:
        return {"_meta": {"file_type": "animation_manifest", "schema_version": "1.0",
                          "generator": "aiforstudy.delivery.resources",
                          "field_notes": {
                              "resources": "§5.3 的 mp4/poster/keyframe/captions/transcript，"
                                           "一律独立相对路径文件",
                              "by_request": "request_id → 资源下标；重复 request_id 拒绝"}},
                "book_id": self.book_id, "unit_id": self.unit_id,
                "schema_version": self.schema_version,
                "resources": [entry.as_dict() for entry in self.entries],
                "by_request": {key: list(value) for key, value in sorted(self.by_request.items())}}


def collect_animation_resources(*, results: list[dict], target_root: str | Path,
                                sources: dict[str, Path] | str | Path, book_id: str,
                                unit_id: str,
                                required: tuple[str, ...] | None = None) -> AnimationManifest:
    """把一批 AnimationResult 的媒体资源**各自成文件**复制到交付目录并登记。

    顺序很要紧：**先全部校验、再动手复制** ——
    踩到 symlink 逃逸／hash 不符时，目标目录里不该留下半份产物。
    """
    target = Path(target_root)
    manifests = []
    for result in results:
        manifests.append(AnimationManifest.from_result(
            result, sources=sources, book_id=book_id, unit_id=unit_id, required=required))
    combined = AnimationManifest(book_id=book_id, unit_id=unit_id)
    combined.merge(tuple(manifests))

    directory = sources if isinstance(sources, (str, Path)) and Path(sources).is_dir() else None
    source_map = {} if directory is not None else dict(sources or {})

    # **先全部校验、再动手复制**：踩到逃逸／hash 不符时目标目录里不留半份产物。
    plan: list[tuple[AnimationResource, Path, Path]] = []
    for entry in combined.entries:
        if directory is not None:
            origin = Path(directory) / entry.relative_path
        else:
            origin = Path(source_map.get(entry.kind) or source_map[entry.relative_path])
        assert_source_is_not_a_symlink(origin, what=f"来源 {entry.kind}（{origin}）")
        if not origin.is_file():
            raise ContractError(f"来源不在盘上：{origin}（{entry.kind}）")
        destination = target / entry.relative_path
        # 落点目录链上任何一段是 symlink 都要先展开核对（含 target 自身）
        assert_contained(destination if destination.exists() else destination.parent,
                         root=target, what=f"落点 {entry.kind}")
        plan.append((entry, origin, destination))

    target.mkdir(parents=True, exist_ok=True)
    assert_contained(target, root=target, what="交付目录")
    for entry, origin, destination in plan:
        if destination.exists() or destination.is_symlink():
            assert_contained(destination, root=target, what=f"落点 {entry.kind}")
            if destination.is_file() and stream_digest(destination) == entry.sha256:
                continue                     # 同内容复用（内容寻址，不重复写）
            raise ContractError(
                f"落点已有不同内容：{destination}（不许静默覆盖；"
                f"同名不同字节要么改名要么拒绝）")
        destination.parent.mkdir(parents=True, exist_ok=True)
        assert_contained(destination, root=target, what=f"落点 {entry.kind}")
        with destination.open("wb") as handle:
            for block in iter_chunks(origin):
                handle.write(block)
        if stream_digest(destination) != entry.sha256:
            destination.unlink(missing_ok=True)
            raise ContractError(f"复制后内容对不上：{destination}")
    return combined


__all__ = ["KINDS", "INLINE_KINDS", "Resource", "ResourceManifest",
           "ANIMATION_KINDS", "ANIMATION_REQUIRED", "ANIMATION_MIMES", "MEDIA_SRC_POLICY",
           "DEFAULT_CHUNK_BYTES", "mime_for", "check_relative_path", "iter_chunks",
           "stream_digest", "parse_range_header", "range_for_response",
           "AnimationResource", "AnimationManifest", "collect_animation_resources"]
