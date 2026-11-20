"""目录层（`blocks/parse/toc/`）—— 解析层重构的新块，**独立于 `adapt.py` 的写入路径**。

## 为什么另起一块（实施方案 H9）

`blocks/parse/mineru/adapt.py` 现在也会写一份 `toc.json`（schema 1.0），
而四本已交付产物的 `toc.json` / `chapters.json` 还在被 `tools/parse_report.py`
与设计文档引用。重构**不许覆盖**它们（H2），所以：

* 新块写自己的文件名：`toc.preflight.json` / `toc.draft.json` / `toc.json`(v2) /
  `toc.review.json` / `toc.preview.json` / `body.json`；
* 新块**只读** PDF 与 `document.md`，绝不写回 `adapt.py` 的产物路径；
* 两边产物并存，用来对比"新的比旧的好在哪"。

## 链条（每一步独立、只用 JSON 交接）

```
PDF ──sources──▶ 三源候选 ──reconcile──▶ toc.json(v2) ──review──▶ toc.review.json
                                    │                                  │
                              preflight                          apply → toc.json 终稿
                                    │                                  │
                                preview ◀──────────────────────────────┘
                                    │
                                  body ──▶ body.json
```

## 四条硬规矩（设计稿 §2.4，实现时逐条遵守）

* **R-a 只增不丢**：目录里的条目无论正文有没有配上，一律进 `nodes`；
* **R-b 不确定要说出来**：抽不出的进 `unresolved`、对不上的进 `disagreements`、
  可疑的进 `suspicious`、拿不准的标 `confirmed: false`；
* **R-c 不猜**：对不齐就如实记，宁可 `confidence: low` 也不编一个像样的层级；
* **R-d 书的目录是唯一骨架**：正文排版推断无权新增或删除章节点。
"""
from __future__ import annotations

from .contract import (  # noqa: F401
    GENERATOR,
    SCHEMA_VERSION,
    ContractError,
    build_toc,
    counts_of,
    empty_toc,
    read_json,
    validate_toc,
    write_json,
    write_toc,
)

__all__ = [
    "GENERATOR", "SCHEMA_VERSION", "ContractError",
    "build_toc", "counts_of", "empty_toc", "read_json", "validate_toc",
    "write_json", "write_toc",
]
