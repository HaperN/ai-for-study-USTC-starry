"""模型规格的校验与容错（规范第七、八节那张 R 表）。

三件事，顺序不能反：

1. **归一化**：模型一定会写歪（`矩阵变形`、`matrix_flow`、把两个模板名都写上、
   数字写成字符串）—— 先按别名表救回来，能救的不重跑（这条在 `diagrams` 上验证过）。
2. **校验**：剩下的一律按 R1~R16 判，**能裁的裁、能删的删**，只有"救不了"才丢弃。
3. **留痕**：所有被改过的地方写进 `tolerance_notes`，被丢的写进 `dropped` —— 
   这样"救了多少、丢了多少"是可数的，而不是"感觉还行"。
"""
from __future__ import annotations

import json
import math
import re

# ── 模板表（规范第四节）：渲染层认识这些，别的一律丢 ──────────────────
TEMPLATES: dict[str, dict] = {
    # P-003：用户原话「哪有矩阵只要改一个地方的」—— 教材里矩阵的动作**从来不是**
    # "拖 4 个滑块改一个数"，而是**整行一起动**（交换两行 / 某行乘非零常数 / 某行的倍数加到另一行）。
    # 所以这个模板的控件是"选一个行操作 + 拖一个系数 λ"，改的是**一整行**，
    # 而且左边矩阵里的行与右边平面上的行向量**一起动** —— 看得见"整行"这件事。
    "row-operation": {
        "kind": "canvas",
        # P-016：这个模板要"矩阵"已经讲过（§2.1 还没讲矩阵，就不该出现"整行一起动"这种模型）。
        # 另外它的 det 读数是"行列式"的机器，行列式还没讲时由校验器**摘掉那个读数**（不是丢掉模型）。
        "needs_names": ["矩阵"],
        # 注意：**不要**把「行列式」写进 needs_names —— 行列式还没讲时我们要的是
        # "摘掉 det 读数、保留模型"（见 strips），而不是把整个模板禁掉。实测踩过这个不一致。
        "strips": ["det"],
        "params": {"matrix": {"type": "matrix", "cols": (2, 3), "required": True},
                   "op": {"type": "enum", "allow": {"swap", "scale", "addMultiple"}},
                   "lambda": {"type": "number", "min": -4, "max": 4},
                   "rows": {"type": "numbers", "max": 2},
                   "show": {"type": "show",
                            "allow": {"grid", "rows", "rowsAfter", "unitSquare", "image", "basis"}}},
        "controls": {"row-actions", "slider", "play", "toggle"},
        "readouts": {"det(A)"},
        "steps": (2, 6),
    },
    "vector-combination": {
        "kind": "canvas",
        # 需要本节正文或已引入的概念名明确包含向量；不按跨书固定编号推断。
        "needs_names": ["向量"],
        "params": {"vectors": {"type": "points", "required": True, "max": 3},
                   "coefficients": {"type": "numbers", "max": 3},
                   "show": {"type": "show",
                            "allow": {"grid", "vectors", "sum", "parallelogram"}}},
        "controls": {"slider", "drag", "toggle"},
        "readouts": {"norm(sum)"},
        "steps": (2, 6),
    },
    "elimination-steps": {
        "kind": "symbolic",
        # 符号型只排方程组/矩阵，不借空间语言
        "needs": [],
        "params": {"form": {"type": "enum", "allow": {"system", "matrix"}},
                   "augmented": {"type": "bool"},
                   "steps": {"type": "step_list", "required": True, "min": 2, "max": 6}},
        "controls": {"play"},
        "readouts": set(),
        "steps": (0, 0),          # symbolic：分步内容在 params.steps 里
    },
    "harmonic-contours": {
        "kind": "svg",
        "params": {"initial_point": {"type": "bounded_point", "required": True},
                   "extent": {"type": "bounded_extent", "required": True}},
        "controls": {"drag", "slider"}, "readouts": set(), "steps": (0, 0),
    },
    "cross-product-3d": {
        "kind": "canvas",
        "needs_names": ["向量积", "向量"],
        "params": {"a": {"type": "vec3", "required": True},
                   "b": {"type": "vec3", "required": True},
                   "view": {"type": "numbers", "max": 2},
                   "show": {"type": "show",
                            "allow": {"grid", "axes", "parallelogram", "normal"}}},
        "controls": {"slider"},
        "readouts": {"norm(cross(a,b))"},
        "steps": (2, 6),
    },
}

ALIASES = {
    # 老模板名（矩阵变形）保留为别名 → 现在一律按"整行动作"渲染，别让老产物变成白板
    "矩阵变形": "row-operation", "matrix_transform": "row-operation",
    "matrixtransform": "row-operation", "matrix": "row-operation",
    "matrix-transform": "row-operation",
    "行变换": "row-operation", "row_operation": "row-operation",
    "row-operations": "row-operation", "行操作": "row-operation",
    "线性组合": "vector-combination", "vector_combination": "vector-combination",
    "linear-combination": "vector-combination", "span": "vector-combination",
    "消元分步": "elimination-steps", "elimination": "elimination-steps",
    "gauss-steps": "elimination-steps", "gauss_steps": "elimination-steps",
    "向量积": "cross-product-3d", "cross_product": "cross-product-3d",
    "cross-product": "cross-product-3d", "外积": "cross-product-3d",
}

MAX_MODELS_PER_UNIT = 3
NUM_LIMIT = 4.0                       # 规范第六节第 4 条：|数值| ≤ 4
LEN = {"title": 20, "caption": 60, "label": 24, "fallback_text": 80,
       "why_here": 60, "view_hint": 80}
KINDS = {"canvas", "symbolic", "svg"}


def _num(value):
    """只认真正的数。`"1/2"` 这类**不救** —— 猜错了比缺一个更糟。"""
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value.strip() if isinstance(value, str) else value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value, limit=NUM_LIMIT):
    n = _num(value)
    if n is None:
        return None, False
    if abs(n) > limit:
        return max(-limit, min(limit, n)), True
    return n, False


def _maybe_json(value):
    """模型有时把 `matrix` 写成字符串 `"[[2,1],[0,1]]"` —— 试一次，能救就救。"""
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in "[{":
            try:
                return json.loads(text)
            except ValueError:
                return None
    return value


def _normalize_matrix(value, notes, where, cols=(2, 2)):
    """只认"两行、列数在 cols 区间内"的矩阵。

    为什么行变换允许 2~3 列：教材 §2.2 的增广矩阵就是 2 行 3 列（系数两块 + 常数列）。
    之前写死 2×2，于是"整行一起动"只能在 2×2 上看，跟课本的例子对不上。"""
    value = _maybe_json(value)
    lo, hi = cols
    if not isinstance(value, list) or len(value) != 2:
        return None
    rows = []
    for row in value:
        if not isinstance(row, list) or not (lo <= len(row) <= hi):
            return None
        cells = []
        for cell in row:
            n, clamped = _clamp(cell)
            if n is None:
                return None
            if clamped:
                notes.append(f"{where}: 数值超过 ±{NUM_LIMIT:g}，已裁到界内")
            cells.append(int(n) if float(n).is_integer() else round(n, 4))
        rows.append(cells)
    return rows


def _normalize_numbers(value, notes, where, limit=4):
    value = _maybe_json(value)
    if not isinstance(value, list):
        return None
    out = []
    for item in value:
        n, clamped = _clamp(item)
        if n is None:
            return None
        if clamped:
            notes.append(f"{where}: 数值超过 ±{NUM_LIMIT:g}，已裁到界内")
        out.append(int(n) if float(n).is_integer() else round(n, 4))
    return out[:limit]


def _normalize_points(value, notes, where, max_points):
    value = _maybe_json(value)
    if not isinstance(value, list):
        return None
    points = []
    for point in value:
        if isinstance(point, list) and len(point) >= 2:
            p = _normalize_numbers(point, notes, where, limit=3)
            if p is None:
                return None
            points.append(p)
    if not points:
        return None
    if len(points) > max_points:
        notes.append(f"{where}: 点太多（{len(points)} > {max_points}），已截断")
        points = points[:max_points]
    return points


def _normalize_step_rows(value, notes, where):
    """`elimination-steps` 的一步：`rows`（或 `matrix`）可以是数字数组，也可以是 LaTeX 串。"""
    value = _maybe_json(value)
    if isinstance(value, dict):
        for key in ("rows", "matrix", "equations"):
            if key in value:
                value = value[key]
                break
    if not isinstance(value, list):
        return None
    rows = []
    for row in value:
        if isinstance(row, str) and row.strip():
            rows.append(row.strip())
        elif isinstance(row, list):
            cells = []
            for cell in row:
                if isinstance(cell, str):
                    cells.append(cell)               # 已经是 LaTeX
                else:
                    n = _num(cell)
                    if n is None:
                        return None
                    cells.append(int(n) if float(n).is_integer() else round(n, 4))
            rows.append(cells)
    return rows or None


def _truncate(text, limit, notes, where):
    text = str(text or "").strip()
    if len(text) > limit:
        notes.append(f"{where}: 超长（{len(text)} > {limit} 字），已截断")
        return text[:limit]
    return text


def normalize_models(specs, body: str = "", available_ids=None,
                     introduced_ids=None, introduced_names=None,
                     all_concept_names=None, allowed_templates=None, template_bindings=None
                     ) -> tuple[list[dict], list[str], list[str]]:
    """归一化 + 校验。返回 (可用的模型, 容错记录, 丢弃记录)。

    `available_ids`：这一节**真实存在**的概念 / 公式详解 / 过程图 id 集合。
    P-003 的相关性判据（R17）拿它来判"模型绑定的那个东西，本节到底有没有"。

    **P-016（用户 2026-09-25）**：模型**不许借"还没讲的东西"**（原话：「解方程有时候不需要
    线性代数后面的那些矩阵和线性空间」「不需要的地方你放一个新手更不明白了」）。再加三条：
      * **R19 模板前置**：模板自带的 `needs`（概念 id）与 `needs_names`（概念名）必须**到本节为止
        已经讲过**（判据来自 `lib/curriculum.py` 的时间线，只用产物、0 token，换书也成立）；
      * **R19b binds_to 的时点**：绑到 `C-…` 时那个概念必须**到本节为止已引入**；
      * **R20 连续量**：canvas 模型必须有**可拖的连续量**（`lambda`/`coefficients`），
        否则它只是一张静态图 —— 这正是"装饰"的机器判据。
      前置名称必须在已引入概念或本节正文中出现；不按跨书固定编号推断。
    """
    notes: list[str] = []
    dropped: list[str] = []
    out: list[dict] = []
    seen_ids: set[str] = set()

    for index, raw in enumerate(specs or [], 1):
        if not isinstance(raw, dict):
            dropped.append(f"第 {index} 个不是对象")
            continue
        mid = str(raw.get("id") or f"M-{index:02d}").strip()
        if not re.fullmatch(r"M-\d{1,2}", mid) or mid in seen_ids:
            dropped.append(f"{mid or '（无 id）'}: id 不合法或重复")
            continue

        name = str(raw.get("template") or raw.get("type") or "").strip()
        key = name.lower().replace(" ", "")
        template = ALIASES.get(name, ALIASES.get(key, key))
        # 模型有时把两个模板名都写上（`matrix-transform|vector-combination`）—— 按参数判
        if template not in TEMPLATES and "|" in name:
            candidates = [ALIASES.get(p.strip(), p.strip()) for p in name.split("|")]
            params = raw.get("params") or {}
            template = next((c for c in candidates
                             if c in TEMPLATES and any(k in params for k in TEMPLATES[c]["params"])), "")
            if template:
                notes.append(f"{mid}: 模板名写了两个（{name}），按参数判定为 {template}")
        if template not in TEMPLATES:
            dropped.append(f"{mid}: 模板 {name or '（空）'} 不在白名单（原值留档）")
            continue

        if allowed_templates is not None and template not in allowed_templates:
            dropped.append(f"{mid}: 模板 {template} 不适用本节的教学对象")
            continue
        spec = TEMPLATES[template]
        if template == "harmonic-contours":
            # Fixed mathematical object only; no silently discarded executable/program inputs.
            permitted = {"id", "template", "type", "kind", "title", "binds_to", "why_here", "why",
                         "anchor_hint", "where", "params", "data", "controls", "readouts", "steps",
                         "caption", "fallback_text", "view_hint"}
            params_raw = raw.get("params", raw.get("data"))
            anchor = str(raw.get("anchor_hint") or raw.get("where") or "").strip()
            reason = str(raw.get("why_here") or raw.get("why") or "").strip()
            bound = str(raw.get("binds_to") or "")
            allowed_bindings = (template_bindings or {}).get(template, [])
            if (set(raw) - permitted or not isinstance(params_raw, dict)
                    or set(params_raw) != {"initial_point", "extent"}
                    or not allowed_bindings or bound not in allowed_bindings
                    or not anchor or anchor not in body or len(anchor) < 6 or not reason):
                dropped.append(f"{mid}: harmonic-contours 需要合法数据、本节解释绑定、正文落点与教学理由")
                continue
            point, extent = params_raw.get("initial_point"), params_raw.get("extent")
            if (isinstance(extent, bool) or not isinstance(extent, (int, float))
                    or _num(extent) is None or not 1 <= extent <= 4
                    or not isinstance(point, list) or len(point) != 2
                    or any(isinstance(n, bool) or not isinstance(n, (int, float))
                           or _num(n) is None or abs(n) > extent for n in point)):
                dropped.append(f"{mid}: harmonic-contours 参数必须有限且 initial_point 落在 extent∈[1,4] 内")
                continue
            raw = {**raw, "params": params_raw, "anchor_hint": anchor, "why_here": reason,
                   "controls": [{"name": "point", "kind": "drag"}, {"name": "point", "kind": "slider"}],
                   "readouts": [], "steps": []}
        kind = spec["kind"]
        # ── R19 模板前置：模板点名要的概念/名字，到本节为止讲过没有 ──
        if introduced_ids is not None:
            missing = [c for c in (spec.get("needs") or []) if c not in set(introduced_ids)]
            if missing:
                dropped.append(f"{mid}: 模板 {template} 需要还没讲的概念 {missing}"
                               f"（前置不合格，已丢弃）")
                continue
        if introduced_names is not None and spec.get("needs_names"):
            names = set(introduced_names)
            # Names have book-local semantics; arbitrary C-ids cannot establish a prerequisite.
            clash = [n for n in spec["needs_names"]
                     if not any(n in x for x in names) and n not in body]
            if clash:
                dropped.append(f"{mid}: 模板 {template} 依赖「{'、'.join(clash)}」，"
                               f"这一节之前还没讲过（前置不合格，已丢弃）")
                continue
        if raw.get("kind") and str(raw.get("kind")) != kind:
            notes.append(f"{mid}: kind 写的 {raw.get('kind')}，按模板表改成 {kind}")
        params_in = raw.get("params") if isinstance(raw.get("params"), dict) else {}
        params: dict = {}
        for pname, rule in spec["params"].items():
            value = params_in.get(pname)
            if value is None:
                if rule.get("required"):
                    dropped.append(f"{mid}: 缺必填参数 {pname}")
                    break
                continue
            ptype = rule["type"]
            if ptype in ("bounded_point", "bounded_extent"):
                pass  # Strict type/domain checks above, never clamp invalid harmonic data.
            elif ptype in ("matrix2", "matrix"):
                value = _normalize_matrix(value, notes, f"{mid}.{pname}",
                                          cols=tuple(rule.get("cols", (2, 2))))
                if value is None:
                    dropped.append(f"{mid}: 参数 {pname} 不是 2×2 数字矩阵")
                    break
            elif ptype == "points":
                value = _normalize_points(value, notes, f"{mid}.{pname}", rule.get("max", 6))
                if value is None:
                    dropped.append(f"{mid}: 参数 {pname} 不是点列表")
                    break
            elif ptype == "numbers":
                value = _normalize_numbers(value, notes, f"{mid}.{pname}", rule.get("max", 4))
                if value is None:
                    dropped.append(f"{mid}: 参数 {pname} 不是数字列表")
                    break
            elif ptype == "step_list":
                steps = []
                for s in value if isinstance(value, list) else []:
                    if not isinstance(s, dict):
                        continue
                    rows = _normalize_step_rows(s, notes, f"{mid}.{pname}")
                    if rows is None:
                        notes.append(f"{mid}: 某一步的 rows 读不出来，已跳过这一步")
                        continue
                    step = {"rows": rows}
                    if s.get("op"):
                        step["op"] = _truncate(s.get("op"), 60, notes, f"{mid}.op")
                    if s.get("note"):
                        step["note"] = _truncate(s.get("note"), 120, notes, f"{mid}.note")
                    steps.append(step)
                if len(steps) < rule.get("min", 2):
                    dropped.append(f"{mid}: 分步只有 {len(steps)} 步，少于 {rule.get('min', 2)} 步")
                    break
                if len(steps) > rule.get("max", 6):
                    notes.append(f"{mid}: 分步 {len(steps)} 步，截到 {rule.get('max', 6)} 步")
                    steps = steps[:rule.get("max", 6)]
                value = steps
            elif ptype == "show":
                allow = rule["allow"]
                picked = [s for s in (value if isinstance(value, list) else []) if s in allow]
                unknown = [s for s in (value if isinstance(value, list) else []) if s not in allow]
                if unknown:
                    notes.append(f"{mid}.{pname}: 去掉了不认识的项 {unknown}")
                value = picked
            elif ptype == "enum":
                if value not in rule["allow"]:
                    notes.append(f"{mid}.{pname}: {value!r} 不在 {sorted(rule['allow'])}，用默认值")
                    continue
            elif ptype == "number":
                # _clamp 返回 (值, 是否被削过) —— 第一版忘了取 [0]，写进了元组，
                # 渲染层拿到 (-2.0, False) 这种"数字不像数字"的东西（实测踩过）
                clamped, cut = _clamp(value, max(abs(rule.get("min", -NUM_LIMIT)),
                                                 abs(rule.get("max", NUM_LIMIT))))
                if clamped is None:
                    notes.append(f"{mid}.{pname}: 不是数字，用默认值")
                    continue
                if cut:
                    notes.append(f"{mid}.{pname}: 超出范围，已收到 {clamped}")
                value = clamped
            elif ptype == "int":
                n = _num(value)
                if n is None:
                    continue
                value = int(max(rule.get("min", 0), min(rule.get("max", 99), n)))
            elif ptype == "bool":
                value = bool(value)
            elif ptype == "vec3":
                nums = _normalize_numbers(value, notes, f"{mid}.{pname}", limit=3)
                if nums is None or len(nums) != 3:
                    dropped.append(f"{mid}: 参数 {pname} 不是三个数")
                    break
                value = nums
            params[pname] = value
        else:
            extra = sorted(set(params_in) - set(spec["params"]))
            if extra:
                notes.append(f"{mid}: params 里去掉了渲染层不认的键 {extra}")
            controls = [c for c in (raw.get("controls") or [])
                        if isinstance(c, dict) and c.get("kind") in spec["controls"]]
            if len(controls) != len(raw.get("controls") or []):
                notes.append(f"{mid}: controls 里去掉了模板不支持的项")
            if not controls:
                controls = [{"name": "default", "kind": sorted(spec["controls"])[0]}]
            readouts = [r for r in (raw.get("readouts") or [])
                        if isinstance(r, dict) and r.get("expr") in spec["readouts"]]
            # 读数里"还没讲的东西"要摘掉：行变换模型的 det 读数是行列式的机器，
            # 行列式还没讲的节里不该出现（摘读数、不丢模型）
            strip = set(spec.get("strips") or [])
            if strip and introduced_names is not None:
                keep = []
                for r in readouts:
                    expr = str(r.get("expr") or "")
                    hit = next((k for k in strip if k in expr), None)
                    if hit and not any(hit in x for x in set(introduced_names)):
                        notes.append(f"{mid}: 摘掉了读数 {expr}（属于还没讲的内容：{hit}）")
                        continue
                    keep.append(r)
                readouts = keep
            steps_out = []
            for s in (raw.get("steps") or []):
                if not isinstance(s, dict):
                    continue
                state = {k: v for k, v in (s.get("state") or {}).items() if k in params}
                if len(state) != len(s.get("state") or {}):
                    notes.append(f"{mid}: 某一步的 state 里有 params 之外的键，已去掉")
                steps_out.append({"label": _truncate(s.get("label"), LEN["label"], notes, f"{mid}.label"),
                                  "state": state})
            lo, hi = spec["steps"]
            if kind == "canvas":
                if len(steps_out) < lo:
                    dropped.append(f"{mid}: 只有 {len(steps_out)} 步（少于 {lo} 步）")
                    continue
                if len(steps_out) > hi:
                    notes.append(f"{mid}: {len(steps_out)} 步，截到 {hi} 步")
                    steps_out = steps_out[:hi]
            else:
                steps_out = []          # symbolic：分步在 params 里
            seen_ids.add(mid)
            # ── R20 连续量：canvas 模型必须能拖一个连续量（静态图不该占位）──
            if kind == "canvas":
                continuous = [k for k in ("lambda", "coefficients", "power")
                              if isinstance(params.get(k), (int, float, list))]
                if not continuous:
                    dropped.append(f"{mid}: canvas 模型没有任何可拖的连续量"
                                   f"（静态图不该配 canvas 模型，已丢弃）")
                    continue
            binds_to = str(raw.get("binds_to") or "").strip()
            why_here = _truncate(raw.get("why_here") or "", LEN["why_here"], notes, f"{mid}.why_here")
            out.append({
                "id": mid,
                "template": template,
                "template_raw": name if name != template else None,
                "kind": kind,
                "title": _truncate(raw.get("title") or "", LEN["title"], notes, f"{mid}.title"),
                "anchor_hint": str(raw.get("anchor_hint") or "").strip(),
                "binds_to": binds_to,          # P-003：它替教材的哪一步干活（概念/公式/过程图 id）
                "why_here": why_here,          # 用一句人话说明"为什么这里需要它"
                "view_hint": _truncate(raw.get("view_hint") or "", LEN["view_hint"],
                                       notes, f"{mid}.view_hint"),
                "params": params,
                "controls": controls,
                "readouts": readouts,
                "steps": steps_out,
                "caption": _truncate(raw.get("caption") or "", LEN["caption"], notes, f"{mid}.caption"),
                "fallback_text": _truncate(raw.get("fallback_text") or "", LEN["fallback_text"],
                                           notes, f"{mid}.fallback_text"),
            })

    # R11/R14：锚点找不到就留空（由渲染层按"正文第一段之后"兜底）；兜底说明空则用图注
    for m in out:
        if not m["anchor_hint"]:
            notes.append(f"{m['id']}: 没有 anchor_hint，引用会落到正文开头")
        if not m["fallback_text"]:
            m["fallback_text"] = m["caption"]
            notes.append(f"{m['id']}: 没给 fallback_text，用图注兜底")
    # R13：一节最多 3 个
    if len(out) > MAX_MODELS_PER_UNIT:
        notes.append(f"一节给了 {len(out)} 个模型，按前 {MAX_MODELS_PER_UNIT} 个保留")
        out = out[:MAX_MODELS_PER_UNIT]
    for m in out:
        # P-008：锚点是"照着当时的正文"写的 —— 正文后来重跑过就会失效。
        # 这里把结论**记进规格**（anchor_hint_ok），构建层与体检报告都要看它；
        # 光在 tolerance_notes 里留一句是不够的（实测：那句话没人看，页面上就是两张卡堆在开头）。
        m["anchor_hint_ok"] = (not m["anchor_hint"]) or (not body) or (m["anchor_hint"] in body)
        if not m["anchor_hint_ok"]:
            notes.append(f"{m['id']}: anchor_hint 在正文里找不到（正文改过 → 建议 --force 重跑这一节）")

    # ── R17 相关性（P-003 的新关卡）：模型必须绑到本节**真实存在**的一个东西上 ──
    # 用户原话：「目前建模相关性不行」—— 病根是"放进哪一节都行 = 哪一节都不贴"。
    available = {str(x) for x in (available_ids or [])}
    for m in out:
        if not m["binds_to"]:
            notes.append(f"{m['id']}: 没写 binds_to（相关性无法核对）")
            m["binds_to_ok"] = False
            continue
        if available and m["binds_to"] not in available:
            notes.append(f"{m['id']}: binds_to={m['binds_to']} 不在本节的概念/公式/过程图里"
                         f"（相关性不合格，已标注）")
            m["binds_to_ok"] = False
            continue
        m["binds_to_ok"] = bool(available)
        # R19b：绑到的概念必须"到本节为止已经讲过"（借后面的概念 = 前置不合格）
        if (m["binds_to"].startswith("C-") and introduced_ids is not None
                and m["binds_to"] not in set(introduced_ids)):
            notes.append(f"{m['id']}: binds_to={m['binds_to']} 是**本节之后**才讲的概念"
                         f"（借了后面的东西）→ binds_to_ok=false")
            m["binds_to_ok"] = False
    # ── R18 三维必须给"从哪看"：不然就是"能拖的装饰"（用户就是这么说的）──
    for m in out:
        if m["template"] == "cross-product-3d" and not m["view_hint"]:
            notes.append(f"{m['id']}: 三维模型没给 view_hint（该往哪看、看什么）")
            m["view_hint"] = "拖动可以转动画面：先把视线转到与 a、b 都垂直的方向，就能看见 a×b 立在它们张成的平面上。"
    return out, notes, dropped


__all__ = ["TEMPLATES", "ALIASES", "normalize_models", "MAX_MODELS_PER_UNIT", "NUM_LIMIT"]
