"""模型规格这一趟的提示词（规范第九节那份，逐字对齐）。

**只做一件事**：读这一节的正文，判断哪里"文字讲不清、必须动手看"，填出 `models[]`。
所以它拿到的输入很少（正文 + 已有的过程图清单），输出也很小 —— 这一趟大概 2~5k token。
"""
from __future__ import annotations

import json

SYSTEM = """你在给当前教材本节的**网页版**加"能上手玩的模型"。

你**只填数据**：选模板、给数字、写一两句说明。
**不要**写模板未声明的坐标范围、相机位置、画布大小、颜色、线宽、字体 —— 这些由渲染层决定，你写了也会被丢掉。

**第一重要：该不该配（"根据需要出建模"）。** 用户 2026-09-25 的原话是
「**我要的是根据需要出建模，不需要的地方你放一个新手更不明白了**」。
所以默认答案是**空数组**。只有下面三问**全部答"是"**，才允许配一个模型：

1. **这一节真有一步"文字说不清、必须看/拖"吗？** —— 能用一两句话讲清的地方，不要配。
   配了的话，`why_here` 必须说清"**没有它，读者会卡在哪一步**"（说不出就别配）。
2. **它用的东西，到这一节为止都讲过了吗？** —— **不许借后面的概念**。
   用户原话：「解方程有时候不需要线性代数后面的那些**矩阵和线性空间**」。
   我会把"这一节已经讲过什么""以后才讲什么"列给你；用到后者一律会被丢弃。
3. **和这一节已有的过程图/批注重复吗？** —— 过程图已经把"步骤的静态排列"画出来了；
   只是把图再动一遍、没有新东西可拖，就不要配。
   已有静态图不自动排除交互：若拖动交点能检验两族等值线、切线方向或原点例外，说明这项新增教学作用后再决定。

**第二重要：相关性。** 用户看过我们做的模型，原话是「**目前建模相关性不行**」。
每个模型都必须能回答这个问题：**"它在替教材做哪一步？"** 答不上来的，就不要做。
* 必填 `binds_to`：正文里那个概念 / 公式详解 / 过程图的 id（例如 `C-0004`、`F-01`、`D-01`）。
* 必填 `why_here`（≤60 字）：一句人话，说清"这里为什么非要动手看"。
* 判断标准：把正文那句话盖住，模型还能讲别的 —— 那它就是"通用演示"，**不要交**。

原则：
* **一个模型只讲一件事**。讲两件就拆成两个（一节最多 3 个）。
* **只为"文字讲不清、必须动手看"的地方配模型**；能用一句话说清的地方，不要配。
* 不要和已有的过程图重复：过程图已经把"步骤的静态排列"画出来了，你只在**需要动手**时才加模型。
* 数字要有界：`|数值| ≤ 4`。
* 矩阵类模型**不许**做成"拖 4 个滑块改某个数字"（用户原话：「**哪有矩阵只要改一个地方的**」）。
  教材里矩阵的动作是**整行一起动**：交换两行、某一行乘一个非零常数、某行的倍数加到另一行 ——
  所以用 `row-operation`，控件是"选一种行操作 + 拖系数 λ"。
* 三维模型只在**真的需要空间感**的节出现（向量积、平面、空间直线），并且**必须**给 `view_hint`
  （怎么转动画面、先看哪里、这一步看到什么），否则就是"能拖的装饰"。

模板（能用哪些**看下面给你的清单**；清单之外的模板这一节用不了，写了会被丢弃）：

| 模板 | 类型 | params | 感受目标 |
|---|---|---|---|
| `row-operation` | canvas | `matrix=[[a,b],[c,d]]`（默认 `[[1,0],[0,1]]`）；`op` ∈ `swap/scale/addMultiple`（交换两行 / 某行乘 λ / 第 i 行加 λ 倍第 j 行）；`lambda`（系数 λ，`|λ|≤4`）；`rows=[i,j]`（第 i 行是**被改动**的那一行）；`show` ⊆ `grid/rows/rowsAfter/unitSquare/image` | 行变换让**整行一起动**；λ=0 时整行消失、矩阵不可逆；行向量怎么跟着变 |
| `vector-combination` | canvas | `vectors=[[x,y],…]`（1~3 个）；`coefficients=[c,…]`；`show` ⊆ `grid/vectors/sum/parallelogram` | 某个向量（比如新的行）是怎么由别的向量组合出来的；共线就铺不满 |
| `elimination-steps` | symbolic | `form="system"`（方程组）或 `"matrix"`（增广矩阵，可加 `augmented:true`）；`steps=[{rows,op,note}]`（2~6 步） | 消元每一步在做什么（一步步对齐教材的演算） |
| `harmonic-contours` | svg | `initial_point=[1,1]`；`extent=3`，范围必须在 [1,4] 内，点必须在范围内 | **固定** $f(z)=z^2$、$u=x^2-y^2$、$v=2xy$；拖动交点观察两族等值线、梯度与切线，原点处导数为零，不能称正交。不允许自定义函数或代码。只有本节已讲共轭调和、等值线及这个平方例子，并有解释落点时可用 |
| `cross-product-3d` | canvas | `a=[x,y,z]`；`b=[x,y,z]`；`view=[方位角,仰角]`（度数，例如 `[40,25]`）；`show` ⊆ `grid/axes/parallelogram/normal` | `a×b` 的方向与大小（必须配 `view_hint`） |

每个模型都要给：
* `id`：`M-01`、`M-02`…（本节内唯一）
* `template`（照抄上表）、`kind`（`canvas`、`symbolic` 或 `svg`，照抄上表）
* `title`（≤20 字，说"看到什么"）、`caption`（≤60 字，回答"该看什么"）
* `binds_to`（正文里的概念/公式/过程图 id）、`why_here`（≤60 字：为什么这里必须动手）
* `anchor_hint`：正文里那句话的**前 6~12 字**，**必须与正文逐字一致**（标点、空格都要一样）。
  它是"把模型摆到正文哪一段"的唯一线索：写完后**自己回正文搜一遍** —— 搜不到，模型就会被挪到
  末尾并被判不合格。**不要用改写过的句子、不要用你自己总结的话。**
* `fallback_text`（≤80 字）：画布不可用时，读者只读正文也能懂
* `view_hint`：三维模型必填（≤80 字，说怎么转、看什么）
* canvas 类还要 `steps`：2~6 步，每步一句 `label`（≤24 字）+ `state`（只写要改的参数，是 params 的部分覆盖）；
  `row-operation` 的每一步最好就是一个**行操作**（`op`/`lambda`/`rows`），这样"下一步"就是教材的下一步
* svg 类不要 `steps`，控件与读数由内置模板提供；harmonic-contours 必须绑定下方清单列出的解释 id。
* symbolic 类不要 `steps`（分步内容在 `params.steps` 里）

只输出 JSON：`{"models": [ … ]}`。**宁可少给，也不要凑数**：这一节确实没有"必须动手"的地方，就输出 `{"models": [], "decision": {"status":"not_applicable", "reason":"为何无需额外动手展示的具体理由", "evidence":[{"quote":"逐字摘自本节正文", "explanation":"已有说明如何满足教学需要"}]}}`。空返回缺理由或正文证据会进入待复核。
"""

SCHEMA = """{
  "models": [
    {
      "id": "M-01",
      "template": "row-operation",
      "kind": "canvas",
      "title": "整行一起动：第2行减2倍第1行",
      "binds_to": "C-0007",
      "why_here": "文字只说得出“换了行”，要看见整行怎么一起动、解为什么不变",
      "anchor_hint": "我们称这三种变换为",
      "params": {"matrix": [[1, 2, 1], [2, 3, 1]], "op": "addMultiple", "lambda": -2,
                 "rows": [2, 1], "show": ["grid", "rows", "rowsAfter"]},
      "controls": [{"name": "op", "kind": "row-actions"},
                   {"name": "lambda", "kind": "slider", "range": [-3, 3], "step": 0.1}],
      "readouts": [{"name": "det", "expr": "det(A)", "label": "行列式"}],
      "steps": [
        {"label": "原矩阵的两行", "state": {"lambda": 0}},
        {"label": "第2行 − 2×第1行", "state": {"lambda": -2}}
      ],
      "caption": "拖动 λ：第2行整行跟着变，第1行不动 —— 这就是课本里的倍加变换",
      "fallback_text": "把第 j 行的 λ 倍加到第 i 行，第 i 行整行一起变，解集不变。"
    }
  ]
}"""


def usable_templates(context: dict) -> list[str]:
    """Compatibility prerequisite view; generation uses the stricter per-unit plan."""
    from model_validate import TEMPLATES
    if "templates" in context:
        return list(context["templates"])
    names = list(context.get("introduced_names") or [])
    out = []
    for key, spec in TEMPLATES.items():
        if key == "harmonic-contours":
            continue  # Requires current lesson text and a matching explanation, not a name alone.
        if any(not any(term in name for name in names) for term in spec.get("needs_names", [])):
            continue
        if key == "elimination-steps" and not any("方程组" in name or "消元" in name for name in names):
            continue
        out.append(key)
    return out


def user_prompt(*, title: str, body: str, diagrams: list[dict], concepts: list[dict],
                explainables: list[dict] | None = None, context: dict | None = None) -> str:
    context = context or {}
    lines = [f"这一节是《{title}》。", "", "【这一节的正文】", body.strip(), ""]
    if context.get("introduced_names"):
        lines += ["【到这一节为止，书里**已经讲过**的概念（可以放心用）】",
                  "、".join(context["introduced_names"][:60]), ""]
    if context.get("later_names"):
        lines += ["【**以后才讲**的概念（本节一个字都不许用，也不许当模型的前提）】",
                  "、".join(context["later_names"][:60]), ""]
    usable = usable_templates(context)
    if context:
        lines += [f"【这一节**能用的模板**（只有这些；其它模板未匹配本节的教学对象或缺前提）】",
                  "、".join(f"`{x}`" for x in usable) or "（这一节一个都用不了 → 请输出空数组）", ""]
    if context.get("bindings"):
        lines += ["【已核实的模板解释落点（binds_to 必须选对应 id）】",
                  str(context["bindings"]), ""]
    if concepts:
        lines += ["【本节的概念（`binds_to` 可以填这些 id）】"]
        for c in concepts:
            lines.append(f"* {c.get('id')} {c.get('name') or ''}")
        lines.append("")
    if explainables:
        lines += ["【本节的公式 / 定理详解（`binds_to` 也可以填这些 id）】"]
        for x in explainables:
            lines.append(f"* {x.get('id')} {x.get('name') or ''}：{x.get('summary') or ''}")
        lines.append("")
    if diagrams:
        lines += ["【这一节已有的过程图（不要重复它们做的事；`binds_to` 也可以填这些 id）】"]
        for d in diagrams:
            lines.append(f"* {d.get('id')} {d.get('template')}：{d.get('title') or '（无标题）'}"
                         f"（{len(d.get('steps') or [])} 步）")
        lines.append("")
    example = SCHEMA
    if "harmonic-contours" in usable:
        offset = body.find("共轭调和")
        example = json.dumps({"models": [{
            "id": "M-01", "template": "harmonic-contours", "kind": "svg",
            "title": "移动交点看等值线",
            "binds_to": (context.get("bindings", {}).get("harmonic-contours") or [""])[0],
            "why_here": "移动交点才能同时看到两族等值线与切线方向，并检查原点的例外。",
            "anchor_hint": body[offset:offset + 10] if offset >= 0 else body[:10],
            "params": {"initial_point": [1, 1], "extent": 3},
            "caption": "移动交点，观察两条切线；原点不适用正交结论。",
            "fallback_text": "u=x²−y²，v=2xy；在非原点处两条等值线正交。"
        }]}, ensure_ascii=False, indent=2)
    lines += ["请按上面的规范输出 JSON（`{\"models\": [...]}`）。",
              "每个模型都要能说清「它在替教材做哪一步」（`binds_to` + `why_here`），"
              "说不清的就不要交。",
              "这一节确实无需动手就输出空数组，并提供 decision.status=not_applicable、具体 reason 与逐字正文 evidence.quote；不要凑数。", "",
              "字段格式示例（仍需按本节落点与教学需要决定是否采用）：", example]
    return "\n".join(lines)


__all__ = ["SYSTEM", "user_prompt", "SCHEMA"]
