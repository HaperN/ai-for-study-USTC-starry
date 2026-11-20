"""确定性正文归一化及历史来源核对。

生产 normalize() 使用包内 vendor/normalizer，来源与文件哈希记录在其
provenance.json；不依赖 upstream CLI 安装。inspect()/verify() 保留对历史
stem_runner/courseforge 安装载荷的只读来源核对，不能代表当前生产入口。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..core.workspace import Workspace
from ..errors import CapabilityError
from .artifacts import Artifact, SourceLock, payload_digest

DIST_NAME = "undergraduate-fitted-exam-system"
OWNER_MODULE = "stem_runner"
UPSTREAM = "courseforge"


def normalize(source: str | Path, project: str | Path) -> dict:
    """Run the frozen production normalizer without importing the upstream CLI.

    inspect/verify below remain read-only comparisons to the historical source.
    The runtime's exact copied inputs and transformations are in vendor provenance.
    """
    from ..vendor.normalizer.source_normalization import normalize_logical_book
    result = normalize_logical_book((Path(source),), Path(project))
    if not result.get('passed'):
        raise CapabilityError(f'正文归一化未通过：{result}')
    return result


def _direct_url(payload: dict) -> str:
    """从 dist-info 的 `direct_url.json` 取安装时记录的来源哈希。

    ⚠️ 这个字段**不存在**时不要伪造：老版本 pip 或从 sdist 安装时它没有 archive hash。
    """
    info = (payload or {}).get("archive_info") or {}
    hashes = info.get("hashes") or {}
    return str(hashes.get("sha256") or info.get("hash", "").replace("sha256=", "") or "")


def inspect(workspace: Workspace, *, source_wheel: Path | None = None) -> Artifact:
    """探测已安装的 normalizer，并与来源 wheel 比对。"""
    try:
        import importlib.metadata as md
        dist = md.distribution(DIST_NAME)
    except Exception as exc:                    # noqa: BLE001
        return Artifact(kind="python-dist", name=DIST_NAME, payload="",
                        notes=(f"取不到已安装的 {DIST_NAME}：{type(exc).__name__}: {exc}",))

    version = dist.version
    dist_root = Path(dist._path) if getattr(dist, "_path", None) else None
    site = dist_root.parent if dist_root else None

    notes: list[str] = []
    payload = ""
    files = 0
    if site is not None:
        module_dir = site / OWNER_MODULE
        if module_dir.is_dir():
            payload, files = payload_digest(module_dir)
        else:
            notes.append(f"site-packages 里没有 {OWNER_MODULE}/ 目录，无法取 payload 指纹")

    recorded = ""
    if dist_root is not None:
        du = dist_root / "direct_url.json"
        if du.is_file():
            try:
                recorded = _direct_url(json.loads(du.read_text(encoding="utf-8")))
            except ValueError:
                notes.append("direct_url.json 不是合法 JSON")

    source_hash = ""
    origin = ""
    if source_wheel is not None and Path(source_wheel).is_file():
        from ..core.digest import digest_file
        source_hash = digest_file(source_wheel)
        origin = str(source_wheel)
    else:
        notes.append("没有给出本地来源 wheel（--source-wheel），只核对了已安装内容")

    upstream_recorded = ""
    try:
        import importlib.metadata as md2
        up = md2.distribution(UPSTREAM)
        notes.append(f"上游依赖 {UPSTREAM}=={up.version}")
        up_root = Path(up._path) if getattr(up, "_path", None) else None
        if up_root is not None and (up_root / "direct_url.json").is_file():
            upstream_recorded = _direct_url(json.loads(
                (up_root / "direct_url.json").read_text(encoding="utf-8")))
    except Exception as exc:                    # noqa: BLE001
        notes.append(f"取不到上游 {UPSTREAM}：{type(exc).__name__}: {exc}")

    if upstream_recorded:
        notes.append(f"上游 {UPSTREAM} 安装时来源哈希 {upstream_recorded}")
    return Artifact(kind="python-dist", name=DIST_NAME, version=version,
                    path=(site / OWNER_MODULE) if site else None,
                    payload=payload, payload_files=files,
                    source_hash=source_hash, recorded_source_hash=recorded,
                    license="见 dist-info/METADATA（本次未取到独立许可证文件）",
                    origin=origin, notes=tuple(notes))


def verify(workspace: Workspace, lock: SourceLock | None = None,
           *, source_wheel: Path | None = None) -> tuple[Artifact, list[str]]:
    """核对现场与锁。返回 `(现场, 问题列表)`。问题非空即**阻断**（不自动修）。"""
    lock = lock or SourceLock.load(workspace.root / "vendor.lock.json")
    art = inspect(workspace, source_wheel=source_wheel)
    problems: list[str] = []
    expected = lock.expected("normalizer")
    if expected:
        for field_name in ("payload", "version"):
            want = expected.get(field_name)
            got = getattr(art, field_name)
            if want and want != got:
                problems.append(
                    f"normalizer 的 {field_name} 与锁不一致：锁 {want} ≠ 现场 {got}")
        if art.drift and "normalizer" not in lock.accepted_drift:
            problems.append(
                f"normalizer 来源漂移：安装时记录的 wheel 哈希 {art.recorded_source_hash} "
                f"≠ 现盘上的 {art.source_hash}。同一版本号、不同字节 —— "
                f"要在 vendor.lock.json 的 accepted_drift 里写明为什么接受，或换回锁定的那一份。")
    else:
        problems.append("vendor.lock.json 里没有 normalizer 条目 —— 尚无可核对的锁定来源")
    return art, problems


def require_available(workspace: Workspace) -> str:
    """解析出 `stem_runner` 的位置；**没有就明确报错**（不静默退回别的实现）。"""
    try:
        import importlib.util
        spec = importlib.util.find_spec(OWNER_MODULE)
    except (ImportError, ValueError) as exc:
        raise CapabilityError(f"import 不到 {OWNER_MODULE}：{exc}") from exc
    if spec is None or not spec.origin:
        raise CapabilityError(
            f"找不到 {OWNER_MODULE}（应由 {DIST_NAME} 提供）。"
            f"方案 §3：normalizer 必须可按锁定来源重建，缺了要明确阻断，不许换成另一版同名包。")
    return spec.origin


__all__ = ["DIST_NAME", "OWNER_MODULE", "UPSTREAM", "inspect", "verify", "require_available"]
