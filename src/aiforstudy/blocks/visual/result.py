"""`AnimationResult`：媒体结果的**冻结契约**（§5.3，schema 1.0）。

## 为什么这个模块必须存在

T07 的适配器产出它、T08 的画面审核消费它、T09 的播放器按它读 manifest。
如果三方各自用自己那套 dict 键，**没有任何一处会报错** —— 播放器只是"读不到资源"、
审核只是"没看到关键帧"，而报告上一切正常。所以这里把 §5.3 的字段变成**构造期断言**。

## 一条最要紧的规则：`fulfilled` 不许伪造

`fulfilled` 的含义是"本阶段的**数学、画面、文件**检查都过了"。所以它在构造期就要求：

* `math_report.status == "pass"`（独立数学检查过了）；
* `frame_report` 存在且不是 `pass` 以外的空壳；
* 四类资源齐全（mp4／poster／captions／transcript）＋ 至少一张关键帧；
* `technical_report.ffmpeg_decode_ok` 为真（视频能完整解码）。

任何一条不满足 → 只能是 `needs_review`／`failed`／`blocked`。
"画面评审可以暂 needs_review，但**不能伪 fulfilled**"就是这条。

`not_applicable` 与 `StageResult` 同一规矩：**必须**带 policy + evidence，
不许拿"缺工具／没运行"冒充不适用（§9）。

## 与 `checks/offline.py` 的分工

本模块只判**结构与语义的一致性**（键、枚举、路径安全、fulfilled 的前置条件），
不碰文件系统；"文件在不在、hash 对不对、视频参数对不对"由
`checks.offline.check_animation_resources` 负责（它要读真实字节，还要调用 ffprobe）。
`validate_against_files()` 把两边接起来，供 T08/T10 一次跑完。
"""
from __future__ import annotations
from ...core.resource_files import safe_relative_path as _safe_relative

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.digest import digest_file
from ...errors import ContractError

#: 冻结的 schema 版本（§5.3）
SCHEMA_VERSION = "1.0"

#: 终态枚举（§5.3）
STATUSES = ("fulfilled", "needs_review", "blocked", "failed", "cancelled",
            "not_applicable")

#: 资源种类（§5.3）
RESOURCE_KINDS = ("mp4", "poster", "keyframe", "captions", "transcript", "audio", "narration")

#: 资源种类 → 冻结的 MIME。字幕必须带 charset，否则中文按 latin-1 解成乱码。
RESOURCE_MIMES = {"mp4": "video/mp4", "poster": "image/png", "keyframe": "image/png",
                  "captions": "text/vtt; charset=utf-8",
                  "transcript": "text/plain; charset=utf-8", "audio":"audio/wav",
                  "narration":"application/json; charset=utf-8"}

#: `fulfilled` 必须齐全的资源种类（关键帧另有"至少一张"的要求）
REQUIRED_FOR_FULFILLED = ("mp4", "poster", "captions", "transcript")

_RESOURCE_FIELDS = frozenset({"kind", "relative_path", "sha256", "bytes", "mime",
                              "timestamp", "step_id", "phase", "action", "object_id", "target_object_id", "goal_id", "claim_ids", "object_ids", "observation"})
_RESULT_FIELDS = frozenset({"schema_version", "request_id", "status", "spec_hash",
                            "compiler_hash", "environment_hash", "source_binding",
                            "resources", "steps", "math_report", "frame_report",
                            "technical_report", "policy", "evidence", "base_dir"})


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)





@dataclass(frozen=True)
class AnimationResource:
    kind: str
    relative_path: str
    sha256: str
    bytes: int
    mime: str
    timestamp: float | None = None
    step_id: str | None = None
    phase: str | None = None
    action: str | None = None
    object_id: str | None = None
    target_object_id: str | None = None
    goal_id: str | None = None
    claim_ids: tuple[str, ...] | None = None
    object_ids: tuple[str, ...] | None = None
    observation: str | None = None

    def __post_init__(self) -> None:
        _require(self.kind in RESOURCE_KINDS,
                 f"资源 kind 必须是 {list(RESOURCE_KINDS)}，实际 {self.kind!r}")
        _require(_safe_relative(self.relative_path),
                 f"资源路径 {self.relative_path!r} 不是安全相对路径"
                 f"（不许绝对路径，也不许 `..` 逃逸）")
        _require(bool(str(self.sha256 or "").strip()),
                 f"{self.kind} 缺 sha256 —— 没有哈希就无法证明播放的是哪一版")
        _require(isinstance(self.bytes, int) and self.bytes >= 0,
                 f"{self.kind} 的 bytes 必须是非负整数，实际 {self.bytes!r}")
        if self.phase is not None:
            _require(self.kind=="keyframe" and self.phase in ("before","during","after","observe"),"keyframe phase 无效")
            _require(self.timestamp is not None and bool(self.step_id),"phase 证据需要实际 timestamp/step_id")
        if self.action is not None:
            _require(self.action in ("reveal","hide","highlight","transform"),"frame action 无效")
        for key in ("claim_ids","object_ids"):
            ids=getattr(self,key)
            if ids is not None:
                _require(isinstance(ids,(list,tuple)) and all(isinstance(i,str) and i for i in ids),key+" 必须为标识符数组")
                object.__setattr__(self,key,tuple(ids))
        want = RESOURCE_MIMES[self.kind]
        _require(self.mime == want,
                 f"{self.kind} 的 MIME 必须是 {want!r}，实际 {self.mime!r}")

    def as_dict(self) -> dict:
        out = {"kind": self.kind, "relative_path": self.relative_path,
               "sha256": self.sha256, "bytes": self.bytes, "mime": self.mime}
        if self.timestamp is not None:
            out["timestamp"] = self.timestamp
        if self.step_id is not None:
            out["step_id"] = self.step_id
        for key in ("phase","action","object_id","target_object_id","goal_id","claim_ids","object_ids","observation"):
            value=getattr(self,key)
            if value is not None: out[key]=list(value) if isinstance(value,tuple) else value
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "AnimationResource":
        unknown = sorted(set(data) - _RESOURCE_FIELDS)
        _require(not unknown,
                 f"资源里有未知字段 {unknown} —— 契约不静默吞字段")
        return cls(kind=str(data.get("kind") or ""),
                   relative_path=str(data.get("relative_path") or ""),
                   sha256=str(data.get("sha256") or ""),
                   bytes=int(data.get("bytes") or 0), mime=str(data.get("mime") or ""),
                   timestamp=data.get("timestamp"), step_id=data.get("step_id"),
                   **{key:data.get(key) for key in ("phase","action","object_id","target_object_id","goal_id","claim_ids","object_ids","observation")})


@dataclass(frozen=True)
class AnimationResult:
    request_id: str
    status: str
    spec_hash: str
    compiler_hash: str
    environment_hash: str
    resources: tuple[AnimationResource, ...] = ()
    steps: tuple[dict, ...] = ()
    math_report: dict = field(default_factory=dict)
    frame_report: dict | None = None
    technical_report: dict = field(default_factory=dict)
    source_binding: dict = field(default_factory=dict)
    policy: str | None = None
    evidence: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION,
                 f"AnimationResult.schema_version 必须是 {SCHEMA_VERSION!r}，"
                 f"实际 {self.schema_version!r}")
        _require(bool(str(self.request_id or "").strip()), "request_id 必填")
        _require(self.status in STATUSES,
                 f"status 必须是 {list(STATUSES)}，实际 {self.status!r}")
        for name in ("spec_hash", "compiler_hash", "environment_hash"):
            _require(bool(str(getattr(self, name) or "").strip()),
                     f"{name} 必填 —— 没有它就无法判断这个结果属于哪一版规格／编译器／环境")
        object.__setattr__(self, "resources", tuple(self.resources))
        object.__setattr__(self, "steps", tuple(dict(s) for s in self.steps))
        if self.status == "not_applicable":
            _require(self.policy and self.evidence,
                     "not_applicable 必须同时给出 policy 与 evidence —— "
                     "§9 不允许'缺工具/没运行'冒充不适用")
        if self.status == "fulfilled":
            self._assert_fulfilled()

    def _assert_fulfilled(self) -> None:
        """`fulfilled` 的**全部**前置条件。少一条就只能降级，不能自称完成。"""
        kinds = [r.kind for r in self.resources]
        missing = [kind for kind in REQUIRED_FOR_FULFILLED if kind not in kinds]
        _require(not missing,
                 f"status=fulfilled 但缺资源 {missing} —— §5.4 要求 "
                 f"MP4／poster／UTF-8 VTT／文字稿齐全")
        _require("keyframe" in kinds,
                 "status=fulfilled 但一张关键帧都没有：每步检查点缺了，"
                 "实际画面无从复核（V02）")
        _require(str(self.math_report.get("status") or "") == "pass",
                 f"status=fulfilled 但数学报告不是 pass（实际 "
                 f"{self.math_report.get('status')!r}）—— 不许伪 fulfilled")
        frame_status = str((self.frame_report or {}).get("status") or "")
        _require(frame_status == "pass",
                 f"status=fulfilled 但画面报告不是 pass（实际 {frame_status!r}）—— "
                 f"画面评审可以暂 needs_review，但不能伪 fulfilled")
        _require(bool(self.technical_report.get("ffmpeg_decode_ok")),
                 "status=fulfilled 但技术报告没有 ffmpeg_decode_ok=True —— "
                 "文件存在、HTTP 200 都不等于**每一帧都能解出来**")

    @property
    def by_kind(self) -> dict[str, list[AnimationResource]]:
        out: dict[str, list[AnimationResource]] = {}
        for resource in self.resources:
            out.setdefault(resource.kind, []).append(resource)
        return out

    @property
    def is_fulfilled(self) -> bool:
        return self.status == "fulfilled"

    def as_dict(self) -> dict:
        out = {"schema_version": self.schema_version, "request_id": self.request_id,
               "status": self.status, "spec_hash": self.spec_hash,
               "compiler_hash": self.compiler_hash,
               "environment_hash": self.environment_hash,
               "source_binding": dict(self.source_binding),
               "resources": [r.as_dict() for r in self.resources],
               "steps": [dict(s) for s in self.steps],
               "math_report": dict(self.math_report),
               "frame_report": dict(self.frame_report) if self.frame_report else None,
               "technical_report": dict(self.technical_report)}
        if self.policy is not None:
            out["policy"] = self.policy
        if self.evidence is not None:
            out["evidence"] = self.evidence
        return out


def parse_animation_result(data: Any) -> AnimationResult:
    """校验并构造 `AnimationResult`。未知字段一律拒绝（不静默吞）。"""
    if isinstance(data, AnimationResult):
        data = data.as_dict()
    _require(isinstance(data, dict),
             f"AnimationResult 必须是 JSON 对象，实际 {type(data).__name__}")
    unknown = sorted(set(data) - _RESULT_FIELDS)
    _require(not unknown, f"AnimationResult 里有未知字段 {unknown}")
    resources = data.get("resources")
    _require(isinstance(resources, list),
             "resources 必须是列表（可以为空，但必须存在）")
    return AnimationResult(
        schema_version=str(data.get("schema_version") or ""),
        request_id=str(data.get("request_id") or ""),
        status=str(data.get("status") or ""),
        spec_hash=str(data.get("spec_hash") or ""),
        compiler_hash=str(data.get("compiler_hash") or ""),
        environment_hash=str(data.get("environment_hash") or ""),
        resources=tuple(AnimationResource.from_dict(r) for r in resources),
        steps=tuple(data.get("steps") or ()),
        math_report=dict(data.get("math_report") or {}),
        frame_report=data.get("frame_report"),
        technical_report=dict(data.get("technical_report") or {}),
        source_binding=dict(data.get("source_binding") or {}),
        policy=data.get("policy"), evidence=data.get("evidence"))


def collect_resources(output_dir: Path) -> tuple[AnimationResource, ...]:
    """扫一个渲染输出目录，装配资源条目。**哈希对内容算**，不看文件名。"""
    output_dir = Path(output_dir)
    out: list[AnimationResource] = []

    def add(kind: str, path: Path, *, timestamp=None, step_id=None) -> None:
        out.append(AnimationResource(kind=kind, relative_path=str(path.relative_to(output_dir)),
                                     sha256=digest_file(path),
                                     bytes=path.stat().st_size, mime=RESOURCE_MIMES[kind],
                                     timestamp=timestamp, step_id=step_id))

    for name, kind in (("video.mp4", "mp4"), ("poster.png", "poster"),
                       ("captions.vtt", "captions"), ("transcript.txt", "transcript")):
        path = output_dir / name
        if path.is_file():
            add(kind, path)
    for path in sorted((output_dir / "keyframes").glob("*.png")) if (output_dir / "keyframes").is_dir() else ():
        # 文件名约定 `<step_id>-<index>.png`（T07 的产物就是这样）；解析不出就只记文件
        stem = path.stem
        step_id, _, index = stem.partition("-")
        add("keyframe", path, step_id=step_id or None,
            timestamp=None if index == "" else None)
    # Voice assets are optional; old silent results retain their original five kinds.
    for path in sorted(output_dir.glob('*.wav')):
        add('audio',path)
    receipts=set(output_dir.glob('*.narration.json'))
    for path in (output_dir/'narration.json',output_dir/'source'/'narration.json'):
        if path.is_file(): receipts.add(path)
    for path in sorted(receipts):
        add('narration',path)
    voice_dir=output_dir/'source'/'narration'
    for path in sorted(voice_dir.glob('*.wav')) if voice_dir.is_dir() else ():
        add('audio',path)
    return tuple(out)


def validate_against_files(result: AnimationResult, base_dir: Path) -> list[str]:
    """核对每个资源**在磁盘上真的存在、字节数与哈希都对得上**。返回问题列表。

    与 `checks.offline.check_animation_resources` 的区别：这里只做"清单 vs 文件"，
    不调用 ffprobe／ffmpeg（那一步更贵，留给交付检查）。
    """
    base_dir = Path(base_dir)
    problems: list[str] = []
    for resource in result.resources:
        path = base_dir / resource.relative_path
        if not path.is_file():
            problems.append(f"{resource.kind} 缺文件 {resource.relative_path}")
            continue
        if path.stat().st_size != resource.bytes:
            problems.append(f"{resource.kind} 字节数不符"
                            f"（声明 {resource.bytes}，实际 {path.stat().st_size}）")
        actual = digest_file(path)
        if actual != resource.sha256:
            problems.append(f"{resource.kind} 的 sha256 与文件不符（内容被换过或已过期）")
    return problems


def load_animation_result(path: Path) -> AnimationResult:
    path = Path(path)
    _require(path.is_file(), f"缺 AnimationResult 文件：{path}")
    try:
        return parse_animation_result(json.loads(path.read_text(encoding="utf-8")))
    except ValueError as exc:
        raise ContractError(f"{path} 不是合法 JSON：{exc}") from exc


__all__ = ["SCHEMA_VERSION", "STATUSES", "RESOURCE_KINDS", "RESOURCE_MIMES",
           "REQUIRED_FOR_FULFILLED", "AnimationResource", "AnimationResult",
           "parse_animation_result", "collect_resources", "validate_against_files",
           "load_animation_result"]
