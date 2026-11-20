"""独立数学检查 `verify_math(spec) -> MathReport`（T06，计划 §5.4）。

## 为什么必须独立

方案 §5.4：

> 独立数学验证**不调用编译器自己的"正确答案函数"**：从 AST 进行独立多项式运算／求导，
> 再对渲染采样进行数值交叉检查，容差 `1e-6`。非数学布局像素误差不能混用此容差。

所以本模块：

* **不引用**同包可信编译模块的任何函数，自建多项式引擎；
* 每条断言走**两条独立路线**：
  1. 把 AST 展开成多项式，做符号求导／系数比对；
  2. 对原始 AST 做五点差分数值采样，交叉检查（路径里带 `fd5`）；
* 宣称的 `expected` 只作为**被检验的声明**，绝不当正确答案：`a x² − b y² (a≠b)`
  宣称"二阶变化抵消"时必须失败；
* 数学容差固定 `1e-6`，布局／像素误差不许混进来；
* **空报告不算 pass**：没有断言时返回 `blocked`，构造 `status="pass"` 的空报告直接报错。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ...core.digest import digest_of
from ...errors import ContractError
from ...contracts.review import MEDIA_REPORT_STATUSES as STATUSES
from .spec import MATH_TOLERANCE, VisualSpecV1, parse_visual_spec, target_expression as _target_expr

#: 只有数学断言用这个容差；画面／布局另算
FD_STEP = 1e-3
CURVATURE_GRID = 5
SCHEMA_VERSION = "1.0"


# ── 多项式引擎（本模块独立实现，不复用任何别处的求导）──────────────────────
def _add(left: Mapping[tuple, float], right: Mapping[tuple, float]) -> dict:
    out = dict(left)
    for key, value in right.items():
        out[key] = out.get(key, 0.0) + value
    return out


def _scale(poly: Mapping[tuple, float], factor: float) -> dict:
    return {key: value * factor for key, value in poly.items()}


def _mul(left: Mapping[tuple, float], right: Mapping[tuple, float]) -> dict:
    out: dict[tuple, float] = {}
    for lkey, lvalue in left.items():
        for rkey, rvalue in right.items():
            merged = dict(lkey)
            for name, power in rkey:
                merged[name] = merged.get(name, 0) + power
            key = tuple(sorted((name, power) for name, power in merged.items() if power))
            out[key] = out.get(key, 0.0) + lvalue * rvalue
    return out


def _from_ast(node: Mapping[str, Any], parameters: Mapping[str, float]) -> dict:
    op = node["op"]
    if op == "const":
        return {(): float(node["value"])}
    if op == "var":
        name = node["name"]
        if name in parameters:
            return {(): float(parameters[name])}
        return {((name, 1),): 1.0}
    if op == "pow":
        base = _from_ast(node["base"], parameters)
        out = {(): 1.0}
        for _ in range(int(node["exponent"])):
            out = _mul(out, base)
        return out
    left = _from_ast(node["left"], parameters)
    right = _from_ast(node["right"], parameters)
    if op == "add":
        return _add(left, right)
    if op == "sub":
        return _add(left, _scale(right, -1.0))
    if op == "mul":
        return _mul(left, right)
    raise ContractError(f"多项式展开遇到未知算子 {op!r}")


def _derivative(poly: Mapping[tuple, float], variable: str) -> dict:
    out: dict[tuple, float] = {}
    for key, coefficient in poly.items():
        powers = dict(key)
        power = powers.get(variable, 0)
        if power == 0:
            continue
        if power == 1:
            del powers[variable]
        else:
            powers[variable] = power - 1
        out[tuple(sorted(powers.items()))] = coefficient * power
    return out


def _evaluate(poly: Mapping[tuple, float], point: Mapping[str, float]) -> float:
    total = 0.0
    for key, coefficient in poly.items():
        term = coefficient
        for name, power in key:
            term *= float(point[name]) ** power
        total += term
    return total


def _nth_derivative_value(poly: Mapping[tuple, float], variable: str, order: int,
                          point: Mapping[str, float]) -> float:
    current = poly
    for _ in range(order):
        current = _derivative(current, variable)
    return _evaluate(current, point)


def _monomial_text(key: tuple) -> str:
    if not key:
        return "1"
    return "*".join(f"{name}^{power}" if power > 1 else str(name) for name, power in key)


# ── 数值路线（对原始 AST 做五点差分；故意与上面分开实现）───────────────────
def _numeric_ast(node: Mapping[str, Any], env: Mapping[str, float],
                 parameters: Mapping[str, float]) -> float:
    op = node["op"]
    if op == "const":
        return float(node["value"])
    if op == "var":
        name = node["name"]
        if name in env:
            return float(env[name])
        return float(parameters[name])
    if op == "pow":
        return float(_numeric_ast(node["base"], env, parameters) ** int(node["exponent"]))
    left = _numeric_ast(node["left"], env, parameters)
    right = _numeric_ast(node["right"], env, parameters)
    if op == "add":
        return left + right
    if op == "sub":
        return left - right
    if op == "mul":
        return left * right
    raise ContractError(f"数值采样遇到未知算子 {op!r}")


def _numeric_fd(node: Mapping[str, Any], parameters: Mapping[str, float], variable: str,
                order: int, point: Mapping[str, float]) -> float:
    step = FD_STEP

    def value(at: float) -> float:
        env = dict(point)
        env[variable] = at
        return _numeric_ast(node, env, parameters)

    center = float(point[variable])
    if order == 1:
        return (value(center - 2 * step) - 8 * value(center - step)
                + 8 * value(center + step) - value(center + 2 * step)) / (12 * step)
    return (-value(center + 2 * step) + 16 * value(center + step) - 30 * value(center)
            + 16 * value(center - step) - value(center - 2 * step)) / (12 * step * step)


# ── 报告类型 ───────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MathCheck:
    check_id: str
    assertion_id: str
    kind: str
    status: str
    method: str
    expected: Any
    actual: Any
    tolerance: float
    evidence_hash: str
    reason: str = ""

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ContractError(f"MathCheck.status: 只能是 {list(STATUSES)}")

    def as_dict(self) -> dict:
        return {"check_id": self.check_id, "assertion_id": self.assertion_id,
                "kind": self.kind, "status": self.status, "method": self.method,
                "expected": self.expected, "actual": self.actual,
                "tolerance": self.tolerance, "evidence_hash": self.evidence_hash,
                "reason": self.reason}


@dataclass(frozen=True)
class MathReport:
    checks: tuple[MathCheck, ...]
    status: str
    evidence_hashes: tuple[str, ...]
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ContractError(f"MathReport.status: 只能是 {list(STATUSES)}")
        if self.status == "pass":
            if not self.checks:
                raise ContractError("空报告不算 pass（计划 §5.3）")
            failing = [check.assertion_id for check in self.checks
                       if check.status != "pass"]
            if failing:
                raise ContractError(f"有未通过的检查却标 pass：{failing}")

    def by_assertion(self, assertion_id: str) -> MathCheck | None:
        for check in self.checks:
            if check.assertion_id == assertion_id:
                return check
        return None

    def as_dict(self) -> dict:
        return {"schema_version": SCHEMA_VERSION, "status": self.status,
                "reasons": list(self.reasons),
                "evidence_hashes": list(self.evidence_hashes),
                "checks": [check.as_dict() for check in self.checks]}


# ── 检查实现 ───────────────────────────────────────────────────────────────


def _point_of(assertion: Mapping[str, Any], spec: VisualSpecV1,
              object_id: str) -> dict[str, float]:
    if "at" in assertion:
        return {"x": float(assertion["at"]["x"]), "y": float(assertion["at"]["y"])}
    obj = spec.object_by_id(object_id)
    domain = obj.get("domain")
    if domain:
        return {"x": (float(domain["x"][0]) + float(domain["x"][1])) / 2.0,
                "y": (float(domain["y"][0]) + float(domain["y"][1])) / 2.0}
    return {"x": 0.0, "y": 0.0}


def _endpoint(obj: Mapping[str, Any], parameters: Mapping[str, float]) -> list[float]:
    items = obj["at"] if obj["kind"] == "point" else obj["end"]
    return [float(_numeric_ast(item, {}, parameters)) for item in items]


def _vector_value(obj: Mapping[str, Any], parameters: Mapping[str, float]) -> list[float]:
    """向量的数学值 = 终点 − 起点（点对象当位置向量）。"""
    if obj["kind"] == "point":
        return _endpoint(obj, parameters)
    end = _endpoint(obj, parameters)
    start = [float(_numeric_ast(item, {}, parameters)) for item in obj["start"]] \
        if "start" in obj else [0.0, 0.0]
    return [end[i] - start[i] for i in range(2)]


def _evidence(assertion_id: str, kind: str, method: str, expected: Any, actual: Any,
              tolerance: float) -> str:
    return digest_of({"assertion_id": assertion_id, "kind": kind, "method": method,
                      "expected": expected, "actual": actual, "tolerance": tolerance})


def _make(assertion: Mapping[str, Any], *, status: str, method: str, expected: Any,
          actual: Any, tolerance: float, reason: str = "") -> MathCheck:
    return MathCheck(
        check_id=f"math-{assertion['assertion_id']}", assertion_id=assertion["assertion_id"],
        kind=assertion["kind"], status=status, method=method, expected=expected,
        actual=actual, tolerance=tolerance,
        evidence_hash=_evidence(assertion["assertion_id"], assertion["kind"], method,
                                expected, actual, tolerance),
        reason=reason)


def _g(value: float) -> str:
    return f"{float(value):g}"


def _partial_derivative(assertion: Mapping[str, Any], spec: VisualSpecV1) -> MathCheck:
    expr = _target_expr(spec, assertion["object_id"])
    point = _point_of(assertion, spec, assertion["object_id"])
    variable, order = assertion["variable"], assertion["order"]
    poly = _from_ast(expr, spec.parameters)
    symbolic = _nth_derivative_value(poly, variable, order, point)
    numeric = _numeric_fd(expr, spec.parameters, variable, order, point)
    expected = float(assertion["expected"])
    tolerance = float(assertion["tolerance"])
    method = "poly_diff:exact + fd5:sampled"
    cross_ok = abs(numeric - symbolic) <= tolerance
    claim_ok = abs(symbolic - expected) <= tolerance
    if claim_ok and cross_ok:
        return _make(assertion, status="pass", method=method, expected=expected,
                     actual=symbolic, tolerance=tolerance)
    reason = (f"{variable} 的 {order} 阶偏导：独立多项式算出 {_g(symbolic)}，"
              f"五点差分 {_g(numeric)}，断言宣称 {_g(expected)}（容差 {tolerance:g}）")
    if not cross_ok:
        reason += "；两条独立路线互相不一致"
    return _make(assertion, status="fail", method=method, expected=expected,
                 actual=symbolic, tolerance=tolerance, reason=reason)


def _derivative_sum(assertion: Mapping[str, Any], spec: VisualSpecV1) -> MathCheck:
    expr = _target_expr(spec, assertion["object_id"])
    point = _point_of(assertion, spec, assertion["object_id"])
    poly = _from_ast(expr, spec.parameters)
    parts, symbolic_total, numeric_total = [], 0.0, 0.0
    for term in assertion["terms"]:
        variable, order = term["variable"], term["order"]
        value = _nth_derivative_value(poly, variable, order, point)
        numeric = _numeric_fd(expr, spec.parameters, variable, order, point)
        symbolic_total += value
        numeric_total += numeric
        parts.append(f"u_{variable}{variable}={_g(value)}")
    expected = float(assertion["expected"])
    tolerance = float(assertion["tolerance"])
    method = "poly_diff:exact + fd5:sampled"
    cross_ok = abs(numeric_total - symbolic_total) <= tolerance
    claim_ok = abs(symbolic_total - expected) <= tolerance
    if claim_ok and cross_ok:
        return _make(assertion, status="pass", method=method, expected=expected,
                     actual=symbolic_total, tolerance=tolerance)
    reason = ("、".join(parts) + f"，和={_g(symbolic_total)} ≠ 宣称 {_g(expected)}"
              "（a≠b 时不得宣称二阶变化抵消）")
    if not cross_ok:
        reason += f"；五点差分和为 {_g(numeric_total)}，两条路线不一致"
    return _make(assertion, status="fail", method=method, expected=expected,
                 actual=symbolic_total, tolerance=tolerance, reason=reason)


def _polynomial(assertion: Mapping[str, Any], spec: VisualSpecV1) -> MathCheck:
    expr = _target_expr(spec, assertion["object_id"])
    poly = _from_ast(expr, spec.parameters)
    declared = {monomial: float(value)
                for monomial, value in assertion["expected_coefficients"].items()}
    keys = set(poly) | {_parse_monomial(monomial) for monomial in declared}
    actual = {_monomial_text(key): round(float(poly.get(key, 0.0)), 12)
              for key in sorted(keys)}
    tolerance = MATH_TOLERANCE
    mismatches = []
    for key in sorted(keys):
        got = poly.get(key, 0.0)
        want = declared.get(_monomial_text(key), 0.0)
        if abs(got - want) > tolerance:
            mismatches.append(f"{_monomial_text(key)}: 实际 {_g(got)} ≠ 宣称 {_g(want)}")
    method = "poly_expand:independent_coefficients"
    if not mismatches:
        return _make(assertion, status="pass", method=method, expected=declared,
                     actual=actual, tolerance=tolerance)
    return _make(assertion, status="fail", method=method, expected=declared, actual=actual,
                 tolerance=tolerance,
                 reason="多项式系数不一致：" + "；".join(mismatches))


def _parse_monomial(monomial: str) -> tuple:
    if monomial == "1":
        return ()
    factors = []
    for factor in monomial.split("*"):
        if "^" in factor:
            name, power = factor.split("^")
            factors.append((name, int(power)))
        else:
            factors.append((factor, 1))
    return tuple(sorted(factors))


def _surface_not_planar(assertion: Mapping[str, Any], spec: VisualSpecV1) -> MathCheck:
    expr = _target_expr(spec, assertion["object_id"])
    obj = spec.object_by_id(assertion["object_id"])
    poly = _from_ast(expr, spec.parameters)
    domain = obj.get("domain") or {"x": [-1, 1], "y": [-1, 1]}
    xs = [domain["x"][0] + (domain["x"][1] - domain["x"][0]) * i / (CURVATURE_GRID - 1)
          for i in range(CURVATURE_GRID)]
    ys = [domain["y"][0] + (domain["y"][1] - domain["y"][0]) * i / (CURVATURE_GRID - 1)
          for i in range(CURVATURE_GRID)]
    curve = 0.0
    for x in xs:
        for y in ys:
            point = {"x": float(x), "y": float(y)}
            uxx = _nth_derivative_value(poly, "x", 2, point)
            uyy = _nth_derivative_value(poly, "y", 2, point)
            uxy = _nth_derivative_value(_derivative(poly, "x"), "y", 1, point)
            curve = max(curve, abs(uxx), abs(uyy), abs(uxy))
    method = "poly_diff:curvature_scan"
    tolerance = MATH_TOLERANCE
    if curve > tolerance:
        return _make(assertion, status="pass", method=method, expected="not_planar",
                     actual=curve, tolerance=tolerance)
    return _make(assertion, status="fail", method=method, expected="not_planar",
                 actual=curve, tolerance=tolerance,
                 reason=("该曲面在采样域内所有二阶变化都为 0，它就是平面；"
                         "不能拿它证明'二阶变化抵消'"))


def _section_is_trace(assertion: Mapping[str, Any], spec: VisualSpecV1) -> MathCheck:
    section = spec.object_by_id(assertion["section"])
    expr = _target_expr(spec, assertion["surface"])
    poly = _from_ast(expr, spec.parameters)
    fixed_variable = section["variable"]
    free_variable = "y" if fixed_variable == "x" else "x"
    fixed_value = float(_numeric_ast(section["at"], {}, spec.parameters))
    low, high = float(section["range"][0]), float(section["range"][1])
    tolerance = MATH_TOLERANCE
    method = "poly_eval + fd5:section_trace"
    worst = 0.0
    count = min(int(section["samples"]), 16)
    for i in range(count):
        free = low + (high - low) * i / (count - 1)
        point = {fixed_variable: fixed_value, free_variable: float(free)}
        from_poly = _evaluate(poly, point)
        from_ast = _numeric_ast(expr, point, spec.parameters)
        worst = max(worst, abs(from_poly - from_ast))
    middle = {fixed_variable: fixed_value, free_variable: (low + high) / 2.0}
    second = _nth_derivative_value(poly, free_variable, 2, middle)
    numeric_second = _numeric_fd(expr, spec.parameters, free_variable, 2, middle)
    opening = "flat" if abs(second) <= tolerance else ("up" if second > 0 else "down")
    actual = {"trace_max_error": worst, "free_variable": free_variable,
              "second_derivative": second, "numeric_second_derivative": numeric_second,
              "opening": opening}
    problems = []
    if worst > tolerance:
        problems.append(f"截线采样与曲面求值最大偏差 {worst:g} 超过容差 {tolerance:g}")
    if abs(numeric_second - second) > tolerance:
        problems.append(f"截线二阶变化两条路线不一致（{_g(second)} vs {_g(numeric_second)}）")
    if opening != assertion["opens"]:
        problems.append(f"固定 {fixed_variable} 的截线在自由变量 {free_variable} 上"
                        f"开口为 {opening}，与宣称 {assertion['opens']} 不符")
    if not problems:
        return _make(assertion, status="pass", method=method, expected=assertion["opens"],
                     actual=actual, tolerance=tolerance)
    return _make(assertion, status="fail", method=method, expected=assertion["opens"],
                 actual=actual, tolerance=tolerance, reason="；".join(problems))


def _vector_sum(assertion: Mapping[str, Any], spec: VisualSpecV1) -> MathCheck:
    tolerance = float(assertion["tolerance"])
    method = "coord_eval + closed_form"
    parts = [_vector_value(spec.object_by_id(operand), spec.parameters)
             for operand in assertion["operands"]]
    total = [0.0, 0.0]
    for part in parts:
        for i in range(2):
            total[i] += part[i]
    expected = [float(assertion["expected"][0]), float(assertion["expected"][1])]
    drawn = _endpoint(spec.object_by_id(assertion["result_object"]), spec.parameters)
    actual = {"sum": total, "drawn_endpoint": drawn}
    problems = []
    if max(abs(total[i] - expected[i]) for i in range(2)) > tolerance:
        problems.append(f"分量相加得 {total}，宣称终点 {expected}")
    if max(abs(drawn[i] - expected[i]) for i in range(2)) > tolerance:
        problems.append(f"画出来的终点 {drawn} 与算出来的 {expected} 不一致")
    if not problems:
        return _make(assertion, status="pass", method=method, expected=expected,
                     actual=actual, tolerance=tolerance)
    return _make(assertion, status="fail", method=method, expected=expected, actual=actual,
                 tolerance=tolerance, reason="；".join(problems))


def _matrix_apply(assertion: Mapping[str, Any], spec: VisualSpecV1) -> MathCheck:
    tolerance = float(assertion["tolerance"])
    method = "matmul2x2:closed_form + coord_eval"
    matrix = spec.object_by_id(assertion["matrix"])["rows"]
    rows = [[float(_numeric_ast(entry, {}, spec.parameters)) for entry in row]
            for row in matrix]
    vector = _endpoint(spec.object_by_id(assertion["vector"]), spec.parameters)
    product = [sum(rows[r][c] * vector[c] for c in range(2)) for r in range(2)]
    expected = [float(assertion["expected"][0]), float(assertion["expected"][1])]
    drawn = _endpoint(spec.object_by_id(assertion["result_object"]), spec.parameters)
    actual = {"product": product, "drawn_endpoint": drawn, "rows": rows, "vector": vector}
    problems = []
    if max(abs(product[i] - expected[i]) for i in range(2)) > tolerance:
        problems.append(f"2x2 乘法得 {product}，宣称终点 {expected}")
    if max(abs(drawn[i] - expected[i]) for i in range(2)) > tolerance:
        problems.append(f"画出来的终点 {drawn} 与算出来的 {product} 不一致")
    if not problems:
        return _make(assertion, status="pass", method=method, expected=expected,
                     actual=actual, tolerance=tolerance)
    return _make(assertion, status="fail", method=method, expected=expected, actual=actual,
                 tolerance=tolerance, reason="；".join(problems))


_RUNNERS = {
    "partial_derivative": _partial_derivative,
    "derivative_sum": _derivative_sum,
    "polynomial": _polynomial,
    "surface_not_planar": _surface_not_planar,
    "section_is_trace": _section_is_trace,
    "vector_sum": _vector_sum,
    "matrix_apply": _matrix_apply,
}


def _blocked(assertion: Mapping[str, Any], reason: str) -> MathCheck:
    return _make(assertion, status="blocked", method="unsupported", expected=None,
                 actual=None, tolerance=MATH_TOLERANCE, reason=reason)


def verify_math(spec: VisualSpecV1 | dict) -> MathReport:
    """独立复核整份规格的数学断言。**不调用可信编译层的任何函数。**"""
    if isinstance(spec, dict):
        spec = parse_visual_spec(spec)
    if not isinstance(spec, VisualSpecV1):
        raise ContractError("verify_math: 需要 VisualSpecV1")
    if not spec.math_assertions:
        return MathReport(checks=(), status="blocked", evidence_hashes=(),
                          reasons=("没有 math_assertions：空报告不算 pass",))

    checks: list[MathCheck] = []
    for assertion in spec.math_assertions:
        runner = _RUNNERS.get(assertion["kind"])
        if runner is None:
            checks.append(_blocked(assertion, f"未支持的断言类型 {assertion['kind']!r}"))
            continue
        try:
            checks.append(runner(assertion, spec))
        except ContractError as exc:
            checks.append(_blocked(assertion, str(exc)))
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
            checks.append(_blocked(assertion, f"无法复核：{exc!r}"))

    if any(check.status == "fail" for check in checks):
        status = "fail"
    elif any(check.status == "blocked" for check in checks):
        status = "blocked"
    elif any(check.status == "needs_review" for check in checks):
        status = "needs_review"
    else:
        status = "pass"
    reasons = tuple(check.reason for check in checks if check.status != "pass")
    if status == "blocked" and not reasons:  # pragma: no cover - 兜底
        reasons = ("存在无法复核的断言",)
    return MathReport(checks=tuple(checks), status=status,
                      evidence_hashes=tuple(check.evidence_hash for check in checks),
                      reasons=reasons)


__all__ = ["MATH_TOLERANCE", "STATUSES", "MathCheck", "MathReport", "verify_math"]
