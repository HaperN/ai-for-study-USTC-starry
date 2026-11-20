"""跨块共用的基础设施：模型调用。

> 这不是"块"。任务基线 G1 要求"块与块之间只通过 JSON 通信"，
> 而模型调用是所有块都要用的底座，所以单独放在 `lib/` 下，谁都可以 import。

设计要点（对应任务基线的全局约束）：
  * **G6 不累积历史**：本类不保存任何对话状态，每次调用都是独立的 messages。
  * **G8 单节可重跑 / 不重复花钱**：按请求指纹缓存到磁盘，命中直接返回。
  * **成本可观测**（E3）：每次调用把用量追加到 `usage.jsonl`。
  * 密钥只从环境变量或 `.secrets/deepseek.env` 读，**不写进代码、不入库**。
"""
from __future__ import annotations

import hashlib
import json
import re
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import usage as _usage          # lib/ 在 sys.path 上（与本项目其它 lib 模块的写法一致）
from files import display_path  # 同上：给"给人看的路径"，仓库外不炸

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                      # AIforStudy/
SECRETS = ROOT / ".secrets" / "deepseek.env"
CACHE_DIR = ROOT / ".cache" / "model"
USAGE_LOG = CACHE_DIR / "usage.jsonl"


class ModelError(RuntimeError):
    """模型调用失败（网络、鉴权、返回格式）。"""


def load_api_key(key: str | None = None) -> str:
    """按 显式参数 → 环境变量 → `.secrets/deepseek.env` 的顺序取密钥。"""
    if key:
        return key
    env = os.environ.get("DEEPSEEK_API_KEY")
    if env:
        return env.strip()
    if SECRETS.is_file():
        for line in SECRETS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() == "DEEPSEEK_API_KEY":
                return value.strip()
    raise ModelError(
        "没有拿到 DEEPSEEK_API_KEY：请设置环境变量，或写进 "
        f"{display_path(SECRETS)}（该文件已被 .gitignore 排除）")


def _loads_lenient(content: str, *, tag: str = "", finish=None):
    """解析模型的 JSON；反斜杠没转义时自动修补一次。

    返回 (data, 是否修补过)。修补也不行就抛 ModelError（带原文前 400 字，方便定位）。
    """
    try:
        return json.loads(content), False
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(DeepSeekClient._repair_json_escapes(content)), True
    except json.JSONDecodeError as exc:
        raise ModelError(
            f"模型返回的不是合法 JSON（tag={tag}, finish_reason={finish}）："
            f"{_redact(content[:400])}") from exc


def _redact(text: str) -> str:
    """任何要打日志/落盘的内容都先过这里，避免密钥泄漏。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key and key in text:
        text = text.replace(key, "<REDACTED>")
    return text


def _start_heartbeat(tag: str, every: float = 15.0):
    """每 15 秒打一行"还在等"；请求退出时用 Event 唤醒并回收。"""
    stop = threading.Event()

    def beat():
        waited = 0.0
        while not stop.wait(every):
            waited += every
            print(f"      …还在等模型 [{tag or '未命名'}]（已 {waited:.0f} 秒）", flush=True)

    thread = threading.Thread(target=beat, daemon=True)
    thread.stop_event = stop
    thread.start()
    return thread


def _stop_heartbeat(thread) -> None:
    """停止等待并回收心跳，成功和异常都不能留下“还在等”的线程。"""
    if thread is not None:
        thread.stop_event.set()
        thread.join()


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0

    @classmethod
    def from_payload(cls, payload: dict) -> "Usage":
        payload = payload or {}
        cache = payload.get("prompt_cache_hit_tokens", 0) or 0
        miss = payload.get("prompt_cache_miss_tokens", 0) or 0
        return cls(
            prompt_tokens=int(payload.get("prompt_tokens", 0) or 0),
            completion_tokens=int(payload.get("completion_tokens", 0) or 0),
            total_tokens=int(payload.get("total_tokens", 0) or 0),
            reasoning_tokens=int(
                (payload.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0),
            cache_hit_tokens=int(cache),
            cache_miss_tokens=int(miss),
        )

    def as_dict(self) -> dict:
        return {"prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "reasoning_tokens": self.reasoning_tokens,
                "cache_hit_tokens": self.cache_hit_tokens,
                "cache_miss_tokens": self.cache_miss_tokens}


class DeepSeekClient:
    """最小可用的 DeepSeek 客户端：只做"一次请求 → 一段 JSON"。"""

    def __init__(self, *, api_key: str | None = None, model: str = DEFAULT_MODEL,
                 effort: str = "high", base_url: str = DEFAULT_BASE_URL,
                 max_tokens: int = 64000, timeout: int = 1000,
                 temperature: float | None = None, seed: int | None = None,
                 cache_dir: Path | None = None, use_cache: bool = True,
                 usage_log: Path | None = None):
        self.api_key = load_api_key(api_key)
        self.model = model
        self.effort = effort
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens
        self.timeout = timeout
        # 采样参数（调研结论：不设 = 用 API 默认温度 → **同一份内容两次复核能报出 5 条 / 3 条**
        # 这种量级的差异，比"改一轮"的效果还大）。要可复现的自动化回路，必须把它钉住。
        # None = 一个字节都不传（老调用方的请求体与缓存指纹**完全不变**）。
        self.temperature = temperature
        self.seed = seed
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.use_cache = use_cache
        self.usage_log = Path(usage_log) if usage_log else (self.cache_dir / "usage.jsonl")
        self.session_usage = Usage()
        self.calls = 0            # 真正打出去的请求次数（缓存命中不算）
        # verbose：打印"开始/心跳/回来"三行 —— 无人自动跑时这是唯一能分辨
        # "在等模型"和"卡死"的凭据（实测踩过：安静了 8 分钟，其实只是调用慢）
        self.verbose = bool(os.environ.get("AIFORSTUDY_VERBOSE_CALLS"))

    # ── 请求指纹与缓存 ──────────────────────────────────────────────────
    def fingerprint(self, messages: list[dict], **extra) -> str:
        payload = {
            "model": self.model,
            "effort": self.effort,
            "max_tokens": self.max_tokens,
            "messages": messages,
            **extra,
        }
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def _cache_path(self, fingerprint: str) -> Path:
        return self.cache_dir / f"{fingerprint}.json"

    def _log_usage(self, fingerprint: str, usage: Usage, seconds: float, cached: bool,
                   tag: str = "") -> None:
        """往 usage.jsonl 追一条。

        ⚠️ **`tag` 一定要记**（2026-09-26 补）：以前只记模型/用量/秒数，没有 tag ——
        于是"发现花了多少、改写花了多少、关闭复核花了多少"只能靠时间戳去猜（实测就是这么干的）。
        有了 tag，按步骤算账、找最贵的一步、看缓存命中率，都是一行统计的事。

        ⚠️ **`run_id` 一定要记**（2026-09-26 补，链路审计 D-5 / 规格 S8）：
        并行跑两章时，原来的记账是"整份日志跑前跑后取差值"，两章会**互相把对方花的钱
        算进自己头上**。记下 run_id（从 `AIFORSTUDY_RUN_ID` 环境变量来，`run_book.py` 注入）
        之后，统计一律 `按 run_id 分组`，串账在结构上不可能发生。
        读法统一收在 `lib/usage.py`（不要在这里另写一份求和逻辑）。
        """
        self.usage_log.parent.mkdir(parents=True, exist_ok=True)
        row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tag": str(tag or ""),
               "run_id": _usage.current_run_id(),
               "fingerprint": fingerprint, "model": self.model, "effort": self.effort,
               "cached": cached, "seconds": round(seconds, 2), **usage.as_dict()}
        with self.usage_log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ── 主调用 ──────────────────────────────────────────────────────────
    def chat_json(self, messages: list[dict], *, tag: str = "",
                  images: list[str] | None = None, use_cache: bool | None = None) -> dict:
        """要一段 JSON 回来。返回 {"data": <解析后的对象>, "usage": {...}, "cached": bool}。

        `images` 是本地图片路径列表（deepseek-flash 支持图片输入）。
        """
        use_cache = self.use_cache if use_cache is None else use_cache
        if images:
            messages = _attach_images(messages, images)
        sampling: dict = {}
        if self.temperature is not None:
            sampling["temperature"] = self.temperature
        if self.seed is not None:
            sampling["seed"] = self.seed
        fingerprint = self.fingerprint(messages, tag=tag, **sampling)
        cache_path = self._cache_path(fingerprint)
        if use_cache and cache_path.is_file():
            saved = json.loads(cache_path.read_text(encoding="utf-8"))
            self._log_usage(fingerprint, Usage(), 0.0, True, tag)
            return {"data": saved["data"], "usage": Usage.from_payload({}).as_dict(),
                    "cached": True, "fingerprint": fingerprint}

        started = time.time()
        # 心跳：这一行是给"人看日志"用的。实测一次发现调用要 124.8 秒，
        # 期间日志**一片安静**，我因此把正常等待误判成卡死（还手动杀过进程）。
        # 有心跳之后，"在等模型"和"卡死"一眼能分开：前者分钟级但持续有输出。
        if self.verbose:
            print(f"    ▸ 调模型 [{tag or '未命名'}]：prompt {sum(len(str(m.get('content') or '')) for m in messages)} 字"
                  f"，effort={self.effort}，超时 {self.timeout}s", flush=True)
            self._heartbeat = _start_heartbeat(tag)

        try:
            from aiforstudy.errors import ConfigError
            from aiforstudy.providers.transport import reasoning_options
            try:
                options = reasoning_options(self.effort)
            except ConfigError as exc:
                raise ModelError(str(exc)) from exc
            body = {
                "model": self.model,
                "messages": messages,
                "response_format": {"type": "json_object"},
                "max_tokens": self.max_tokens,
                **options,
                **sampling,
            }
            payload = self._post("/chat/completions", body)
        finally:
            if self.verbose:
                _stop_heartbeat(getattr(self, "_heartbeat", None))
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        finish = choice.get("finish_reason")
        if not content.strip():
            # 最常见的原因：思考（reasoning）把 max_tokens 吃光了，正文一个字没出。
            # 这种情况必须报清楚，不能当成"返回了空 JSON"。
            reasoning = message.get("reasoning_content") or ""
            hint = ("思考占满了输出额度：请调大 max_tokens"
                    if finish == "length" else "模型没有返回正文")
            raise ModelError(
                f"{hint}（tag={tag}, finish_reason={finish}, "
                f"max_tokens={self.max_tokens}, 思考 {len(reasoning)} 字符）")
        if self.verbose:
            print(f"    ◂ 回来 [{tag or '未命名'}]：{time.time() - started:.1f}s，"
                  f"{payload.get('usage', {}).get('completion_tokens', '?')} token 输出", flush=True)
        data, repaired = _loads_lenient(content, tag=tag, finish=finish)
        usage = Usage.from_payload(payload.get("usage"))
        self.calls += 1
        self.session_usage = _add_usage(self.session_usage, usage)
        self._log_usage(fingerprint, usage, time.time() - started, False, tag)
        if use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(
                {"tag": tag, "data": data, "usage": usage.as_dict()},
                ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"data": data, "usage": usage.as_dict(), "cached": False,
                "fingerprint": fingerprint, "seconds": round(time.time() - started, 2),
                "json_repaired": repaired}

    @staticmethod
    def _repair_json_escapes(content: str) -> str:
        r"""把 JSON 里**非法的反斜杠**补成双反斜杠（正确写法：逐字符扫描，整对吃掉）。

        为什么需要：数学内容里的 LaTeX（`\lambda`、`\boldsymbol`）在 JSON 字符串里
        必须写成 `\\lambda`。模型经常忘，于是 `json.loads` 报
        "Invalid \escape"。这是**内容问题不是格式问题** —— 直接丢掉太可惜，
        补一下就能用（`\l` → `\\l`，解出来正是想要的那个反斜杠+字母）。

        ⚠️ **这里原来是一个正则，而那个正则有 bug**（2026-09-27 全流程试跑实测抓出，
        它把 212 个单元的概念抽取整体阻断在 34/212）：

            原来：`re.sub(r'\\(?!["\\\\/bfnrtu])', r"\\\\", content)`

        逐字符扫描的后果是：遇到**合法的** `\\`（JSON 里表示一个字面反斜杠）时，
        正则只看第一个反斜杠（后面跟 `\`，判定合法、跳过），**转头把第二个反斜杠
        当成"漏写的"又补一个** —— 于是 `\\varepsilon` 被改成 `\\\varepsilon`，
        **反而制造出一个新的非法转义**。
        实测证据：同一次返回，修补前报 `char 211`，修补后报 `char 264`，
        而出错处正是 `$\\\varepsilon`（三个反斜杠）。

        正确做法是**扫描器**：见到反斜杠就看下一个字符
          · 是 `u` 且后接 4 位十六进制 → 合法 `\uXXXX`，**整 6 个字符**吃掉
          · 是 `" \ / b f n r t u` 之一 → 合法转义，**整 2 个字符**吃掉
          · 其它（含 LaTeX 的 `\v` `\e`，以及 `\u` 后面不是 4 位十六进制的 `\underline`）
            → 补成 `\\`，只前进 1 个字符
        这样既不误伤合法转义，也能修掉漏写的。
        """
        # ⚠️ `u` **不在**这个集合里：它只有在后接 4 位十六进制时才是合法转义
        #    （`\u4e2d` 合法、`\underline` 不合法）——所以单独走上面的分支判。
        #    漏了这一点，`\underline` 会被当成合法 `\u` 放过去，照样解析失败
        #    （这条是测试抓出来的，见 tests/test_wiring.py）。
        simple = set('"\\/bfnrt')
        hexdigits = set("0123456789abcdefABCDEF")
        out: list[str] = []
        i, n = 0, len(content)
        while i < n:
            ch = content[i]
            if ch != "\\":
                out.append(ch)
                i += 1
                continue
            nxt = content[i + 1] if i + 1 < n else ""
            if nxt == "u" and len(content[i + 2:i + 6]) == 4 \
                    and set(content[i + 2:i + 6]) <= hexdigits:
                out.append(content[i:i + 6])          # 合法 \uXXXX：整 6 个吃掉
                i += 6
            elif nxt in simple:
                out.append(content[i:i + 2])          # 合法转义：整 2 个吃掉
                i += 2
            else:
                out.append("\\\\")                    # 漏写的：补一个反斜杠
                i += 1
        return "".join(out)

    def _post(self, path: str, body: dict) -> dict:
        from aiforstudy.providers.http_exchange import exchange

        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {self.api_key}"}
        last_error = None
        for attempt in range(1, 4):
            try:
                status, raw = exchange(self.base_url + path, method="POST", body=payload,
                                       headers=headers, timeout=self.timeout)
                if status < 300:
                    return json.loads(raw.decode("utf-8"))
                if status not in RETRY_STATUS:
                    raise ModelError(f"HTTP {status}")
                last_error = f"HTTP {status}"
            except (TimeoutError, OSError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < 3:
                time.sleep(2 ** attempt)
        raise ModelError(f"重试 3 次仍失败：{last_error}")


def _attach_images(messages: list[dict], images: list[str]) -> list[dict]:
    """把本地图片路径变成 data URL，附到最后一条 user 消息上。"""
    import base64
    import mimetypes

    parts: list[dict] = []
    for path in images:
        p = Path(path)
        if not p.is_file():
            raise ModelError(f"图片不存在：{p}")
        mime = mimetypes.guess_type(p.name)[0] or "image/png"
        encoded = base64.b64encode(p.read_bytes()).decode("ascii")
        parts.append({"type": "image_url",
                      "image_url": {"url": f"data:{mime};base64,{encoded}"}})
    out = [dict(message) for message in messages]
    for message in reversed(out):
        if message.get("role") == "user":
            text = message.get("content")
            message["content"] = [{"type": "text", "text": text if isinstance(text, str)
                                   else json.dumps(text, ensure_ascii=False)}] + parts
            return out
    raise ModelError("要附图时必须有一条 user 消息")


def _add_usage(left: Usage, right: Usage) -> Usage:
    return Usage(
        prompt_tokens=left.prompt_tokens + right.prompt_tokens,
        completion_tokens=left.completion_tokens + right.completion_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
        reasoning_tokens=left.reasoning_tokens + right.reasoning_tokens,
        cache_hit_tokens=left.cache_hit_tokens + right.cache_hit_tokens,
        cache_miss_tokens=left.cache_miss_tokens + right.cache_miss_tokens,
    )


def usage_summary(usage_log: Path | None = None) -> dict:
    """汇总历史用量（E3 成本可观测）。"""
    path = Path(usage_log) if usage_log else USAGE_LOG
    if not path.is_file():
        return {"calls": 0, "cached_calls": 0, "total_tokens": 0}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    real = [r for r in rows if not r.get("cached")]
    return {
        "calls": len(real),
        "cached_calls": len(rows) - len(real),
        "prompt_tokens": sum(r.get("prompt_tokens", 0) for r in real),
        "completion_tokens": sum(r.get("completion_tokens", 0) for r in real),
        "reasoning_tokens": sum(r.get("reasoning_tokens", 0) for r in real),
        "total_tokens": sum(r.get("total_tokens", 0) for r in real),
    }


__all__ = ["DeepSeekClient", "ModelError", "Usage", "load_api_key",
           "usage_summary", "DEFAULT_MODEL", "CACHE_DIR", "USAGE_LOG"]
