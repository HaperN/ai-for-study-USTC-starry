"""外部服务适配（方案 §3 的 `providers/`）。

> MinerU、LLM、normalizer、审核器适配

一条贯穿本包的原则：**外部依赖要能被替换成替身，也要能被证明是哪一份**。

* `mineru`   —— 路线判定：复用 / 云端 / 阻断；**绝不回退 OCR**；
* `llm`      —— 按角色取参数；密钥只走引用；
* `normalizer` —— 指出真正被 import 的 `stem_runner` 是哪一份，并与锁比对；
* `auditor`  —— 本地 vendor 的内容指纹 + 许可证记录；路径注入收在这一处；
* `capabilities` —— 预检：给模型的可用能力来自探测结果，不是"这台机器恰好装了什么"；
* `artifacts` —— 依赖制品指纹与 `vendor.lock.json`。

M6 会在这里补上真正的 gateway（预约额度、记账、重试、缓存）与回放/替身实现。
"""
from __future__ import annotations

from .artifacts import Artifact, SourceLock, payload_digest
from .capabilities import Capabilities, Capability, probe

__all__ = ["Artifact", "SourceLock", "payload_digest", "Capabilities", "Capability", "probe",
           "CallRequest", "CallResult", "Gateway", "MineruLedger", "replay_transport",
           "RealTransport", "RefusingTransport", "API_KEY_ENV"]
