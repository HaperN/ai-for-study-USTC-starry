"""M10 建议稿 → 可应用复核的**桥**：没有它，视觉复核的结论永远进不来。

## 这个缺口实测长什么样

真实书上那两条标题分歧（`狄利克雷` vs `狭利克雷`、`幂级数` vs `基级数`）
**已经被带图的视觉复核裁决过了**（模型看着目录页截图，`action=conflict`、
`chosen_source=body-heading-shapes`），但结论**永远进不了 `data/parse/.../toc.review.json`**：

```
review_toc(...)   → 建议稿：顶层 suspects/decisions/summary，**inputs_digest = None**
apply_review(...) → 要求 inputs_digest == expected_inputs_digest(parse_root)
                    ⇒ 建议稿**必然**被判"不可消费旧决策"（§15 A08）
```

中间**没有任何转换**。代价实测：CH04 那一章报出 32 条 `needs_review`，
其中 **22 条**就是这么来的 —— 而且被 `toc` 与 `export` **各报一次**。
所以那一章的"不合格"**主要不是内容问题，是答案没送到**。

## 判据

1. 转换出来的复核**指纹必须对得上**（否则照样被拒）；
2. 裁决**要落到正确的 `toc_id`** 上（草稿按 `line` 记、`toc_id=null`；分歧按 `toc_id` 记）；
3. **对不上就报错，不静默丢** —— 悄悄丢一条裁决，读的人会以为"复核没提到它"；
4. **弃权（`unsure`）要变成 `leave-pending`**，不许变成"已裁决"（那是编结论）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aiforstudy.blocks.parse.review import (DirectoryReview, apply_review,
                                            expected_inputs_digest, review_from_draft)
from aiforstudy.errors import ContractError

ROOT = Path(__file__).resolve().parents[2]
REAL_PARSE = ROOT / "data" / "parse" / "complex-analysis-v2"

#: 真实分歧的形状（取自 `toc.v2.json`，逐字）
DISAGREEMENTS = [
    {"kind": "body-title", "toc_id": "T-0020", "title": "调和函数的性质和狭利克雷问题",
     "printed": "4.2 调和函数的性质和狭利克雷问题",
     "body": "4.2 调和函数的性质和狄利克雷问题",
     "resolution": "留待模型视觉复核裁决（以目录页截图为准，P7）", "arbiter": "pending",
     "why": "正文…狄利克雷…目录…狭利克雷…"},
    {"kind": "body-title", "toc_id": "T-0023", "title": "基级数", "printed": "5.2 基级数",
     "body": "5.2 幂级数", "resolution": "留待模型视觉复核裁决", "arbiter": "pending",
     "why": "正文…幂级数…目录…基级数…"},
]


def _parse_root(tmp_path: Path) -> Path:
    """最小 parse 根：只要 `expected_inputs_digest` 能算出指纹即可。"""
    (tmp_path / "toc.v2.json").write_text(json.dumps(
        {"_meta": {"pdf_sha256": "sha256:pdf", "document_sha256": "sha256:doc",
                   "sources_used": ["printed-toc", "body-heading-shapes"]},
         "nodes": [], "disagreements": DISAGREEMENTS}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "toc.draft.json").write_text(json.dumps({"nodes": []}), encoding="utf-8")
    return tmp_path


def _draft(decisions) -> dict:
    return {"_meta": {}, "suspects": [], "decisions": decisions, "summary": {}}


# ────────────────────────────────────────── 判据 1+2

def test_a_conflict_decision_lands_on_the_right_disagreement(tmp_path):
    """**判据 1+2**：指纹对得上、且裁决落到正确的 `toc_id` 上。"""
    root = _parse_root(tmp_path)
    draft = _draft([
        {"line": 20, "action": "conflict", "toc_id": None, "number": "4.2",
         "title": "调和函数的性质和狄利克雷问题",
         "chosen_source": "body-heading-shapes",
         "resolution": "目录页截图上印的是「狄」", "reason": "第 1 张截图…清楚印着…",
         "confidence": "high"},
        {"line": 23, "action": "conflict", "toc_id": None, "number": "5.2",
         "title": "幂级数", "chosen_source": "body-heading-shapes",
         "resolution": "首字是「幂」不是「基」", "reason": "第 2 张截图…",
         "confidence": "high"},
    ])
    review = review_from_draft(draft, root, disagreements=DISAGREEMENTS)
    assert review.inputs_digest == expected_inputs_digest(root), \
        "转换出来的指纹必须与期望一致，否则照样被当成过期拒绝"
    subjects = {d.subject: d for d in review.decisions}
    assert set(subjects) == {"T-0020", "T-0023"}
    assert subjects["T-0020"].title == "调和函数的性质和狄利克雷问题"
    assert subjects["T-0023"].title == "幂级数"
    assert all(d.verdict == "manual-title" for d in review.decisions)

    applied = apply_review(review, expected_inputs_digest=expected_inputs_digest(root),
                           disagreements=DISAGREEMENTS)
    assert applied.stale is False and len(applied.applied) == 2
    assert applied.pending == (), "两条都裁决了，不该还有 pending"


# ────────────────────────────────────────── 判据 3

def test_an_unmatchable_decision_is_a_hard_error_not_a_silent_drop(tmp_path):
    """**判据 3**：对不上就报错。静默丢一条裁决 = 读的人以为"复核没提到它"。"""
    root = _parse_root(tmp_path)
    draft = _draft([{"line": 99, "action": "conflict", "toc_id": None, "number": "9.9",
                     "title": "一条本书里根本没有的标题", "chosen_source": "printed-toc",
                     "reason": "x"}])
    with pytest.raises(ContractError, match="对不上"):
        review_from_draft(draft, root, disagreements=DISAGREEMENTS)


def test_an_unsure_decision_becomes_leave_pending_not_a_verdict(tmp_path):
    """**判据 4**：弃权 → `leave-pending`，**不许**变成"已裁决"。

    真实书上第一次无图复核就是这种：模型如实说"没有截图，不能裁决"。
    如果那一轮被当成"已裁决"，我就会以为目录复核做完了。
    """
    root = _parse_root(tmp_path)
    draft = _draft([{"line": 20, "action": "unsure", "toc_id": None, "number": "4.2",
                     "title": "调和函数的性质和狄利克雷问题",
                     "resolution": "本轮未附任何目录页截图（0 张图），无法核对",
                     "reason": "缺少截图时不能据正文直接裁决", "confidence": "low"}])
    review = review_from_draft(draft, root, disagreements=DISAGREEMENTS)
    assert [d.verdict for d in review.decisions] == ["leave-pending"]
    applied = apply_review(review, expected_inputs_digest=expected_inputs_digest(root),
                           disagreements=DISAGREEMENTS)
    assert applied.applied == (), "弃权**不是**裁决"
    assert [n.subject for n in applied.pending] == ["T-0020"], "弃权要留在 pending 里"


def test_a_stale_digest_is_still_refused(tmp_path):
    """指纹对不上时**仍然**拒绝（§15 A08）—— 这条桥不许把 A08 拆掉。"""
    root = _parse_root(tmp_path)
    review = DirectoryReview(inputs_digest="sha256:another-input", decisions=())
    applied = apply_review(review, expected_inputs_digest=expected_inputs_digest(root),
                           disagreements=DISAGREEMENTS)
    assert applied.stale is True and "另一份输入" in applied.reason


# ────────────────────────────────────────── 真实数据

@pytest.mark.skipif(not (REAL_PARSE / "toc.v2.json").is_file(),
                    reason="没有真实书产物")
def test_the_real_draft_converts_and_applies():
    """**端到端用真实数据**：真实草稿 → 转换 → 应用，两条都落地。

    这条最重要：它证明那 22 条"未裁决"**不是内容问题**，而是一条接线缺失。
    """
    import glob
    drafts = sorted(glob.glob(str(ROOT / "data" / "books" / "complex-analysis-v2"
                                  / "runs" / "*tocreview2" / "toc.review.suggested.json")))
    if not drafts:
        pytest.skip("还没有生成过建议稿")
    toc = json.loads((REAL_PARSE / "toc.v2.json").read_text(encoding="utf-8"))
    draft = json.loads(Path(drafts[-1]).read_text(encoding="utf-8"))
    review = review_from_draft(draft, REAL_PARSE,
                               disagreements=toc.get("disagreements") or [],
                               produced_by="测试")
    assert len(review.decisions) == 2
    applied = apply_review(review, expected_inputs_digest=expected_inputs_digest(REAL_PARSE),
                           disagreements=toc.get("disagreements") or [])
    assert applied.stale is False
    assert len(applied.applied) == 2 and not applied.pending
    titles = sorted(d.title for d in applied.applied)
    assert titles == ["幂级数", "调和函数的性质和狄利克雷问题"]
