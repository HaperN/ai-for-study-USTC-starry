"""T06 独立数学检查测试（计划 §5.4）。

这里的 oracle 是**测试自己手写的多项式／矩阵算术**，与 `math_check.py` 内部实现、
与 `compiler.py` 的数值求值都不是同一份代码。三处必须给出同一个数。

特别检查 §5.4 的硬要求：
* `a[(x−h)²−(y−k)²]` 的导数、字幕、截线随 a/h/k 变化（不是固定 2 / −2）；
* `a x² − b y², a≠b` 若宣称"抵消"，checker **必须拒绝**；
* 2×2 矩阵终点误差 ≤ 1e-6；
* 空报告不算 pass。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "visual"
for extra in (ROOT / "src", FIXTURES):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import builders  # noqa: E402

from aiforstudy.blocks.visual.compiler import RenderProfile, compile_scene  # noqa: E402
from aiforstudy.blocks.visual.math_check import (  # noqa: E402
    MATH_TOLERANCE, MathReport, verify_math)
from aiforstudy.blocks.visual.spec import parse_visual_spec  # noqa: E402
from aiforstudy.errors import ContractError  # noqa: E402

SOURCE_SAMPLES = ("saddle-a1-h0-k0", "saddle-a0.5-h0.5-k-0.5",
                  "saddle-a2-h0.5-k-0.5", "pseudo-cancel-a2-b3",
                  "vector-sum", "matrix-apply")


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.spec.json").read_text(encoding="utf-8"))


# ── 测试自带的独立 oracle（手写算术，不是被测代码）────────────────────────
def oracle_uxx(a: float) -> float:
    """u = a[(x−h)²−(y−k)²] ⇒ u_xx = 2a。"""
    return 2.0 * a


def oracle_uyy(a: float) -> float:
    return -2.0 * a


def oracle_laplacian(a: float) -> float:
    return oracle_uxx(a) + oracle_uyy(a)


def oracle_matvec(rows, vector):
    out = []
    for row in rows:
        total = 0.0
        for entry, value in zip(row, vector):
            total += float(entry) * float(value)
        out.append(total)
    return out


def oracle_vecadd(left, right):
    return [float(a) + float(b) for a, b in zip(left, right)]


# ── §5.4-1：参数化鞍面 ─────────────────────────────────────────────────────
@pytest.mark.parametrize("name,a", [("saddle-a0.5-h0.5-k-0.5", 0.5),
                                    ("saddle-a2-h0.5-k-0.5", 2.0)])
def test_derivatives_follow_parameters(name, a):
    report = verify_math(parse_visual_spec(load(name)))
    assert report.status == "pass", report.reasons
    uxx = report.by_assertion("uxx")
    uyy = report.by_assertion("uyy")
    assert uxx.status == "pass" and uyy.status == "pass"
    assert abs(uxx.actual - oracle_uxx(a)) <= 1e-9
    assert abs(uyy.actual - oracle_uyy(a)) <= 1e-9
    assert abs(uxx.expected - oracle_uxx(a)) <= 1e-12
    assert uxx.actual != 2.0 or a == 1.0, "不许固定输出 2"


def test_laplacian_is_zero_but_surface_is_not_flat():
    report = verify_math(parse_visual_spec(load("saddle-a1-h0-k0")))
    lap = report.by_assertion("lap")
    assert lap.status == "pass" and abs(lap.actual - oracle_laplacian(1.0)) <= 1e-9
    not_flat = report.by_assertion("notflat")
    assert not_flat.status == "pass", "z=x²−y² 不是平面，这条断言必须由曲率算出"
    assert abs(not_flat.actual) > 1e-6


def test_polynomial_expansion_matches_manual_coefficients():
    report = verify_math(parse_visual_spec(load("saddle-a2-h0.5-k-0.5")))
    check = report.by_assertion("poly")
    assert check.status == "pass"
    manual = builders.saddle_coefficients(2.0, 0.5, -0.5)
    for monomial, value in manual.items():
        assert abs(check.actual[monomial] - value) <= 1e-9, monomial


# ── §5.4-2：a≠b 的伪抵消必须被拒绝 ─────────────────────────────────────────
def test_pseudo_cancellation_is_rejected():
    report = verify_math(parse_visual_spec(load("pseudo-cancel-a2-b3")))
    assert report.status == "fail"
    lap = report.by_assertion("lap")
    assert lap.status == "fail"
    # u_xx=2a=4、u_yy=−2b=−6、和=−2；信息里要能看见这两个数
    assert abs(lap.actual - (oracle_uxx(2.0) + oracle_uyy(3.0))) <= 1e-9
    assert "4" in lap.reason and "6" in lap.reason
    uxx = report.by_assertion("uxx")
    assert uxx.status == "fail" and abs(uxx.actual - 4.0) <= 1e-9
    assert any("抵消" in reason or "≠" in reason or "!=" in reason
               for reason in report.reasons), report.reasons


def test_false_derivative_claim_is_rejected_even_when_ast_differs():
    data = load("saddle-a1-h0-k0")
    assert verify_math(parse_visual_spec(data)).status == "pass"
    data["math_assertions"][0]["expected"] = 2.0    # 真实 u_xx=2*a=2 -> 改 a
    data["parameters"]["a"] = 3.0
    for obj in data["objects"]:
        if obj.get("expr") is not None:
            obj["expr"] = builders.saddle_expr(3.0, 0.0, 0.0)
    report = verify_math(parse_visual_spec(data))
    assert report.status == "fail"
    assert report.by_assertion("uxx").status == "fail"


# ── §5.4-3：矩阵与向量 ─────────────────────────────────────────────────────
def test_matrix_endpoint_error_within_tolerance():
    data = load("matrix-apply")
    k = data["parameters"]["k"]
    report = verify_math(parse_visual_spec(data))
    check = report.by_assertion("apply")
    assert check.status == "pass", check.reason
    manual = oracle_matvec([[1.0, k], [3.0, 4.0]], [1.0, 2.0])
    error = max(abs(a - b) for a, b in zip(check.actual["product"], manual))
    assert error <= 1e-6, f"矩阵终点误差 {error} 超过 1e-6"
    drawn_error = max(abs(a - b) for a, b in zip(check.actual["drawn_endpoint"], manual))
    assert drawn_error <= 1e-6, f"画出来的终点误差 {drawn_error} 超过 1e-6"
    assert check.tolerance <= 1e-6


def test_matrix_wrong_endpoint_is_rejected():
    data = load("matrix-apply")
    data["math_assertions"][0]["expected"] = [5.0, 12.0]      # 真实 [5, 11]
    report = verify_math(parse_visual_spec(data))
    assert report.status == "fail"
    assert report.by_assertion("apply").status == "fail"


def test_matrix_result_object_must_match_math():
    data = load("matrix-apply")
    index = next(i for i, o in enumerate(data["objects"]) if o["object_id"] == "mv")
    data["objects"][index]["end"] = [builders.C(5.0), builders.C(12.0)]
    report = verify_math(parse_visual_spec(data))
    assert report.status == "fail", "画出来的终点和算出来的不一致，必须失败"


def test_vector_sum_different_inputs_give_correct_endpoints():
    for v1, v2 in (((1.0, 2.0), (2.0, 3.0)), ((4.0, -1.0), (-2.0, 5.0))):
        spec = parse_visual_spec(builders.vector_sum_spec(v1, v2))
        report = verify_math(spec)
        check = report.by_assertion("sum")
        manual = oracle_vecadd(v1, v2)
        assert check.status == "pass", check.reason
        assert max(abs(a - b) for a, b in zip(check.actual["sum"], manual)) <= 1e-6
        assert max(abs(a - b)
                   for a, b in zip(check.actual["drawn_endpoint"], manual)) <= 1e-6
        scene = compile_scene(spec, RenderProfile.DRAFT)
        drawn = scene.samples["objects"]["vsum"]["end"]
        assert max(abs(a - b) for a, b in zip(drawn, manual)) <= 1e-6


def test_vector_sum_wrong_input_endpoint_is_rejected():
    data = builders.vector_sum_spec((1.0, 2.0), (2.0, 3.0))
    index = next(i for i, o in enumerate(data["objects"]) if o["object_id"] == "vsum")
    data["objects"][index]["end"] = [builders.C(9.0), builders.C(9.0)]
    report = verify_math(parse_visual_spec(data))
    assert report.status == "fail"


# ── §5.4-4：截线方向与"不是平面" ───────────────────────────────────────────
def test_section_trace_and_opening_direction():
    report = verify_math(parse_visual_spec(load("saddle-a1-h0-k0")))
    assert report.by_assertion("tr_y").status == "pass"
    assert report.by_assertion("tr_x").status == "pass"


def test_wrong_opening_direction_is_rejected():
    data = load("saddle-a1-h0-k0")
    index = next(i for i, a in enumerate(data["math_assertions"])
                 if a["assertion_id"] == "tr_y")
    data["math_assertions"][index]["opens"] = "down"       # 真实是 up
    report = verify_math(parse_visual_spec(data))
    assert report.status == "fail"
    assert report.by_assertion("tr_y").status == "fail"


def test_flat_surface_fails_the_not_planar_claim():
    data = load("saddle-a1-h0-k0")
    plane = builders.MUL(builders.C(2.0), builders.V("x"))       # z = 2x 是平面
    data["objects"] = [
        {"object_id": "u", "kind": "surface", "expr": plane,
         "domain": {"x": [-1, 1], "y": [-1, 1]}, "resolution": [8, 8]}]
    data["steps"] = [{"step_id": "s1", "caption": "看这个曲面。", "start": 0.0, "end": 4.0,
                      "actions": [{"action": "reveal", "object_id": "u"}],
                      "checkpoints": [], "expected_change": "出现曲面"}]
    data["math_assertions"] = [{"assertion_id": "notflat", "kind": "surface_not_planar",
                                "object_id": "u"}]
    report = verify_math(parse_visual_spec(data))
    assert report.status == "fail", "平面不能满足'不是平面'的断言"


# ── 空报告 / 状态 / 独立性 ─────────────────────────────────────────────────
def test_empty_report_is_not_pass():
    data = load("saddle-a1-h0-k0")
    data["math_assertions"] = []
    for step in data["steps"]:
        step["caption"] = "没有数学占位符的分镜字幕。"
    report = verify_math(parse_visual_spec(data))
    assert report.checks == ()
    assert report.status != "pass" and report.reasons
    with pytest.raises(ContractError):
        MathReport(checks=(), status="pass", evidence_hashes=(), reasons=())


def test_report_status_enum_is_closed():
    with pytest.raises(ContractError):
        MathReport(checks=(), status="probably_fine", evidence_hashes=(), reasons=())


def test_report_carries_evidence_hashes():
    report = verify_math(parse_visual_spec(load("saddle-a1-h0-k0")))
    assert report.evidence_hashes and len(report.evidence_hashes) == len(report.checks)
    for check in report.checks:
        assert check.evidence_hash.startswith("sha256:")
        assert check.method


def test_oracle_inside_math_check_is_independent_of_compiler(monkeypatch):
    import aiforstudy.blocks.visual.compiler as compiler
    import aiforstudy.blocks.visual.math_check as math_check

    source = Path(math_check.__file__).read_text(encoding="utf-8")
    assert "compiler" not in source, "独立检查器不得 import/引用可信编译层"
    assert "MATH_TOLERANCE" in source

    def _boom(*_args, **_kwargs):
        raise AssertionError("独立数学检查复用了可信编译层的求值函数")

    monkeypatch.setattr(compiler, "eval_ast", _boom)
    report = verify_math(parse_visual_spec(load("saddle-a1-h0-k0")))
    assert report.status == "pass"


def test_evidence_shows_two_independent_routes():
    report = verify_math(parse_visual_spec(load("saddle-a1-h0-k0")))
    method = report.by_assertion("uxx").method
    assert "poly" in method and "fd5" in method, method


def test_math_tolerance_is_not_reused_for_layout_pixels():
    assert MATH_TOLERANCE == 1e-6
    source = Path(sys.modules["aiforstudy.blocks.visual.math_check"].__file__) \
        .read_text(encoding="utf-8").lower()
    for layout_word in ("pixel", "bbox", "overflow", "font_size"):
        assert layout_word not in source, f"数学容差模块混进了布局概念：{layout_word}"


# ── 夹具可复现（T07 会直接消费）────────────────────────────────────────────
@pytest.mark.parametrize("name", SOURCE_SAMPLES)
def test_fixture_reports_are_reproducible(name):
    stored = json.loads((FIXTURES / f"{name}.math_report.json").read_text(encoding="utf-8"))
    fresh = verify_math(parse_visual_spec(load(name))).as_dict()
    assert fresh == stored
