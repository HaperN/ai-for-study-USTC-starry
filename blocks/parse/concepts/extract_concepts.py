"""概念抽取（任务 T1.7）：逐单元抽"定义 + 定理"，合并成全局唯一的 `concepts.json`。

## 为什么是"唯一来源"

任务基线 G4（2026-09-29 改）：概念**只在一处登记**（`concepts.json`），
解释只在一处撰写（教案），两边靠概念 id 绑定。
教案 JSON 的正文里只写 `[[CONCEPT:C-0001]]`，解释不复制过去。
这样同一个概念不会在两节里出现两种说法。

## 做法

1. **逐单元抽**：对每个 `lesson_unit` 用它的原文调一次模型，只抽**定义**和**定理**
   （按决策 3，不抽方法/例子/易错点）。结果立刻落盘到 `concepts.parts/<unit>.json`
   —— 所以**单节可重跑**（G8），某一节抽坏了不影响别的节。
2. **全局合并**：把各单元结果按"名称归一化 + 类型"合并。同一概念只保留一条，
   后面的出现**只追加 `occurrences`，不重写 brief/explanation**（按方案第五条）。
3. **发 id**：`C-0001` 递增，稳定（同一输入重跑，顺序一致）。

## 字段

* （**没有** brief/explanation —— 2026-09-29 起）解释与配图由**教案阶段**产出：
  抽取只做识别（名字/类型/原文严格表述/依据片段），
  教案对"本节首次出现的概念"写解释并指定用哪张图（或请求生成一张），
  网页渲染时把两者合并进概念抽屉。
* `formal`      原文里的严格表述，**逐字来自原文**
* `source_quote` 抽它时依据的原文片段（便于回查，也是"没编造"的证据）
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from aiforstudy.blocks.concepts.coverage import normalize_coverage_name as normalize_name, covers_name as _covers

SCHEMA_VERSION = "1.0"
GENERATOR = "aiforstudy.parse.concepts"
PART_SCHEMA_VERSION = "1.0"
# 2026-09-23 用户拍板：把"命题/推论"也收进来（原来只收定义+定理）。
# 理由：正文和讲解会引用"命题1.1.1"，读者点不了 —— 能不能点取决于**读者遇到没遇到**，
# 而不是它属于哪一类。教材用"命题"往往是因为它是辅助结论，对读者一样重要。
#: 抽取范围（2026-09-29 用户定案：**给任何初学者都能用**，不能被书的标签框住）。
#: 原来只有四类，于是书里 46 次「定律」、67 次「性质」无处可去 ——
#: 实测《电路分析基础》的**欧姆定律根本没成卡**（作者在前言里说"欧姆定律无他，只是 u=Ri 而已"，
#: 把它并进了电阻元件的定义）—— 对作者是取舍，对**查这个词的初学者**就是缺页。
ALLOWED_TYPES = {"definition", "theorem", "proposition", "corollary",
                 "law", "property", "formula"}
TYPE_LABELS = {"definition": "定义", "theorem": "定理", "law": "定律",
               "property": "性质", "proposition": "命题", "corollary": "推论",
               "formula": "公式"}

SYSTEM_PROMPT = """你在把教材原文整理成"概念卡片"，供新手学习使用。

## 抽七类（**读者会去查的，都得有卡**）
| 原文怎么写 | type | 例 |
|---|---|---|
| 定义 | `definition` | 集总参数元件、线性电阻元件 |
| 定理 | `theorem` | 戴维南定理、置换定理 |
| **定律 / 原理** | `law` | **欧姆定律**、基尔霍夫定律、叠加原理 |
| **性质 / 特点** | `property` | 电阻元件的无记忆性、电容电压的连续性质 |
| 命题 | `proposition` | 命题 1.1.1 |
| 推论 | `corollary` | 推论 1.1 |
| **有名字的公式** | `formula` | 分压公式、相量形式的欧姆定律 |

⚠️ 三条判断规则：

1. **看原文怎么称呼它**：写"定律/原理"填 `law`，写"性质/特点"填 `property`。
2. **"并入别处讲"的也要成卡**：有些结论作者并进了另一段叙述（例：欧姆定律被并进"电阻元件"的定义里讲）。
   只要它在原文里有**名字**、有**可引的表述**，就**单独出一张卡** —— 初学者会按这个名字来查。
3. `formula` 只收**有名字的**公式；推导过程里的普通公式**不收**。

不要抽：方法、例子、易错点、习题、章节导语。（"引理"单独出现时按命题处理。）
原文中的习题、思考题和原书答案保留给阅读器，仅作资料记录；不要解题，也不要把题干要求证明的结论或题目中引用的定理当作本节新增概念。

## 输出格式：**先清点，再成卡**

```json
{"named_results": [{"name": "欧姆定律", "label": "定律", "quote": "逐字原文片段"}],
 "concepts": [ ... ]}
```

* `named_results` 是**清点**：本节里出现的**所有有名字的** 定律/定理/原理/性质/法则/公式/定义/命题/推论，
  **一个都不许漏** —— 包括只用一句话提到的、以及在别节定义但本节引用的。
* `concepts` 必须**覆盖 `named_results` 里的每一条**（程序会逐条核对，漏了会让你重做）。

## 这一步**只做识别，不写解释**（2026-09-29 定案）

"怎么讲给初学者听懂 + 配什么图"**不是这一步的事** —— 它连同这一节的讲解一起交给
**下一阶段（教案）**（那边能看到整节讲解的上下文，也能给概念配图）。
这里写解释等于**两个地方各写一遍**：既费钱，又容易前后矛盾。

## 每条卡片只填四个字段
- `name`        概念名称，简短（如"上界""确界""欧姆定律"）。
- `type`        七类之一（见上表）。
- `formal`      原文里的严格表述（定义原文 / 定理的条件与结论），**逐字来自原文**，不要改写。
- `source_quote` 你抽这条时依据的**原文片段**（逐字复制一小段，用于回查）。

**不要**输出 `brief` / `explanation` —— 写了也会被丢掉。

## 如果原文里已经是 LaTeX（重要）
从清洗块过来的原文，公式已经是**正确的 LaTeX**（如 `$\\boldsymbol{a}$`、
`$\\overrightarrow{AB}$`、`$|\\boldsymbol{a}|$`）。
- `formal` 直接照抄这些 LaTeX，**不要换成别的写法**；
- `formal` 里的公式照抄，**不要**换成 `a1,a2` 这种 OCR 写法。

## 硬规则
0. **原文里每个带编号的条目（定义/定理/命题/推论）都必须出一条卡片，一个都不许漏。**
   实测：从清洗后的原文重抽时漏掉了 9 条（"向量加法的性质""数乘运算律"这类），
   导致讲解里已经写好的引用变成死链 —— 漏收比多收严重得多。
1. **不要编造原文没有的概念。** 原文没讲的，一个字都不要加。
2. 同一节里重复出现的同一个概念只出一条。
3. 只输出 JSON：{"named_results":[{"name":"...","label":"定律","quote":"..."}],
   "concepts":[{"name":"...","type":"law","formal":"...","source_quote":"..."}]}
4. 这一节确实没有定义或定理时，输出 {"concepts": []}。"""


#: 编号条目里的数字部分：`命题5.7.2` → `5.7.2`
_LABEL_NUMBER = re.compile(r"^(\d+(?:\.\d+)*)")


def _label_number(label: str) -> str:
    """从 `命题5.7.2` / `定义 5.7.1` 里取出编号数字部分。"""
    text = str(label or "")
    match = re.search(r"(\d+(?:\.\d+)*)", text)
    return match.group(1) if match else ""


def _own_prefix(unit: dict) -> str:
    """本节自己的编号前缀（`anchor=5.7.1` → `5.7`；取到倒数第二段，兼容节/小节两种粒度）。"""
    match = _LABEL_NUMBER.match(str(unit.get("anchor") or "").strip())
    if not match:
        return ""
    parts = match.group(1).split(".")
    return ".".join(parts[:-1]) if len(parts) >= 2 else parts[0]


def own_labels(unit: dict, labels: list[str]) -> tuple[list[str], list[str]]:
    """把编号条目分成"**本节自己的**"和"**引用的他节条目**"。

    ⚠️ 为什么必须分（2026-09-29 实测抓出，省一半钱）：
    防漏检查原来要求"原文里出现的每个编号条目都要有卡片"——可**引用**别的节的条目
    （实测《清华线代》§5.7.1 酉矩阵 引用了 6 条 §5.3/§5.4 的命题）模型合理地**不会**
    在本节给它们做卡片，于是**必然触发重问**：那一节两档都白花了一次调用
    （high 19,623 / low 23,943 token，两次都只为补这 6 条引用）。
    引用条目本来就该在**它们自己那一节**里成卡，不该在本节重复要求。
    """
    prefix = _own_prefix(unit)
    if not prefix:
        return list(labels), []
    owns, refs = [], []
    for label in labels:
        number = _label_number(label)
        (owns if number == prefix or number.startswith(prefix + ".") else refs).append(label)
    return owns, refs


# ── "必须覆盖"的三类清单（2026-09-29 定案：三类并集，差异全部记账）──────
#: 具名结论：`欧姆定律`、`戴维南定理`、`叠加原理`、`电容电压的连续性质`…
_NAMED_RESULT = re.compile(r"([\u4e00-\u9fffA-Za-z]{2,12}(?:定理|定律|原理|法则|性质|公式))")
#: 正则扫出来的"名字"常带句子前缀（`用叠加原理`、`及欧姆定律`）→ 先去前缀
_NAMED_PREFIX = re.compile(
    r"^(?:用|由|及|和|与|或|根据|依据|利用|运用|试用|应用|又由|再由|据|按|依照|以|配合|"
    r"即|见|如|先|再|可|能|会|应|需|要|把|将|使|让|对|在|从|向|给|为|是|有|无|该|此|其|"
    r"这|那|各|两|三|所|被|的|了|与)+")


def named_results_in_text(text: str) -> list[str]:
    """正则扫出原文里**具名**的结论（独立于模型的第二份清单）。

    只用来**交叉核对**：正则必然有噪声（`用叠加原理` 这种句子片段），所以
    它扫到的名字**不会**直接要求成卡 —— 只在"模型清点和卡片都没提到它"时记进账本，
    让人（或下一轮）决定要不要收（R-b：不确定要说出来，但不猜）。
    """
    out, seen = [], set()
    for raw in _NAMED_RESULT.findall(text or ""):
        name = _NAMED_PREFIX.sub("", raw).strip()
        if len(name) < 4 or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def notation_gaps(unit_text: str, rows: list[dict]) -> list[dict]:
    """Preserved parse-only hints; missing implementation is an explicit error."""
    from blocks.parse.concepts.notation_inventory import notation_gaps as discover
    return discover(unit_text,rows)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: 名字**末尾**的括注：`命题 5.3.2（本节引用的实空间对应命题）`
_NAME_TAIL_PAREN = re.compile(r"\s*[（(]([^（()）]{1,80})[)）]\s*$")
#: 类型词与编号之间的空格：`定义 5.7.1` → `定义5.7.1`（两档风格统一）
_TYPE_SPACE = re.compile(r"^(定义|定理|命题|引理|推论|公式|性质)\s+(?=\d)")


def split_name_note(name: str) -> tuple[str, str]:
    """把**显示名**里末尾的括注剥出来 → `(干净名, 括注)`。

    为什么要剥（2026-09-29 实测）：high 档抽出来的名字带着解释性括注 ——
    `命题 5.3.2（本节引用的实空间对应命题）`；那种括注是**说明**，不是标题的一部分，
    放在卡片标题里又长又不像标题（low 档反而干净：`命题5.3.2`）。

    ⚠️ 三条自我约束：
      * **只剥末尾**的括注 —— 中间的（`定理 1.1（柯西不等式）的推广`）剥了会改变意思；
      * 剥完剩下的名字**至少 2 个字符**，否则不剥（宁可留着括注，也不能把名字剥空）；
      * 剥下来的括注**不丢**：进 `name_note`，原名进 `name_raw`（R-a 只增不丢）。
    """
    text = str(name or "").strip()
    text = _TYPE_SPACE.sub(r"\1", text)          # 类型词后多余的空格先收掉
    match = _NAME_TAIL_PAREN.search(text)
    if not match:
        return text, ""
    cleaned = text[: match.start()].strip()
    if len(cleaned) < 2:
        return text, ""
    return cleaned, match.group(1).strip()


# ── 逐单元抽 ────────────────────────────────────────────────────────────
def _named_from(data: dict) -> list[str]:
    """模型**清点**出来的具名结论（`named_results`）—— 只取名字，去重保序。"""
    out, seen = [], set()
    for row in (data or {}).get("named_results") or []:
        name = row.get("name") if isinstance(row, dict) else row
        name = str(name or "").strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _rows_from(data: dict) -> list[dict]:
    rows = []
    for row in (data or {}).get("concepts", []) or []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("type", "")).strip()
        name = str(row.get("name", "")).strip()
        if kind not in ALLOWED_TYPES or not name:
            continue
        # 只收"识别"出来的四样（2026-09-29 定案）：解释与配图归下一阶段（教案）。
        # 模型偶尔仍会多写 brief/explanation —— 这里一律丢掉，免得两处各写一遍。
        rows.append({
            "name": name,
            "type": kind,
            "formal": str(row.get("formal", "")).strip(),
            "source_quote": str(row.get("source_quote", "")).strip(),
        })
    return rows


def extract_unit(unit: dict, text: str, client, retries: int = 1,
                 log=None) -> dict:
    """抽一个单元。返回该单元的 part（含 _meta）。

    **漏条目会重跑**：原文里每个编号条目都应该有一条卡片。实测从清洗后的原文
    重抽时漏了 9 条（"向量加法的性质"这类），结果讲解里写好的引用变成死链。
    """
    if not text.strip():
        return _part(unit, [], note="该单元原文为空，跳过")
    message = (f"单元：{unit.get('unit_id')}（{unit.get('title')}）\n\n"
               f"原文如下：\n\n<<<\n{text}\n>>>")
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": message}]
    # ── 三类"必须覆盖"清单（2026-09-29 定案）─────────────────────────────
    # ① 本节**自己的编号条目**（正则，最硬）；引用的他节条目只记账（见 own_labels）
    from aiforstudy.core.source_boundaries import source_material_spans
    teaching_text = text
    for start, end in reversed(source_material_spans(text)):
        teaching_text = teaching_text[:start] + ''.join('\n' if c == '\n' else ' ' for c in text[start:end]) + teaching_text[end:]
    labels, referenced = own_labels(unit, numbered_labels(teaching_text))
    # ② **具名结论**：模型清点（下面每次返回都会带来）+ 正则独立扫（只做交叉核对）
    regex_named = named_results_in_text(teaching_text)
    rows: list[dict] = []
    named: list[str] = []
    missing: list[str] = []
    missing_named: list[str] = []
    for attempt in range(retries + 1):
        tag = f"concepts-{unit.get('unit_id')}" + (f":a{attempt}" if attempt else "")
        result = client.chat_json(messages, tag=tag)
        rows = _rows_from(result.get("data"))
        named = _named_from(result.get("data"))
        missing = missing_labels(labels, rows) if labels else []
        # 模型自己清点出来的具名结论，**必须都有卡**（它说了就得做到；做不到就重问）
        missing_named = [n for n in named if not _covers(n, rows)]
        if not missing and not missing_named:
            break
        if attempt < retries:
            forgot = missing + missing_named
            if log:
                log(f"  {unit.get('unit_id')} 漏了 {len(forgot)} 条（编号条目 {len(missing)} / "
                    f"具名结论 {len(missing_named)}），带着清单重跑：{forgot[:3]}")
            messages = messages + [
                {"role": "assistant", "content": json.dumps(result.get("data"),
                                                            ensure_ascii=False)},
                {"role": "user", "content":
                 "上面这版漏了下面这些条目（一个都不许漏，请补上并重新输出完整 JSON；"
                 "注意：`named_results` 里列过的每一条都必须出现在 `concepts` 里）：\n"
                 + "\n".join(f"- {label}" for label in forgot)},
            ]

    # ③ 记号 watchlist（规范里"第一次出现必须给最小定义"那批）：本地词表，0 花费
    notation_missing = notation_gaps(teaching_text, rows)
    # 正则扫到、但模型清点和卡片里都没提到的名字 → **只记账**（正则有噪声，不猜）
    unconfirmed = [n for n in regex_named
                   if not _covers(n, rows) and normalize_name(n) not in
                   {normalize_name(x) for x in named}]
    must_cover = len(labels) + len(named)
    part = _part(unit, rows, named=named, fingerprint=_fingerprint_of(client), coverage={
        "编号条目": len(labels),
        "漏掉的条目": missing,
        "具名结论": len(named),
        "漏掉的具名结论": missing_named,
        # **三态**：只有真的存在"可核对清单"时才给绿灯；否则标 None + 原因
        # （原来"没有可核对项"也显示 `完整: True` —— 那是个**假绿灯**，实测 61 节全是它）
        "完整": (not missing and not missing_named) if must_cover else None,
        "完整说明": ("本节没有编号条目、也没有具名结论可核对" if not must_cover
                     else ("全部覆盖" if (not missing and not missing_named) else "有漏项")),
        # 引用的他节条目：**只记账、不要求成卡**（它们在自己的节里成卡）
        "引用的他节条目": referenced,
        # 正则独立扫到、但两处都没提的候选（多半是句子片段，交人抽查）
        "待确认的具名候选": unconfirmed[:12],
        "记号缺口": notation_missing,
    })
    return part


IDS_FILE = "concepts.ids.json"


def _read_json(path: Path):
    if not Path(path).is_file():
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_registry(parse_dir: Path) -> dict[str, str]:
    payload = _read_json(Path(parse_dir) / IDS_FILE) or {}
    return dict(payload.get("ids") or {})


def write_registry(parse_dir: Path, registry: dict[str, str], *, book_title: str = "",
                   seed_from: str = "") -> None:
    payload = {
        "_meta": {
            "file_type": "concept_ids",
            "schema_version": SCHEMA_VERSION,
            "generator": GENERATOR,
            "field_notes": {
                "ids": "概念名（归一化）→ 概念 id。重抽概念时同名沿用旧 id，"
                       "新概念从最大号往后排 —— 这样已有讲解里的 [[CONCEPT:...]] 不会指错。",
                "seed_from": "这份注册表最初是从哪来的（现有 concepts.json / 上次的注册表）",
            },
        },
        "book_title": book_title,
        "seed_from": seed_from,
        "ids": dict(sorted(registry.items())),
    }
    (Path(parse_dir) / IDS_FILE).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


LABEL = re.compile(r"(定义|定理|命题|推论|引理)\s*\d+(?:\.\d+)+")


def numbered_labels(text: str) -> list[str]:
    """原文里所有带编号的条目（`定义1.1.1`、`命题 1.1.2` …），去重保序。"""
    seen, labels = set(), []
    for match in LABEL.finditer(text or ""):
        label = f"{match.group(1)}{match.group(0)[len(match.group(1)):].strip()}"
        label = re.sub(r"\s+", "", label)
        if label not in seen:
            seen.add(label)
            labels.append(label)
    return labels


def missing_labels(labels: list[str], rows: list[dict]) -> list[str]:
    """哪些编号条目在抽出来的卡片里找不到（卡片里会带 formal / source_quote）。"""
    haystack = " ".join(
        f"{row.get('formal', '')} {row.get('source_quote', '')} {row.get('name', '')}"
        for row in rows)
    haystack = re.sub(r"\s+", "", haystack)
    return [label for label in labels if label not in haystack]


def unit_source(parse_dir: Path, unit: dict, *, prefer_cleaned: bool = True) -> tuple[Path, str]:
    """这一节抽概念该读哪份文本。

    **优先清洗后的**（`latex/<单元>.md`）：公式已经被整理成正确 LaTeX，
    概念卡就不会把 OCR 垃圾学进去（实测：从 OCR 原文抽出来的"命题1.1.1"解释里
    写着 `入a+μb=0` —— OCR 把 λ 认成了"入"）。
    没有清洗产物才退回 OCR 原文。
    """
    parse_dir = Path(parse_dir)
    cleaned = parse_dir / "latex" / f"{unit['unit_id']}.md"
    if prefer_cleaned and cleaned.is_file():
        return cleaned, "latex"
    return parse_dir / str(unit.get("original_text") or ""), "original_text"


def _fingerprint_of(client) -> dict:
    """模型侧的指纹：**提示词 + 模型名 + 档位**。

    ⚠️ 为什么必须记（2026-09-29 抓出）：分片原来只按**原文哈希**判"要不要重抽" ——
    改了提示词，旧分片会被**原样复用**，新方法等于没生效（白改一场）。
    """
    from aiforstudy.core import source_boundaries
    return {
        "prompt_sha256": _sha256(SYSTEM_PROMPT),
        "source_boundary_sha256": hashlib.sha256(Path(source_boundaries.__file__).read_bytes()).hexdigest(),
        "model": str(getattr(client, "model", "") or ""),
        "effort": str(getattr(client, "effort", "") or ""),
    }


def _part(unit: dict, rows: list[dict], note: str = "",
          *, named: list[str] | None = None, coverage: dict | None = None,
          fingerprint: dict | None = None) -> dict:
    part = {
        "_meta": {
            "file_type": "concepts_part",
            "schema_version": PART_SCHEMA_VERSION,
            "generator": GENERATOR,
            "unit_id": unit.get("unit_id"),
            "text_sha256": _sha256(str(unit.get("_text", ""))),
            # **模型侧指纹**：提示词/模型/档位变了 → 分片判 stale 重抽（否则改提示词不生效）
            **(fingerprint or {}),
            "field_notes": {
                "concepts[].type": "definition / theorem / law / property / proposition / corollary / formula",
                "concepts[]": "只识别：name/type/formal/source_quote；解释与配图由教案阶段产出",
                "concepts[].formal": "原文里的严格表述（逐字）",
                "concepts[].source_quote": "抽取依据的原文片段",
                "coverage": "原文里带编号的条目清点：一共几个、漏了哪几个（漏了会自动重跑一次）",
            },
        },
        "unit": {"unit_id": unit.get("unit_id"), "level": unit.get("level"),
                 "chapter": unit.get("chapter"), "section": unit.get("section"),
                 "title": unit.get("title"), "anchor": unit.get("anchor")},
        "concepts": rows,
        "note": note,
    }
    if coverage is not None:
        part["coverage"] = coverage
    if named is not None:
        part["named_results"] = named
    return part


# ── 全局合并 ────────────────────────────────────────────────────────────
def concept_number(registry: dict[str, str]) -> int:
    """注册表里已用过的最大号 + 1。"""
    biggest = 0
    for value in registry.values():
        match = re.fullmatch(r"C-(\d+)", str(value))
        if match:
            biggest = max(biggest, int(match.group(1)))
    return biggest + 1


def merge_parts(parts: list[dict], *, book_title: str = "",
                registry: dict[str, str] | None = None) -> dict:
    """把各单元结果合并成全局唯一的 concepts.json。

    `registry`（名字 → id）是**概念 id 的稳定器**（2026-09-23 加）：
    重抽概念时同名沿用旧 id，新概念从最大号往后排。
    没有它的时候 id 是按出现顺序 `C-0001` 递增的，**中途多一条概念，后面全部后移**
    —— 实测把"命题"收进来之后，C-0017 从"线性组合"变成了"两向量共线的充要条件"，
    已有讲解里的 `[[CONCEPT:C-00xx]]` 就会指错概念。
    """
    merged: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for part in parts:
        unit = part.get("unit") or {}
        unit_id = unit.get("unit_id")
        for row in part.get("concepts", []):
            # 每一层都对自己的输入设防：merge 也可能被外部直接调用，
            # 不能假设上游一定已经过滤过类型（按决策 3 只收定义/定理）。
            if row.get("type") not in ALLOWED_TYPES or not row.get("name"):
                continue
            # ⚠️ **键用原始名字算**（`normalize_name(row["name"])`）—— `concepts.ids.json`
            #    是按这个键发的 id，键一变，已有讲解里的 `[[CONCEPT:C-00xx]]` 就会指错概念。
            #    清洗只动**显示名**（`name`），另存 `name_note` / `name_raw`。
            key = (normalize_name(row["name"]), row["type"])
            if key not in merged:
                order.append(key)
                clean_name, name_note = split_name_note(row["name"])
                entry_new = {
                    "name": clean_name,
                    "type": row["type"],
                    # ⚠️ **没有 brief/explanation**（2026-09-29 定案）：这两个字段
                    # 由**教案阶段**产出（连同这一节的讲解与配图），render 时合并进抽屉。
                    # 抽取阶段只负责"把它找出来"。
                    "formal": row["formal"],
                    "occurrences": [],
                    "source_quotes": [],
                }
                if name_note:                     # 剥下来的括注要留着（只增不丢）
                    entry_new["name_note"] = name_note
                    entry_new["name_raw"] = row["name"]
                merged[key] = entry_new
            entry = merged[key]
            # 同一概念第二次出现：**只追加位置**，不重写解释（方案第五条）。
            # 但**同一个单元里重复出现只记一次** —— 模型偶尔会在同一节里把同一个概念
            # 分成两条返回（实测 C-0042 就这样），不能因此变成"出现了 2 次"。
            if not any(o["unit_id"] == unit_id for o in entry["occurrences"]):
                entry["occurrences"].append({"unit_id": unit_id, "chapter": unit.get("chapter"),
                                             "section": unit.get("section"),
                                             "title": unit.get("title"), "anchor": unit.get("anchor")})
            if row["source_quote"] and row["source_quote"] not in entry["source_quotes"]:
                entry["source_quotes"].append(row["source_quote"])
            # 首次没抽到 formal/brief 的，用后面的补上（不覆盖已有内容）
            for field in ("formal",):
                if not entry.get(field) and row.get(field):
                    entry[field] = row[field]

    # ── 发 id：优先用注册表里已有的，新概念往后排 ──────────────────────
    registry = dict(registry or {})
    next_number = concept_number(registry)
    taken: set[str] = set()
    reused_ids, new_ids = 0, 0
    concepts = []
    for key in order:
        entry = merged[key]
        name_key = key[0]
        concept_id = registry.get(name_key)
        if concept_id and concept_id in taken:
            concept_id = None          # 同名但不同类型：不能共用一个 id
        if concept_id:
            reused_ids += 1
        else:
            while f"C-{next_number:04d}" in taken:
                next_number += 1
            concept_id = f"C-{next_number:04d}"
            next_number += 1
            new_ids += 1
            if name_key not in registry:
                registry[name_key] = concept_id
        taken.add(concept_id)
        concepts.append({"id": concept_id, **entry})

    units_with_concepts = sum(1 for p in parts if p.get("concepts"))
    return {
        "_meta": {
            "file_type": "concepts",
            "schema_version": SCHEMA_VERSION,
            "generator": GENERATOR,
            "field_notes": {
                "concepts[].id": "全局唯一概念 id；教案正文用 [[CONCEPT:C-0001]] 引用",
                "concepts[].type": "definition / theorem / proposition / corollary"
                                   "（2026-09-23 起收四类）",
                "concepts[].brief": "1~2 句，渲染层鼠标悬停显示",
                "concepts[].explanation": "完整通俗解释（Markdown），渲染层右侧抽屉显示",
                "concepts[].formal": "原文严格表述（逐字）",
                "concepts[].occurrences[]": "这个概念在哪些单元出现；重复出现只加这里，不重写解释",
                "concepts[].source_quotes[]": "抽取依据的原文片段",
                "source_parts": "合并前的分片文件；单节重跑只需重生成对应分片",
                "id_registry": "名字 → 概念 id 的注册表（稳定器）。重抽时同名沿用旧 id，"
                               "新概念从最大号往后排；另存一份在 concepts.ids.json",
            },
            "book_title": book_title,
        },
        "counts": {
            "units_total": len(parts),
            "units_with_concepts": units_with_concepts,
            "concepts_total": len(concepts),
            "definitions": sum(1 for c in concepts if c["type"] == "definition"),
            "theorems": sum(1 for c in concepts if c["type"] == "theorem"),
            "propositions": sum(1 for c in concepts if c["type"] == "proposition"),
            "corollaries": sum(1 for c in concepts if c["type"] == "corollary"),
            "multi_occurrence": sum(1 for c in concepts if len(c["occurrences"]) > 1),
            "ids_reused": reused_ids,
            "ids_new": new_ids,
        },
        "source_parts": [{"unit_id": (p.get("unit") or {}).get("unit_id"),
                          "concepts": len(p.get("concepts", []))} for p in parts],
        "id_registry": registry,
        "concepts": concepts,
    }


# ── 编排 ────────────────────────────────────────────────────────────────
def _extract_one(parse_dir: Path, unit: dict, client, *, force: bool,
                 retries: int, log=None, prefer_cleaned: bool = True) -> dict:
    """抽一节（含"分片还在不在/要不要重抽"的判断）。**串行与并行共用这一条路径。**

    并行安全性：每个单元只写自己的 `concepts.parts/<unit>.json`（路径互不相同），
    所以多线程各写各的，不会互相踩。
    """
    parts_dir = parse_dir / "concepts.parts"
    unit_id = unit["unit_id"]
    part_path = parts_dir / f"{unit_id}.json"
    source, source_kind = unit_source(parse_dir, unit, prefer_cleaned=prefer_cleaned)
    if not source.is_file():
        return {"unit_id": unit_id, "status": "skipped", "why": "找不到原文"}
    text = source.read_text(encoding="utf-8")
    source_sha = _sha256(text)
    if part_path.is_file() and not force:
        saved = _read_json(part_path) or {}
        meta = saved.get("_meta", {}) or {}
        same_text = meta.get("source_sha256") == source_sha
        fp = _fingerprint_of(client)
        same_model = all(meta.get(k) == v for k, v in fp.items())
        if same_text and same_model:
            return {"unit_id": unit_id, "status": "reused"}
        # 源文本或提示词变了 → 必须重抽（记账成 'restale'，供 --json 汇总与测试核对）
        stale_flag = True
    else:
        stale_flag = False
    unit = {**unit, "_text": text}
    try:
        part = extract_unit(unit, text, client, retries=retries, log=log)
    except Exception as exc:                      # noqa: BLE001
        # ⚠️ **单节失败不许拖垮整批**（2026-09-27 实测抓出）：CH07-S02 的返回带非法转义，
        #    异常一路冒到 CLI，212 节在 34/212 处整体中止，前面 34 节白等。
        #    失败单节**记下来并继续**，最后能 `--only` 精确补跑。
        return {"unit_id": unit_id, "status": "failed",
                "error": f"{type(exc).__name__}: {exc}"[:300]}
    part["_meta"].update({
        "text_sha256": source_sha,
        "source_sha256": source_sha,
        "source": source_kind,
        "source_path": str(source.relative_to(parse_dir)).replace(chr(92), "/"),
    })
    part_path.write_text(json.dumps(part, ensure_ascii=False, indent=2) + chr(10),
                         encoding="utf-8")
    return {"unit_id": unit_id, "status": "extracted", "was_stale": stale_flag}


def extract_concepts(parse_dir: str | Path, *, client, only_units: list[str] | None = None,
                     force: bool = False, retries: int = 1, progress=None,
                     log=None, parallel: int = 1, client_factory=None,
                     prefer_cleaned: bool = True) -> dict:
    """逐单元抽取 → 分片落盘 → 合并。

    `concepts.parts/<unit>.json` 存在就跳过（不重复花钱）；`force=True` 才重抽。
    所以"只重跑某一节"= 删掉那一节的分片再跑。

    **并行**（2026-09-29 加，用户定案"开并行开思考"）：`parallel > 1` 且给了
    `client_factory` 时，用线程池并发跑（每个 worker 一个自己的客户端）。
    为什么值得：实测每节 **44 秒里有 43 秒是模型在思考**（推理占输出 87%），
    这段时间**完全可以叠起来** —— 8 路并行把整本 45 分钟压到约 6 分钟，**花费一分不多**。

    两处 2026-09-23 的改动：
      * **F1**：读的是清洗后的原文（`latex/<单元>.md`）而不是 OCR 原文；分片里记
        `source_sha256`，**源文本变了就自动重抽**（否则清洗完了概念卡还是旧的）。
      * **F2**：概念 id 走注册表（`concepts.ids.json`），重抽时同名沿用旧 id。
    """
    parse_dir = Path(parse_dir)
    chapters_path = parse_dir / "chapters.json"
    if not chapters_path.is_file():
        raise ValueError(f"找不到 chapters.json：{chapters_path}")
    chapters = json.loads(chapters_path.read_text(encoding="utf-8"))
    units = chapters.get("lesson_units", [])
    if not units:
        raise ValueError("chapters.json 里没有 lesson_units，先跑导出层")

    parts_dir = parse_dir / "concepts.parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    wanted = set(only_units) if only_units else None

    registry = load_registry(parse_dir)
    if not registry:
        # 第一次用注册表：拿现有 concepts.json 播种，**保住当前这批 id**
        seed_source = "现有 concepts.json"
        old = _read_json(parse_dir / "concepts.json") or {}
        for concept in old.get("concepts", []):
            key = normalize_name(concept.get("name"))
            if key and concept.get("id"):
                registry[key] = concept["id"]
    else:
        seed_source = "concepts.ids.json"

    # 要处理的单元（按书的顺序；`--only` 过滤）
    work = [u for u in units if wanted is None or u["unit_id"] in wanted]
    statuses: list[dict] = []
    worker_clients: list = []      # 并行时收 worker 客户端（**串行路径必须也先初始化**，
                                   # 否则后面汇总用量那行 UnboundLocalError —— 2026-09-29 实测踩过）

    if parallel > 1 and client_factory is not None and len(work) > 1:
        # **并行**：每节 40+ 秒里绝大部分是模型在思考 → 叠起来跑，花费不变。
        from concurrent.futures import ThreadPoolExecutor, as_completed
        done = 0
        with ThreadPoolExecutor(max_workers=max(1, int(parallel))) as pool:
            futures = {}
            for unit in work:
                worker_client = client_factory()
                worker_clients.append(worker_client)
                futures[pool.submit(_extract_one, parse_dir, unit, worker_client,
                                    force=force, retries=retries, log=None,
                                    prefer_cleaned=prefer_cleaned)] = unit["unit_id"]
            for future in as_completed(futures):
                unit_id = futures[future]
                done += 1
                try:
                    row = future.result()
                except Exception as exc:              # noqa: BLE001
                    row = {"unit_id": unit_id, "status": "failed",
                           "error": f"{type(exc).__name__}: {exc}"[:300]}
                statuses.append(row)
                if log:
                    log(f"  [{done}/{len(work)}] {unit_id} → {row['status']}"
                        + (f"（{row.get('error', '')[:60]}）" if row["status"] == "failed" else ""))
    else:
        for index, unit in enumerate(work, 1):
            if progress is not None:
                progress(index, len(work), unit["unit_id"])
            row = _extract_one(parse_dir, unit, client, force=force,
                              retries=retries, log=log, prefer_cleaned=prefer_cleaned)
            statuses.append(row)

    # ⚠️ **并行的账要并回来**（2026-09-29 实测抓出）：用量记在**客户端实例**上，
    #    并行时每个 worker 一个客户端 —— 不汇总的话 CLI 只报主客户端的计数（恒为 0），
    #    于是"跑了 3 节、花了多少"显示成 **0 token**，是假账。
    if worker_clients:
        for worker in worker_clients:
            client.calls = int(getattr(client, "calls", 0)) + int(getattr(worker, "calls", 0))
            base = getattr(client, "session_usage", None)
            extra = getattr(worker, "session_usage", None)
            if base is None or extra is None:
                continue
            for field in ("prompt_tokens", "completion_tokens", "total_tokens",
                          "reasoning_tokens", "cache_hit_tokens", "cache_miss_tokens"):
                setattr(base, field, int(getattr(base, field, 0)) + int(getattr(extra, field, 0)))

    extracted = sum(1 for r in statuses if r["status"] == "extracted")
    reused = sum(1 for r in statuses if r["status"] == "reused")
    skipped = sum(1 for r in statuses if r["status"] == "skipped")
    # 「分片在、但源文本或提示词变了」→ 已重抽；单独计数（`units_restale`）
    stale = sum(1 for r in statuses if r.get("was_stale"))
    failed: list[dict] = [{"unit_id": r["unit_id"], "error": r.get("error", "")}
                          for r in statuses if r["status"] == "failed"]
    for row in failed:
        if log:
            log(f"  ✗ {row['unit_id']} 抽概念失败 → **跳过继续**，"
                f"稍后可 `--only {row['unit_id']}` 补")

    # 合并：按 lesson_units 的顺序读分片；id 由注册表定，跟顺序无关
    parts = []
    for unit in units:
        part_path = parts_dir / f"{unit['unit_id']}.json"
        if part_path.is_file():
            parts.append(_read_json(part_path))
    parts = [p for p in parts if p]
    merged = merge_parts(parts, book_title=(chapters.get("book") or {}).get("title", ""),
                         registry=registry)
    # 回写注册表：把这一轮新发的号也存下来，下次重抽才能沿用
    registry = merged.pop("id_registry", registry)
    orphan = len(set(registry) - {normalize_name(c["name"]) for c in merged["concepts"]})
    merged["counts"].update(units_extracted=extracted, units_skipped=skipped,
                            units_reused=reused, units_restale=stale,
                            units_failed=len(failed),
                            ids_registry=len(registry), ids_orphan=orphan)
    write_registry(parse_dir, registry, book_title=merged["_meta"].get("book_title", ""),
                   seed_from=seed_source)
    (parse_dir / "concepts.json").write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if failed:
        # 失败清单落盘：不藏在日志里，好让人（和下一趟）知道**具体缺哪几节**
        (parse_dir / "concepts.failed.json").write_text(
            json.dumps({"_meta": {"file_type": "concepts_failed",
                                  "note": "抽概念时失败、被跳过的单元；用 --only <这些 id> 补跑"},
                        "failed": failed}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    return {"concepts": str(parse_dir / "concepts.json"),
            "parts_dir": str(parts_dir), "ids": str(parse_dir / IDS_FILE),
            "failed": failed, **merged["counts"]}


__all__ = ["extract_concepts", "extract_unit", "merge_parts", "normalize_name",
           "unit_source", "load_registry", "write_registry", "concept_number",
           "SCHEMA_VERSION", "PART_SCHEMA_VERSION", "GENERATOR", "ALLOWED_TYPES",
           "TYPE_LABELS", "IDS_FILE"]
