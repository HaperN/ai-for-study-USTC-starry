"""把 OCR 原文里的公式改成正确的 LaTeX（清洗块）。

一句话：**逐段抄原文，只准动公式**。

为什么要有这一层：原文是扫描 OCR 出来的，公式经常被弄坏 —— 实测一节 540 字里
就有 9 处（`AB` 其实是 $\\overrightarrow{AB}$、`a的模用a表示` 丢了竖线、
`a//b`、`a上b`、`向量DA`……）。教案块的讲解顺手修了这些，但那是"寄生"的：
只有跑过教案的节才有正确公式，读者看到原文还是坏的。

契约（G1/G3）：
  输入  original_text/<单元>.md（**只读**）+ chapters.json
  输出  <parse_dir>/latex/<单元>.json   ← 契约产物，一节一个文件
        <parse_dir>/latex/<单元>.md     ← 给人看的镜像（可读的清洗后全文）
        <parse_dir>/latex.index.json    ← 进度、校验、每节请求字节数

**"只准动公式"是能机械判定的**（不是靠模型自觉）：
  1. 每段必须带 `source_quote`（原文逐字），校验它能在原文里逐字找到；
  2. 所有段的 source_quote 按顺序拼起来必须覆盖原文 ≥95%（挡漏段、乱序）；
  3. 清洗版与原文做差异比对，**所有新增字符必须落在 `$...$` 里**，
     非公式区域的删除量有很小的一点点预算（够删掉 OCR 垃圾，不够删掉一句话）。
  这三条合起来：模型没法借"修公式"之名改写正文。
"""
from __future__ import annotations

import difflib
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "lib"))

import clean_prompt as P                            # noqa: E402
from files import load_json, sha256_file, write_json   # noqa: E402

GENERATOR = "aiforstudy.clean"
SCHEMA_VERSION = "1.0"
MAX_REQUEST_BYTES = 200 * 1024          # G6
COVERAGE_MIN = 0.95                     # source_quote 拼起来要覆盖原文这个比例
# source_quote 与原文的相似度下限。**不要求逐字完全相同**：
# 实测模型在公式密集的段落上经常差一两个字符（把逗号规范化之类），
# 严格相等会让 60%+ 的节重跑一次（成本翻到 1.75 倍）。
# 但漏句/并段/改写会让相似度掉得远不止 1%，所以 0.99 仍然拦得住真问题。
QUOTE_MIN_SIMILARITY = 0.99      # 兼容用；实际用下面的"按长度给余量"
DELETE_BUDGET_RATIO = 0.15              # 公式外允许删除的总比例（兜底，防"碎刀割肉"）
PROSE_PUNCT = "。！？；，、：（）《》"
CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
MATH_PAIR = re.compile(r"\$\$.*?\$\$|\$[^$]*\$", re.S)


# ── 切段与公式定位 ──────────────────────────────────────────────────────
def split_paragraphs(text: str) -> list[str]:
    """按空行切段。**段内保留原换行**（OCR 是硬折行的）。"""
    blocks = re.split(r"\n\s*\n", (text or "").strip())
    return [block.strip("\n") for block in blocks if block.strip()]


def flatten(text: str) -> str:
    """去掉所有空白（用于覆盖率统计）。"""
    return re.sub(r"\s+", "", text or "")


def squash(text: str) -> str:
    r"""把连续空白折成一个空格（**保留一个空格**）。

    定位公式必须用这个，不能用 flatten：把空白删光之后，
    上一行结尾的 `0$` 和下一行开头的 `$-\mu` 会粘成 `0$$-`，
    被当成"显示公式定界符" —— 实测第 1.1.3 节因此被误报"在公式以外改了字"。
    折成一个空格就没有这个问题；而"合并硬折行"只是空白差异，会被忽略掉。
    """
    return re.sub(r"\s+", " ", text or "").strip()


def _blank_only(text: str) -> bool:
    return not text.strip()


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    """把区间两头空白裁掉。

    踩过的坑：difflib 给的片段**会带上紧邻的空白**（比如 `' $\\boldsymbol{'` 落在
    [47,61)，而真正的公式区间是 [48,78)），于是"是否落在公式内"判成否 →
    把纯公式改动误报成"在公式以外改了字"。实测这一条会系统性地把大量节标成待复核。
    """
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def math_spans(flat: str) -> list[tuple[int, int]]:
    """`$...$` / `$$...$$` 在（已去空白的）文本里的区间，含定界符。"""
    return [(m.start(), m.end()) for m in MATH_PAIR.finditer(flat)]


def cjk_count(text: str) -> int:
    """汉字个数 —— 判断"正文有没有丢"要看这个，不是看总字数。

    实测：一节 518 字的原文洗完少了 50 个字符，全是 `DA`、`AB`、`//`、`上`
    这类被吸进公式的 OCR 垃圾，**一个汉字都没丢**。按总字数报警会一直误报，
    而误报的警告会让人以后不看警告。
    """
    return len(re.findall(r"[\u4e00-\u9fff]", text or ""))


def strip_math(text: str) -> str:
    """去掉所有 $...$ 片段，剩下的就是正文（用 squash 定位，别用 flatten）。"""
    return MATH_PAIR.sub("", squash(text))


def math_bodies(text: str) -> list[str]:
    """每个公式片段的内容（不含定界符，已去空白）。用 squash 定位，别用 flatten。"""
    squashed = squash(text)
    bodies = []
    for span in math_spans(squashed):
        bodies.append(flatten(squashed[span[0]:span[1]]).strip("$"))
    return [b for b in bodies if b]


def _inside(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(start <= span[0] and span[1] <= end for start, end in spans)


def _looks_like_prose(text: str) -> str | None:
    """这段被删掉的原文"像不像正文"？像就返回原因。

    只看两个信号，因为**光看字符数会误报**（OCR 垃圾 `Aa1+A2a2` 也有 9 个字符）：
      * 连续 3 个以上汉字 —— 正文的特征；
      * 有汉字**并且**有句读 —— 正文的特征。
    另外删掉一长串（>12 字符）也算，但要求里面真有汉字。
    踩过的坑：一开始写"含句读就违规"，于是删掉一个孤立的 `（` 或 `：` 就被判
    "把正文删掉了"，逼着模型重跑（纯误报）。
    """
    if not text:
        return None
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    if cjk >= 3:
        return f"含 {cjk} 个汉字"
    if cjk >= 1 and any(ch in PROSE_PUNCT for ch in text):
        return "含汉字和句读"
    if cjk >= 1 and len(text) > 12:
        return f"一次删了 {len(text)} 个字符（含汉字）"
    return None


def diff_inside_math(source: str, cleaned: str) -> dict:
    """比对原文与清洗版，判断"是不是只动了公式"。

    返回 {"inserted_outside": [...], "deleted_outside_chars": n,
          "budget": n, "ok": bool, "changed_math": n}
    """
    a, b = squash(source), squash(cleaned)
    spans_b = math_spans(b)
    spans_a = math_spans(a)
    inserted_outside: list[str] = []
    deleted_outside = 0
    suspicious: list[str] = []
    changed_math = 0
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        # 纯空白差异一律忽略：合并硬折行、多个空格变一个空格，都只是排版
        if _blank_only(a[i1:i2]) and _blank_only(b[j1:j2]):
            continue
        if tag in ("replace", "insert") and j2 > j1:
            left, right = _trim(b, j1, j2)
            if right <= left:
                continue                        # 纯空白，不算改动
            if _inside((left, right), spans_b):
                changed_math += 1
            else:
                inserted_outside.append(b[left:right])
        if tag in ("replace", "delete") and i2 > i1:
            left, right = _trim(a, i1, i2)
            if right <= left:
                continue
            if not _inside((left, right), spans_a):
                deleted_outside += right - left
                reason = _looks_like_prose(a[left:right])
                if reason:
                    suspicious.append(f"{a[left:right][:20]}（{reason}）")
    budget = max(6, round(len(a) * DELETE_BUDGET_RATIO))
    return {"inserted_outside": inserted_outside,
            "deleted_outside_chars": deleted_outside,
            "deleted_outside_prose": suspicious,
            "budget": budget,
            "changed_math": changed_math,
            "ok": (not inserted_outside and not suspicious
                   and deleted_outside <= budget)}


def coverage(original: str, quotes: list[str]) -> tuple[float, list[str]]:
    """source_quote 必须逐字出现在原文里，且按顺序拼起来覆盖原文。

    返回（覆盖率, 定位不到的引文列表）。
    """
    flat_original = flatten(original)
    cursor = 0
    matched = 0
    missing: list[str] = []
    for quote in quotes:
        flat_quote = flatten(quote)
        if not flat_quote:
            continue
        position = flat_original.find(flat_quote, cursor)
        if position < 0:
            missing.append(quote[:60])
            continue
        matched += len(flat_quote)
        cursor = position + len(flat_quote)
    if not flat_original:
        return 1.0, missing
    return matched / len(flat_original), missing


def coverage_gaps(original: str, quotes: list[str], *, min_run: int = 3) -> list[str]:
    """原文里**没被任何段落覆盖**的片段（漏抄了什么，得能一眼看到）。

    光报一个"覆盖率 97%"没用 —— 人还是不知道漏的是哪几个字。
    """
    flat = flatten(original)
    covered = bytearray(len(flat))
    cursor = 0
    for quote in quotes:
        flat_quote = flatten(quote)
        if not flat_quote:
            continue
        position = flat.find(flat_quote, cursor)
        if position < 0:
            continue
        covered[position:position + len(flat_quote)] = b"\x01" * len(flat_quote)
        cursor = position + len(flat_quote)
    gaps: list[str] = []
    start: int | None = None
    for index, flag in enumerate(covered):
        if not flag and start is None:
            start = index
        elif flag and start is not None:
            if index - start >= min_run:
                gaps.append(flat[start:index])
            start = None
    if start is not None and len(flat) - start >= min_run:
        gaps.append(flat[start:])
    return gaps[:10]


def validate_clean(text_payload: dict, *, original: str, original_sha: str,
                   source_sha_recorded: str | None = None) -> dict:
    """清洗块的免费校验。返回 {"problems": [], "warnings": [], "stats": {}}。

    `source_sha_recorded` 不给就读 payload 里的 `source_sha256`（正常路径）。
    """
    problems: list[str] = []
    warnings: list[str] = []
    paragraphs = text_payload.get("paragraphs") or []
    if source_sha_recorded is None:
        source_sha_recorded = text_payload.get("source_sha256")

    if not paragraphs:
        problems.append("paragraphs 是空的")

    quotes = [str(p.get("source_quote") or "") for p in paragraphs]
    ratio, missing = coverage(original, quotes)
    for quote in missing:
        problems.append(f"source_quote 在原文里找不到（不许自己改写原文）：{quote!r}")

    # **逐段逐字对齐**：段落数必须一样，每段的 source_quote 必须等于原文对应那一段。
    # 一开始只查"覆盖率 ≥95%"，结果模型把标题整行丢了（`### 1.1.1 向量及其表示`）
    # 还照样通过 —— 95% 这种模糊阈值挡不住"少抄一行"。宁可让它重跑。
    expected = split_paragraphs(original)
    if len(paragraphs) != len(expected):
        problems.append(
            f"段落数不对：原文 {len(expected)} 段，你返回了 {len(paragraphs)} 段"
            f"（不许合并、拆分或漏段；小节标题也是一段，必须照样返回）")
    else:
        for index, (paragraph, want) in enumerate(zip(paragraphs, expected), start=1):
            got_flat, want_flat = flatten(paragraph.get("source_quote")), flatten(want)
            if got_flat == want_flat:
                continue
            ratio = difflib.SequenceMatcher(None, got_flat, want_flat).ratio()
            # 按长度给余量：允许 max(2, 2%) 个字符的差异。
            # 固定阈值对短段落太严（45 字的段落差 1 个标点，相似度就掉到 0.978）。
            allowed = max(2, round(0.02 * len(want_flat)))
            floor = 1 - allowed / max(1, len(want_flat))
            if ratio >= floor:
                # 差一两个字符（标点/空格规范化）不算抄错，但要留个痕
                warnings.append(f"第 {index} 段的 source_quote 与原文有极小差异"
                                f"（相似度 {ratio:.3f}），已按合格处理")
                continue
            problems.append(
                f"第 {index} 段的 source_quote 与原文不一致（必须逐字照抄）："
                f"原文是「{want_flat[:24]}…」，你写的是「{got_flat[:24]}…」"
                f"（相似度只有 {ratio:.3f}）")

    for index, paragraph in enumerate(paragraphs, start=1):
        cleaned = str(paragraph.get("text_md") or "").strip()
        quote = str(paragraph.get("source_quote") or "")
        if not cleaned:
            problems.append(f"第 {index} 段 text_md 是空的")
            continue
        if cleaned.count("$") % 2:
            problems.append(f"第 {index} 段的 $ 不成对（LaTeX 定界符坏了）")
        report = diff_inside_math(quote, cleaned)
        if report["inserted_outside"]:
            # 用 " | " 连接，别用全角斜杠 —— 它会把片段糊成一句，读起来像文本里真有那个字符
            sample = " | ".join(report["inserted_outside"][:3])[:60]
            problems.append(f"第 {index} 段在公式以外改了字（{sample}）—— 只准改公式")
        if report["deleted_outside_prose"]:
            sample = "；".join(report["deleted_outside_prose"][:2])[:60]
            problems.append(f"第 {index} 段把正文删掉了：{sample}")
        elif report["deleted_outside_chars"] > report["budget"]:
            problems.append(
                f"第 {index} 段删掉了 {report['deleted_outside_chars']} 个公式以外的字"
                f"（预算 {report['budget']}）")

    if source_sha_recorded and original_sha and source_sha_recorded != original_sha:
        problems.append("原文文件的 sha256 与生成时不一致（原文被动过了）")

    cleaned_all = "\n\n".join(str(p.get("text_md") or "") for p in paragraphs)

    # 统计要**看得懂**：
    #   * 正文长度比的是"去掉公式之后"的字数 —— 直接比总字数会被 LaTeX 标记灌水
    #     （实测 518 字洗完显示 939 字，其实正文一个字没多）。
    #   * "修正公式处数"数的是"清洗版里那些在原文中找不到的公式片段"，
    #     不是 diff 片段数（按 diff 数会数出 60 处，实际只有十几处）。
    source_body = flatten(strip_math(original))
    cleaned_body = flatten(strip_math(cleaned_all))
    source_bodies = set(math_bodies(original))
    new_formulas = [b for b in math_bodies(cleaned_all) if b not in source_bodies]
    body_delta = len(cleaned_body) - len(source_body)
    source_cjk, cleaned_cjk = cjk_count(source_body), cjk_count(cleaned_body)
    cjk_delta = cleaned_cjk - source_cjk
    stats = {
        "段落数": len(paragraphs),
        "原文字数": len(flatten(original)),
        "原文正文字数": len(source_body),
        "清洗后正文字数": len(cleaned_body),
        "正文增减": body_delta,
        "汉字增减": cjk_delta,
        "公式片段数": len(math_bodies(cleaned_all)),
        "修正公式处数": len(new_formulas),
        "覆盖率": round(ratio, 4),
    }
    # 只有**汉字少了**才可能是真丢了正文（少几个是 `上` 这类被吸进公式的）
    if cjk_delta < -max(8, round(source_cjk * 0.01)):
        warnings.append(f"汉字少了 {-cjk_delta} 个（原文 {source_cjk} 个），"
                        f"可能有正文被吞进公式或漏掉了")
    if ratio < 1.0 and ratio >= COVERAGE_MIN:
        gaps = coverage_gaps(original, quotes)
        shown = "、".join(f"「{gap}」" for gap in gaps[:4])
        warnings.append(f"source_quote 只覆盖原文 {ratio:.0%}，这些原文内容没进任何段落："
                        f"{shown}{'…' if len(gaps) > 4 else ''}")
    return {"problems": problems, "warnings": warnings, "stats": stats}


# ── 调用模型 ────────────────────────────────────────────────────────────
def build_messages(unit: dict, original: str, corrections: list[dict] | None = None
                   ) -> tuple[list[dict], int]:
    system = P.system_prompt()
    user = [f"本节：{unit.get('anchor')} {unit.get('title')}（单元 id {unit['unit_id']}）",
            f"本节原文共 {len(split_paragraphs(original))} 段，请逐段返回。", ""]
    if corrections:
        user += ["上一道工序（教案块）已经发现这些位置有问题，供参考，不要漏："]
        user += [f"  {c.get('原文', '')} → {c.get('讲解', '')}"
                 for c in corrections[:20]]
        user += [""]
    user += ["本节原文（逐字，不要修改任何非公式内容）：", original.strip()]
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": "\n".join(user)}]
    size = sum(len(m["content"].encode("utf-8")) for m in messages)
    return messages, size


def _assemble(unit: dict, data: dict, *, original_rel: str, original_sha: str,
              request_bytes: int, client=None, revision: int = 1) -> dict:
    return {
        "_meta": {
            "file_type": "clean_text",
            "schema_version": SCHEMA_VERSION,
            "generator": GENERATOR,
            "source_sha256": original_sha,
            "field_notes": {
                "source": "原文文件的**引用**（相对 data/parse/）；清洗块永远不改原文（G5）",
                "source_sha256": "生成这一节时原文的 sha256，用来证明原文没被动过",
                "paragraphs[].source_quote": "这一段原文**逐字**（校验要求能在原文里原样找到）",
                "paragraphs[].text_md": "清洗后的这一段：公式写成正确 LaTeX，其它一字不动",
                "paragraphs[].changes[]": "这一段里改了什么（原文片段 → 正确写法）",
                "checks.only_math_changed": "机械判定的结果：正文有没有被偷偷改写",
                "generated_by.request_bytes": "这一节的请求字节数，用于证明 G6（不随节号增长）",
            },
        },
        "unit_id": unit["unit_id"],
        "anchor": unit.get("anchor"),
        "title": unit.get("title"),
        "level": unit.get("level"),
        "chapter": unit.get("chapter"),
        "section": unit.get("section"),
        "source": original_rel,
        "source_sha256": original_sha,
        "paragraphs": data.get("paragraphs") or [],
        "revision": revision,
        "generated_by": {"model": getattr(client, "model", None),
                         "effort": getattr(client, "effort", None),
                         "request_bytes": request_bytes},
    }


def _corrections_hint(parse_dir: Path, unit_id: str) -> list[dict]:
    """教案块发现过的 OCR 错误（如果有），当先验喂进来。免费，不额外调模型。"""
    lesson_dir = Path(str(parse_dir).replace("parse", "lesson", 1)) / "lessons"
    payload = load_json(lesson_dir / f"{unit_id}.json")
    if not payload:
        return []
    return payload.get("corrections") or []


def clean_unit(parse_dir: Path, unit: dict, *, client, out_dir: Path,
               retries: int = 1, log=print) -> dict:
    parse_dir, out_dir = Path(parse_dir), Path(out_dir)
    rel = unit.get("original_text")
    if not rel:
        raise ValueError(f"单元 {unit['unit_id']} 没有 original_text")
    source_path = parse_dir / rel
    if not source_path.is_file():
        raise FileNotFoundError(f"单元 {unit['unit_id']} 的原文不存在：{source_path}")
    original = source_path.read_text(encoding="utf-8")
    original_sha = sha256_file(source_path)
    original_rel = str(source_path.relative_to(parse_dir)).replace("\\", "/")

    hints = _corrections_hint(parse_dir, unit["unit_id"])
    messages, request_bytes = build_messages(unit, original, hints)
    if request_bytes > MAX_REQUEST_BYTES:
        raise ValueError(f"{unit['unit_id']} 的请求 {request_bytes} 字节超过 G6 上限")

    previous = load_json(out_dir / f"{unit['unit_id']}.json")
    revision = int((previous or {}).get("revision") or 0) + 1
    problems: list[str] = []
    warnings: list[str] = []
    stats: dict = {}
    data: dict = {}
    calls = 0
    for attempt in range(retries + 1):
        result = client.chat_json(messages, tag=f"clean:{unit['unit_id']}:r{revision}:a{attempt}")
        calls += 1
        data = result["data"]
        payload = _assemble(unit, data, original_rel=original_rel, original_sha=original_sha,
                            request_bytes=request_bytes, client=client, revision=revision)
        report = validate_clean(payload, original=original, original_sha=original_sha,
                                source_sha_recorded=original_sha)
        problems, warnings, stats = report["problems"], report["warnings"], report["stats"]
        if not problems:
            break
        if attempt < retries:
            log(f"  {unit['unit_id']} 第 {attempt + 1} 次不合格，带着问题重跑：{problems[:2]}")
            messages = messages + [
                {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)},
                {"role": "user", "content": "上面这版有问题，请修正后重新输出完整 JSON"
                                            "（注意：除了公式，一个字都不许改）：\n"
                                            + "\n".join(f"- {p}" for p in problems)},
            ]

    verdicts = [diff_inside_math(str(p.get("source_quote") or ""),
                                 str(p.get("text_md") or "")) for p in payload["paragraphs"]]
    payload["checks"] = {
        "problems": problems,
        "warnings": warnings,
        "needs_review": bool(problems),
        "only_math_changed": all(v["ok"] for v in verdicts) and bool(verdicts),
        "stats": stats,
    }
    payload["generated_by"].update({"model_calls": calls,
                                    "corrections_hint": len(hints)})
    write_json(out_dir / f"{unit['unit_id']}.json", payload)
    # 给人看的镜像：清洗后的全文，直接可读
    (out_dir / f"{unit['unit_id']}.md").write_text(
        "\n\n".join(str(p.get("text_md") or "") for p in payload["paragraphs"]) + "\n",
        encoding="utf-8")
    return {"payload": payload, "problems": problems, "warnings": warnings,
            "stats": stats, "model_calls": calls, "request_bytes": request_bytes,
            "only_math_changed": payload["checks"]["only_math_changed"]}


def revalidate(parse_dir: Path, out_dir: Path | None = None, *, log=print) -> dict:
    """**免费复检**：拿当前校验规则把已有产物的 checks 重算一遍（不调模型）。

    为什么需要：`run` 对已有产物直接跳过（不重复花钱），所以**校验规则改了以后
    旧产物上的 checks 是过期的**——实测踩过：改了"删正文"的判定规则，产物里还写着
    旧规则算出来的警告。这个命令只重算校验，不改一个字的内容。
    """
    parse_dir = Path(parse_dir)
    out_dir = Path(out_dir) if out_dir else parse_dir / "latex"
    changed, rows = 0, []
    for path in sorted(out_dir.glob("*.json")):
        payload = load_json(path)
        if not payload or payload.get("_meta", {}).get("file_type") != "clean_text":
            continue
        unit_id = payload.get("unit_id")
        source = parse_dir / (payload.get("source") or "")
        original = source.read_text(encoding="utf-8") if source.is_file() else ""
        report = validate_clean(payload, original=original,
                                original_sha=payload.get("source_sha256") or "")
        before = (payload.get("checks") or {}).get("problems")
        payload["checks"] = {
            "problems": report["problems"], "warnings": report["warnings"],
            "needs_review": bool(report["problems"]),
            "only_math_changed": not report["problems"] and bool(payload.get("paragraphs")),
            "stats": report["stats"],
        }
        if before != report["problems"]:
            changed += 1
        write_json(path, payload)
        rows.append({"unit_id": unit_id, "problems": len(report["problems"]),
                     "warnings": len(report["warnings"])})
    summary = {"复检": len(rows), "结论有变化": changed,
               "仍有问题": [r["unit_id"] for r in rows if r["problems"]],
               "逐节": rows}
    log(f"复检 {len(rows)} 节，其中 {changed} 节的结论变了；仍有问题 "
        f"{len(summary['仍有问题'])} 节：{summary['仍有问题']}")
    return summary


def run(parse_dir: Path, out_dir: Path | None = None, *, only: list[str] | None = None,
        force: bool = False, client=None, retries: int = 1, dry_run: bool = False,
        log=print) -> dict:
    parse_dir = Path(parse_dir)
    out_dir = Path(out_dir) if out_dir else parse_dir / "latex"
    chapters = load_json(parse_dir / "chapters.json")
    if chapters is None:
        raise FileNotFoundError(f"找不到 {parse_dir / 'chapters.json'}，先跑解析块")
    units = {u["unit_id"]: u for u in chapters.get("lesson_units", [])}
    book = (chapters.get("book") or {}).get("title") or ""

    before = {str(p.relative_to(parse_dir)).replace("\\", "/"): sha256_file(p)
              for p in sorted((parse_dir / "original_text").glob("*.md"))}

    order = [u for u in chapters["lesson_units"] if not only or u["unit_id"] in set(only)]
    if only:
        unknown = sorted(set(only) - set(units))
        if unknown:
            raise ValueError(f"chapters.json 里没有这些单元：{unknown}")
    todo = [u for u in order if force or not (out_dir / f"{u['unit_id']}.json").is_file()]
    if dry_run:
        return {"todo": [u["unit_id"] for u in todo], "skipped": len(order) - len(todo),
                "plan": "只算会跑哪些，不调模型"}

    rows: list[dict] = []
    calls = 0
    started = time.time()
    for position, unit in enumerate(todo, start=1):
        log(f"  [{position}/{len(todo)}] {unit['unit_id']} {unit.get('title')} …")
        try:
            outcome = clean_unit(parse_dir, unit, client=client, out_dir=out_dir,
                                 retries=retries, log=log)
        except Exception as exc:
            log(f"  {unit['unit_id']} 失败：{type(exc).__name__}: {exc}")
            rows.append({"unit_id": unit["unit_id"], "status": "failed",
                         "error": f"{type(exc).__name__}: {exc}"})
            continue
        calls += outcome["model_calls"]
        flag = "" if outcome["only_math_changed"] else "　⚠ 正文可能被改过"
        log(f"  {unit['unit_id']} 完成：{outcome['stats'].get('段落数')} 段，"
            f"修正公式 {outcome['stats'].get('修正公式处数')} 处，"
            f"正文增减 {outcome['stats'].get('正文增减')} 字，"
            f"覆盖率 {outcome['stats'].get('覆盖率', 0):.0%}{flag}")
        rows.append({
            "unit_id": unit["unit_id"], "anchor": unit.get("anchor"),
            "title": unit.get("title"),
            "status": "needs_review" if outcome["problems"] else "done",
            "path": f"{unit['unit_id']}.json", "md": f"{unit['unit_id']}.md",
            "problems": outcome["problems"], "warnings": outcome["warnings"],
            "stats": outcome["stats"], "model_calls": outcome["model_calls"],
            "request_bytes": outcome["request_bytes"],
            "only_math_changed": outcome["only_math_changed"],
        })

    after = {str(p.relative_to(parse_dir)).replace("\\", "/"): sha256_file(p)
             for p in sorted((parse_dir / "original_text").glob("*.md"))}
    previous_index = load_json(out_dir / "latex.index.json") or {}
    merged = {row["unit_id"]: row for row in previous_index.get("units", [])}
    for row in rows:
        merged[row["unit_id"]] = row
    all_rows = [merged[u["unit_id"]] for u in chapters["lesson_units"] if u["unit_id"] in merged]
    sizes = [r.get("request_bytes") or 0 for r in all_rows if r.get("request_bytes")]
    index = {
        "_meta": {
            "file_type": "clean_index",
            "schema_version": SCHEMA_VERSION,
            "generator": GENERATOR,
            "field_notes": {
                "units[]": "每节状态：done / needs_review / failed，以及覆盖率、修正公式处数、正文增减",
                "units[].only_math_changed": "机械判定：这一节是不是只改了公式（正文没被改写）",
                "units[].request_bytes": "请求字节数（G6：不随节号增长、≤200KB）",
                "original_text_sha256": "跑清洗块**之前**记下的原文指纹",
                "original_text_unchanged": "跑完之后再比一次；true 表示原文一个字节没动（G5）",
            },
        },
        "book": book,
        "units": all_rows,
        "totals": {
            "units_total": len(chapters["lesson_units"]),
            "done": sum(1 for r in all_rows if r.get("status") == "done"),
            "needs_review": sum(1 for r in all_rows if r.get("status") == "needs_review"),
            "failed": sum(1 for r in all_rows if r.get("status") == "failed"),
            "formulas_fixed": sum((r.get("stats") or {}).get("修正公式处数") or 0
                                  for r in all_rows),
            "model_calls_this_run": calls,
            "seconds": round(time.time() - started, 1),
        },
        "max_request_bytes": max(sizes) if sizes else 0,
        "original_text_sha256": before,
        "original_text_unchanged": before == after,
    }
    write_json(out_dir / "latex.index.json", index)
    return {"index": index, "ran": rows, "model_calls": calls}


__all__ = ["run", "revalidate", "clean_unit", "build_messages", "split_paragraphs", "flatten", "squash",
           "math_spans", "diff_inside_math", "coverage", "coverage_gaps",
           "cjk_count", "strip_math", "math_bodies", "validate_clean",
           "MAX_REQUEST_BYTES", "COVERAGE_MIN", "GENERATOR"]
