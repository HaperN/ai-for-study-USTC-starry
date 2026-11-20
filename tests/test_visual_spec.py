"""T06 规格解析测试：合法规格能解析，非法规格**必须拒绝并定位字段**。

这些测试对准计划 §5.3 的能力边界：模型只能给受限 AST 的数学对象，
不能给 Python／TeX／路径／URL／NaN，也不能超上限后靠"截断"混过去。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "visual"
for extra in (ROOT / "src", FIXTURES):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import builders  # noqa: E402  (tests/fixtures/visual/builders.py)

from aiforstudy.blocks.visual.spec import (  # noqa: E402
    ACTIONS, KINDS, MAX_ACTIONS, MAX_AST_DEPTH, MAX_AST_NODES, MAX_CAPTION_CHARS,
    MAX_LABEL_CHARS, MAX_MESH, MAX_OBJECTS, MAX_STEPS, TeachingPlanV1,
    UnsupportedVisualSpec, VisualSpecV1, parse_teaching_plan, parse_visual_spec)
from aiforstudy.errors import ContractError  # noqa: E402

FROZEN_FIELDS = {
    "schema_version", "request_id", "unit_id", "source_refs", "lesson_anchor",
    "source_claims", "dependency_paths", "objective", "parameters",
    "objects", "steps", "math_assertions", "render_profile",
}


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.spec.json").read_text(encoding="utf-8"))


def reject(data: dict, *fragments: str) -> str:
    with pytest.raises(ContractError) as excinfo:
        parse_visual_spec(data)
    message = str(excinfo.value)
    for fragment in fragments:
        assert fragment in message, f"错误信息未定位到 {fragment!r}：{message}"
    return message


# ── 1. 合法规格 ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", ["saddle-a1-h0-k0", "saddle-a0.5-h0.5-k-0.5",
                                  "saddle-a2-h0.5-k-0.5", "vector-sum", "matrix-apply"])
def test_fixture_specs_parse(name):
    spec = parse_visual_spec(load(name))
    assert isinstance(spec, VisualSpecV1)
    assert set(spec.as_dict()) == FROZEN_FIELDS, "固定字段一个都不能少、也不能多"
    assert spec.objects and spec.steps and spec.math_assertions
    assert spec.render_profile in ("draft", "final")


def test_saddle_objects_cover_required_kinds():
    spec = parse_visual_spec(load("saddle-a1-h0-k0"))
    kinds = {obj["kind"] for obj in spec.objects}
    assert {"axes2d", "axes3d", "surface", "section", "formula", "label", "point"} <= kinds
    section = spec.object_by_id("trace_y")
    assert section["surface"] == "u" and section["variable"] == "y"


def test_matrix_is_2x2_finite_reals():
    spec = parse_visual_spec(load("matrix-apply"))
    matrix = spec.object_by_id("m")
    assert len(matrix["rows"]) == 2 and all(len(row) == 2 for row in matrix["rows"])


def test_parameters_are_normalized_numbers():
    spec = parse_visual_spec(load("saddle-a0.5-h0.5-k-0.5"))
    assert spec.parameters == {"a": 0.5, "h": 0.5, "k": -0.5}
    assert all(isinstance(v, (int, float)) and not isinstance(v, bool)
               for v in spec.parameters.values())


def test_teaching_plan_parses():
    plan = parse_teaching_plan(json.loads(
        (FIXTURES / "teaching-plan.json").read_text(encoding="utf-8")))
    assert isinstance(plan, TeachingPlanV1)
    assert plan.unit_id == "CH04" and len(plan.steps) == 2
    assert plan.steps[0]["claim_ids"] == ["c1"]


def test_teaching_plan_rejects_missing_and_unknown_claim():
    data = builders.teaching_plan_spec()
    broken = copy.deepcopy(data)
    del broken["visual_requests"]
    reject_plan(broken, "visual_requests")
    broken = copy.deepcopy(data)
    broken["steps"][0]["claim_ids"] = ["c9"]
    reject_plan(broken, "steps[0].claim_ids")


def reject_plan(data: dict, *fragments: str) -> str:
    with pytest.raises(ContractError) as excinfo:
        parse_teaching_plan(data)
    message = str(excinfo.value)
    for fragment in fragments:
        assert fragment in message, f"错误信息未定位到 {fragment!r}：{message}"
    return message


# ── 2. 禁止任意 Python / TeX / 路径 / URL ──────────────────────────────────
def test_rejects_python_code_in_label():
    data = load("saddle-a1-h0-k0")
    index = next(i for i, o in enumerate(data["objects"]) if o["kind"] == "label")
    data["objects"][index]["text"] = "__import__('os').system('ls')"
    reject(data, f"objects[{index}].text")


def test_rejects_python_call_ast_node():
    data = load("saddle-a1-h0-k0")
    index = next(i for i, o in enumerate(data["objects"]) if o["kind"] == "surface")
    data["objects"][index]["expr"] = {"op": "call", "name": "eval", "args": []}
    reject(data, f"objects[{index}].expr")


def test_rejects_expression_string_instead_of_ast():
    data = load("saddle-a1-h0-k0")
    index = next(i for i, o in enumerate(data["objects"]) if o["kind"] == "surface")
    data["objects"][index]["expr"] = "x**2 - y**2"
    reject(data, f"objects[{index}].expr")


def test_rejects_division_and_trig_operators():
    """除法奇点与三角算子首期明确不支持 —— 不是"换成别的曲线"，是拒绝。"""
    for bad in ({"op": "div", "left": builders.V("x"), "right": builders.V("y")},
                {"op": "sin", "arg": builders.V("x")}):
        data = load("saddle-a1-h0-k0")
        index = next(i for i, o in enumerate(data["objects"]) if o["kind"] == "surface")
        data["objects"][index]["expr"] = bad
        reject(data, f"objects[{index}].expr")


def test_rejects_arbitrary_tex():
    data = load("saddle-a1-h0-k0")
    index = next(i for i, o in enumerate(data["objects"]) if o["kind"] == "label")
    data["objects"][index]["text"] = r"\frac{a}{b}"
    reject(data, f"objects[{index}].text")

    data = load("saddle-a1-h0-k0")
    index = next(i for i, o in enumerate(data["objects"]) if o["kind"] == "formula")
    data["objects"][index]["tex"] = r"\int_0^1 x\,dx"
    reject(data, f"objects[{index}]")


def test_rejects_paths_and_urls():
    data = load("saddle-a1-h0-k0")
    data["dependency_paths"] = ["../../etc/passwd"]
    reject(data, "dependency_paths[0]")

    data = load("saddle-a1-h0-k0")
    data["dependency_paths"] = ["/etc/passwd"]
    reject(data, "dependency_paths[0]")

    data = load("saddle-a1-h0-k0")
    data["source_refs"] = ["https://evil.example/x"]
    reject(data, "source_refs[0]")

    data = load("saddle-a1-h0-k0")
    index = next(i for i, o in enumerate(data["objects"]) if o["kind"] == "label")
    data["objects"][index]["text"] = "见 http://example.com"
    reject(data, f"objects[{index}].text")


# ── 3. 有限数值 ────────────────────────────────────────────────────────────
def test_rejects_nan_and_inf():
    data = load("saddle-a1-h0-k0")
    data["parameters"]["a"] = float("nan")
    reject(data, "parameters.a")

    data = load("saddle-a1-h0-k0")
    data["parameters"]["a"] = float("inf")
    reject(data, "parameters.a")

    data = load("saddle-a1-h0-k0")
    data["objects"][0]["x_range"] = [-2, float("inf")]
    reject(data, "objects[0].x_range")

    data = load("saddle-a1-h0-k0")
    data["math_assertions"][0]["expected"] = float("nan")
    reject(data, "math_assertions[0].expected")


# ── 4. 未知对象与引用完整性 ────────────────────────────────────────────────
def test_rejects_unknown_object_kind():
    data = load("saddle-a1-h0-k0")
    data["objects"][0]["kind"] = "torus"
    reject(data, "objects[0].kind")


def test_rejects_pixel_point_cloud_disguised_as_math_object():
    """禁止模型直接输出像素点云冒充数学对象（计划 §5.3）。"""
    index = next(i for i, o in enumerate(load("saddle-a1-h0-k0")["objects"])
                 if o["kind"] == "surface")

    data = load("saddle-a1-h0-k0")
    data["objects"][index] = {"object_id": "u", "kind": "curve",
                              "points": [[0, 0], [1, 1], [2, 4]]}
    reject(data, f"objects[{index}]")

    data = load("saddle-a1-h0-k0")
    data["objects"][index] = {"object_id": "u", "kind": "curve", "form": "graph2d",
                              "points": [[0, 0], [1, 1]], "x_range": [-1, 1]}
    reject(data, f"objects[{index}]")

    data = load("saddle-a1-h0-k0")
    data["objects"][index] = {"object_id": "u", "kind": "polyline",
                              "points": [[0, 0], [1, 1]]}
    reject(data, f"objects[{index}].kind")


def test_rejects_duplicate_object_id():
    data = load("saddle-a1-h0-k0")
    data["objects"].append(copy.deepcopy(data["objects"][0]))
    reject(data, "objects")


def test_rejects_section_without_surface():
    data = load("saddle-a1-h0-k0")
    index = next(i for i, o in enumerate(data["objects"]) if o["kind"] == "section")
    data["objects"][index]["surface"] = "missing"
    reject(data, f"objects[{index}].surface")


def test_rejects_action_referencing_missing_object():
    data = load("saddle-a1-h0-k0")
    data["steps"][0]["actions"][0]["object_id"] = "nope"
    reject(data, "steps[0].actions[0].object_id")


def test_rejects_unknown_action():
    data = load("saddle-a1-h0-k0")
    data["steps"][0]["actions"][0]["action"] = "execute_shell"
    reject(data, "steps[0].actions[0].action")


# ── 5. 时段 ────────────────────────────────────────────────────────────────
def test_rejects_negative_time():
    data = load("saddle-a1-h0-k0")
    data["steps"][0]["start"] = -1.0
    reject(data, "steps[0].start")


def test_rejects_overlapping_and_reversed_steps():
    data = load("saddle-a1-h0-k0")
    data["steps"][1]["start"] = 4.0
    data["steps"][1]["end"] = 12.0
    reject(data, "steps[1]")

    data = load("saddle-a1-h0-k0")
    data["steps"][2]["end"] = data["steps"][2]["start"]
    reject(data, "steps[2].end")


# ── 6. 上限 ────────────────────────────────────────────────────────────────
def test_rejects_too_many_objects():
    data = load("saddle-a1-h0-k0")
    while len(data["objects"]) <= MAX_OBJECTS:
        index = len(data["objects"])
        data["objects"].append({"object_id": f"extra{index}", "kind": "label",
                                "text": "多余标签", "at": [0, 0]})
    reject(data, "objects", str(MAX_OBJECTS))


def test_rejects_too_many_steps():
    data = load("saddle-a1-h0-k0")
    step = copy.deepcopy(data["steps"][0])
    while len(data["steps"]) <= MAX_STEPS:
        index = len(data["steps"])
        clone = copy.deepcopy(step)
        clone["step_id"] = f"extra{index}"
        clone["start"] = 100.0 + index * 10
        clone["end"] = 110.0 + index * 10
        data["steps"].append(clone)
    reject(data, "steps", str(MAX_STEPS))


def test_rejects_too_many_actions():
    data = load("saddle-a1-h0-k0")
    data["steps"][0]["actions"] = [{"action": "reveal", "object_id": "axes3d"}
                                   for _ in range(MAX_ACTIONS + 1)]
    reject(data, "steps[0].actions", str(MAX_ACTIONS))


def test_rejects_oversized_mesh():
    data = load("saddle-a1-h0-k0")
    index = next(i for i, o in enumerate(data["objects"]) if o["kind"] == "surface")
    data["objects"][index]["resolution"] = [MAX_MESH + 1, MAX_MESH + 1]
    reject(data, f"objects[{index}].resolution", str(MAX_MESH))


def test_rejects_overlong_label_and_caption():
    data = load("saddle-a1-h0-k0")
    index = next(i for i, o in enumerate(data["objects"]) if o["kind"] == "label")
    data["objects"][index]["text"] = "字" * (MAX_LABEL_CHARS + 1)
    reject(data, f"objects[{index}].text")

    data = load("saddle-a1-h0-k0")
    data["steps"][0]["caption"] = "字" * (MAX_CAPTION_CHARS + 1)
    reject(data, "steps[0].caption")


def test_rejects_overcomplex_ast_depth_and_nodes():
    deep = builders.V("x")
    for _ in range(MAX_AST_DEPTH + 2):
        deep = builders.ADD(deep, builders.C(1))
    data = load("saddle-a1-h0-k0")
    index = next(i for i, o in enumerate(data["objects"]) if o["kind"] == "surface")
    data["objects"][index]["expr"] = deep
    reject(data, f"objects[{index}].expr")

    # 平衡树：节点数超标但深度不大 —— 单独证明**节点上限**也会被查
    nodes = [builders.C(i) for i in range(MAX_AST_NODES + 40)]
    while len(nodes) > 1:
        nodes = [builders.ADD(nodes[i], nodes[i + 1]) if i + 1 < len(nodes) else nodes[i]
                 for i in range(0, len(nodes), 2)]
    data = load("saddle-a1-h0-k0")
    data["objects"][index]["expr"] = nodes[0]
    reject(data, f"objects[{index}].expr")


def test_rejects_bad_pow_exponent():
    for exponent in (-1, 5, 1.5):
        data = load("saddle-a1-h0-k0")
        index = next(i for i, o in enumerate(data["objects"]) if o["kind"] == "surface")
        data["objects"][index]["expr"] = builders.POW(builders.V("x"), exponent)
        reject(data, f"objects[{index}].expr")


def test_rejects_matrix_not_2x2_or_using_free_variable():
    data = load("matrix-apply")
    index = next(i for i, o in enumerate(data["objects"]) if o["kind"] == "matrix2d")
    data["objects"][index]["rows"] = [[builders.C(1)]] * 3
    reject(data, f"objects[{index}].rows")

    data = load("matrix-apply")
    data["objects"][index]["rows"] = [[builders.C(1), builders.V("x")],
                                      [builders.C(3), builders.C(4)]]
    reject(data, f"objects[{index}].rows")


def test_assertion_tolerance_cannot_loosen_math_tolerance():
    data = load("vector-sum")
    data["math_assertions"][0]["tolerance"] = 1e-3
    reject(data, "math_assertions[0].tolerance")


def test_caption_placeholder_must_reference_known_assertion_and_no_braces():
    data = load("saddle-a1-h0-k0")
    data["steps"][1]["caption"] = "u_xx={value:missing}"
    reject(data, "steps[1].caption")

    data = load("saddle-a1-h0-k0")
    data["steps"][1]["caption"] = "u_xx={x^2}"
    reject(data, "steps[1].caption")


def test_rejects_bad_profile():
    data = load("saddle-a1-h0-k0")
    data["render_profile"] = "cinematic"
    reject(data, "render_profile")


def test_rejects_missing_frozen_field():
    data = load("saddle-a1-h0-k0")
    del data["dependency_paths"]
    reject(data, "dependency_paths")


# ── 7. unsupported 不等于"换一道题" ────────────────────────────────────────
def test_unsupported_is_explicit_and_not_substituted():
    with pytest.raises(UnsupportedVisualSpec) as excinfo:
        parse_visual_spec({"unsupported": "三角与分段算子不在首期能力内"})
    assert "三角" in excinfo.value.reason
    assert not hasattr(excinfo.value, "spec"), "unsupported 不得伪造一份规格"


# ── 8. 规范化 hash ─────────────────────────────────────────────────────────
def test_normalized_hash_is_stable_across_representation_changes():
    original = load("saddle-a1-h0-k0")
    shuffled = json.loads(json.dumps(original))
    shuffled = {key: shuffled[key] for key in reversed(list(shuffled))}
    shuffled["objects"][0] = {key: shuffled["objects"][0][key]
                              for key in reversed(list(shuffled["objects"][0]))}
    shuffled["parameters"]["a"] = 1                       # 1.0 -> 1
    shuffled["objects"][2]["resolution"] = [24.0, 24.0]    # 24 -> 24.0
    shuffled["math_assertions"][0]["expected"] = 2         # 2.0 -> 2
    assert parse_visual_spec(original).spec_hash == parse_visual_spec(shuffled).spec_hash


def test_parameter_change_changes_hash():
    low = parse_visual_spec(load("saddle-a0.5-h0.5-k-0.5"))
    high = parse_visual_spec(load("saddle-a2-h0.5-k-0.5"))
    assert low.spec_hash != high.spec_hash


# ── 9. 字幕与导数由同一数学规格派生 ────────────────────────────────────────
def test_caption_and_derivative_derive_from_the_same_spec():
    from aiforstudy.blocks.visual.compiler import RenderProfile, compile_scene
    from aiforstudy.blocks.visual.math_check import verify_math

    for name, a in (("saddle-a0.5-h0.5-k-0.5", 0.5), ("saddle-a2-h0.5-k-0.5", 2.0)):
        spec = parse_visual_spec(load(name))
        scene = compile_scene(spec, RenderProfile.DRAFT)
        report = verify_math(spec)
        captions = "".join(scene.captions)
        assert f"u_xx={fmt(2.0 * a)}" in captions, captions
        check = report.by_assertion("uxx")
        assert check is not None and abs(check.actual - 2.0 * a) <= 1e-9
    assert "u_xx=1" in caption_of("saddle-a0.5-h0.5-k-0.5")
    assert "u_xx=4" in caption_of("saddle-a2-h0.5-k-0.5")


def test_caption_follows_math_not_the_claim():
    """改数学对象而不改断言：字幕必须跟着**数学**变，checker 必须拒绝不一致。"""
    from aiforstudy.blocks.visual.compiler import RenderProfile, compile_scene
    from aiforstudy.blocks.visual.math_check import verify_math

    data = load("saddle-a0.5-h0.5-k-0.5")
    index = next(i for i, o in enumerate(data["objects"]) if o["object_id"] == "u")
    data["objects"][index]["expr"] = builders.saddle_expr(3.0, 0.5, -0.5)
    spec = parse_visual_spec(data)
    scene = compile_scene(spec, RenderProfile.DRAFT)
    report = verify_math(spec)
    assert "u_xx=6" in "".join(scene.captions)      # 2*3
    assert report.status == "fail"


def caption_of(name: str) -> str:
    from aiforstudy.blocks.visual.compiler import RenderProfile, compile_scene
    return "".join(compile_scene(parse_visual_spec(load(name)), RenderProfile.DRAFT).captions)


def test_math_is_not_written_in_two_places():
    """同一份数学规格既派生字幕数值、又派生断言实际值 —— 不是两处各写一份。"""
    from aiforstudy.blocks.visual.compiler import RenderProfile, compile_scene
    from aiforstudy.blocks.visual.math_check import verify_math

    data = load("saddle-a2-h0.5-k-0.5")
    a = 2.0
    spec = parse_visual_spec(data)
    captions = "".join(compile_scene(spec, RenderProfile.DRAFT).captions)
    report = verify_math(spec)
    # 字幕里的 u_xx/u_yy/和，与独立检查算出的实际值来自同一份 AST
    assert f"u_xx={fmt(2.0 * a)}" in captions
    assert f"u_yy={fmt(-2.0 * a)}" in captions
    assert abs(report.by_assertion("uxx").actual - 2.0 * a) <= 1e-9
    assert abs(report.by_assertion("uyy").actual - (-2.0 * a)) <= 1e-9
    assert abs(report.by_assertion("lap").actual) <= 1e-9


def test_vector_and_matrix_samples_reuse_the_same_object_action_steps():
    """二维向量加法与 2x2 矩阵作用复用同一套对象／动作／步骤结构（不另开语言）。"""
    step_keys = {"step_id", "caption", "start", "end", "actions", "checkpoints",
                 "expected_change"}
    for name in ("saddle-a1-h0-k0", "vector-sum", "matrix-apply"):
        spec = parse_visual_spec(load(name))
        assert spec.steps, name
        for step in spec.steps:
            assert set(step) == step_keys, name
            for action in step["actions"]:
                assert action["action"] in ACTIONS, name
        assert all(obj["kind"] in KINDS for obj in spec.objects), name
    vector = parse_visual_spec(load("vector-sum"))
    matrix = parse_visual_spec(load("matrix-apply"))
    assert {obj["kind"] for obj in vector.objects} & \
        {obj["kind"] for obj in matrix.objects} >= {"axes2d", "vector", "point"}


def fmt(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.10g}"


def test_text_only_teaching_plan_needs_no_visual_request():
    data = builders.teaching_plan_spec()
    data['visual_requests'] = []
    assert parse_teaching_plan(data).visual_requests == ()


def test_retired_lesson_assembly_cannot_execute_historical_teaching_plan():
    from aiforstudy.pipeline.stage_io import _legacy
    from aiforstudy.errors import ContractError
    data = builders.teaching_plan_spec()
    assert parse_teaching_plan(data).unit_id == data['unit_id']
    with pytest.raises(ContractError,match='retired'):
        _legacy('generate')


@pytest.mark.parametrize('math_text', [
    '函数有两个自变量的写法，例如 $f(x,y)$',
    r'解析函数 $f=u+\mathrm{i}v$ 满足 $\frac{\partial u}{\partial x}=\frac{\partial v}{\partial y}$。',
    r'$f\prime(z)\neq 0$ 且 $x<y$ 时，观察 $\{u=K_1\}$。',
    r'数组 $\boldsymbol{a}=(a_1,\dots,a_n)$；张成空间 $\langle\boldsymbol{a}\rangle$。',
    r'向量 a∈S\\S1；标准形 $\begin{pmatrix}I_r&O\\O&O\end{pmatrix}$。',
    r'若 S1 是 S 的子向量组，对任意 a∈S\S1，S1∪{a} 线性相关。',
    r'$\dim V=n$，$\det A\ne0$，$V=W_1\oplus W_2$，$\tilde{x}\in V\setminus\{0\}$。',
    r'当 $\#S\geq 2$，考察向量组的元素个数。',
])
def test_teaching_plan_preserves_inert_math_prose(math_text):
    data = builders.teaching_plan_spec()
    data['prerequisites'] = [math_text]
    data['claims'][0]['text'] = math_text
    data['steps'][0]['explanation_anchor'] = math_text
    data['visual_requests'][0]['anchor'] = math_text
    result = parse_teaching_plan(data).as_dict()
    assert result == data


@pytest.mark.parametrize('bad', [r'\input{/etc/passwd}', r'\write18{whoami}',
    r'\begin{filecontents}{a.py}bad\end{filecontents}', r'\newcommand{\run}{bad}',
    '<script>alert(1)</script>', 'https://example.com', '../private', 'eval(1)', '\x00'])
def test_teaching_plan_still_rejects_active_payloads(bad):
    data = builders.teaching_plan_spec()
    data['objective'] = bad
    with pytest.raises(ContractError, match='objective'):
        parse_teaching_plan(data)


def test_teaching_plan_accepts_and_preserves_requested_media_provenance():
    data = builders.teaching_plan_spec()
    claim = data['claims'][0]['claim_id']
    data['visual_requests'][0].update(request_id='req-curves', source_claims=[claim],
                                     dependency_paths=['/teaching_plan/claims/0'])
    assert parse_teaching_plan(data).as_dict() == data


@pytest.mark.parametrize('field,value', [('source_claims', ['nonexistent']),
    ('dependency_paths', ['../secrets']), ('request_id', '../bad')])
def test_teaching_plan_validates_optional_media_provenance(field, value):
    data = builders.teaching_plan_spec()
    data['visual_requests'][0][field] = value
    with pytest.raises(ContractError, match=field):
        parse_teaching_plan(data)
