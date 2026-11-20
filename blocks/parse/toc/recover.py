"""**视觉恢复标题行**：目录声明了某一节，但正文里抽不到它的标题行 → 看图把它捡回来。

## 为什么有这个模块（2026-09-30，用户定案）

实测个案《电路分析基础》§2.5「晶体管的大信号模型」：

* 书**目录**（PDF 书签 + 印刷目录页）两条独立来源**写法一致**，印在第 94 页（PDF 113）；
* 但正文里**没有那一行**：这册是**扫描件**（整页一张图，文字层 0 字），
  而该节标题印在**黑底白字标题栏**里（`＊§ 2-5 …`）→ **OCR 漏了这一行**；
* 结果：这一节的内容被并进了上一节（§2.4「电路的对偶性」8,567 字，明显偏大），
  页面上只能显示一条「目录有、正文里没找到」。

用户定案的做法：**让视觉复核顺手把它捡回来** —— 模型在截图里**已经读到**那行原文，
所以让它带着**页码证据**把标题行恢复进结构（标 `recovered_by="vision"`）。
这是**可核对的恢复**，不是猜。

## 名字别混：复核里的 `recover` 是**另一个方向**

复核的 `recover`＝「**目录漏条**」：模型从目录页截图里**捞回目录上我们没抽到的条目**（往 `nodes` 里追加）。
本模块＝「**正文缺行**」：目录是对的，**正文里的标题行**没了（OCR 漏）→ 往**正文**里补一行。
两个方向相反，所以这里所有名字都带 `body`：

* 产物 `toc.recover.json`（由结构层消费，**不写回** `toc.v2.json`）；
* 记录上标 `recovered_by="vision"`。

## 与 `toc_review` 的分工

| | `toc_review`（视觉复核） | 本模块（视觉恢复） |
|---|---|---|
| 管什么 | 两源**写法不一致**（OCR 错字、层级标错） | 目录有、正文**整行缺失** |
| 产物 | `toc.review.json`（裁决） | `toc.recover.json`（要插入的标题行 + 定位锚） |
| 谁消费 | `toc apply`（改标题/层级） | **结构层**（`--recover`：把标题行插进正文再切单元） |

## 为什么不直接改结构、非要一个锚

程序要在 `document.md` 里找到**插入点**。靠"页码"插入会把上一节的正文拦腰切断
（标题常印在页面下部的标题栏里）。所以要求模型给出**标题之后紧接着的第一行正文**
（`first_line_after`）—— 程序拿它**逐字定位**：

* 找得到 → 在这行**前面**插入标题行（可核对：插进去的是截图里读到的原文）；
* 找不到 → **不插**（记 `rejected: no_anchor`），宁可保持"目录有、正文缺"的老实状态。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .contract import ROOT, sha256_text
from .toc_review import (PROMPTS, _pdf_path, make_client,
                         render_toc_pages)

HERE = Path(__file__).resolve().parent

ARTIFACT = "toc.recover.json"
PROMPT_FILE = "recover_heading.md"
MAX_IMAGES = 6


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def missing_nodes(parse_dir: str | Path) -> list[dict]:
    """目录声明了、但正文**一行都没配上**的节点（`toc.alignment.json` 的账本）。

    只认这一种：`unmatched_authority`。**"配上了但被规则丢掉"的（dropped）不算** ——
    那些是另一类问题（见 D-22 的书眉），不该在这里被"恢复"。
    """
    parse_dir = Path(parse_dir)
    alignment = _load(parse_dir / "toc.alignment.json") or {}
    toc = _load(parse_dir / "toc.v2.json") or {}
    by_id = {str(n.get("toc_id")): n for n in (toc.get("nodes") or [])}
    out: list[dict] = []
    for row in alignment.get("unmatched_authority") or []:
        node = by_id.get(str(row.get("toc_id"))) or row
        out.append({
            "toc_id": row.get("toc_id"),
            "number": node.get("number"),
            "title": node.get("title"),
            "level": node.get("level"),
            "page": (node.get("page") or {}).get("pdf") or row.get("page"),
        })
    return out


def _node_block(nodes: list[dict]) -> str:
    return "\n".join(
        f"- `{n['toc_id']}`：目录记作 **{n.get('number')} {n.get('title')}**"
        f"（层级 {n.get('level')}，印在第 {n.get('page')} 页）"
        for n in nodes)


def recover_body_headings(parse_dir: str | Path, *, pdf: str | Path | None = None,
                     client=None, effort: str = "high",
                     log=None) -> dict:
    """看图把"目录有、正文缺"的标题行捡回来 → 写 `toc.recover.json`。"""
    parse_dir = Path(parse_dir)
    toc = _load(parse_dir / "toc.v2.json") or {}
    nodes = missing_nodes(parse_dir)
    pdf_path = Path(pdf) if pdf else _pdf_path(toc, parse_dir)

    if not nodes:
        payload = {"_meta": {"file_type": "toc_recover", "schema_version": "1.0",
                             "generator": "aiforstudy.parse.toc.recover",
                             "toc_sha256": sha256_text(json.dumps(toc.get("nodes") or [],
                                                                  ensure_ascii=False, sort_keys=True)),
                             "reason": "没有'目录有、正文缺'的节点，无需恢复"},
                   "recovered": []}
        (parse_dir / ARTIFACT).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload

    if pdf_path is None or not Path(pdf_path).is_file():
        raise ValueError("视觉恢复需要 PDF（要渲染那一页的截图）")

    pages = sorted({int(n["page"]) for n in nodes if n.get("page")})
    if len(pages) > MAX_IMAGES:
        pages = pages[:MAX_IMAGES]
        if log:
            log(f"  ⚠️ 缺标题的页有 {len(pages)} 张，只送前 {MAX_IMAGES} 张")
    shots = render_toc_pages(pdf_path, pages, parse_dir / "toc.recover.shots")
    if log:
        log(f"  渲染 {len(shots)} 张页图：{pages}")

    prompt = (PROMPTS / PROMPT_FILE).read_text(encoding="utf-8").replace(
        "%NODES%", _node_block(nodes))
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "请按要求只输出 JSON。"
                                    f"图片按顺序对应 PDF 第 {pages} 页。"},
    ]
    if client is None:
        client = make_client(effort=effort)
    result = client.chat_json(messages, tag="toc-recover",
                              images=[s["path"] for s in shots])
    data = result.get("data") or {}
    recovered, rejected = _validate(data.get("headings"), nodes)
    payload = {
        "_meta": {
            "file_type": "toc_recover",
            "schema_version": "1.0",
            "generator": "aiforstudy.parse.toc.recover",
            "toc_sha256": sha256_text(json.dumps(toc.get("nodes") or [],
                                                 ensure_ascii=False, sort_keys=True)),
            "pdf": str(pdf_path),
            "pages": pages,
            "images": len(shots),
            "tokens": (result.get("usage") or {}),
            "model": getattr(client, "model", ""),
            "effort": getattr(client, "effort", ""),
        },
        "recovered": recovered,
        "rejected": rejected,
    }
    payload["_meta"]["recovered_count"] = len(recovered)
    (parse_dir / ARTIFACT).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
    return payload


def _norm(text: str) -> str:
    return "".join(ch for ch in str(text or "") if ch.isalnum())


def _validate(rows, nodes: list[dict]) -> tuple[list[dict], list[dict]]:
    """**只接受"确实找到了、而且锚够长"的**；其余一律记进 `rejected`（不插）。"""
    by_id = {str(n["toc_id"]): n for n in nodes}
    recovered, rejected = [], []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        toc_id = str(row.get("toc_id") or "")
        target = by_id.get(toc_id)
        if target is None:
            rejected.append({"toc_id": toc_id, "why": "不在待恢复清单里"})
            continue
        if not row.get("found", True):
            rejected.append({"toc_id": toc_id, "why": "模型说这页上看不到标题行",
                             "note": str(row.get("note") or row.get("reason") or "")[:120]})
            continue
        title = str(row.get("printed_title") or row.get("title") or "").strip()
        anchor = str(row.get("first_line_after") or "").strip()
        if len(_norm(title)) < 4:
            rejected.append({"toc_id": toc_id, "why": f"标题太短/为空：{title!r}"})
            continue
        if len(_norm(anchor)) < 8:
            # **没有锚就不插**：宁可保持"目录有、正文缺"，也不把上一节的正文拦腰切断
            rejected.append({"toc_id": toc_id, "why": "没给标题后的第一行正文（无法定位插入点）",
                             "title": title})
            continue
        recovered.append({
            "toc_id": toc_id,
            "declared_title": target.get("title"),
            "number": str(row.get("number") or target.get("number") or "").strip(),
            "level": int(row.get("level") or target.get("level") or 2),
            "title": title,
            "first_line_after": anchor,
            "page": row.get("page") or target.get("page"),
            "confidence": str(row.get("confidence") or "").strip() or None,
            "recovered_by": "vision",
        })
    return recovered, rejected


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="看图恢复「目录有、正文缺」的标题行")
    ap.add_argument("parse_dir")
    ap.add_argument("--pdf")
    ap.add_argument("--effort", default="high")
    args = ap.parse_args(argv)
    parse_dir = Path(args.parse_dir)
    if not parse_dir.is_absolute():
        parse_dir = ROOT / parse_dir
    payload = recover_body_headings(parse_dir, pdf=args.pdf, effort=args.effort,
                               log=lambda m: print(m, flush=True))
    print(json.dumps({"recovered": len(payload["recovered"]),
                      "rejected": payload["rejected"],
                      "tokens": payload["_meta"].get("tokens")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
