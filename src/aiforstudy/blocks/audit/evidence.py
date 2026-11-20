"""证据定位：把「审核员说它改好了」变成**正文里的真实切片**（§5.2）。

## 这个模块治的是什么病

调研 §1.2 记录了两条真实反例（夹具见 `tests/fixtures/audit_feedback/`）：

| 反例 | 旧链路的表现 | 后果 |
|---|---|---|
| **S01** 判词用『……』把三处正文拼成一段引文 | `quote.strip() in current_text` 直接 False | 内容其实对得上，却永远关不掉；下一轮还去让**正文生成器**改（责任分派错误） |
| **S02** 引文与正文只差 LaTeX 展示空白（`\\int _ {0}` vs `\\int_{0}`） | 同上，False；而旧的容差定位器 `lib/text.locates_in` 又**什么都放行** | 一边把对的判成错的，一边把错的判成对的 |

所以定位必须分成**明确的三档**，且每一档都有真实 span 或明确理由：

* `valid` —— 给出**真实切片**（`quote == text[start:end]`）。允许的"弹性"只有一种：
  **控制序列／分组／上下标边界的展示空白**，并且保留原文 offset 映射；
* `ambiguous` —— 归一后正文里有多处匹配。宁可让人来看，**绝不挑第一处**；
* `invalid` —— 逐字找不到、且差异**不只是**允许的展示空白（符号、变量、上下标、`\\text{}`
  内部文本、未授权字符），理由要点到具体位置。

## 三条明确的"禁止"（§5.2 原文）

1. 禁止去掉一般空格（`a b` 还是 `a b`）；
2. 禁止去掉全部符号／靠相似度匹配（最多报一个"最长公共片段"供人看，但**不据此关单**）；
3. 禁止改变 `\\text{a b}` 内部的文本 —— `\\text{...}` 一类宏的组内**一格空白都不归一**。

## 这个模块不做什么

不调模型、不调度、不写文件。定位是纯字符串函数，因此可以离线、可复现
（`test_audit_evidence.py::test_locate_evidence_makes_zero_model_calls` 钉死这一点）。
"""
from __future__ import annotations

import difflib
import re

from .contracts import CriterionVerdict, EvidenceResolution, EvidenceSpan

#: 归一规则的版本号。写进 `EvidenceResolution.normalization["rule"]`，
#: 让"这条证据是靠哪一版归一才成立的"可复核、可回溯。
NORMALIZATION_RULE = "latex_display_whitespace@1"

#: 引文两端去空白这条**轻归一**的规则名（只切引文自身，不动正文）。
OUTER_STRIP_RULE = "outer_strip@1"

#: 引文里**绝不允许**出现、也**绝不**被归一掉的字符（零宽／双向控制／软连字符）。
#: 为什么单列：它们肉眼看不见，一旦"顺手去掉就能匹配"，就等于允许引文与正文不一致。
UNAUTHORIZED_CHARS: dict[str, str] = {
    "\u200b": "ZERO WIDTH SPACE",
    "\u200c": "ZERO WIDTH NON-JOINER",
    "\u200d": "ZERO WIDTH JOINER",
    "\u2060": "WORD JOINER",
    "\ufeff": "ZERO WIDTH NO-BREAK SPACE / BOM",
    "\u00ad": "SOFT HYPHEN",
    "\u180e": "MONGOLIAN VOWEL SEPARATOR",
    "\u202a": "LEFT-TO-RIGHT EMBEDDING",
    "\u202b": "RIGHT-TO-LEFT EMBEDDING",
    "\u202c": "POP DIRECTIONAL FORMATTING",
    "\u202d": "LEFT-TO-RIGHT OVERRIDE",
    "\u202e": "RIGHT-TO-LEFT OVERRIDE",
    "\u2066": "LEFT-TO-RIGHT ISOLATE",
    "\u2067": "RIGHT-TO-LEFT ISOLATE",
    "\u2068": "FIRST STRONG ISOLATE",
    "\u2069": "POP DIRECTIONAL ISOLATE",
}

#: 省略号标记：只认中文省略号（`……` / `…`）。`\dots` 是 LaTeX 命令，不是拼接符。
_ELLIPSIS_RE = re.compile(r"『…+』|「…+」|…+")

#: `\text{...}` 一类**其内容属于可见文本**的宏。它们组内不做任何空白归一。
_TEXT_MACROS = frozenset({"text", "textrm", "textnormal", "textup", "textit", "textsl",
                          "mbox", "hbox"})

#: 允许归一的展示空白种类（会进 `normalization["removed"][i]["kind"]`）。
DISPLAY_WHITESPACE_KINDS = ("control_sequence_boundary", "sub_sup_brace",
                            "group_open", "group_close")

_CONTROL_SEQUENCE_TAIL = re.compile(r"\\[A-Za-z]+$")


# --------------------------------------------------------------------------- #
# 归一：只动"控制序列／分组／上下标边界的展示空白"
# --------------------------------------------------------------------------- #
def _text_group_mask(text: str) -> list[bool]:
    """标出每个字符是否落在 `\\text{...}` 一类宏的组内（组内不归一）。"""
    mask = [False] * len(text)
    for match in re.finditer(r"\\([A-Za-z]+)[ \t]*\{", text):
        if match.group(1) not in _TEXT_MACROS:
            continue
        brace = text.index("{", match.start())
        depth = 0
        index = brace
        while index < len(text):
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        stop = min(index, len(text) - 1)
        for position in range(brace, stop + 1):
            mask[position] = True
    return mask


def _classify_whitespace(text: str, start: int, stop: int,
                         mask: list[bool]) -> str | None:
    """一段空白（`[start, stop)`）算不算"允许归一的展示空白"。

    四类边界，逐条对应 §5.2 的原话：

    * `\\int _` → `\\int_`：控制序列与 `_`／`^`／`{` 之间；
    * `_ {` → `_{`：上下标与分组之间；
    * `{ 0`／`, x` → `{0`／`,x`：分组开始符之后；
    * `0 }` → `0}`：分组结束符之前。

    落在这四类之外的空白（例如 `{1} {2 \\pi}` 里 `}` 与 `{` 之间那个空格）
    **一律不动** —— 那正是 S02 缓存 `1a3d4d22…` 的判词必须失败的原因。
    """
    # A display boundary needs characters on both sides. In particular,
    # stop == len(text) is ordinary trailing whitespace, not a mask index.
    if start == 0 or stop >= len(text):
        return None
    if mask[start] or mask[stop]:
        return None
    previous = text[start - 1]
    following = text[stop] if stop < len(text) else ""
    if following in "_^{" and _CONTROL_SEQUENCE_TAIL.search(text[:start]):
        return "control_sequence_boundary"
    if previous in "_^" and following == "{":
        return "sub_sup_brace"
    if previous in "{,":
        return "group_open"
    if following in "}]":
        return "group_close"
    return None


def normalize_display_whitespace(text: str) -> tuple[str, list[int], list[dict]]:
    """把"展示空白"归一掉，并给出**原文 offset 映射**。

    返回 `(normalized, index_map, removed)`：

    * `normalized` —— 归一后的文本；
    * `index_map[i]` —— 归一文本第 `i` 个字在**原文**里的下标（用于把匹配映射回原文）；
    * `removed` —— 被去掉的每个字符 `{"index", "char", "kind"}`（index 是原文下标）。
    """
    text = str(text or "")
    mask = _text_group_mask(text)
    out_chars: list[str] = []
    index_map: list[int] = []
    removed: list[dict] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            stop = index
            while stop < len(text) and text[stop].isspace():
                stop += 1
            kind = _classify_whitespace(text, index, stop, mask)
            if kind:
                for position in range(index, stop):
                    removed.append({"index": position, "char": text[position],
                                    "kind": kind})
                index = stop
                continue
        out_chars.append(char)
        index_map.append(index)
        index += 1
    return "".join(out_chars), index_map, removed


def _shift_table(index_map: list[int]) -> list[list[int]]:
    """把 `index_map` 压成"位移发生变化"的折点表：`[[归一坐标, 原文坐标, 位移], ...]`。

    它**完整决定**归一坐标 → 原文坐标的映射（两折点之间位移不变），
    所以可以既放进证据 JSON、又能被 `map_to_original` 复算。
    """
    shifts: list[list[int]] = []
    for norm_index, original_index in enumerate(index_map):
        delta = original_index - norm_index
        if not shifts or shifts[-1][2] != delta:
            shifts.append([norm_index, original_index, delta])
    return shifts


def map_to_original(normalization: dict, norm_index: int) -> int:
    """把归一坐标映射回原文坐标（`normalization["shift_table"]` 的逆用）。"""
    shifts = list(normalization.get("shift_table") or [])
    if not shifts:
        return int(norm_index)
    chosen = shifts[0]
    for entry in shifts:
        if entry[0] <= norm_index:
            chosen = entry
        else:
            break
    return int(norm_index) + int(chosen[2])


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _matches(haystack: str, needle: str) -> list[int]:
    """`needle` 在 `haystack` 里所有**不重叠**出现的起点（与 `str.count` 同口径）。"""
    if not needle:
        return []
    out: list[int] = []
    cursor = 0
    while True:
        position = haystack.find(needle, cursor)
        if position < 0:
            return out
        out.append(position)
        cursor = position + len(needle)


def _first_unauthorized(text: str) -> tuple[str, str] | None:
    for char in str(text or ""):
        name = UNAUTHORIZED_CHARS.get(char)
        if name:
            return char, name
    return None


def _codepoint(char: str) -> str:
    return f"U+{ord(char):04X}"


def _strip_unauthorized(text: str) -> str:
    return "".join(c for c in str(text or "") if c not in UNAUTHORIZED_CHARS)


def _diff_hint(needle: str, haystack: str) -> str:
    """给一个**具体**的失败理由：最长公共片段落在哪、两边起头分别是什么。

    刻意只作为**给人看的线索**：它不参与任何"算不算通过"的判断 ——
    凭相似度关单正是 S02 第一条反例的入口。
    """
    matcher = difflib.SequenceMatcher(None, needle, haystack, autojunk=False)
    a, b, size = matcher.find_longest_match(0, len(needle), 0, len(haystack))
    return (f"最长公共片段只有 {size} 字（引文[{a}:{a + size}] ↔ 正文[{b}:{b + size}]）；"
            f"引文起头 {needle[:24]!r}，正文对应处 {haystack[b:b + 24]!r}")


def _span(artifact_id: str, artifact_hash: str, json_pointer: str,
          start: int, end: int, text: str) -> EvidenceSpan:
    return EvidenceSpan(artifact_id=artifact_id, artifact_hash=artifact_hash,
                        json_pointer=json_pointer, start=int(start), end=int(end),
                        quote=text[int(start):int(end)])


# --------------------------------------------------------------------------- #
# 单段定位
# --------------------------------------------------------------------------- #
def _locate_one(needle: str, text: str, *, artifact_id: str, artifact_hash: str,
                json_pointer: str) -> dict:
    """在 `text` 里定位**一段**引文；返回 `{status, reason, span, offsets, normalization}`。"""
    raw = str(needle or "")
    effective = raw.strip()
    if not effective:
        return {"status": "invalid", "reason": "引文是空白，没有任何可定位的内容",
                "span": None, "offsets": (), "normalization": {}}

    unauthorized = _first_unauthorized(effective)
    if unauthorized is not None:
        char, name = unauthorized
        return {"status": "invalid",
                "reason": f"引文里含未授权字符 {_codepoint(char)}（{name}）—— "
                          f"不许靠去掉它来匹配，请从正文里逐字重抄这一段",
                "span": None, "offsets": (), "normalization": {}}

    exact = _matches(text, effective)
    if len(exact) == 1:
        start = exact[0]
        normalization = {}
        if effective != raw:
            normalization = {"rule": OUTER_STRIP_RULE,
                             "removed": [],
                             "shift_table": [[0, start, start - 0]],
                             "mapped_offsets": [[0, start], [len(effective),
                                                             start + len(effective)]]}
        return {"status": "valid", "reason": f"逐字命中，span=[{start},{start + len(effective)})",
                "span": _span(artifact_id, artifact_hash, json_pointer, start,
                              start + len(effective), text),
                "offsets": ((start, start + len(effective)),),
                "normalization": normalization}
    if len(exact) > 1:
        offsets = tuple((position, position + len(effective)) for position in exact)
        return {"status": "ambiguous",
                "reason": f"逐字命中 {len(exact)} 处 —— 必须人工确认指哪一处，不许挑第一处",
                "span": None, "offsets": offsets, "normalization": {}}

    normalized_text, index_map, removed = normalize_display_whitespace(text)
    normalized_quote, _, quote_removed = normalize_display_whitespace(effective)

    if normalized_quote == effective and normalized_text == text:
        return {"status": "invalid",
                "reason": "逐字找不到，而且正文与引文都没有可归一的展示空白："
                          + _diff_hint(effective, text),
                "span": None, "offsets": (), "normalization": {}}

    hits = _matches(normalized_text, normalized_quote)
    if len(hits) == 1:
        norm_start = hits[0]
        norm_end = norm_start + len(normalized_quote)
        original_start = index_map[norm_start]
        original_end = index_map[norm_end - 1] + 1
        inside = [entry for entry in removed
                  if original_start <= entry["index"] < original_end]
        normalization = {
            "rule": NORMALIZATION_RULE,
            "removed": inside,
            "shift_table": _shift_table(index_map),
            "mapped_offsets": [[norm_start, original_start], [norm_end, original_end]],
            "normalized_match": [norm_start, norm_end],
            "quote_removed": list(quote_removed),
        }
        return {"status": "valid",
                "reason": f"逐字差 {len(inside)} 处展示空白，归一后唯一命中 "
                          f"正文[{original_start},{original_end})（已映射回原文）",
                "span": _span(artifact_id, artifact_hash, json_pointer, original_start,
                              original_end, text),
                "offsets": ((original_start, original_end),),
                "normalization": normalization}
    if len(hits) > 1:
        offsets = []
        for norm_start in hits:
            norm_end = norm_start + len(normalized_quote)
            offsets.append((index_map[norm_start], index_map[norm_end - 1] + 1))
        return {"status": "ambiguous",
                "reason": f"展示空白归一后正文里有 {len(hits)} 处匹配 —— "
                          f"必须人工确认指哪一处，不许挑第一处",
                "span": None, "offsets": tuple(offsets),
                "normalization": {"rule": NORMALIZATION_RULE,
                                  "normalized_match": list(hits),
                                  "shift_table": _shift_table(index_map)}}

    stripped_bad = _strip_unauthorized(effective)
    unauthorized = _first_unauthorized(effective)
    if unauthorized is not None and stripped_bad != effective and _matches(text, stripped_bad):
        char, name = unauthorized
        return {"status": "invalid",
                "reason": f"正文里逐字存在的是**去掉未授权字符 {_codepoint(char)}"
                          f"（{name}）之后**的版本 —— 那不算逐字命中，"
                          f"请从正文里重抄这一段",
                "span": None, "offsets": (), "normalization": {}}

    return {"status": "invalid",
            "reason": "逐字找不到，差异也不只是控制序列／分组／上下标边界的展示空白："
                      + _diff_hint(effective, normalized_text),
            "span": None, "offsets": (), "normalization": {}}


# --------------------------------------------------------------------------- #
# 冻结接口
# --------------------------------------------------------------------------- #
def locate_quote(quote: str, text: str, *, artifact_id: str, artifact_hash: str,
                 json_pointer: str) -> EvidenceResolution:
    """定位**一段（可能带『……』的）引文**。`locate_evidence` 的单段内核。"""
    needle = str(quote or "")
    if not needle.strip():
        return EvidenceResolution(status="invalid",
                                  reason="引用为空或全空白 —— 没有引用的『已修好』不算证据")
    if not str(artifact_id or "").strip() or not str(artifact_hash or "").strip():
        return EvidenceResolution(
            status="invalid",
            reason="定位引文必须同时给出 artifact_id 与 artifact_hash："
                   "没有 hash 就分不清这条证据对应哪一版")
    if not str(json_pointer or "").strip():
        return EvidenceResolution(status="invalid",
                                  reason="引用没有指明 json_pointer，无法确定它在哪一段文本里")

    if _ELLIPSIS_RE.search(needle):
        whole = _locate_one(needle, text, artifact_id=artifact_id,
                            artifact_hash=artifact_hash, json_pointer=json_pointer)
        if whole["status"] == "valid":
            return EvidenceResolution(status="valid", reason=whole["reason"],
                                      spans=(whole["span"],),
                                      normalization=whole["normalization"],
                                      matched_offsets=whole["offsets"])
        if whole["status"] == "ambiguous":
            return EvidenceResolution(status="ambiguous", reason=whole["reason"],
                                      matched_offsets=whole["offsets"])
        parts = [part.strip() for part in _ELLIPSIS_RE.split(needle)]
        parts = [part for part in parts if part]
        if len(parts) < 2:
            return EvidenceResolution(
                status="invalid",
                reason="这段引文带『……』，但按省略号拆开后只剩一段有效内容 —— "
                       "省略号必须真的把多处正文分开，不能拿它连缀半句")
        if len(set(parts)) != len(parts):
            return EvidenceResolution(
                status="invalid",
                reason="省略号引文里出现了**重复的片段** —— 同一段正文被引用两次，"
                       "不能算两处证据")
        spans: list[EvidenceSpan] = []
        offsets: list[tuple[int, int]] = []
        normalizations: list[dict] = []
        failures: list[str] = []
        # 逐段都尝试定位（**不提前 return**）：这样"哪几段是真、哪一段是拼的"能一起报出来，
        # 生成器与复核才拿得到可执行的下一步。
        for index, part in enumerate(parts, start=1):
            one = _locate_one(part, text, artifact_id=artifact_id,
                              artifact_hash=artifact_hash, json_pointer=json_pointer)
            if one["status"] == "valid":
                spans.append(one["span"])
                offsets.append(one["offsets"][0])
                if one["normalization"]:
                    normalizations.append(one["normalization"])
                continue
            failures.append(f"第 {index} 段{one['reason']}")
            if one["status"] == "ambiguous":
                offsets.extend(one["offsets"])
        if failures:
            return EvidenceResolution(
                status="invalid",
                reason="省略号引文的 " + "；".join(failures)
                       + f"。已定位 {len(spans)} 段真实 span，但整段引文**不能**凭相似度接受",
                spans=tuple(spans), matched_offsets=tuple(offsets))
        for previous, current in zip(spans, spans[1:]):
            if current.start < previous.end:
                return EvidenceResolution(
                    status="invalid",
                    reason="省略号拼出来的片段在正文里顺序不符或相互重叠 —— "
                           "这是把分处几段的文字拼成一段的典型样子",
                    spans=tuple(spans), matched_offsets=tuple(offsets))
        normalization: dict = {}
        if len(normalizations) == 1:
            normalization = normalizations[0]
        elif normalizations:
            normalization = {"rule": NORMALIZATION_RULE, "per_span": normalizations}
        return EvidenceResolution(
            status="valid",
            reason=f"『……』拆成 {len(parts)} 段，逐段在正文里定位到 {len(spans)} 个真实 span",
            spans=tuple(spans), normalization=normalization,
            matched_offsets=tuple(offsets))

    one = _locate_one(needle, text, artifact_id=artifact_id, artifact_hash=artifact_hash,
                      json_pointer=json_pointer)
    if one["status"] == "valid":
        return EvidenceResolution(status="valid", reason=one["reason"],
                                  spans=(one["span"],), normalization=one["normalization"],
                                  matched_offsets=one["offsets"])
    if one["status"] == "ambiguous":
        return EvidenceResolution(status="ambiguous", reason=one["reason"],
                                  matched_offsets=one["offsets"])
    return EvidenceResolution(status="invalid", reason=one["reason"])


def resolve_json_pointer(document, pointer: str) -> tuple[bool, object]:
    """按 RFC 6901 取 `pointer` 指向的值。返回 `(是否找到, 值)`。"""
    pointer = str(pointer or "")
    if pointer == "":
        return True, document
    if not pointer.startswith("/"):
        return False, None
    current = document
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return False, None
            current = current[token]
        elif isinstance(current, (list, tuple)):
            if not token.isdigit() or int(token) >= len(current):
                return False, None
            current = current[int(token)]
        else:
            return False, None
    return True, current


def locate_evidence(verdict: CriterionVerdict, artifact: dict) -> EvidenceResolution:
    """把一条判词里的引用**逐段落到 artifact 的真实切片**上（§5.2 冻结接口）。

    判词里的 `spans` 是**待验证的声明**（模型抄回来的引文）；本函数返回的
    `EvidenceResolution.spans` 才是**已核对**的真实区间。
    """
    artifact = dict(artifact or {})
    artifact_hash = str(artifact.get("hash") or artifact.get("artifact_hash") or "")
    if not artifact_hash:
        return EvidenceResolution(
            status="invalid",
            reason="artifact 没有带 hash —— 无法证明这条引文对应哪一版内容，宁可拒绝")
    if str(verdict.artifact_hash) != artifact_hash:
        return EvidenceResolution(
            status="invalid",
            reason=f"判词针对 artifact {str(verdict.artifact_hash)[:24]}…，"
                   f"而当前 artifact 是 {artifact_hash[:24]}… —— "
                   f"旧版本的证据不能用来关新版本的工单")
    declared = tuple(verdict.spans)
    if not declared:
        return EvidenceResolution(
            status="invalid",
            reason="判词没有给出任何引用片段 —— 只回一句『已修好』不算通过")

    spans: list[EvidenceSpan] = []
    offsets: list[tuple[int, int]] = []
    rejections: list[str] = []
    normalizations: list[dict] = []
    ambiguous_offsets: list[tuple[int, int]] = []
    ambiguous_reasons: list[str] = []
    for index, declaration in enumerate(declared, start=1):
        found, value = resolve_json_pointer(artifact, declaration.json_pointer)
        if not found or not isinstance(value, str):
            rejections.append(f"第 {index} 个引用片段指向 {declaration.json_pointer!r}，"
                              f"artifact 里那里不是字符串 —— 引用落到了不存在的位置")
            continue
        artifact_id = str(declaration.artifact_id or artifact.get("artifact_id") or "")
        resolution = locate_quote(declaration.quote, value, artifact_id=artifact_id,
                                  artifact_hash=artifact_hash,
                                  json_pointer=declaration.json_pointer)
        # 部分片段可能已经落成真实 span：它们是**证据**，不能因为整条判词不通过就丢掉。
        spans.extend(resolution.spans)
        offsets.extend((span.start, span.end) for span in resolution.spans)
        if resolution.status == "valid":
            if resolution.normalization:
                normalizations.append(resolution.normalization)
        elif resolution.status == "ambiguous":
            # `ambiguous` 与 `invalid` 是两档，不能混：归一后多处匹配是"要人来看"，
            # 不是"这条证据是假的"（混在一起会让理由说不清该补引用还是该改内容）。
            ambiguous_offsets.extend(resolution.matched_offsets)
            ambiguous_reasons.append(f"第 {index} 个引用片段{resolution.reason}")
        else:
            rejections.append(f"第 {index} 个引用片段{resolution.reason}")

    if rejections:
        return EvidenceResolution(
            status="invalid",
            reason=f"{len(rejections)} 个引用片段不能接受（已定位 {len(spans)} 个真实 span）："
                   + "；".join(rejections + ambiguous_reasons),
            spans=tuple(spans), matched_offsets=tuple(offsets) + tuple(ambiguous_offsets))
    if ambiguous_offsets:
        return EvidenceResolution(
            status="ambiguous",
            reason="；".join(ambiguous_reasons) or "正文里有多处匹配 —— 不许挑第一处",
            spans=tuple(spans), matched_offsets=tuple(ambiguous_offsets))
    if not spans:
        return EvidenceResolution(status="invalid",
                                  reason="判词的引用一个都没能落成真实切片")
    normalization: dict = {}
    if len(normalizations) == 1:
        normalization = normalizations[0]
    elif normalizations:
        normalization = {"rule": NORMALIZATION_RULE, "per_span": normalizations}
    return EvidenceResolution(
        status="valid",
        reason=f"{len(declared)} 个引用片段全部定位成真实 span（共 {len(spans)} 段）",
        spans=tuple(spans), normalization=normalization, matched_offsets=tuple(offsets))


__all__ = ["DISPLAY_WHITESPACE_KINDS", "NORMALIZATION_RULE", "OUTER_STRIP_RULE",
           "UNAUTHORIZED_CHARS", "locate_evidence", "locate_quote", "map_to_original",
           "normalize_display_whitespace", "resolve_json_pointer"]
