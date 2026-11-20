"""T06 夹具构造器：**按参数**生成受限场景规格，不是硬编码的验收样本。

要点（对应计划 §5.3/§5.4）：

* 表达式是受限 AST（const / var / add / sub / mul / 整数 pow / 声明参数），
  没有表达式字符串、没有 TeX、没有属性访问。
* 期望值全部由参数算出（`2a`、`−2a`、展开系数），**不是**任何地方固定写 2 / −2。
* `pseudo_cancel_spec` 故意宣称一个**错误的**抵消（a≠b），用来验证 checker 会拒绝。
* 向量加法与矩阵作用复用同一套 objects / steps / actions，只换数学内容。
"""
from __future__ import annotations

from typing import Any


# ── 受限 AST 构造小工具 ────────────────────────────────────────────────────
def C(value: float) -> dict:
    return {"op": "const", "value": value}


def V(name: str) -> dict:
    return {"op": "var", "name": name}


def ADD(left: dict, right: dict) -> dict:
    return {"op": "add", "left": left, "right": right}


def SUB(left: dict, right: dict) -> dict:
    return {"op": "sub", "left": left, "right": right}


def MUL(left: dict, right: dict) -> dict:
    return {"op": "mul", "left": left, "right": right}


def POW(base: dict, exponent: int) -> dict:
    return {"op": "pow", "base": base, "exponent": exponent}


def saddle_expr(a: float, h: float, k: float) -> dict:
    """`a[(x−h)² − (y−k)²]`。"""
    return MUL(C(a), SUB(POW(SUB(V("x"), C(h)), 2), POW(SUB(V("y"), C(k)), 2)))


def saddle_coefficients(a: float, h: float, k: float) -> dict:
    """展开 `a[(x−h)² − (y−k)²]` 的系数（独立于被测 checker 的算法）。"""
    return {
        "x^2": a,
        "x": -2.0 * a * h,
        "y^2": -a,
        "y": 2.0 * a * k,
        "1": a * h * h - a * k * k,
    }


def pseudo_expr(a: float, b: float) -> dict:
    """`a x² − b y²`（a≠b 时 u_xx+u_yy ≠ 0）。"""
    return SUB(MUL(C(a), POW(V("x"), 2)), MUL(C(b), POW(V("y"), 2)))


# ── 场景规格 ──────────────────────────────────────────────────────────────
def _base(unit_id: str, request_id: str, objective: str, parameters: dict,
          objects: list, steps: list, assertions: list,
          render_profile: str = "draft") -> dict:
    return {
        "schema_version": "1.0",
        "request_id": request_id,
        "unit_id": unit_id,
        "source_refs": [f"{unit_id}#p12", f"{unit_id}#p13"],
        "lesson_anchor": "为什么二阶变化抵消不表示曲面是平面",
        "source_claims": ["u(x,y)=x^2-y^2", "u_xx+u_yy=0"],
        "dependency_paths": ["lesson/body.md", "concepts/index.json"],
        "objective": objective,
        "parameters": parameters,
        "objects": objects,
        "steps": steps,
        "math_assertions": assertions,
        "render_profile": render_profile,
    }


def _saddle_objects(a: float, h: float, k: float, expr: dict) -> list:
    return [
        {"object_id": "axes2d", "kind": "axes2d", "x_range": [-2, 2], "y_range": [-2, 2],
         "x_label": "x 方向", "y_label": "y 方向"},
        {"object_id": "axes3d", "kind": "axes3d", "x_range": [-2, 2], "y_range": [-2, 2],
         "z_range": [-4, 4]},
        {"object_id": "u", "kind": "surface", "expr": expr,
         "domain": {"x": [-2, 2], "y": [-2, 2]}, "resolution": [24, 24],
         "label": "曲面 u：高度", "color": "blue"},
        {"object_id": "trace_y", "kind": "section", "surface": "u", "variable": "y",
         "at": C(k), "range": [-2, 2], "samples": 48, "color": "red",
         "label": "固定 y：沿 x 的截线"},
        {"object_id": "trace_x", "kind": "section", "surface": "u", "variable": "x",
         "at": C(h), "range": [-2, 2], "samples": 48, "color": "green",
         "label": "固定 x：沿 y 的截线"},
        {"object_id": "formula_u", "kind": "formula", "expr": expr, "label": "u(x,y)"},
        {"object_id": "note_xy", "kind": "label", "text": "x、y 是两个平面方向，u 是高度",
         "at": [C(-2), C(2)]},
        {"object_id": "origin", "kind": "point", "space": "math2d", "at": [C(0), C(0)]},
    ]


def _saddle_steps(a: float, h: float, k: float) -> list:
    opens_x = "up" if a > 0 else "down"
    opens_y = "down" if a > 0 else "up"
    return [
        {"step_id": "s1", "caption": "x、y 是两个平面方向，u 是高度。",
         "start": 0.0, "end": 8.0,
         "actions": [{"action": "reveal", "object_id": "axes3d"},
                     {"action": "reveal", "object_id": "u"},
                     {"action": "reveal", "object_id": "formula_u"}],
         "checkpoints": [{"at": 4.0, "note": "先看清 x、y、u 三个方向"}],
         "expected_change": "出现三维坐标系与曲面"},
        {"step_id": "s2",
         "caption": f"固定 y：沿 x 的二阶变化 u_xx={{value:uxx}}，截线开口{opens_x}。",
         "start": 8.0, "end": 16.0,
         "actions": [{"action": "reveal", "object_id": "trace_y"},
                     {"action": "highlight", "object_id": "u"},
                     {"action": "camera", "zoom": 1.2}],
         "checkpoints": [{"at": 12.0, "note": "只变 x，u 沿 x 开口向上"}],
         "expected_change": "出现沿 x 的抛物线截线"},
        {"step_id": "s3",
         "caption": f"固定 x：沿 y 的二阶变化 u_yy={{value:uyy}}，截线开口{opens_y}。",
         "start": 16.0, "end": 24.0,
         "actions": [{"action": "reveal", "object_id": "trace_x"},
                     {"action": "highlight", "object_id": "u"}],
         "checkpoints": [{"at": 20.0, "note": "只变 y，u 沿 y 开口向下"}],
         "expected_change": "出现沿 y 的抛物线截线"},
        {"step_id": "s4",
         "caption": "两方向二阶变化相加 u_xx+u_yy={value:lap}=0；和为零不等于曲面是平面。",
         "start": 24.0, "end": 32.0,
         "actions": [{"action": "reveal", "object_id": "note_xy"},
                     {"action": "highlight", "object_id": "formula_u"},
                     {"action": "pause", "seconds": 2.0}],
         "checkpoints": [{"at": 28.0, "note": "可暂停回看"}],
         "expected_change": "两条截线与求和结论同屏"},
    ]


def _saddle_assertions(a: float, h: float, k: float) -> list:
    return [
        {"assertion_id": "uxx", "kind": "partial_derivative", "object_id": "u",
         "variable": "x", "order": 2, "at": {"x": 0.25, "y": -0.25}, "expected": 2.0 * a},
        {"assertion_id": "uyy", "kind": "partial_derivative", "object_id": "u",
         "variable": "y", "order": 2, "at": {"x": 0.25, "y": -0.25}, "expected": -2.0 * a},
        {"assertion_id": "lap", "kind": "derivative_sum", "object_id": "u",
         "terms": [{"variable": "x", "order": 2}, {"variable": "y", "order": 2}],
         "at": {"x": 0.5, "y": 0.5}, "expected": 0.0},
        {"assertion_id": "notflat", "kind": "surface_not_planar", "object_id": "u"},
        {"assertion_id": "poly", "kind": "polynomial", "object_id": "u",
         "expected_coefficients": saddle_coefficients(a, h, k)},
        {"assertion_id": "tr_y", "kind": "section_is_trace", "section": "trace_y",
         "surface": "u", "variable": "y", "opens": "up" if a > 0 else "down"},
        {"assertion_id": "tr_x", "kind": "section_is_trace", "section": "trace_x",
         "surface": "u", "variable": "x", "opens": "down" if a > 0 else "up"},
    ]


def saddle_spec(a: float = 1.0, h: float = 0.0, k: float = 0.0, *,
                unit_id: str = "CH04", request_id: str = "vis-saddle",
                render_profile: str = "draft") -> dict:
    """正确的鞍面规格：期望值全部由 a/h/k 算出。"""
    expr = saddle_expr(a, h, k)
    return _base(unit_id, request_id,
                 "说明 x/y 是两个方向、u 是高度；区分二阶变化抵消与曲面是平面",
                 {"a": a, "h": h, "k": k},
                 _saddle_objects(a, h, k, expr), _saddle_steps(a, h, k),
                 _saddle_assertions(a, h, k), render_profile)


def pseudo_cancel_spec(a: float = 2.0, b: float = 3.0) -> dict:
    """`a x² − b y²`（a≠b）却**宣称** u_xx=2、u_yy=−2、和为 0 —— 必须被拒绝。

    真实值：u_xx=2a=4、u_yy=−2b=−6、和=−2。
    """
    expr = pseudo_expr(a, b)
    objects = _saddle_objects(a, 0.0, 0.0, expr)
    assertions = [
        {"assertion_id": "uxx", "kind": "partial_derivative", "object_id": "u",
         "variable": "x", "order": 2, "expected": 2.0},
        {"assertion_id": "uyy", "kind": "partial_derivative", "object_id": "u",
         "variable": "y", "order": 2, "expected": -2.0},
        {"assertion_id": "lap", "kind": "derivative_sum", "object_id": "u",
         "terms": [{"variable": "x", "order": 2}, {"variable": "y", "order": 2}],
         "expected": 0.0},
        {"assertion_id": "poly", "kind": "polynomial", "object_id": "u",
         "expected_coefficients": {"x^2": a, "y^2": -b}},
    ]
    return _base("CH04", "vis-pseudo-cancel",
                 "反例：a≠b 时不得宣称二阶变化抵消", {"a": a, "b": b},
                 objects, _saddle_steps(a, 0.0, 0.0), assertions)


def vector_sum_spec(v1: tuple = (1.0, 2.0), v2: tuple = (2.0, 3.0), *,
                    unit_id: str = "CH04", request_id: str = "vis-vector-sum") -> dict:
    ex = v1[0] + v2[0]
    ey = v1[1] + v2[1]
    objects = [
        {"object_id": "axes2d", "kind": "axes2d", "x_range": [-4, 6], "y_range": [-2, 8],
         "x_label": "x", "y_label": "y"},
        {"object_id": "v1", "kind": "vector", "space": "math2d",
         "start": [C(0), C(0)], "end": [C(v1[0]), C(v1[1])], "color": "blue"},
        {"object_id": "v2", "kind": "vector", "space": "math2d",
         "start": [C(v1[0]), C(v1[1])], "end": [C(ex), C(ey)], "color": "green"},
        {"object_id": "vsum", "kind": "vector", "space": "math2d",
         "start": [C(0), C(0)], "end": [C(ex), C(ey)], "color": "red"},
        {"object_id": "tip", "kind": "point", "space": "math2d", "at": [C(ex), C(ey)]},
    ]
    steps = [
        {"step_id": "s1", "caption": "把 v1 与 v2 首尾相接。", "start": 0.0, "end": 6.0,
         "actions": [{"action": "reveal", "object_id": "axes2d"},
                     {"action": "reveal", "object_id": "v1"},
                     {"action": "reveal", "object_id": "v2"}],
         "checkpoints": [{"at": 3.0, "note": "看两个分向量"}],
         "expected_change": "出现两条分向量"},
        {"step_id": "s2", "caption": "合向量的终点是 {value:sum}。", "start": 6.0, "end": 12.0,
         "actions": [{"action": "reveal", "object_id": "vsum"},
                     {"action": "reveal", "object_id": "tip"},
                     {"action": "pause", "seconds": 1.5}],
         "checkpoints": [{"at": 9.0, "note": "读出终点坐标"}],
         "expected_change": "出现合向量与终点"},
    ]
    assertions = [
        {"assertion_id": "sum", "kind": "vector_sum", "operands": ["v1", "v2"],
         "result_object": "vsum", "expected": [ex, ey], "tolerance": 1e-6},
    ]
    return _base(unit_id, request_id, "用同一套对象/动作表达二维向量加法",
                 {}, objects, steps, assertions)


def matrix_apply_spec(k: float = 2.0, *, unit_id: str = "CH04",
                      request_id: str = "vis-matrix-apply") -> dict:
    """2×2 矩阵 `[[1,k],[3,4]]` 作用在向量 `(1,2)` 上；k 是声明参数。"""
    vector = (1.0, 2.0)
    ex = 1.0 * vector[0] + k * vector[1]
    ey = 3.0 * vector[0] + 4.0 * vector[1]
    objects = [
        {"object_id": "axes2d", "kind": "axes2d", "x_range": [-4, 8], "y_range": [-4, 14],
         "x_label": "x", "y_label": "y"},
        {"object_id": "m", "kind": "matrix2d",
         "rows": [[C(1.0), V("k")], [C(3.0), C(4.0)]], "label": "2x2 矩阵 M"},
        {"object_id": "v", "kind": "vector", "space": "math2d",
         "start": [C(0), C(0)], "end": [C(vector[0]), C(vector[1])], "color": "blue"},
        {"object_id": "mv", "kind": "vector", "space": "math2d",
         "start": [C(0), C(0)], "end": [C(ex), C(ey)], "color": "red"},
        {"object_id": "mv_tip", "kind": "point", "space": "math2d", "at": [C(ex), C(ey)]},
    ]
    steps = [
        {"step_id": "s1", "caption": "先看被作用的向量 v。", "start": 0.0, "end": 5.0,
         "actions": [{"action": "reveal", "object_id": "axes2d"},
                     {"action": "reveal", "object_id": "m"},
                     {"action": "reveal", "object_id": "v"}],
         "checkpoints": [{"at": 2.5, "note": "读出 v 的两个分量"}],
         "expected_change": "出现矩阵与向量"},
        {"step_id": "s2", "caption": "矩阵作用的终点是 {value:apply}。", "start": 5.0, "end": 11.0,
         "actions": [{"action": "reveal", "object_id": "mv"},
                     {"action": "reveal", "object_id": "mv_tip"},
                     {"action": "camera", "zoom": 1.4, "phi": 15.0}],
         "checkpoints": [{"at": 8.0, "note": "核对终点坐标"}],
         "expected_change": "出现矩阵作用后的终点"},
    ]
    assertions = [
        {"assertion_id": "apply", "kind": "matrix_apply", "matrix": "m", "vector": "v",
         "result_object": "mv", "expected": [ex, ey], "tolerance": 1e-6},
    ]
    return _base(unit_id, request_id, "用同一套对象/动作表达 2x2 矩阵作用",
                 {"k": k}, objects, steps, assertions)


def teaching_plan_spec() -> dict:
    return {
        "unit_id": "CH04",
        "source_hash": "sha256:" + "0" * 64,
        "objective": "理解二阶变化抵消不表示曲面是平面",
        "prerequisites": ["偏导数定义", "二阶偏导记号"],
        "claims": [
            {"claim_id": "c1", "text": "u(x,y)=x^2-y^2 的 u_xx=2、u_yy=-2"},
            {"claim_id": "c2", "text": "u_xx+u_yy=0"},
        ],
        "steps": [
            {"step_id": "t1", "question": "x、y 和 u 各是什么？",
             "explanation_anchor": "body#p12", "claim_ids": ["c1"],
             "observe": "两个平面方向加一个高度", "pause_hint": "先停一下看清坐标"},
            {"step_id": "t2", "question": "两个方向的二阶变化为什么会抵消？",
             "explanation_anchor": "body#p13", "claim_ids": ["c1", "c2"],
             "observe": "一条开口向上、一条开口向下", "pause_hint": "逐条对照"},
        ],
        "visual_requests": [
            {"learning_goal": "看到鞍面不是平面", "misconception": "和为零就等于平",
             "observe": "两条截线方向相反", "claim_ids": ["c1"], "anchor": "body#p12"},
        ],
    }


__all__ = ["C", "V", "ADD", "SUB", "MUL", "POW", "saddle_expr", "saddle_coefficients",
           "pseudo_expr", "saddle_spec", "pseudo_cancel_spec", "vector_sum_spec",
           "matrix_apply_spec", "teaching_plan_spec"]


def dump_specs() -> dict[str, Any]:
    """夹具 JSON 的名字 → 规格（build_fixtures.py 与测试都从这里取）。"""
    return {
        "saddle-a1-h0-k0": saddle_spec(1.0, 0.0, 0.0),
        "saddle-a0.5-h0.5-k-0.5": saddle_spec(0.5, 0.5, -0.5),
        "saddle-a2-h0.5-k-0.5": saddle_spec(2.0, 0.5, -0.5),
        "pseudo-cancel-a2-b3": pseudo_cancel_spec(2.0, 3.0),
        "vector-sum": vector_sum_spec(),
        "matrix-apply": matrix_apply_spec(),
    }
