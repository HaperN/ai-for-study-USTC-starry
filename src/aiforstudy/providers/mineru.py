"""MinerU 适配：**生产只保留 MinerU**，并且**永不回退 OCR**。

方案 §1 已确认的选择：*生产只保留 MinerU，支持已有本地结果或 API 获取；移除旧本地 OCR／
正则推层级生产入口。*

方案 §6 的接通要求：

> MinerU 入库：已有结果核验 PDF/source hash 后复用；API 获取支持分块续传；
> refetch 真正传递到每块；**失败不回退 OCR**。

方案 §11：

> MinerU 分块下载重试同样记录尝试次数，**源哈希不同不得复用旧分块**。

为什么"不回退"要写成代码而不是写在注释里：本次实跑抓到过代价——
漏掉 `--use-api` 时程序安静地走了本地 OCR，公式全丢（`$` 计数 **5**，而 MinerU 路线是
**11 276**）。那一次不是"跑失败了"，是**跑成功了但产物是坏的**，最坏的一种失败。
所以 `decide()` 在两条路都不通时返回 `blocked` 并说明两条路各缺什么，绝不悄悄降级。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.jsonio import read_json
from ..errors import CapabilityError, ConfigError

MINERU_DIR = "mineru"
LAYOUT_MARKERS = ("layout.json", "middle_json.json")
ROUTE_REUSE = "reuse-local"
ROUTE_API = "mineru-api"
ROUTE_BLOCKED = "blocked"


@dataclass(frozen=True)
class RouteDecision:
    route: str
    reason: str
    parse_root: Path
    mineru_dir: Path
    source_hash: str = ""
    recorded_source_hash: str = ""
    requires_network: bool = False
    requires_api_key: bool = False
    evidence: tuple[str, ...] = ()

    @property
    def blocked(self) -> bool:
        return self.route == ROUTE_BLOCKED

    def as_dict(self) -> dict:
        return {"route": self.route, "reason": self.reason,
                "parse_root": str(self.parse_root), "mineru_dir": str(self.mineru_dir),
                "source_hash": self.source_hash,
                "recorded_source_hash": self.recorded_source_hash,
                "requires_network": self.requires_network,
                "requires_api_key": self.requires_api_key,
                "evidence": list(self.evidence)}


def local_result_present(parse_root: Path) -> tuple[bool, Path]:
    mineru = Path(parse_root) / MINERU_DIR
    if not mineru.is_dir():
        return False, mineru
    for marker in LAYOUT_MARKERS:
        if (mineru / marker).is_file():
            return True, mineru
    # 目录在、但没有任何已知契约的文件：**不算"有结果"**
    return False, mineru


def _normalized_hash(value: str) -> str:
    return value.strip().lower().removeprefix('sha256:')


def _source_claims(parse_root: Path) -> list[tuple[str, str]]:
    """Read every source declaration; priority never authorizes a contradiction.

    Current PDF bytes are supplied separately as ``source_hash``. For historical
    records, prefer ingest.source_sha256, then its supported aliases, then the
    original fetch receipt. Absent declarations remain unknown; corrupt present
    records and disagreeing declarations must not become a reusable cache.
    """
    claims: list[tuple[str, str]] = []

    def document(name):
        path = Path(parse_root) / name
        if not path.exists():
            return {}
        try:
            value = read_json(path)
        except ConfigError as exc:
            raise CapabilityError(f'{name} 来源记录不可读取；不得复用旧分块') from exc
        if not isinstance(value, dict):
            raise CapabilityError(f'{name} 来源记录必须是对象；不得复用旧分块')
        return value

    def add(label, value):
        if value is None or value == '':
            return
        if not isinstance(value, str) or not _normalized_hash(value):
            raise CapabilityError(f'{label} 源哈希格式无效；不得复用旧分块')
        claims.append((label, value.strip()))

    ingest = document('ingest.json')
    for key in ('source_sha256', 'pdf_sha256', 'source_hash'):
        add('ingest.json.' + key, ingest.get(key))
    if isinstance(ingest.get('pdf'), dict):
        add('ingest.json.pdf.sha256', ingest['pdf'].get('sha256'))
    fetch = document('mineru/fetch.json')
    meta = fetch.get('_meta', {})
    if not isinstance(meta, dict):
        raise CapabilityError('mineru/fetch.json._meta 来源记录必须是对象；不得复用旧分块')
    add('mineru/fetch.json._meta.source_sha256', meta.get('source_sha256'))
    if len({_normalized_hash(value) for _, value in claims}) > 1:
        raise CapabilityError('源哈希声明冲突（' + ', '.join(label for label, _ in claims) +
                              '）；不得复用旧分块，请核对来源或显式 refresh 重取')
    return claims


def recorded_source_hash(parse_root: Path) -> str:
    """Return the preferred consistent claim; missing stays empty, conflicts fail."""
    claims = _source_claims(parse_root)
    return claims[0][1] if claims else ''


def resolve_api_token(token: str | None = None, *, secret_paths=()) -> tuple[str, str]:
    """Shared credential resolution for preflight and the actual fetch client."""
    import os
    if token and token.strip():
        return token.strip(), 'explicit'
    names = ('MINERU_API_TOKEN', 'MINERU_API_KEY', 'MINERU_TOKEN')
    for name in names:
        value = os.environ.get(name, '').strip()
        if value:
            return value, 'env:' + name
    for path in secret_paths:
        path = Path(path)
        if not path.is_file():
            continue
        for line in path.read_text(encoding='utf-8').splitlines():
            name, _, value = line.strip().partition('=')
            value = value.strip().strip('"').strip("'")
            if name.strip() in names and value:
                return value, f'file:{path}#{name.strip()}'
    return '', ''


def has_api_credentials(workspace) -> tuple[bool, str]:
    """Check usable credentials, not merely the existence of a secrets file."""
    value, ref = resolve_api_token(secret_paths=(workspace.root / '.secrets/mineru.env',))
    return bool(value), ref or '环境变量与 .secrets/mineru.env 里都没有 MinerU 凭据'


def decide(workspace, parse_root: Path, *, source_hash: str = "",
           refresh: bool = False, allow_api: bool = True) -> RouteDecision:
    """决定这次走哪条路。**只做判定，不下载、不写产物。**"""
    parse_root = Path(parse_root)
    present, mineru_dir = local_result_present(parse_root)
    evidence: list[str] = []
    recorded = ''
    try:
        claims = _source_claims(parse_root)
        recorded = claims[0][1] if claims else ''
    except CapabilityError as exc:
        if present and not refresh:
            return RouteDecision(route=ROUTE_BLOCKED, reason=str(exc),
                                 parse_root=parse_root, mineru_dir=mineru_dir,
                                 source_hash=source_hash)
        # Refresh/new acquisition does not reuse the invalid old records.
        claims = []
        evidence.append('旧来源记录无效，本次仅允许重新获取：' + str(exc))

    if present and not refresh:
        if (source_hash and recorded and
                _normalized_hash(source_hash) != _normalized_hash(recorded)):
            return RouteDecision(
                route=ROUTE_BLOCKED,
                reason=(f"目录里已有 MinerU 产物，但它对应的源哈希 {recorded} "
                        f"≠ 本次的 {source_hash}。方案 §11：源哈希不同**不得复用旧分块**。"
                        f"要重取请显式 --refresh（本次不会自动覆盖旧产物）。"),
                parse_root=parse_root, mineru_dir=mineru_dir,
                source_hash=source_hash, recorded_source_hash=recorded,
                evidence=tuple(evidence))
        detail = f"已有 MinerU 产物（{mineru_dir}）"
        if recorded:
            detail += f"，记录的源哈希 {recorded}"
            evidence.append('来源声明：' + ', '.join(label for label, _ in claims))
            if not source_hash:
                evidence.append('未提供当前 PDF 源哈希，无法证明已有记录与当前 PDF 一致')
        if not recorded:
            detail += "，但 ingest/fetch 记录都没有源哈希 —— 无法证明它对应的就是这份 PDF"
            evidence.append("缺少源哈希：保留旧资料复用，但不能声称已核对 PDF 来源")
        return RouteDecision(route=ROUTE_REUSE, reason=detail, parse_root=parse_root,
                             mineru_dir=mineru_dir, source_hash=source_hash,
                             recorded_source_hash=recorded, evidence=tuple(evidence))

    if not allow_api:
        return RouteDecision(
            route=ROUTE_BLOCKED,
            reason=("没有 MinerU 产物，且本次不允许走 API。**不会退回本地 OCR**："
                    "实测本地路线会丢公式（$ 计数 5，而 MinerU 是 11276）"),
            parse_root=parse_root, mineru_dir=mineru_dir, source_hash=source_hash,
            recorded_source_hash=recorded, evidence=tuple(evidence))

    ok, detail = has_api_credentials(workspace)
    if not ok:
        return RouteDecision(
            route=ROUTE_BLOCKED,
            reason=(f"没有 MinerU 产物，也没有 API 凭据（{detail}）。"
                    f"两条路都不通时**明确阻断**，不退回本地 OCR。"),
            parse_root=parse_root, mineru_dir=mineru_dir, source_hash=source_hash,
            recorded_source_hash=recorded, requires_api_key=True,
            evidence=tuple(evidence))
    return RouteDecision(route=ROUTE_API,
                         reason=f"没有可复用的 MinerU 产物，走云端 API（{detail}）",
                         parse_root=parse_root, mineru_dir=mineru_dir,
                         source_hash=source_hash, recorded_source_hash=recorded,
                         requires_network=True, requires_api_key=True,
                         evidence=tuple(evidence))


def require_route(workspace, parse_root: Path, **kw) -> RouteDecision:
    """判定 + 阻断即抛。调用方拿到的永远是**能走**的路线。"""
    decision = decide(workspace, parse_root, **kw)
    if decision.blocked:
        raise CapabilityError(decision.reason)
    return decision


__all__ = ["MINERU_DIR", "LAYOUT_MARKERS", "ROUTE_REUSE", "ROUTE_API", "ROUTE_BLOCKED",
           "RouteDecision", "local_result_present", "recorded_source_hash",
           "has_api_credentials", "decide", "require_route"]
