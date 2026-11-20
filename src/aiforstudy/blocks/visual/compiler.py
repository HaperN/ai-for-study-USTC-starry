"""批准数学上下文与 VisualSpecV2 → 冻结采样、MathTex 和可信字面量源码。

## 职责边界

* V2 必须提供 ApprovedMathContext，核对目标、主张、对象、模型摘要与共享数学验证；
* V2 数学量与公式由共享 AST 求值和格式化，曲线沿共享定义域分段；
* V1 函数仅为历史规格与测试保留，生产入口不接受 V1；
* 输出源码是**数据驱动**的 Python 字面量，模型文本只作为 JSON 数据出现，
  不参与求值、不拼进代码标识符；
* **不做任何模型调用**：没有可注入的 client／transport／gateway 参数，
  本模块也不 import 任何供应商或网络模块（见 `test_visual_compiler.py`）。

输出路径由宿主分配：本任务不写视频、不落盘、不调用 Manim。
"""
from __future__ import annotations

import pprint
from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping

from ...core.digest import digest_text
from ...errors import ContractError
from .spec import (MATH_TOLERANCE, PROFILES, VisualSpecV1, VisualSpecV2, eval_const_ast,
                   parse_visual_spec, validate_spec_structure, target_expression as _target_expr)

#: 编译器版本进 `CompiledScene`，让"同一份规格换编译器"这件事可追溯
COMPILER_VERSION = "visual-compiler-1.0"

#: 五点差分步长（可信层的数值路线；独立检查有它自己的路线）
FD_STEP = 1e-3


@dataclass(frozen=True)
class RenderProfile:
    """只有 draft / final 两档（计划 §5.3）。"""

    name: str
    DRAFT: ClassVar["RenderProfile"]
    FINAL: ClassVar["RenderProfile"]

    def __post_init__(self) -> None:
        if self.name not in PROFILES:
            raise ContractError(f"profile.name: 只能是 {list(PROFILES)}，实际 {self.name!r}")

    def __str__(self) -> str:  # pragma: no cover - 展示用
        return self.name


RenderProfile.DRAFT = RenderProfile("draft")
RenderProfile.FINAL = RenderProfile("final")


@dataclass(frozen=True)
class CompiledScene:
    """可信编译产物。保存 spec_hash、compiler_version、可信源码及其 hash。"""

    spec_hash: str
    compiler_version: str
    source: str
    source_hash: str
    profile: str
    samples: Mapping[str, Any] = field(default_factory=dict)
    captions: tuple[str, ...] = ()
    formulas: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"spec_hash": self.spec_hash, "compiler_version": self.compiler_version,
                "source_hash": self.source_hash, "profile": self.profile,
                "captions": list(self.captions), "formulas": list(self.formulas),
                "sample_objects": sorted(self.samples.get("objects", {})),
                "parameters": dict(self.samples.get("parameters", {}))}


# ── 数值求值（可信层自己的路线）────────────────────────────────────────────
def eval_ast(node: Mapping[str, Any], env: Mapping[str, float],
             parameters: Mapping[str, float]) -> float:
    """对规范 AST 求值。`env` 给 x／y／t 的当前值，其余名字当声明参数。"""
    op = node["op"]
    if op == "const":
        return float(node["value"])
    if op == "var":
        name = node["name"]
        if name in env:
            return float(env[name])
        try:
            return float(parameters[name])
        except KeyError:  # pragma: no cover - 校验期已保证
            raise ContractError(f"参数 {name!r} 未声明") from None
    if op == "pow":
        return float(eval_ast(node["base"], env, parameters) ** node["exponent"])
    left = eval_ast(node["left"], env, parameters)
    right = eval_ast(node["right"], env, parameters)
    if op == "add":
        return left + right
    if op == "sub":
        return left - right
    if op == "mul":
        return left * right
    raise ContractError(f"未知算子 {op!r}")  # pragma: no cover


def eval_coords(items: Any, parameters: Mapping[str, float]) -> list[float]:
    return [eval_ast(item, {}, parameters) for item in items]


def fd_derivative(node: Mapping[str, Any], parameters: Mapping[str, float], variable: str,
                  order: int, point: Mapping[str, float], step: float = FD_STEP) -> float:
    """五点差分求偏导（可信层用于把数值写进字幕／源码；不是独立检查的路线）。"""

    def value(at: float) -> float:
        env = dict(point)
        env[variable] = at
        return eval_ast(node, env, parameters)

    center = float(point[variable])
    if order == 1:
        return (value(center - 2 * step) - 8 * value(center - step)
                + 8 * value(center + step) - value(center + 2 * step)) / (12 * step)
    if order == 2:
        return (-value(center + 2 * step) + 16 * value(center + step) - 30 * value(center)
                + 16 * value(center - step) - value(center - 2 * step)) / (12 * step * step)
    raise ContractError(f"order: 首期只支持 1–2 阶，实际 {order}")


# ── 公式排版（内置符号模板，不接受任意 TeX）────────────────────────────────
def fmt_number(value: float) -> str:
    number = float(value)
    if number == 0.0:
        return "0"
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    return f"{number:.10g}"


def _precedence(node: Mapping[str, Any]) -> int:
    op = node["op"]
    if op in ("add", "sub"):
        return 1
    if op == "mul":
        return 2
    if op == "pow":
        return 3
    if op == "const" and float(node["value"]) < 0:
        return 1
    return 4


def print_formula(node: Mapping[str, Any], parameters: Mapping[str, float]) -> str:
    """把受限 AST 排成可读公式（信任的模板，不是模型给的 TeX）。"""
    op = node["op"]
    if op == "const":
        return fmt_number(node["value"])
    if op == "var":
        name = node["name"]
        if name in parameters:
            return fmt_number(float(parameters[name]))
        return str(name)
    if op == "pow":
        base = print_formula(node["base"], parameters)
        if _precedence(node["base"]) < 4:
            base = f"({base})"
        exponent = int(node["exponent"])
        if exponent == 0:
            return "1"
        if exponent == 1:
            return base
        return f"{base}^{exponent}"
    left = print_formula(node["left"], parameters)
    right = print_formula(node["right"], parameters)
    if op == "add":
        if _precedence(node["right"]) < 2:
            right = f"({right})"
        return f"{left} + {right}"
    if op == "sub":
        if _precedence(node["right"]) < 2:
            right = f"({right})"
        return f"{left} - {right}"
    if _precedence(node["left"]) < 2:
        left = f"({left})"
    if _precedence(node["right"]) < 3:
        right = f"({right})"
    return f"{left}*{right}"


# ── 采样 ───────────────────────────────────────────────────────────────────
def _linspace(low: float, high: float, count: int) -> list[float]:
    if count == 1:
        return [float(low)]
    step = (float(high) - float(low)) / (count - 1)
    return [0.0 if abs(float(low) + i * step) < 1e-15 else float(low) + i * step
            for i in range(count)]


def _assertion_point(assertion: Mapping[str, Any], spec: VisualSpecV1,
                     object_id: str) -> dict:
    if "at" in assertion:
        return {"x": float(assertion["at"]["x"]), "y": float(assertion["at"]["y"])}
    obj = spec.object_by_id(object_id)
    domain = obj.get("domain")
    if domain:
        return {"x": (float(domain["x"][0]) + float(domain["x"][1])) / 2.0,
                "y": (float(domain["y"][0]) + float(domain["y"][1])) / 2.0}
    return {"x": 0.0, "y": 0.0}


def _derive_values(spec: VisualSpecV1) -> dict[str, Any]:
    """每个数学断言在这份规格下的**真实值**（字幕占位符就从这里取）。

    ⚠️ 故意不看断言自己的 `expected`：字幕必须跟着数学对象走，
    否则一个错误的 `expected` 会把错数字直接印到画面上。
    """
    values: dict[str, Any] = {}
    for assertion in spec.math_assertions:
        kind = assertion["kind"]
        if kind in ("partial_derivative", "derivative_sum"):
            object_id = assertion["object_id"]
            expr = _target_expr(spec, object_id)
            point = _assertion_point(assertion, spec, object_id)
            if kind == "partial_derivative":
                values[assertion["assertion_id"]] = fd_derivative(
                    expr, spec.parameters, assertion["variable"], assertion["order"], point)
            else:
                total = 0.0
                for term in assertion["terms"]:
                    total += fd_derivative(expr, spec.parameters, term["variable"],
                                           term["order"], point)
                values[assertion["assertion_id"]] = total
        elif kind == "polynomial":
            values[assertion["assertion_id"]] = _poly_coefficients(
                _target_expr(spec, assertion["object_id"]), spec.parameters)
        elif kind == "surface_not_planar":
            expr = _target_expr(spec, assertion["object_id"])
            values[assertion["assertion_id"]] = _max_curvature(expr, spec.parameters)
        elif kind == "section_is_trace":
            values[assertion["assertion_id"]] = _section_opening(spec, assertion)
        elif kind == "vector_sum":
            total = [0.0, 0.0]
            for operand in assertion["operands"]:
                value = _vector_value(spec.object_by_id(operand), spec.parameters)
                total = [total[0] + value[0], total[1] + value[1]]
            values[assertion["assertion_id"]] = total
        elif kind == "matrix_apply":
            matrix = spec.object_by_id(assertion["matrix"])["rows"]
            evaluated = [[eval_ast(entry, {}, spec.parameters) for entry in row]
                         for row in matrix]
            vector = _object_endpoint(spec.object_by_id(assertion["vector"]),
                                      spec.parameters)
            values[assertion["assertion_id"]] = _matvec(evaluated, vector)
    return values


def _poly_coefficients(node: Mapping[str, Any], parameters: Mapping[str, float]) -> dict:
    """展开成单项式系数（可信层用于字幕／样例；独立检查有自己的实现）。"""

    def poly(current: Mapping[str, Any]) -> dict:
        op = current["op"]
        if op == "const":
            return {(): float(current["value"])}
        if op == "var":
            name = current["name"]
            if name in parameters:
                return {(): float(parameters[name])}
            return {((name, 1),): 1.0}
        if op == "pow":
            base = poly(current["base"])
            result = {(): 1.0}
            for _ in range(int(current["exponent"])):
                result = _poly_mul(result, base)
            return result
        left, right = poly(current["left"]), poly(current["right"])
        if op == "add":
            return _poly_add(left, right)
        if op == "sub":
            return _poly_add(left, {key: -value for key, value in right.items()})
        return _poly_mul(left, right)

    expanded = poly(node)
    out: dict[str, float] = {}
    for key, value in expanded.items():
        if abs(value) < 1e-15:
            continue
        out[_monomial_text(key)] = value
    return dict(sorted(out.items()))


def _poly_mul(left: Mapping[tuple, float], right: Mapping[tuple, float]) -> dict:
    result: dict[tuple, float] = {}
    for lkey, lvalue in left.items():
        for rkey, rvalue in right.items():
            merged = dict(lkey)
            for name, exponent in rkey:
                merged[name] = merged.get(name, 0) + exponent
            key = tuple(sorted((name, power) for name, power in merged.items() if power))
            result[key] = result.get(key, 0.0) + lvalue * rvalue
    return result


def _poly_add(left: Mapping[tuple, float], right: Mapping[tuple, float]) -> dict:
    result = dict(left)
    for key, value in right.items():
        result[key] = result.get(key, 0.0) + value
    return result


def _monomial_text(key: tuple) -> str:
    if not key:
        return "1"
    return "*".join(f"{name}^{power}" if power > 1 else name for name, power in key)


def _max_curvature(node: Mapping[str, Any], parameters: Mapping[str, float]) -> float:
    coefficients = _poly_coefficients(node, parameters)
    second = 0.0
    for monomial, value in coefficients.items():
        powers = _monomial_powers(monomial)
        if powers.get("x", 0) + powers.get("y", 0) >= 2:
            second = max(second, abs(value))
    return second


def _monomial_powers(monomial: str) -> dict:
    if monomial == "1":
        return {}
    out = {}
    for factor in monomial.split("*"):
        if "^" in factor:
            name, power = factor.split("^")
            out[name] = int(power)
        else:
            out[factor] = 1
    return out


def _section_opening(spec: VisualSpecV1, assertion: Mapping[str, Any]) -> str:
    section = spec.object_by_id(assertion["section"])
    expr = _target_expr(spec, assertion["surface"])
    free = "y" if section["variable"] == "x" else "x"
    fixed = eval_const_ast(section["at"], spec.parameters)
    low, high = section["range"]
    point = {section["variable"]: fixed, free: (float(low) + float(high)) / 2.0}
    second = fd_derivative(expr, spec.parameters, free, 2, point)
    if abs(second) <= MATH_TOLERANCE:
        return "flat"
    return "up" if second > 0 else "down"


def _object_endpoint(obj: Mapping[str, Any], parameters: Mapping[str, float]) -> list[float]:
    if obj["kind"] == "point":
        return eval_coords(obj["at"], parameters)
    if obj["kind"] in ("vector", "arrow"):
        return eval_coords(obj["end"], parameters)
    raise ContractError(f"对象 {obj.get('object_id')!r} 没有终点")


def _vector_value(obj: Mapping[str, Any], parameters: Mapping[str, float]) -> list[float]:
    """向量的数学值 = 终点 − 起点（点对象当位置向量）。"""
    if obj["kind"] == "point":
        return _object_endpoint(obj, parameters)
    end = _object_endpoint(obj, parameters)
    start = eval_coords(obj["start"], parameters) if "start" in obj else [0.0, 0.0]
    return [end[i] - start[i] for i in range(len(end))]


def _matvec(rows, vector) -> list[float]:
    return [sum(float(entry) * float(value) for entry, value in zip(row, vector))
            for row in rows]


# ── 对象编译 ───────────────────────────────────────────────────────────────
def _sample_surface(obj: Mapping[str, Any], parameters: Mapping[str, float]) -> dict:
    x_low, x_high = obj["domain"]["x"]
    y_low, y_high = obj["domain"]["y"]
    nx, ny = obj["resolution"]
    xs = _linspace(x_low, x_high, nx)
    ys = _linspace(y_low, y_high, ny)
    grid = [[eval_ast(obj["expr"], {"x": x, "y": y}, parameters) for x in xs]
            for y in ys]
    return {"kind": obj["kind"], "x_values": xs, "y_values": ys, "u": grid,
            "resolution": [nx, ny]}


def _sample_section(obj: Mapping[str, Any], parameters: Mapping[str, float],
                    surface: Mapping[str, Any]) -> dict:
    fixed_variable = obj["variable"]
    free_variable = "y" if fixed_variable == "x" else "x"
    fixed_value = eval_const_ast(obj["at"], parameters)
    low, high = obj["range"]
    count = obj["samples"]
    free_values = _linspace(low, high, count)
    points2d, points3d, heights = [], [], []
    for free in free_values:
        env = {fixed_variable: fixed_value, free_variable: free}
        height = eval_ast(surface["expr"], env, parameters)
        heights.append(height)
        if free_variable == "x":
            points3d.append([free, fixed_value, height])
            points2d.append([free, height])
        else:
            points3d.append([fixed_value, free, height])
            points2d.append([free, height])
    return {"kind": "section", "fixed_variable": fixed_variable,
            "fixed_value": fixed_value, "free_variable": free_variable,
            "free_values": free_values, "u": heights, "points2d": points2d,
            "points3d": points3d, "range": [low, high], "samples": count}


def _sample_curve(obj: Mapping[str, Any], parameters: Mapping[str, float]) -> dict:
    count = obj["samples"]
    if obj["form"] == "graph2d":
        low, high = obj["x_range"]
        xs = _linspace(low, high, count)
        points = [[x, eval_ast(obj["expr"], {"x": x}, parameters)] for x in xs]
        return {"kind": "curve", "form": "graph2d", "points2d": points, "samples": count}
    low, high = obj["t_range"]
    ts = _linspace(low, high, count)
    points = []
    for t in ts:
        point = [eval_ast(obj["expr"][name], {"t": t}, parameters)
                 for name in sorted(obj["expr"])]
        points.append(point)
    return {"kind": "curve", "form": "parametric", "points": points, "samples": count}


def _compile_object(obj: Mapping[str, Any], spec: VisualSpecV1,
                    values: Mapping[str, Any], by_id: Mapping[str, dict]) -> dict:
    parameters = spec.parameters
    kind = obj["kind"]
    if kind == "axes2d":
        return {"kind": kind, "x_range": list(obj["x_range"]), "y_range": list(obj["y_range"]),
                "x_label": obj.get("x_label", "x"), "y_label": obj.get("y_label", "y")}
    if kind == "axes3d":
        return {"kind": kind, "x_range": list(obj["x_range"]), "y_range": list(obj["y_range"]),
                "z_range": list(obj["z_range"])}
    if kind == "point":
        return {"kind": kind, "space": obj["space"], "at": eval_coords(obj["at"], parameters)}
    if kind in ("vector", "arrow"):
        start = eval_coords(obj["start"], parameters) if "start" in obj else \
            [0.0] * len(obj["end"])
        return {"kind": kind, "space": obj["space"], "start": start,
                "end": eval_coords(obj["end"], parameters)}
    if kind == "matrix2d":
        return {"kind": kind,
                "rows": [[eval_ast(entry, {}, parameters) for entry in row]
                         for row in obj["rows"]]}
    if kind in ("surface", "plane"):
        return _sample_surface(obj, parameters)
    if kind == "curve":
        return _sample_curve(obj, parameters)
    if kind == "section":
        return _sample_section(obj, parameters, by_id[obj["surface"]])
    if kind == "label":
        return {"kind": kind, "text": _resolve_placeholders(obj["text"], values,
                                                            "objects.label.text"),
                "at": eval_coords(obj["at"], parameters), "space": obj["space"]}
    if kind == "formula":
        return {"kind": kind, "text": print_formula(obj["expr"], parameters)}
    raise ContractError(f"objects: 无法编译的对象类型 {kind!r}")  # pragma: no cover


# ── 字幕 / 源码 ────────────────────────────────────────────────────────────
def _format_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "(" + ", ".join(fmt_number(item) for item in value) + ")"
    if isinstance(value, bool):  # pragma: no cover - 目前没有布尔断言值
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return fmt_number(value)
    return str(value)


def _resolve_placeholders(text: str, values: Mapping[str, Any], path: str) -> str:
    import re

    def replace(match) -> str:
        key = match.group(1)
        if key not in values:
            raise ContractError(f"{path}: 占位符引用了不存在的数学断言 {key!r}")
        return _format_value(values[key])

    return re.sub(r"\{value:([A-Za-z][A-Za-z0-9_]{0,31})\}", replace, text)


def _render_source(profile: RenderProfile, payload: Mapping[str, Any]) -> str:
    body = pprint.pformat(dict(payload), width=100, sort_dicts=True)
    return (
        '"""T06 可信编译产物（由 aiforstudy.blocks.visual.compiler 生成）。\n\n'
        "所有数学量都来自受限 AST 的数值采样；所有文字只作为数据出现，\n"
        "不参与求值、也不拼进代码标识符。本文件不渲染视频，输出路径由宿主分配。\n"
        '"""\n'
        "from __future__ import annotations\n\n"
        f"COMPILER_VERSION = {COMPILER_VERSION!r}\n"
        f"PROFILE = {profile.name!r}\n"
        f"SCENE = {body}\n\n\n"
        "def construct(scene):\n"
        '    """由 T07 的 manim_adapter 把 SCENE 变成 Manim 对象。"""\n'
        "    raise NotImplementedError('T07 manim_adapter 负责渲染')\n"
    )


def _coerce_profile(profile: Any) -> RenderProfile:
    if isinstance(profile, RenderProfile):
        return profile
    if isinstance(profile, str):
        return RenderProfile(profile)
    raise ContractError(f"profile: 必须是 RenderProfile，实际 {type(profile).__name__}")


def compile_scene(spec: VisualSpecV1 | VisualSpecV2 | dict, profile: RenderProfile | str,
                  *, math_context=None, cancel=None) -> CompiledScene:
    """把受限规格编译成可信源码与采样数据。**不调用任何模型、不写文件。**"""
    if isinstance(spec, dict):
        spec = parse_visual_spec(spec)
    spec = validate_spec_structure(spec)
    profile = _coerce_profile(profile)
    if isinstance(spec, VisualSpecV2):
        return _compile_v2(spec, profile, math_context, cancel=cancel)

    values = _derive_values(spec)
    by_id = {obj["object_id"]: obj for obj in spec.objects}
    samples = {obj["object_id"]: _compile_object(obj, spec, values, by_id)
               for obj in spec.objects}
    captions = tuple(_resolve_placeholders(step["caption"], values,
                                           f"steps[{i}].caption")
                     for i, step in enumerate(spec.steps))
    formulas = tuple(samples[obj["object_id"]]["text"]
                     for obj in spec.objects if obj["kind"] == "formula")

    payload = {
        "format": "visual-scene-1.0",
        "compiler_version": COMPILER_VERSION,
        "profile": profile.name,
        "parameters": dict(spec.parameters),
        "objects": samples,
        "captions": list(captions),
        "formulas": list(formulas),
        "steps": [{"step_id": step["step_id"], "start": step["start"], "end": step["end"],
                   "actions": step["actions"], "checkpoints": step["checkpoints"],
                   "caption": captions[i], "expected_change": step["expected_change"]}
                  for i, step in enumerate(spec.steps)],
        "math_values": {key: (dict(value) if isinstance(value, dict) else value)
                        for key, value in values.items()},
    }
    source = _render_source(profile, payload)
    return CompiledScene(spec_hash=spec.spec_hash, compiler_version=COMPILER_VERSION,
                         source=source, source_hash=digest_text(source),
                         profile=profile.name,
                         samples={"objects": samples, "parameters": dict(spec.parameters)},
                         captions=captions, formulas=formulas)


__all__ = ["COMPILER_VERSION", "FD_STEP", "RenderProfile", "CompiledScene", "compile_scene",
           "eval_ast", "eval_coords", "fd_derivative", "print_formula", "fmt_number"]


def _approved_v2_bindings(spec, context, *, cancel=None):
    from ..media.math_context import ApprovedMathContext
    from ..math import verify_math
    if not isinstance(context, ApprovedMathContext):
        raise ContractError('VisualSpecV2 编译必须提供 ApprovedMathContext')
    request=context.request
    if spec.unit_id!=context.unit_id:
        raise ContractError('VisualSpecV2 unit_id 与批准内容不匹配')
    if request['kind']!='animation' or request['request_id']!=spec.request_id:
        raise ContractError('VisualSpecV2 request_id/kind 与批准请求不匹配')
    if set(spec.goal_ids)!=set(request['goal_refs']) or set(spec.claim_ids)!=set(request['claim_ids']):
        raise ContractError('VisualSpecV2 goal_ids/claim_ids 与批准请求不匹配')
    if sorted(spec.math_model_refs,key=lambda r:r['model_id'])!=sorted(request['math_model_refs'],key=lambda r:r['model_id']):
        raise ContractError('VisualSpecV2 model digest 摘要与批准请求不匹配')
    models={r['model_id']:context.model_by_id(r['model_id']) for r in spec.math_model_refs}
    bound={}
    for obj in spec.objects:
        if 'model_id' in obj:
            ref={'model_id':obj['model_id'],'object_id':obj['math_object_id']}
            context.object_by_ref(ref)
            bound[obj['object_id']]=(obj['model_id'],obj['math_object_id'])
    selected={mid:set() for mid in models};demonstrated=set()
    for step in spec.steps:
        if not set(step['claim_ids'])<=set(context.goal_claim_ids(step['goal_id'])):
            raise ContractError('step goal_id 与 claim_ids 无批准教学关系')
        mids=set().union(*(set(context.claim_model_ids(cid)) for cid in step['claim_ids']))
        shown={bound[oid] for oid in step['object_ids'] if oid in bound}
        relevant=set()
        for ref in step['math_claim_refs']:
            mid=ref['model_id'];cid=ref['claim_id']
            if mid not in mids: raise ContractError('step 数学模型与内容主张无关 unrelated')
            relations = ((models[mid].relation_by_id(ref['relation_id']),) if 'relation_id' in ref
                         else models[mid].relations_for_claim(cid))
            for claim in relations:
                if claim['claim_id'] != cid:
                    raise ContractError('step relation_id 与教学 claim_id 不匹配')
                rid = claim['relation_id']
                required={claim[k] for k in ('math_object_id','lhs_object_id','rhs_object_id','expected_object_id','inverse_object_id') if k in claim}
                objects_required={(mid,oid) for oid in required}
                inherited=step['kind']=='observe' and (mid,rid) in demonstrated and bool(objects_required & shown)
                if not required or (not objects_required<=shown and not inherited):
                    raise ContractError('step 必须呈现数学 relation 的完整相关对象；无关 unrelated 断言不能背书')
                relevant|=objects_required;selected[mid].add(rid)
                if objects_required<=shown: demonstrated.add((mid,rid))
        if not shown or shown-relevant:
            raise ContractError('step 可见数学对象与所选断言无关 unrelated')
        if step['kind']=='change':
            changed={bound[oid] for a in step['actions'] for oid in (a.get('object_id'),a.get('target_object_id')) if oid in bound}
            if not changed or not changed<=relevant: raise ContractError('change 动作必须呈现本步目标的相关数学对象变化')
    reports=[]
    for mid,ids in selected.items():
        if not ids: raise ContractError('批准数学模型没有教学步骤使用')
        cancelled=(lambda: bool(cancel.is_set() if hasattr(cancel,"is_set") else cancel() if callable(cancel) else cancel.cancelled)) if cancel is not None else None
        report=verify_math(models[mid],[{'relation_id':rid} for rid in sorted(ids)],cancel=cancelled)
        if cancelled is not None and cancelled():
            if hasattr(cancel,"checkpoint"): cancel.checkpoint()
            from ...errors import Cancelled
            raise Cancelled("动画数学编译已取消")
        if report.status!='pass':
            detail='; '.join(f'{c.claim_id}:{c.status}/{c.reason_code}: {c.reason}' for c in report.checks)
            raise ContractError(f'数学检查 {report.status}，不能编译：{detail}')
        reports.append(report.as_dict())
    return {'context_digest':context.digest,'content_digest':context.content_digest,
            'approval_digest':context.approval_digest,'request':request,
            'models':[{'digest':m.digest,'model':m.as_dict()} for m in models.values()],
            'reports':reports},models


def _v2_sample(obj, model, context):
    """All mathematics uses the shared AST; the worker receives frozen numbers/TeX."""
    from ..math import evaluate_expression,format_expression
    from ..math.expression import ast_names
    import math
    kind=obj['kind'];source=model.object_by_id(obj['math_object_id'])
    def scalar(expr, values):
        value=evaluate_expression(expr,model.symbols,values)
        if isinstance(value,complex) or not math.isfinite(float(value)):
            raise ContractError('动画需要有限实值，不能忽略复数或域错误')
        return float(value)
    if kind=='formula':
        if source['kind']!='scalar': raise ContractError('formula 当前要求 scalar 数学对象；向量/矩阵使用相应对象')
        return {'kind':'formula','tex':format_expression(source['expr'],backend='mathtex',symbols=model.symbols),'rendering':'trusted_mathtex'}
    if kind=='vector':
        if source['kind']!='vector' or len(source['entries'])!=(2 if obj['space']=='math2d' else 3):
            raise ContractError('vector 空间维数与批准数学对象不一致')
        end=[scalar(e,{}) for e in source['entries']]
        return {'kind':'vector','space':obj['space'],'start':[0.0]*len(end),'end':end}
    if kind=='matrix2d':
        if source['kind']!='matrix' or len(source['rows'])!=2 or any(len(r)!=2 for r in source['rows']):
            raise ContractError('matrix2d 要求批准的 2x2 矩阵')
        return {'kind':kind,'rows':[[scalar(e,{}) for e in r] for r in source['rows']]}
    if source['kind']!='scalar': raise ContractError('曲线/曲面要求 scalar 数学对象')
    if kind=='curve':
        from ..media.plot import sample_series
        sampled=sample_series(context,{'model_id':obj['model_id'],'object_id':obj['math_object_id']},'x',obj['x_range'],samples=obj['samples'])
        return {'kind':'curve','form':'graph2d','segments':sampled['segments'],
                'samples':sum(len(p) for p in sampled['segments']),
                'domain_evidence':{k:v for k,v in sampled.items() if k!='segments'}}
    expr=source['expr']
    def continuous(node):
        op=node['op']
        if op in ('div','log','sqrt') or (op=='pow' and node['exponent']<0):
            raise ContractError('domain_segmentation_unsupported: 此动画表达式可能存在断点；请使用支持分段的静态图或返修数学域')
        for value in node.values():
            if isinstance(value,dict): continuous(value)
    continuous(expr)
    if any(s['domain']!='real' or s.get('assumptions') for s in model.symbols if s['name'] in ast_names(expr)):
        raise ContractError('domain_segmentation_unsupported: 曲面仅支持无额外假设的实变量域，不能忽略批准符号条件')
    if model.conditions:
        raise ContractError('domain_segmentation_unsupported: 动画采样尚不支持条件域，不能忽略批准模型条件')
    allowed={'x'} if kind=='curve' else {'x','y'}
    if not ast_names(expr)<=allowed: raise ContractError('动画采样含未赋值参数；必须在批准模型中具体化')
    xs=_linspace(*obj['domain']['x'],obj['resolution'][0]);ys=_linspace(*obj['domain']['y'],obj['resolution'][1])
    return {'kind':'surface','x_values':xs,'y_values':ys,'resolution':list(obj['resolution']),
            'u':[[scalar(expr,{'x':x,'y':y}) for x in xs] for y in ys]}


def _compile_v2(spec,profile,context,*,cancel=None):
    from ..media.presentation_policy import presentation_policy
    binding,models=_approved_v2_bindings(spec,context,cancel=cancel)
    objects={}
    for obj in spec.objects:
        if 'model_id' in obj: sampled=_v2_sample(obj,models[obj['model_id']],context)
        else: sampled={k:v for k,v in obj.items() if k!='object_id'}
        for key in ('label','color'):
            if key in obj: sampled[key]=obj[key]
        objects[obj['object_id']]=sampled
    steps=spec.steps;captions=tuple(s['caption'] for s in steps)
    formulas=tuple(o['tex'] for o in objects.values() if o['kind']=='formula')
    payload={'format':'visual-scene-2.0','compiler_version':COMPILER_VERSION,'profile':profile.name,
             'parameters':{},'objects':objects,'captions':list(captions),'formulas':list(formulas),
             'steps':steps,'math_values':{},'math_binding':binding,'presentation_policy':presentation_policy()}
    source=_render_source(profile,payload)
    return CompiledScene(spec_hash=spec.spec_hash,compiler_version=COMPILER_VERSION,source=source,
        source_hash=digest_text(source),profile=profile.name,samples={'objects':objects,'parameters':{},'math_binding':binding},
        captions=captions,formulas=formulas)
