"""T02：**证据定位**的行为测试（S01／S02 真实夹具 + 反例）。

这一组测试钉死的是调研 §1.2 里那两条真实反例，以及"不许放宽到能过"的边界：

| 用例 | 旧链路 | 本测试要求 |
|---|---|---|
| S01 引文用『……』拼三处正文 | `quote.strip() in text` → False | 拆成**多个真实 span**；定位不到的那一段必须 `invalid` 并点名 |
| S02 引文与正文只差 `\\int _ {0}` / `\\int_{0}` | 旧判据 False（误杀）；旧容差定位器什么都放行 | 归一后定位成功，**保留原文 offset 映射** |
| S02 缓存 `1a3d4d22…` 第 1 条（差 `{1} {2 \\pi}`） | 旧容差定位器**放行** | 必须 `invalid`（那不是控制序列／分组边界） |
| U+200B／错误符号／重复文本／多处匹配 | 没有这个概念 | 未授权字符 invalid；符号改动 invalid；多处 → ambiguous |

夹具只读：`tests/fixtures/audit_feedback/`（来源见其中 `SOURCES.md`，来自被保护的旧 run）。
计划依据：主计划 §5.2；验收 A03、A04。
"""
from __future__ import annotations

import hashlib
import inspect
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from aiforstudy.blocks.audit import evidence as evidence_module  # noqa: E402
from aiforstudy.blocks.audit.contracts import (  # noqa: E402
    CriterionVerdict,
    EvidenceSpan,
)
from aiforstudy.blocks.audit.evidence import (  # noqa: E402
    NORMALIZATION_RULE,
    UNAUTHORIZED_CHARS,
    locate_evidence,
    locate_quote,
    map_to_original,
    normalize_display_whitespace,
)

FIXTURES = ROOT / "tests" / "fixtures" / "audit_feedback"
POINTER = "/body_markdown"


# --------------------------------------------------------------------------- #
# 夹具装载（不复制、不改写；偏移一律从夹具自带的 `_verdict_analysis` 推）
# --------------------------------------------------------------------------- #
def load_case(case: str) -> tuple[dict, str, list[dict]]:
    analysis = json.loads((FIXTURES / f"{case}_analysis.json").read_text("utf-8"))
    raw = (FIXTURES / f"{case}_current_text_window.md").read_text("utf-8")
    text = raw[:-1] if raw.endswith("\n") else raw
    assert len(text) == analysis["window"]["len"], (case, len(text))
    responses = json.loads((FIXTURES / f"{case}_reviewer_responses.json").read_text("utf-8"))
    return analysis, text, responses


def artifact_for(unit: str, text: str) -> dict:
    return {"artifact_id": f"lesson:{unit}", "hash": "sha256:" + hashlib.sha256(
        text.encode("utf-8")).hexdigest(), "body_markdown": text}


def declared_verdict(quote: str, artifact: dict, *, unit: str, why: str,
                     criterion_id: str = "C-EVIDENCE-1", status: str = "pass",
                     role: str = "judge", artifact_hash: str | None = None) -> CriterionVerdict:
    """按复核原文造一条判词：引用是**待验证的声明 span**（start/end 还是占位）。"""
    span = EvidenceSpan(artifact_id=artifact["artifact_id"],
                        artifact_hash=artifact_hash or artifact["hash"],
                        json_pointer=POINTER, start=0, end=len(quote), quote=quote)
    return CriterionVerdict(ticket_id=f"ticket:{unit}", criterion_id=criterion_id,
                            criteria_version=1,
                            artifact_hash=artifact_hash or artifact["hash"],
                            status=status, why=why, spans=(span,),
                            verifier={"role": role, "model": "fixture", "version": "v1"})


def fixture_offsets(analysis: dict, detail: dict) -> list[tuple[int, int]]:
    """夹具测得的**可定位片段**的绝对 offset（在完整 body_markdown 里的下标）。"""
    out: list[tuple[int, int]] = []
    for part in detail["parts"]:
        if part.get("exact"):
            out.append((part["start"], part["end"]))
        elif part.get("stripped_exact_start", -1) >= 0:
            out.append((part["stripped_exact_start"],
                        part["stripped_exact_start"] + part["stripped_exact_len"]))
    return out


# --------------------------------------------------------------------------- #
# S01：省略号拼接 → 多个真实 span，定位不到的必须 invalid
# --------------------------------------------------------------------------- #
def test_s01_ellipsis_quote_splits_into_real_spans_and_still_fails_closed():
    analysis, text, responses = load_case("s01")
    artifact = artifact_for("CH04-S01", text)
    response = responses[0]                       # cache e8ea8279（第一条反例）
    verdict_raw = response["verdicts"][0]
    detail = response["_verdict_analysis"][0]
    assert detail["quote_has_ellipsis"] is True and detail["ellipsis_part_count"] == 3

    verdict = declared_verdict(verdict_raw["quote"], artifact, unit="CH04-S01",
                               why=verdict_raw["why"])
    resolution = locate_evidence(verdict, artifact)

    # 第 2 段是 `$u(x,y)=...` 而正文是 `$$u(x,y)=...,$$` —— 符号真的不同，
    # 不许凭相似度（最长公共 173 字）关单。
    assert resolution.status == "invalid", resolution.reason
    assert "第 2 段" in resolution.reason
    assert "相似度" in resolution.reason or "展示空白" in resolution.reason

    # 能逐字定位的两段必须给出**真实** span（不是拼接串本身）。
    assert len(resolution.spans) >= 2
    for span in resolution.spans:
        assert text[span.start:span.end] == span.quote
        assert span.artifact_hash == artifact["hash"]
    window_start = analysis["window"]["start"]
    located_abs = [(window_start + span.start, window_start + span.end)
                   for span in resolution.spans]
    assert located_abs == fixture_offsets(analysis, detail)
    # 绝不能把整串拼接文本当成一个 span 接受
    assert all(span.quote != verdict_raw["quote"] for span in resolution.spans)


def test_s01_different_cache_also_reports_the_pinned_part():
    """第二条 S01 缓存（`d07170e1…`）同样：第 2 段定位不到，理由要点到具体位置。"""
    analysis, text, responses = load_case("s01")
    artifact = artifact_for("CH04-S01", text)
    response = responses[1]
    verdict_raw = response["verdicts"][0]
    detail = response["_verdict_analysis"][0]
    resolution = locate_evidence(
        declared_verdict(verdict_raw["quote"], artifact, unit="CH04-S01",
                         why=verdict_raw["why"]), artifact)
    assert resolution.status == "invalid"
    assert "第 2 段" in resolution.reason
    window_start = analysis["window"]["start"]
    assert [(window_start + s.start, window_start + s.end) for s in resolution.spans] \
        == fixture_offsets(analysis, detail)


def test_s02_ellipsis_quote_with_all_parts_exact_is_valid_with_two_spans():
    """S02 第 5 条：『……』两段都逐字命中 → valid，且给出两个真实 span。"""
    analysis, text, responses = load_case("s02")
    artifact = artifact_for("CH04-S02", text)
    response = responses[0]
    verdict_raw = response["verdicts"][4]
    detail = response["_verdict_analysis"][4]
    assert detail["ellipsis_part_count"] == 2

    resolution = locate_evidence(
        declared_verdict(verdict_raw["quote"], artifact, unit="CH04-S02",
                         why=verdict_raw["why"]), artifact)
    assert resolution.status == "valid", resolution.reason
    assert len(resolution.spans) == 2
    window_start = analysis["window"]["start"]
    assert [(window_start + s.start, window_start + s.end) for s in resolution.spans] \
        == fixture_offsets(analysis, detail)
    for first, second in zip(resolution.spans, resolution.spans[1:]):
        assert second.start > first.end          # 有序、不重叠


# --------------------------------------------------------------------------- #
# S02：展示空白归一 + offset 映射
# --------------------------------------------------------------------------- #
def test_s02_display_whitespace_is_normalized_and_maps_back_to_original():
    analysis, text, responses = load_case("s02")
    artifact = artifact_for("CH04-S02", text)
    response = responses[0]
    verdict_raw = response["verdicts"][2]        # cache 46e9529d，第 3 条（232 字）
    detail = response["_verdict_analysis"][2]
    assert detail["strict_containment"] is False
    assert detail["parts"][0]["whitespace_normalized_in_body"] is True

    resolution = locate_evidence(
        declared_verdict(verdict_raw["quote"], artifact, unit="CH04-S02",
                         why=verdict_raw["why"]), artifact)
    assert resolution.status == "valid", resolution.reason
    assert len(resolution.spans) == 1
    span = resolution.spans[0]
    assert text[span.start:span.end] == span.quote      # 真实切片

    normalization = resolution.normalization
    assert normalization["rule"] == NORMALIZATION_RULE
    removed = normalization["removed"]
    assert removed, "必须记录被归一掉的展示空白"
    assert {entry["kind"] for entry in removed} <= set(evidence_module.DISPLAY_WHITESPACE_KINDS)

    # offset 映射必须能回到原文：映射表两端 + 逐点映射函数
    norm_start, original_start = normalization["mapped_offsets"][0]
    norm_end, original_end = normalization["mapped_offsets"][-1]
    assert (original_start, original_end) == (span.start, span.end)
    assert map_to_original(normalization, norm_start) == original_start
    assert map_to_original(normalization, norm_end - 1) == original_end - 1

    # 夹具测得的命中区域必须落在这个真实 span 之内（否则"映射回原文"是假的）
    window_start = analysis["window"]["start"]
    hit_start, hit_end = detail["parts"][0]["longest_common_in_body"]
    assert window_start + span.start <= hit_start
    assert window_start + span.end >= hit_end
    # 归一后的引文与归一后的真实切片必须一致
    assert normalize_display_whitespace(span.quote)[0] \
        == normalize_display_whitespace(verdict_raw["quote"])[0]


def test_s02_symbol_boundary_difference_is_not_normalized_away():
    """缓存 `1a3d4d22…` 第 1 条：差的是 `{1} {2 \\pi}` vs `{1}{2 \\pi}` —— 不许放宽规则让它过。"""
    _analysis, text, responses = load_case("s02")
    artifact = artifact_for("CH04-S02", text)
    response = responses[1]
    verdict_raw = response["verdicts"][0]
    detail = response["_verdict_analysis"][0]
    assert detail["strict_containment"] is False
    assert detail["parts"][0]["whitespace_normalized_in_body"] is False

    resolution = locate_evidence(
        declared_verdict(verdict_raw["quote"], artifact, unit="CH04-S02",
                         why=verdict_raw["why"]), artifact)
    assert resolution.status == "invalid", resolution.reason
    assert "展示空白" in resolution.reason
    # 这不是"只差一处空格"就放行的场合：`}` 与 `{` 之间**不在**允许归一的边界里
    assert normalize_display_whitespace(r"\frac {1} {2 \pi}")[0] \
        != normalize_display_whitespace(r"\frac {1}{2 \pi}")[0]


# --------------------------------------------------------------------------- #
# 归一器本身的边界（三条"禁止"）
# --------------------------------------------------------------------------- #
def test_normalizer_only_touches_display_whitespace_at_allowed_boundaries():
    normalize = lambda text: normalize_display_whitespace(text)[0]  # noqa: E731

    assert normalize(r"\int _ {0}") == r"\int_{0}"          # 控制序列 + 上下标
    assert normalize(r"\int_ {0}") == r"\int_{0}"
    assert normalize(r"\int _ {0} ^ {2 \pi}") == normalize(r"\int_ {0} ^ {2 \pi}")

    # ① 一般空格不许去掉
    assert normalize("a b") == "a b"
    # ② 符号／上下标本身不许改
    assert normalize("x^2 + y_1") == "x^2 + y_1"
    assert normalize("x^2") != normalize("x_2")
    # ③ `\text{...}` 内部文本一格都不许动（这里逗号后的空格本来会被 `group_open` 规则吃掉）
    assert normalize(r"\text{a, b}") == r"\text{a, b}"
    assert normalize(r"\text{a }") == r"\text{a }"
    removed_kinds = {entry["kind"] for entry in
                     normalize_display_whitespace(r"\text{a, b}")[2]}
    assert removed_kinds == set()


def test_unauthorized_characters_are_rejected_with_a_specific_reason():
    text = "甲句。乙句。"
    artifact = artifact_for("CH04-S01", text)
    quote = "甲\u200b句。"
    resolution = locate_evidence(
        declared_verdict(quote, artifact, unit="CH04-S01", why="看起来一样"), artifact)
    assert resolution.status == "invalid"
    assert "U+200B" in resolution.reason
    assert UNAUTHORIZED_CHARS["\u200b"] in resolution.reason
    # 去掉零宽字符之后**确实**能命中 —— 正因如此才必须拒绝（不许悄悄去掉它）
    assert "甲句。" in text


def test_wrong_symbol_and_repeated_text_do_not_close():
    text = "折线路径给出 $u=-4xy-y+C$，于是 $C=1$。"
    artifact = artifact_for("CH04-S01", text)
    wrong = locate_evidence(
        declared_verdict("折线路径给出 $u=-4xy-y+D$", artifact, unit="CH04-S01",
                         why="公式对得上"), artifact)
    assert wrong.status == "invalid"
    assert "最长公共片段" in wrong.reason          # 理由要点到具体位置，不许一句"找不到"

    repeated_text = "目标句。别的。目标句。"
    repeated = locate_evidence(
        declared_verdict("目标句。", artifact_for("CH04-S01", repeated_text),
                         unit="CH04-S01", why="它还在"), artifact_for("CH04-S01",
                                                                     repeated_text))
    assert repeated.status == "ambiguous"
    assert len(repeated.matched_offsets) == 2


def test_normalized_multiple_matches_are_ambiguous_not_first_match():
    text = r"前 $x \int _ {0} y$ 后 $x \int _ {0} y$"
    artifact = artifact_for("CH04-S02", text)
    resolution = locate_evidence(
        declared_verdict(r"x \int_{0} y", artifact, unit="CH04-S02",
                         why="只差展示空白"), artifact)
    assert resolution.status == "ambiguous"
    assert len(resolution.matched_offsets) >= 2
    assert "第一处" in resolution.reason


def test_stale_artifact_hash_and_missing_spans_are_invalid():
    text = "甲句。"
    artifact = artifact_for("CH04-S01", text)
    stale = locate_evidence(
        declared_verdict("甲句。", artifact, unit="CH04-S01", why="改好了",
                         artifact_hash="sha256:" + "f" * 64), artifact)
    assert stale.status == "invalid"
    assert "旧版本" in stale.reason

    no_span = CriterionVerdict(ticket_id="ticket:CH04-S01", criterion_id="C1",
                               criteria_version=1, artifact_hash=artifact["hash"],
                               status="needs_evidence", why="还没核", spans=(),
                               verifier={"role": "judge"})
    missing = locate_evidence(no_span, artifact)
    assert missing.status == "invalid"
    assert "没有给出任何引用片段" in missing.reason


# --------------------------------------------------------------------------- #
# 调用次数：定位证据不许调模型、也不许出网
# --------------------------------------------------------------------------- #
def test_locate_evidence_makes_zero_model_calls(monkeypatch):
    """S01／S02 的定位全跑一遍，模型调用数必须是 0（A03：只修证据，不新增正文调用）。"""
    import offline_guard

    from aiforstudy.providers import gateway as gateway_module
    from aiforstudy.providers import transport as transport_module

    assert offline_guard.is_installed()
    calls = {"gateway": 0, "transport": 0}

    def boom_gateway(*_args, **_kwargs):
        calls["gateway"] += 1
        raise AssertionError("locate_evidence 不许发起模型调用")

    def boom_transport(*_args, **_kwargs):
        calls["transport"] += 1
        raise AssertionError("locate_evidence 不许走真实传输")

    monkeypatch.setattr(gateway_module.Gateway, "call", boom_gateway)
    monkeypatch.setattr(transport_module.RealTransport, "__call__", boom_transport)

    checked = 0
    for case in ("s01", "s02"):
        _analysis, text, responses = load_case(case)
        artifact = artifact_for("CH04-S01" if case == "s01" else "CH04-S02", text)
        for response in responses:
            for raw in response["verdicts"]:
                locate_evidence(declared_verdict(raw["quote"], artifact,
                                                 unit=artifact["artifact_id"],
                                                 why=raw["why"]), artifact)
                checked += 1
    assert checked == 10
    assert calls == {"gateway": 0, "transport": 0}

    # 结构上也钉死：这个模块不 import 任何"会调模型"的东西
    source = inspect.getsource(evidence_module)
    for forbidden in ("chat_json", "Gateway", "transport", "providers"):
        assert forbidden not in source


def test_locate_quote_requires_hash_and_pointer():
    """没有 hash／pointer 就没有"对应哪一版、在哪一段"——宁可拒绝。"""
    resolution = locate_quote("甲句。", "甲句。", artifact_id="lesson:X",
                              artifact_hash="", json_pointer=POINTER)
    assert resolution.status == "invalid" and "hash" in resolution.reason
    resolution = locate_quote("甲句。", "甲句。", artifact_id="lesson:X",
                              artifact_hash="sha256:x", json_pointer="")
    assert resolution.status == "invalid" and "json_pointer" in resolution.reason


@pytest.mark.parametrize("case", ["s01", "s02"])
def test_fixture_windows_are_the_protected_run_slices(case):
    """夹具自检：窗口就是被保护 run 切出来的那一段，不是"差不多的引用"。"""
    analysis, text, _responses = load_case(case)
    assert analysis["window"]["len"] == len(text)
    for detail in analysis["window_exact_span_coverage"]:
        start = detail["start"] - analysis["window"]["start"]
        end = detail["end"] - analysis["window"]["start"]
        assert 0 <= start < end <= len(text)


@pytest.mark.parametrize('text', ['教材原文。\n', 'a\t \n', '\\int \n', '{ \n', '\\text{尾部 \n'])
def test_trailing_whitespace_is_not_a_math_boundary(text):
    normalized, index_map, removed = normalize_display_whitespace(text)
    assert normalized == text
    assert index_map == list(range(len(text)))
    assert removed == []


def test_missing_quote_in_newline_terminated_source_is_invalid_not_exception():
    result = locate_quote('不存在的教材证据', '第一句教材。\n第二句教材。\n',
                          artifact_id='source:CH04', artifact_hash='sha256:test',
                          json_pointer='/text')
    assert result.status == 'invalid'
