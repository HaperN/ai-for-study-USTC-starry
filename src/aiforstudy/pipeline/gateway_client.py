"""One cancellable, attempt-journal-backed client for every production model call."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
from ..core.jsonio import read_json_soft
from .stage_io import _store_root
from .services import StageContext
from ..providers.review_failures import ReviewCallFailure


class ModelCallFailed(ReviewCallFailure):
    """模型请求没拿到可用响应（重试上限内全是坏 JSON/空文本/网络错误）。"""


class GatewayClient:
    """把 `Gateway` 适配成旧实现认得的那种客户端。

    旧实现的调用面只有两处（全旧树 grep 过）：`client.chat_json(messages, tag=...)`
    与 `getattr(client, "model"/"effort")`。所以这里**不复制** `DeepSeekClient` 的实现，
    只做薄适配：`messages`/`tag` → `CallRequest`，`CallResult` → 旧实现期望的
    `{"data", "cached", "fingerprint"}`。缓存、有限尝试、取消全部归 Gateway。
    """

    def __init__(self, gateway, *, role: str, model: str, effort: str, stage: str,
                 unit_key: str, bucket: str, scope_kind: str = "unit") -> None:
        self.gateway = gateway
        self.role = role
        # 旧实现在 `_assemble` 里读这两个属性记"这条产物是谁写的"，必须给。
        self.model = model
        self.effort = effort
        self.stage = stage
        self.unit_key = unit_key
        self.bucket = bucket
        self.scope_kind = scope_kind
        self.resource_id = ""
        self.calls = 0
    def chat_json(self, messages: list[dict], *, tag: str = "",
                  images: list[str] | None = None, use_cache: bool | None = None,
                  prompt_version: str = "", rules_version: str = "",
                  protocol_errors: bool = False,
                  syntax_repair: bool = False,
                  **_ignored) -> dict:
        """旧实现的唯一入口。

        `use_cache` 与其它关键字**收下但不另做一套缓存**：缓存归 Gateway（按指纹），
        壳再写第二个缓存就是"同一件事写两处"，两边迟早不一致。

        `protocol_errors=True`：交付物协议无法解析时**不判死**，把模型原文原样放进
        `{UNPARSED_PROTOCOL: (原文, 解析错误)}` 交回调用方，由它当成可纠正错误处理。
        默认 False —— 其余调用方的行为与从前逐字一致。
        """
        from ..providers.gateway import CallRequest, EMPTY_PROTOCOL, UNPARSED_PROTOCOL
        from ..core.prompt_assets import message_versions
        actual_prompt, actual_rules = message_versions(messages)
        request = CallRequest(
            role=self.role, messages=list(messages), tag=tag,
            unit_key=self.unit_key, stage=self.stage, bucket=self.bucket, resource_id=self.resource_id,
            scope_kind=self.scope_kind, images=tuple(images or ()),
            prompt_version=prompt_version or actual_prompt,
            rules_version=rules_version or actual_rules,
            protocol_errors=bool(protocol_errors),
            syntax_repair=bool(syntax_repair),
            # ⚠️ tag 进逻辑身份（见本节说明）。少了它，K 次独立复核会退化成 1 次。
            extra_identity={"tag": tag})
        result = self.gateway.call(request)
        self.calls += 1
        if (not result.ok and result.failure_kind == 'unparsed'
                and isinstance(result.text, str) and result.text.strip()):
            return {UNPARSED_PROTOCOL: (result.text, result.error)}
        if not result.ok and result.failure_kind == 'empty_response' and protocol_errors:
            return {EMPTY_PROTOCOL: result.error}
        if not result.ok:
            raise ModelCallFailed(
                f"模型请求没拿到可用响应（{result.error or '未知'}；"
                f"尝试 {result.attempts} 次，执行次数已持久化）。数据侧不许把这类情况当成'没问题'。",
                failure_kind=result.failure_kind or "internal")
        return {"data":result.payload,"cached":bool(result.cached),
                "fingerprint":result.fingerprint}


def _replay_responses(cache_dir: Path) -> dict:
    """把回放库读成 `{请求指纹: (text, usage)}`。

    每份固定响应一个 JSON 文件：`{"key": "<digest>", "text": "<模型返回的文本>", "usage": {...}}`。
    只认带 `key` 的 —— 键必须由 `replay_transport` 按**请求内容**算出，
    所以这里不按文件名猜、也不按消息内容凑。
    """
    out: dict = {}
    directory = Path(cache_dir)
    if not directory.is_dir():
        return out
    for path in sorted(directory.rglob("*.json")):
        payload = read_json_soft(path)
        if not isinstance(payload, dict) or not payload.get("key"):
            continue
        out[str(payload["key"])] = (str(payload.get("text") or ""),
                                    {})
    return out


def _transport_for(ctx: StageContext):
    """选这一次的模型传输。返回 `(transport 或 None, 出处)`。

    顺序有意如此：**先认注入的替身**（测试与装配方给什么就用什么），
    再按档位取回放；`release` 档没有注入时**返回 None**，由调用方明确 `blocked` ——
    传输只由入口装配，阶段不会另建外部连接。
    """
    from ..providers.gateway import replay_transport
    injected = getattr(ctx.services.providers, "llm", None)
    if injected is not None:
        return injected, "injected"
    if ctx.request.profile.value == "replay":
        cache_dir = Path(ctx.workspace.root) / ".cache" / "replay"
        return replay_transport(_replay_responses(cache_dir)), "replay"
    return None, "absent"


def _gateway_client(ctx: StageContext, *, role: str, bucket: str):
    """建一个 Gateway 支撑的客户端。返回 `(client 或 None, 传输出处)`。"""
    transport, source = _transport_for(ctx)
    if transport is None:
        return None, source
    from ..providers.gateway import Gateway
    store = ctx.services.store
    run_id = str(getattr(store, "run_id", "") or "")
    execution_root = Path(getattr(store,"root",_store_root(ctx)))
    ctx.services.control.bind(execution_root / "execution.json",run_id=run_id)
    roles = ctx.workspace.config_json("models.json").get("roles") or {}
    spec = roles.get(role) or {}
    gateway = Gateway(transport=transport, roles=roles,
                      control=ctx.services.control, cancel=ctx.services.cancel,
                      cache_dir=_store_root(ctx) / "model-cache",
                      run_id=run_id, book_id=ctx.context.book_id,
                      call_observer=getattr(ctx.services.providers, 'call_observer', None))
    client = GatewayClient(gateway, role=role,
                           model=str(spec.get("model") or ""),
                           effort=str(spec.get("effort") or ""),
                           stage=ctx.node.stage, unit_key=ctx.node.scope_key,
                           bucket=bucket)
    return client, source
