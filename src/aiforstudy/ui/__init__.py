"""本地界面（方案 §3 的 `ui/`：本地 HTTP 服务、API、任务视图）。

M8 落地：

* `api.py` —— **CLI 与 HTTP 共用的请求校验器**（等价请求必须生成相同 plan digest，A06）；
* `server.py` —— 本机 HTTP 服务：短轮询事件游标、幂等提交、取消/恢复、
  重启后把无存活 worker 的 running 改成 interrupted、产物按登记 ID 定位（禁路径穿越）、
  写请求同源 + 启动令牌、响应里出现密钥字段直接报错。
* 界面本体在 `resources/control-ui/`（原生 HTML/JS，无框架、无外部请求）。

⚠️ 界面**不含业务规则**：按钮可用性由服务端返回的 capability/state 决定（§5）。
"""
from __future__ import annotations

from .api import ALLOWED_KEYS, capabilities_payload, normalize_request, request_to_cli_args
from .server import ControlServer, ControlState, RunningTask

__all__ = ["ALLOWED_KEYS", "capabilities_payload", "normalize_request", "request_to_cli_args",
           "ControlServer", "ControlState", "RunningTask"]
