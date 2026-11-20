"""MinerU 云端 API：把 PDF 自动捞成产物（省掉"网页端手工上传 + 下载"那两步）。

## 它在管道里的位置

    手工路线：你在 mineru.net 上传 → 等 → 下载 layout.json/images → 放进 data/parse/<书>/mineru/
    这条路：  fetch（本文件）→ 自动落进 data/parse/<书>/mineru/ → 后面接 preflight + adapt

**它只负责"把产物搬到本地"**，不认识我们的契约（那是 `preflight` 与 `adapt` 的事）——
所以云端换了字段也污染不到下游：适配块会认版本、认不出就停下。

## 接口流程（按官方文档；每步都踩过坑，注释里写了为什么）

    ① POST /api/v4/file-urls/batch        申请预签名上传地址（顺带把解析参数交给它）
    ② PUT  {预签名 URL}  ← **裸 PUT，一个头都不能带**（带了 Content-Type 就 403 签名不匹配）
    ③ GET  /api/v4/extract-results/batch/{batch_id}   轮询到 done
    ④ 下载 full_zip_url → 解压到 <parse_dir>/mineru/（layout.json / content_list.json / full.md / images/）

## 后端怎么选：默认 `vlm`（实测定的，不是拍的）

两个后端的输出**不一样**，而且差别关系到"原文保真"（实测同一页）：

| | `pipeline` | `vlm`（默认） |
|---|---|---|
| 返回的 `_backend` | `pipeline` | **`hybrid`** —— 和网页端那份**同一个后端** |
| 标点 | 把书上的**半角** `,` 规范化成**全角** `，` | **保留半角** `,`（和书上一致） |
| 折行 | 保留 PDF 的视觉折行 | 合并成整段 |
| 忠实度 | 差一点（改了标点） | 好（我裁第 7 页图核对过：书上就是半角逗号） |

而且 `vlm` 的第 7 页与网页端那份**逐行完全一致**（12 行全同）。
所以默认用 `vlm`：既忠实、又能与已有产物对齐。要 `pipeline` 就显式 `--model-version pipeline`。

## 额度与限制（官方文档）

* 每天 **2000 页**最高优先级（超出只是降优先级，不是不能用）
* 单文件 200MB / **200 页**（实测：289 页会被拒，报 `number of pages exceeds limit (200 pages)`；
  网上流传的"600 页"是自部署版的限制，**云 API 是 200** —— 所以超过 200 页要**自动切块再合并**）
* 预签名地址 24 小时有效
* `page_ranges` 支持只解析某几页（例如先花 2 页试通整条路）
* 错误码：`A0202` token 无效、`A0211` token 过期、`-60005` 超 200MB、
  `-60006` 页数超限、`-60018` 当日额度用尽

## 密钥

按 `显式参数 → 环境变量 MINERU_API_TOKEN → .secrets/mineru.env` 的顺序读，不写进代码、不入库。
"""
from __future__ import annotations

import json
import sys
import time
import zipfile
from contextvars import ContextVar
from contextlib import contextmanager
import shutil
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "lib"))          # lib/

from files import sha256_file, write_json                  # noqa: E402

API = "https://mineru.net/api/v4"
SECRETS = (HERE.parents[2] / ".secrets" / "mineru.env",
           Path.home() / ".secrets" / "mineru.env")
FINAL_STATES = ("done", "failed")
ERROR_HINTS = {
    "A0202": "token 无效 —— 去 mineru.net/apiManage/token 重新拿一个",
    "A0211": "token 过期 —— 重新拿一个",
    "-60005": "文件超过 200MB",
    "-60006": "页数超过云 API 的 200 页上限（fetch 会自动切块，除非你显式给了 --pages）",
    "-60018": "当日解析额度用尽（每天 2000 页最高优先级，明天再来或降优先级继续）",
}
API_PAGE_LIMIT = 200          # 云 API 单文件页数上限（实测）
MAX_BYTES = 200 * 1024 * 1024


class MinerUFetchError(RuntimeError):
    """网络/接口/额度这类"这条路暂时走不通"的错（和格式错分开）。"""


class MinerUUnreachable(MinerUFetchError):
    """**临时**连不上（DNS 抖动、超时）。可以重试 —— 长时间任务不该被一次抖动打死。

    实测踩过：289 页分两块，第一块解析到 117/200 页时 DNS 临时解析失败，
    整个任务直接失败、已经解析的 200 页白等。所以现在这类错误在轮询里会重试。
    """


# One optional absolute deadline across upload / poll / download / all chunks.
# None explicitly permits an unlimited operation and HTTP response wait.
_FETCH_CONTROL = ContextVar("mineru_fetch_control", default=None)


def _remaining(default=60):
    control = _FETCH_CONTROL.get()
    if control is None:
        return default
    deadline, checkpoint = control
    if checkpoint:
        checkpoint()
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise MinerUFetchError("MinerU 操作超过总 deadline；保留已验证分块，停止新派发")
    return remaining if default is None else min(default, remaining)


def _wait(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        remaining = _remaining()
        delay = max(0.0, min(0.2, end - time.monotonic()))
        time.sleep(delay if remaining is None else min(delay, remaining))
    _remaining()


@contextmanager
def _control(timeout_s, checkpoint=None):
    parent = _FETCH_CONTROL.get()
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    if parent:
        if parent[0] is not None:
            deadline = parent[0] if deadline is None else min(deadline, parent[0])
        checkpoint = checkpoint or parent[1]
    token = _FETCH_CONTROL.set((deadline, checkpoint))
    try:
        _remaining()
        yield
    finally:
        _FETCH_CONTROL.reset(token)


def load_token(token: str | None = None) -> str:
    """显式参数 → 环境变量 → .secrets/mineru.env。"""
    from aiforstudy.providers.mineru import resolve_api_token
    value, _ = resolve_api_token(token, secret_paths=SECRETS)
    if value:
        return value
    raise MinerUFetchError(
        "找不到 MinerU token。三种给法任选：\n"
        "  ① --token <token>\n"
        "  ② 环境变量 MINERU_API_TOKEN\n"
        f"  ③ 写进 {SECRETS[0]}（一行 `MINERU_API_TOKEN=...`，该目录已 gitignore）\n"
        "拿 token：mineru.net 登录 → https://mineru.net/apiManage/token")


def _request(url: str, *, method: str = "GET", body: bytes | None = None,
             headers: dict | None = None, timeout: float | None = 60) -> tuple[int, bytes]:
    from aiforstudy.providers.http_exchange import exchange
    try:
        return exchange(url, method=method, body=body, headers=headers,
                        timeout=_remaining(timeout), checkpoint=_remaining)
    except (TimeoutError, OSError) as exc:
        raise MinerUUnreachable("MinerU HTTP 请求未完成；远端状态未知") from exc


def _api_json(url: str, token: str, *, method: str = "GET", payload: dict | None = None,
              timeout: int = 60) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    status, raw = _request(url, method=method, body=body,
                           headers={"Authorization": f"Bearer {token}",
                                    "Content-Type": "application/json",
                                    "Accept": "application/json"},
                           timeout=timeout)
    if status == 429 or status >= 500:
        raise MinerUUnreachable(f'MinerU 服务暂不可用（HTTP {status}）')
    if not 200 <= status < 300:
        raise MinerUFetchError(f'MinerU 请求失败（HTTP {status}）')
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MinerUFetchError(f"MinerU 返回的不是 JSON（HTTP {status}）") from exc
    if not isinstance(data, dict):
        raise MinerUFetchError('MinerU 返回的 JSON 必须是对象')
    code = data.get("code")
    if code not in (0, None):
        raise MinerUFetchError(_explain(code, data.get("msg") or ""))
    return data


def bare_put(url: str, data: bytes, *, timeout: int = 600) -> tuple[int, bytes]:
    """裸 PUT：**一个头都不带**。

    为什么不能用 `urllib`：它在带 body 时会**自动**加上
    `Content-Type: application/x-www-form-urlencoded`；而 MinerU 给的预签名地址
    把 `Content-Type` 算进了签名（签名时是空的），于是多这一个头就
    **403 SignatureDoesNotMatch**（实测踩过，白等了一次批次）。
    `http.client` 只加 `Host` / `Content-Length` / `Accept-Encoding`，不会加 Content-Type。
    """
    return _request(url, method="PUT", body=data, headers={}, timeout=timeout)


def _explain(code, msg: str) -> str:
    hint = ERROR_HINTS.get(str(code))
    return f"MinerU 返回 code={code} msg={msg}" + (f"\n  → {hint}" if hint else "")


def create_batch(pdf: Path, token: str, *, model_version: str = "vlm",
                 language: str = "ch", page_ranges: str | None = None,
                 is_ocr: bool = False, data_id: str | None = None) -> tuple[str, str]:
    """① 申请预签名上传地址。返回 (batch_id, upload_url)。"""
    pdf = Path(pdf)
    if not pdf.is_file():
        raise MinerUFetchError(f"找不到 PDF：{pdf}")
    size = pdf.stat().st_size
    if size > MAX_BYTES:
        raise MinerUFetchError(f"PDF 有 {size / 1024 / 1024:.0f}MB，超过 200MB 上限")
    entry = {"name": pdf.name, "is_ocr": bool(is_ocr)}
    if page_ranges:
        entry["page_ranges"] = page_ranges
    if data_id:
        entry["data_id"] = data_id
    payload = {"files": [entry], "model_version": model_version,
               "enable_formula": True, "enable_table": True, "language": language}
    data = _api_json(f"{API}/file-urls/batch", token, method="POST", payload=payload)
    if data.get("code") != 0:
        raise MinerUFetchError(_explain(data.get("code"), data.get("msg") or ""))
    body = data.get("data") or {}
    urls = body.get("file_urls") or []
    if not body.get("batch_id") or not urls:
        raise MinerUFetchError(f"申请上传地址没拿到 batch_id/file_urls：{data}")
    return str(body["batch_id"]), str(urls[0])


def upload(upload_url: str, pdf: Path, *, timeout: int = 600) -> None:
    """② 裸 PUT 文件字节。

    **一个头都不要带**：这个地址是 MinerU 预先签好名的，多一个 `Content-Type`
    就会让签名对不上（实测 403 SignatureDoesNotMatch）。
    """
    status, raw = bare_put(upload_url, Path(pdf).read_bytes(), timeout=timeout)
    if status not in (200, 201, 204):
        raise MinerUFetchError(f"上传失败（HTTP {status}）：{raw[:200]!r}")


@dataclass
class Progress:
    states: list = field(default_factory=list)

    def note(self, state: str, detail: str = "") -> None:
        item = f"{state}{('/' + detail) if detail else ''}"
        if not self.states or self.states[-1] != item:
            self.states.append(item)


def poll(batch_id: str, token: str, *, timeout_s: float | None = 1800, interval_s: float = 5.0,
         log=print, progress: Progress | None = None,
         transient_retries: int = 6) -> dict:
    """③ 轮询到 done/failed。返回那一条 extract_result。

    网络抖动（`MinerUUnreachable`）会**退避重试**，不直接失败 ——
    实测被一次 DNS 抖动打死过，200 页白等。
    """
    progress = progress or Progress()
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    consecutive = 0
    while True:
        _remaining()
        if deadline is not None and time.monotonic() >= deadline:
            raise MinerUFetchError(f"等 MinerU 超过 {timeout_s} 秒；批次 {batch_id}")
        try:
            request_timeout = _remaining(None if timeout_s is None else 60)
            if deadline is not None:
                left = deadline - time.monotonic()
                request_timeout = left if request_timeout is None else min(request_timeout, left)
            data = _api_json(f"{API}/extract-results/batch/{batch_id}", token,
                             timeout=request_timeout)
            consecutive = 0
        except MinerUUnreachable as exc:
            consecutive += 1
            if consecutive > transient_retries:
                raise MinerUFetchError(
                    f"连续 {consecutive} 次连不上 MinerU，先放弃。批次 id 是 {batch_id}，"
                    f"网络恢复后可以直接查：GET {API}/extract-results/batch/{batch_id}") from exc
            wait = min(2 ** consecutive, 30)
            log(f"  ...网络抖了一下（{exc}），{wait} 秒后重试（{consecutive}/{transient_retries}）")
            _wait(wait if deadline is None else min(wait, max(0, deadline - time.monotonic())))
            continue
        if data.get("code") != 0:
            raise MinerUFetchError(_explain(data.get("code"), data.get("msg") or ""))
        items = ((data.get("data") or {}).get("extract_result")) or []
        if not items:
            raise MinerUFetchError(f"批次 {batch_id} 没有返回结果项：{data}")
        item = items[0]
        state = str(item.get("state") or "")
        detail = ""
        extract = item.get("extract_progress") or {}
        if extract.get("total_pages"):
            detail = f"{extract.get('extracted_pages')}/{extract.get('total_pages')} 页"
        progress.note(state, detail)
        log(f"  MinerU 状态：{state}{(' ' + detail) if detail else ''}")
        _remaining()
        if deadline is not None and time.monotonic() >= deadline:
            raise MinerUFetchError(
                f"等 MinerU 超过 {timeout_s} 秒还没完成（当前 {state}）。"
                f"批次 id 是 {batch_id}，可以稍后手工查询："
                f"GET {API}/extract-results/batch/{batch_id}")
        if state in FINAL_STATES:
            return item
        _wait(interval_s if deadline is None else min(interval_s, max(0, deadline - time.monotonic())))


def extract_zip(zip_path: Path, dest: Path) -> list[str]:
    """④ 解压。防目录穿越，也防别人塞进来的绝对路径。"""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            name = member.filename.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                raise MinerUFetchError(f"压缩包里有个可疑路径，拒绝解压：{name}")
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, open(target, "wb") as out:
                out.write(source.read())
            written.append(name)
    return written


def page_count(pdf: str | Path) -> int | None:
    """本地数页数（用来决定要不要切块）。数不出来就返回 None，让调用方试一次整篇。"""
    for module in ("pymupdf", "fitz"):
        try:
            doc = __import__(module).open(str(pdf))
            try:
                return int(doc.page_count)
            finally:
                doc.close()
        except Exception:
            continue
    return None


def plan_chunks(total: int | None, limit: int = API_PAGE_LIMIT) -> list[tuple[int | None, int | None]]:
    """按 200 页上限切块。数不出页数时返回"不切"（[(None, None)]），让对方试一次再说。"""
    if not total or total <= limit:
        return [(None, None)]
    return [(start, min(start + limit - 1, total)) for start in range(1, total + 1, limit)]


def _fetch_one(pdf: Path, dest: Path, token: str, *, page_ranges: str | None,
               model_version: str, language: str, is_ocr: bool, timeout_s: float | None,
               interval_s: float, log=print, refetch: bool = False) -> dict:
    """捞一块：申请地址 → 裸 PUT → 轮询 → 下载 → 解压到 dest。返回这一块的记录。

    **可续**：dest 里已经有 layout.json 就直接复用（不重新上传、不重新解析）——
    长任务中途断了不用从头再来，也不用重复占额度。
    """
    _remaining()
    identity = {"source_sha256": sha256_file(pdf), "page_ranges": page_ranges,
                "model_version": model_version, "language": language, "is_ocr": is_ocr}
    receipt = dest / "_chunk_identity.json"
    try:
        saved = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    files = saved.get("files") or {}
    if (not refetch and saved.get("identity") == identity and "layout.json" in files
            and all((dest / name).is_file() and sha256_file(dest / name) == value
                    for name, value in files.items())):
        log(f"   复用已验证分块：{dest.name}")
        return {"page_ranges": page_ranges, "batch_id": saved.get("batch_id"), "reused": True,
                "states": [], "files": sorted(files), "extract_progress": {}}
    # Old bytes must never mingle with a refreshed result.
    if dest.exists():
        shutil.rmtree(dest)
    progress = Progress()
    batch_id, upload_url = create_batch(pdf, token, model_version=model_version,
                                        language=language, page_ranges=page_ranges,
                                        is_ocr=is_ocr)
    log(f"   批次 {batch_id}"
        + (f"（第 {page_ranges} 页）" if page_ranges else "（全篇）"))
    _remaining()
    upload(upload_url, pdf, timeout=_remaining(600))
    item = poll(batch_id, token, timeout_s=timeout_s, interval_s=interval_s,
                log=log, progress=progress)
    if item.get("state") != "done":
        raise MinerUFetchError(f"解析失败：{item.get('err_msg') or item.get('state')}")
    zip_url = item.get("full_zip_url")
    if not zip_url:
        raise MinerUFetchError(f"done 了却没给 full_zip_url：{item}")
    status, blob = _request(zip_url, timeout=_remaining(600))
    _remaining()
    if status != 200:
        raise MinerUFetchError(f"下载结果包失败（HTTP {status}）")
    dest.mkdir(parents=True, exist_ok=True)
    zip_path = dest / "_result.zip"
    zip_path.write_bytes(blob)
    written = extract_zip(zip_path, dest)
    zip_path.unlink()
    _remaining()
    write_json(receipt, {"identity": identity, "batch_id": batch_id,
                         "files": {name: sha256_file(dest / name) for name in written}})
    return {"page_ranges": page_ranges, "batch_id": batch_id,
            "states": progress.states, "files": sorted(written),
            "extract_progress": item.get("extract_progress") or {}}


def merge_chunks(chunk_dirs: list[Path], offsets: list[int], dest: Path) -> dict:
    """把分块产物**合并成一份**（这就是"整本书被切成 200 页一块"之后必须做的事）。

    关键：分块返回的 `page_idx` 是**从 0 开始**的（实测：要第 201-202 页，回来是 `[0, 1]`），
    所以每块都要按它的起始页**加偏移**，否则整本书的页码会全部错位 ——
    而页码错位会让 `[第 N 页]` 标记、插图页码、`mineru.audit.json` 的页↔行号映射全错。

    合并的东西：`layout.json`（主力）、`content_list*.json`、`full.md`、`images/`。
    **不合并 `_origin.pdf`**（那是每块的切片，不是原书），原书路径记在 `fetch.json` 里。
    """
    dest = Path(dest)
    images_dir = dest / "images"
    pages: list[dict] = []
    meta: dict = {}
    content: list = []
    content_v2: list = []
    markdown: list[str] = []
    image_count = 0
    versions: set = set()

    for chunk, offset in zip(chunk_dirs, offsets):
        layout = json.loads((chunk / "layout.json").read_text(encoding="utf-8"))
        versions.add((layout.get("_version_name"), layout.get("_backend")))
        for key, value in layout.items():
            if key.startswith("_") and key not in meta:
                meta[key] = value
        for page in layout.get("pdf_info") or []:
            page = dict(page)
            page["page_idx"] = int(page.get("page_idx") or 0) + offset
            pages.append(page)
        for path in sorted(chunk.glob("*content_list.json")):
            for item in json.loads(path.read_text(encoding="utf-8")):
                item = dict(item)
                if isinstance(item.get("page_idx"), int):
                    item["page_idx"] += offset
                content.append(item)
        for path in sorted(chunk.glob("*content_list_v2.json")):
            for group in json.loads(path.read_text(encoding="utf-8")):
                group = [dict(i) for i in group]
                for item in group:
                    if isinstance(item.get("page_idx"), int):
                        item["page_idx"] += offset
                content_v2.append(group)
        if (chunk / "full.md").is_file():
            markdown.append((chunk / "full.md").read_text(encoding="utf-8").rstrip())
        for image in sorted((chunk / "images").glob("*")) if (chunk / "images").is_dir() else []:
            images_dir.mkdir(parents=True, exist_ok=True)
            target = images_dir / image.name
            if target.exists() and target.read_bytes() != image.read_bytes():
                raise MinerUFetchError(f"两张同名但内容不同的图，不敢合并：{image.name}")
            if not target.exists():
                target.write_bytes(image.read_bytes())
            image_count += 1

    pages.sort(key=lambda page: int(page.get("page_idx") or 0))
    dest.mkdir(parents=True, exist_ok=True)
    write_json(dest / "layout.json", {**meta, "pdf_info": pages})
    if content:
        write_json(dest / "content_list.json", content)
    if content_v2:
        write_json(dest / "content_list_v2.json", content_v2)
    if markdown:
        (dest / "full.md").write_text("\n\n".join(markdown) + "\n", encoding="utf-8")
    return {"pages": len(pages), "images": image_count,
            "versions": sorted(f"{v[0]}/{v[1]}" for v in versions),
            "content_items": len(content)}


def _fetch(pdf: str | Path, out_dir: str | Path, *, token: str | None = None,
          page_ranges: str | None = None, chunk_pages: int = API_PAGE_LIMIT,
          model_version: str = "vlm", language: str = "ch", is_ocr: bool = False,
          timeout_s: float | None = 1800, interval_s: float = 5.0, keep_chunks: bool = False,
          refetch: bool = False, log=print) -> dict:
    """PDF → `data/parse/<书>/mineru/`（layout.json / content_list.json / full.md / images/）。

    **超过 200 页会自动切块再合并**（云 API 单文件上限 200 页，实测）。
    返回一份记录（写进 `mineru/fetch.json`），留档：什么时候、用什么参数、结果是什么。
    """
    pdf = Path(pdf)
    out_dir = Path(out_dir)
    mineru_dir = out_dir / "mineru"
    key = load_token(token)

    if page_ranges:
        chunks = [(None, None)]                     # 用户显式指定了就照办，不自动切
        ranges = [page_ranges]
    else:
        total = page_count(pdf)
        chunks = plan_chunks(total, chunk_pages)
        ranges = [f"{s}-{e}" if s else None for s, e in chunks]
        if total is None:
            log("   ⚠ 数不出 PDF 页数（缺 pymupdf）：先按整篇试一次，超限再提示你切块")

    log(f"① 准备上传：{pdf.name}，{pdf.stat().st_size / 1024:.0f}KB，"
        f"{len(ranges)} 块"
        + (f"（页数超过 {chunk_pages} 页上限，自动切块）" if len(ranges) > 1 else ""))
    staging = out_dir / "_fetch_chunks"
    chunk_records = []
    chunk_dirs = []
    for index, (span, rng) in enumerate(zip(chunks, ranges), 1):
        _remaining()
        if len(ranges) > 1:
            log(f"② 第 {index}/{len(ranges)} 块：第 {span[0]}-{span[1]} 页")
        else:
            log("② 上传并等它解析")
        dest = staging / (rng or "all")
        try:
            record = _fetch_one(pdf, dest, key, page_ranges=rng, model_version=model_version,
                                language=language, is_ocr=is_ocr, timeout_s=timeout_s,
                                interval_s=interval_s, log=log, refetch=refetch)
            _remaining()
        except MinerUFetchError as exc:
            if "exceeds limit" in str(exc) or "-60006" in str(exc):
                raise MinerUFetchError(
                    f"{exc}\n  → 云 API 单文件上限 200 页。fetch 会自动切块，前提是它能数出页数：\n"
                    f"     装 pymupdf（pip install pymupdf）让它自己数，或用 --pages 手动指定范围") from exc
            raise
        chunk_records.append(record)
        chunk_dirs.append(dest)

    offsets = [(span[0] - 1) if span[0] else 0 for span in chunks]
    log("③ 合并" + (f" {len(chunk_dirs)} 块" if len(chunk_dirs) > 1 else ""))
    _remaining()
    merged = merge_chunks(chunk_dirs, offsets, mineru_dir)
    if not keep_chunks:
        for directory in sorted(staging.glob("*"), reverse=True):
            for item in sorted(directory.rglob("*"), reverse=True):
                item.unlink() if item.is_file() else item.rmdir()
            directory.rmdir()
        staging.rmdir()

    versions = merged["versions"]
    warnings = []
    if len(versions) > 1:
        warnings.append(f"各块的解析版本不一致：{versions}（合并结果可能风格不统一）")
    record = {
        "_meta": {
            "file_type": "mineru-fetch", "schema_version": "1.0",
            "generator": "aiforstudy.parse.mineru.fetch",
            "source_sha256": sha256_file(pdf),
            "field_notes": {
                "pdf": "送上去的那份 PDF（sha256 在 _meta.source_sha256）",
                "page_ranges": "只解析了哪几页（空=全篇）",
                "chunks[]": "分块记录：每块的页码范围、批次号、状态流转、落盘文件",
                "merged": "合并结果：总页数、图片数、各块解析版本、content_list 条数",
                "note": "分块返回的 page_idx 是从 0 开始的，合并时已按起始页加偏移（实测验证过）",
                "origin_pdf": "原书 PDF 的路径（分块产物里的 _origin.pdf 是切片，没有合并进来）",
            },
        },
        "pdf": str(pdf), "bytes": pdf.stat().st_size,
        "page_ranges": page_ranges, "model_version": model_version, "language": language,
        "chunks": chunk_records, "merged": merged, "warnings": warnings,
        "batch_id": chunk_records[0]["batch_id"] if len(chunk_records) == 1 else None,
        "states": chunk_records[0]["states"] if len(chunk_records) == 1 else [],
        "files": sorted(str(p.relative_to(mineru_dir)) for p in mineru_dir.rglob("*")
                        if p.is_file()),
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    write_json(mineru_dir / "fetch.json", record)
    log(f"   落盘 {merged['pages']} 页、{merged['images']} 张图 → {mineru_dir}")
    for warning in warnings:
        log(f"   ⚠ {warning}")
    return record


__all__ = ["MinerUFetchError", "MinerUUnreachable", "fetch", "load_token", "create_batch", "upload", "bare_put",
           "poll", "extract_zip", "merge_chunks", "page_count", "plan_chunks", "Progress"]


def fetch(pdf: str | Path, out_dir: str | Path, *, timeout_s: float | None = 1800,
          checkpoint=None, **kwargs) -> dict:
    """Fetch with cancellation and an optional absolute deadline across all chunks."""
    with _control(timeout_s, checkpoint):
        return _fetch(pdf, out_dir, timeout_s=timeout_s, **kwargs)
