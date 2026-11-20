"""受限数学场景规格 `VisualSpecV1` 与教学计划 `TeachingPlanV1`（T06）。

## 这个模块存在的理由

旧声明式图机制（`blocks/figures/declarative.py`，**该模块已删除**：D10 清扫）只允许 800×500 像素的
line / circle / rect / polyline / text：模型必须**自己猜像素坐标**，没有坐标系、
函数、曲面语义。方案 §1.3 与 §4.2 的结论是：模型只给**数学对象与分镜**，
几何与画面由可信层算。

所以这里定义的规格：

* 数学对象（曲面／曲线／截面／向量／矩阵）**只携带受限 AST**，不携带像素点云；
* AST 首期只允许 const / x,y,t / add / sub / mul / 整数 pow(0–4) / 声明参数，
  没有表达式字符串、没有属性访问、没有除法奇点、没有三角与分段；
* 文字（标签、字幕）只能是纯文本加 `{value:<assertion_id>}` 占位符，
  值由可信层从**同一份数学规格**派生；禁止任意 TeX、路径、URL、Python；
* 上限（对象／步／动作／AST 深度与节点／网格／字数）超限即结构错误，
  并指出具体字段 —— **截断后声称合格是禁止的**。

`unsupported` 是显式终态：模型说"这个我表达不了"时抛 `UnsupportedVisualSpec`，
调用方应转 blocked，**不许换成另一道数学题**。

## 接口（冻结）

```python
parse_visual_spec(data: dict) -> VisualSpecV1
parse_teaching_plan(data: dict) -> TeachingPlanV1
```
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
import json
from ..media.presentation_policy import MAX_ANIMATION_STEPS, validate_duration
from typing import Any, Mapping

from ...core.digest import digest_of
from ...errors import ContractError

SCHEMA_VERSION = "1.0"

#: 计划 §5.3 的硬上限
MAX_OBJECTS = 32
MAX_STEPS = MAX_ANIMATION_STEPS
MAX_ACTIONS = 120
MAX_AST_DEPTH = 12
MAX_AST_NODES = 256
MAX_MESH = 64
MAX_LABEL_CHARS = 80
MAX_CAPTION_CHARS = 120
MAX_TEXT_CHARS = 200
MAX_PARAMETERS = 32
MAX_SOURCE_REFS = 32
MAX_DEPENDENCY_PATHS = 64
MAX_CURVE_SAMPLES = 512
MAX_ASSERTIONS = 32
MAX_COEFFICIENTS = 64
#: 数学断言容差上限：布局／像素误差**不能**混用这个容差
MATH_TOLERANCE = 1e-6
MAX_CAMERA_ZOOM = 10.0
MIN_CAMERA_ZOOM = 0.1
MAX_CAMERA_ANGLE = 360.0
MAX_PAUSE_SECONDS = 10.0

DIRECTION_VARS = ("x", "y", "t")
KINDS = ("axes2d", "axes3d", "point", "arrow", "vector", "plane", "curve", "surface",
         "section", "label", "formula", "matrix2d")
COLORS = ("ink", "blue", "red", "green", "gray")
ACTIONS = ("reveal", "hide", "transform", "highlight", "camera", "pause")
PROFILES = ("draft", "final")
ASSERTION_KINDS = ("partial_derivative", "derivative_sum", "polynomial",
                   "surface_not_planar", "section_is_trace", "vector_sum", "matrix_apply")
OPEN_MODES = ("up", "down", "flat")
FROZEN_FIELDS = ("schema_version", "request_id", "unit_id", "source_refs", "lesson_anchor",
                 "source_claims", "dependency_paths", "objective", "parameters",
                 "objects", "steps", "math_assertions", "render_profile")

_COMMON_OBJECT_KEYS = {"object_id", "kind", "label", "color"}
_KIND_KEYS = {
    "axes2d": {"x_range", "y_range", "x_label", "y_label"},
    "axes3d": {"x_range", "y_range", "z_range"},
    "point": {"space", "at"},
    "arrow": {"space", "start", "end"},
    "vector": {"space", "start", "end"},
    "plane": {"expr", "domain", "resolution"},
    "curve": {"form", "expr", "x_range", "t_range", "samples", "space"},
    "surface": {"expr", "domain", "resolution"},
    "section": {"surface", "variable", "at", "range", "samples"},
    "label": {"text", "at", "space"},
    "formula": {"expr"},
    "matrix2d": {"rows"},
}
_ANALYTIC_KINDS = ("curve", "surface", "plane")
_STEP_KEYS = {"step_id", "caption", "start", "end", "actions", "checkpoints",
              "expected_change"}
_ACTION_KEYS = {
    "reveal": {"action", "object_id"},
    "hide": {"action", "object_id"},
    "highlight": {"action", "object_id"},
    "transform": {"action", "object_id", "expr", "domain"},
    "camera": {"action", "zoom", "phi", "theta"},
    "pause": {"action", "seconds"},
}
_ASSERTION_KEYS = {
    "partial_derivative": {"assertion_id", "kind", "object_id", "variable", "order",
                           "at", "expected", "tolerance"},
    "derivative_sum": {"assertion_id", "kind", "object_id", "terms", "at", "expected",
                       "tolerance"},
    "polynomial": {"assertion_id", "kind", "object_id", "expected_coefficients"},
    "surface_not_planar": {"assertion_id", "kind", "object_id"},
    "section_is_trace": {"assertion_id", "kind", "section", "surface", "variable", "opens"},
    "vector_sum": {"assertion_id", "kind", "operands", "result_object", "expected",
                   "tolerance"},
    "matrix_apply": {"assertion_id", "kind", "matrix", "vector", "result_object",
                     "expected", "tolerance"},
}
_AST_KEYS = {
    "const": {"op", "value"},
    "var": {"op", "name"},
    "add": {"op", "left", "right"},
    "sub": {"op", "left", "right"},
    "mul": {"op", "left", "right"},
    "pow": {"op", "base", "exponent"},
}

_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
#: request_id 允许连字符／点（"vis-saddle-01" 这类稳定名），但仍禁止路径与协议
_REQUEST_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{0,95}$")
_PARAM_RE = re.compile(r"^[a-z][a-z0-9_]{0,15}$")
_MONOMIAL_RE = re.compile(r"^(?:1|[a-z](?:\^[0-4])?(?:\*[a-z](?:\^[0-4])?)*)$")
_PLACEHOLDER_RE = re.compile(r"\{value:([A-Za-z][A-Za-z0-9_]{0,31})\}")

#: 任何文本里都不允许出现的东西
_FORBIDDEN_TEXT = (
    ("\\", "反斜杠（TeX 命令／Windows 路径）"),
    ("://", "URL"),
    ("__", "Python 双下划线"),
    ("`", "反引号代码"),
    ("$", "TeX 数学定界符"),
    ("<", "尖括号／标签"),
    (">", "尖括号／标签"),
)
_PYTHON_HINTS = ("import ", "eval(", "exec(", "open(", "lambda", "os.system", "subprocess",
                 "pickle", "shutil")


class UnsupportedVisualSpec(ContractError):
    """模型明确表示"这个场景我表达不了"。

    这不是格式错误，而是**能力边界**：调用方应把它变成 `unsupported`／blocked，
    **绝不能**顺手换一张"长得差不多"的图 —— 读者看到的图就是教学内容。
    """

    def __init__(self, reason: str):
        self.reason = str(reason).strip() or "unspecified"
        super().__init__(f"场景能力不支持：{self.reason}（不得替换成另一道题）")


# ── 基础校验小工具 ─────────────────────────────────────────────────────────
def _fail(path: str, message: str) -> None:
    raise ContractError(f"{path}: {message}")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _number(value: Any, path: str) -> float:
    if not _is_number(value):
        _fail(path, "必须是有限数值")
    result = float(value)
    if not math.isfinite(result):
        _fail(path, "必须是有限数值（NaN／inf 一律不允许）")
    return result


def _norm_number(value: Any, path: str):
    """规范化数值：整数值存成 int，`-0.0` 归零 —— 让 2 与 2.0 得到同一个 hash。"""
    result = _number(value, path)
    if result == 0.0:
        return 0
    if result.is_integer() and abs(result) < 1e15:
        return int(result)
    return result


def _integer(value: Any, path: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(path, f"必须是 {low}–{high} 的整数")
    if float(value) != int(value):
        _fail(path, f"必须是 {low}–{high} 的整数")
    result = int(value)
    if not low <= result <= high:
        _fail(path, f"必须是 {low}–{high} 的整数，实际 {result}")
    return result


def _looks_like_path(text: str) -> bool:
    if text.startswith(("/", "~")):
        return True
    if "../" in text or "..\\" in text:
        return True
    return bool(re.match(r"^[A-Za-z]:[\\/]", text))


def _text(value: Any, path: str, *, limit: int = MAX_TEXT_CHARS,
          allow_placeholders: bool = False, required: bool = True) -> tuple[str, list[str]]:
    """纯文本校验。返回（文本，引用到的 assertion_id 列表）。"""
    if not isinstance(value, str):
        _fail(path, "必须是文本")
    text = value if not required else value.strip()
    if required and not text:
        _fail(path, "不能为空")
    if len(value) > limit:
        _fail(path, f"超过 {limit} 字上限（实际 {len(value)}）")
    for bad, why in _FORBIDDEN_TEXT:
        if bad in text:
            _fail(path, f"出现不允许的内容：{why}")
    for hint in _PYTHON_HINTS:
        if hint in text:
            _fail(path, f"出现不允许的内容：{hint!r}")
    if _looks_like_path(text):
        _fail(path, "出现不允许的路径写法")
    if any(ord(ch) < 32 for ch in text):
        _fail(path, "出现控制字符")
    references: list[str] = []
    if allow_placeholders:
        stripped = _PLACEHOLDER_RE.sub("", text)
        if "{" in stripped or "}" in stripped:
            _fail(path, "只允许 {value:<assertion_id>} 占位符，不允许其它花括号")
        references = [match.group(1) for match in _PLACEHOLDER_RE.finditer(text)]
    elif "{" in text or "}" in text:
        _fail(path, "不允许花括号")
    return text, references


# TeachingPlan prose is inert context, never a Manim label or TeX program.
# Preserve standard mathematical notation while keeping VisualSpec's stricter
# executable/renderable text contract unchanged.
_PLAN_MATH_COMMANDS = frozenset("""
mathrm mathbf boldsymbol mathit mathcal mathbb operatorname text frac dfrac tfrac sqrt
partial nabla Delta delta alpha beta gamma theta phi varphi rho sigma tau
pi epsilon varepsilon zeta eta lambda mu nu omega Omega Gamma Sigma
bar overline underline hat widehat vec dot ddot prime int iint iiint oint
sum prod lim min max sup inf sin cos tan cot sinh cosh log ln exp
left right big Big bigg Bigg le leq leqslant ge geq geqslant ne neq
approx equiv sim simeq propto in notin subset subseteq supset supseteq
cup cap emptyset forall exists infty cdot times div pm mp circ
rightarrow leftarrow Rightarrow Leftarrow leftrightarrow Leftrightarrow
to mapsto ell Re Im lvert rvert vert lVert rVert Vert langle rangle dots ldots cdots
underbrace overbrace limits nolimits quad qquad displaystyle
dim det ker hom rank tilde widetilde mid vdots ddots binom xrightarrow bf S
oplus backslash setminus overrightarrow longrightarrow not square tag
""".split())
_PLAN_MATH_ENVIRONMENTS = frozenset({'matrix', 'pmatrix', 'bmatrix', 'Bmatrix',
    'vmatrix', 'Vmatrix', 'array', 'aligned', 'alignedat', 'gathered', 'cases'})


def _plan_text(value: Any, path: str, *, limit: int = MAX_TEXT_CHARS) -> tuple[str, list[str]]:
    if not isinstance(value, str):
        _fail(path, "必须是文本")
    if len(value) > limit:
        _fail(path, f"超过 {limit} 字上限（实际 {len(value)}）")
    if re.search(r"</?[A-Za-z][^>]*>", value):
        _fail(path, "不允许 HTML 标签")
    if _looks_like_path(value):
        _fail(path, "出现不允许的路径写法")
    def math_environment(match):
        if match.group(2) not in _PLAN_MATH_ENVIRONMENTS:
            _fail(path, f"不允许的数学环境：{match.group(2)}")
        return "math"
    checked = re.sub(r"\\(begin|end)\{([^{}]+)\}", math_environment, value)
    # Consume matrix row separators before command lexing: \\O is a row break
    # followed by the zero-block symbol O, not a command named O.
    checked = checked.replace("\\\\", " ")
    def math_command(match):
        command = match.group(1)
        if command not in _PLAN_MATH_COMMANDS:
            _fail(path, f"不允许的数学命令：{command}")
        return "math"
    checked = re.sub(r"\\([A-Za-z]+)", math_command, checked)
    checked = re.sub(r"\\[#{},;! ()\[\]|]", " ", checked)
    checked = checked.translate(str.maketrans({c: " " for c in "$<>{}"}))
    _text(checked, path, limit=max(limit, len(checked)))
    return value.strip(), []


def _rel_path(value: Any, path: str) -> str:
    text, _ = _text(value, path, limit=MAX_TEXT_CHARS)
    if ":" in text:
        _fail(path, "不允许盘符或协议")
    if text.startswith(("/", "~")) or "../" in text or "..\\" in text:
        _fail(path, "只允许不越界的相对路径")
    return text


def _dependency_path(value: Any, path: str) -> str:
    # JSON Pointer denotes lesson data, never an OS path. Legacy relative
    # identifiers remain readable for frozen fixtures and stored specs.
    if isinstance(value, str) and value.startswith("/"):
        if len(value) > MAX_TEXT_CHARS or not re.fullmatch(r"(?:/[A-Za-z0-9_~-]+)+", value) or re.search(r"~(?![01])", value):
            _fail(path, "必须是合法的教案 JSON Pointer")
        if value.split('/')[1] not in {'body_markdown','explanation_markdown','teaching_plan','annotations','examples','explainables','figures','figure_requests','concepts','diagrams','models'}:
            _fail(path, "JSON Pointer 必须指向教案内容字段")
        return value
    return _rel_path(value, path)


def _identifier(value: Any, path: str, pattern: re.Pattern) -> str:
    if not isinstance(value, str) or not pattern.match(value):
        _fail(path, "必须是字母开头的稳定标识符")
    return value


def _mapping(value: Any, path: str) -> dict:
    if not isinstance(value, dict):
        _fail(path, "必须是对象")
    return value


def _sequence(value: Any, path: str, *, low: int, high: int) -> list:
    if not isinstance(value, list):
        _fail(path, "必须是数组")
    if not low <= len(value) <= high:
        _fail(path, f"元素数必须在 {low}–{high} 之间，实际 {len(value)}")
    return value


# ── 受限 AST ───────────────────────────────────────────────────────────────
def normalize_ast(node: Any, path: str, *, allowed_names: set[str]) -> tuple[dict, int, int]:
    """把模型给的 AST 规范化。返回（规范 AST，深度，节点数）。"""
    if isinstance(node, str):
        _fail(path, "数学表达式必须是受限 AST，不接受表达式字符串")
    if not isinstance(node, dict):
        _fail(path, "数学表达式必须是受限 AST 对象")
    op = node.get("op")
    if op not in _AST_KEYS:
        _fail(path, f"不支持的算子 {op!r}（首期只允许 const/var/add/sub/mul/pow）")
    extra = set(node) - _AST_KEYS[op]
    if extra:
        _fail(path, f"算子 {op!r} 出现不允许的字段 {sorted(extra)}")
    if op == "const":
        if "value" not in node:
            _fail(path, "const 缺少 value")
        return {"op": "const", "value": _norm_number(node["value"], f"{path}.value")}, 1, 1
    if op == "var":
        name = node.get("name")
        if name not in allowed_names:
            _fail(path, f"变量 {name!r} 未声明（可用：{sorted(allowed_names)}）")
        return {"op": "var", "name": name}, 1, 1
    if op == "pow":
        base, depth, nodes = normalize_ast(node.get("base"), f"{path}.base",
                                           allowed_names=allowed_names)
        exponent = _integer(node.get("exponent"), f"{path}.exponent", 0, 4)
        return {"op": "pow", "base": base, "exponent": exponent}, depth + 1, nodes + 1
    left, ldepth, lnodes = normalize_ast(node.get("left"), f"{path}.left",
                                         allowed_names=allowed_names)
    right, rdepth, rnodes = normalize_ast(node.get("right"), f"{path}.right",
                                          allowed_names=allowed_names)
    return ({"op": op, "left": left, "right": right},
            1 + max(ldepth, rdepth), 1 + lnodes + rnodes)


def _ast(node: Any, path: str, *, params: set[str],
         free_vars: tuple[str, ...] = DIRECTION_VARS) -> dict:
    allowed = set(free_vars) | set(params)
    try:
        normalized, depth, nodes = normalize_ast(node, path, allowed_names=allowed)
    except RecursionError:
        _fail(path, "AST 嵌套过深")
    if depth > MAX_AST_DEPTH:
        _fail(path, f"AST 深度 {depth} 超过上限 {MAX_AST_DEPTH}")
    if nodes > MAX_AST_NODES:
        _fail(path, f"AST 节点数 {nodes} 超过上限 {MAX_AST_NODES}")
    return normalized


def ast_names(node: dict) -> set[str]:
    """规范 AST 里用到的名字（变量／参数）。"""
    op = node["op"]
    if op == "const":
        return set()
    if op == "var":
        return {node["name"]}
    if op == "pow":
        return ast_names(node["base"])
    return ast_names(node["left"]) | ast_names(node["right"])


def eval_const_ast(node: dict, parameters: Mapping[str, float]) -> float:
    """求值一个只含 const／声明参数的 AST（用于有限性检查，不做任何导数）。"""
    op = node["op"]
    if op == "const":
        return float(node["value"])
    if op == "var":
        try:
            return float(parameters[node["name"]])
        except KeyError:  # pragma: no cover - 校验期已保证存在
            raise ContractError(f"参数 {node['name']!r} 未声明") from None
    if op == "pow":
        base = eval_const_ast(node["base"], parameters)
        return float(base ** node["exponent"])
    left = eval_const_ast(node["left"], parameters)
    right = eval_const_ast(node["right"], parameters)
    if op == "add":
        return left + right
    if op == "sub":
        return left - right
    return left * right


def _const_ast(node: Any, path: str, *, params: set[str]) -> dict:
    """只允许 const／声明参数的 AST（点、向量、矩阵、截面固定值）。"""
    normalized = _ast(node, path, params=params, free_vars=())
    return normalized


def _coord_ast(value: Any, path: str, *, params: set[str]) -> dict:
    if _is_number(value):
        return {"op": "const", "value": _norm_number(value, path)}
    return _const_ast(value, path, params=params)


def _coord_list(value: Any, path: str, *, params: set[str], length: int) -> list:
    if not isinstance(value, list):
        _fail(path, "必须是坐标数组")
    if len(value) != length:
        _fail(path, f"必须是 {length} 维坐标，实际 {len(value)} 维")
    return [_coord_ast(item, f"{path}[{i}]", params=params) for i, item in enumerate(value)]


def _range(value: Any, path: str) -> list:
    if not isinstance(value, list) or len(value) != 2:
        _fail(path, "必须是 [下界, 上界]")
    low = _number(value[0], f"{path}[0]")
    high = _number(value[1], f"{path}[1]")
    if not low < high:
        _fail(path, "下界必须小于上界")
    return [_norm_number(low, f"{path}[0]"), _norm_number(high, f"{path}[1]")]


def _domain(value: Any, path: str, *, params: set[str]) -> dict:
    data = _mapping(value, path)
    extra = set(data) - {"x", "y"}
    if extra:
        _fail(path, f"不允许的字段 {sorted(extra)}")
    if set(data) != {"x", "y"}:
        _fail(path, "必须同时给出 x 与 y 的范围")
    return {"x": _range(data["x"], f"{path}.x"), "y": _range(data["y"], f"{path}.y")}


def _resolution(value: Any, path: str) -> list:
    if value is None:
        return [16, 16]
    if not isinstance(value, list) or len(value) != 2:
        _fail(path, "必须是 [nx, ny]")
    return [_integer(value[0], f"{path}[0]", 1, MAX_MESH),
            _integer(value[1], f"{path}[1]", 1, MAX_MESH)]


def _samples(value: Any, path: str, default: int = 64) -> int:
    if value is None:
        return default
    return _integer(value, path, 2, MAX_CURVE_SAMPLES)


# ── 数据类 ─────────────────────────────────────────────────────────────────
def target_expression(spec: VisualSpecV1, object_id: str) -> Mapping[str, Any]:
    """Locate a differentiable expression; each consumer verifies its own math."""
    obj = spec.object_by_id(object_id)
    if obj['kind'] in ('surface', 'plane', 'formula'):
        return obj['expr']
    raise ContractError(f"对象 {object_id!r} 没有可求导的表达式")


@dataclass(frozen=True)
class VisualSpecV1:
    """受限场景规格。字段与计划 §5.3 的固定列表逐字对应。"""

    schema_version: str
    request_id: str
    unit_id: str
    source_refs: tuple[str, ...]
    lesson_anchor: str
    source_claims: tuple[str, ...]
    dependency_paths: tuple[str, ...]
    objective: str
    parameters: Mapping[str, float]
    objects: tuple[dict, ...]
    steps: tuple[dict, ...]
    math_assertions: tuple[dict, ...]
    render_profile: str

    def object_by_id(self, object_id: str) -> dict:
        for obj in self.objects:
            if obj["object_id"] == object_id:
                return obj
        raise ContractError(f"找不到对象 {object_id!r}")

    def assertion_by_id(self, assertion_id: str) -> dict:
        for item in self.math_assertions:
            if item["assertion_id"] == assertion_id:
                return item
        raise ContractError(f"找不到数学断言 {assertion_id!r}")

    def as_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "unit_id": self.unit_id,
            "source_refs": list(self.source_refs),
            "lesson_anchor": self.lesson_anchor,
            "source_claims": list(self.source_claims),
            "dependency_paths": list(self.dependency_paths),
            "objective": self.objective,
            "parameters": dict(self.parameters),
            "objects": [dict(obj) for obj in self.objects],
            "steps": [dict(step) for step in self.steps],
            "math_assertions": [dict(item) for item in self.math_assertions],
            "render_profile": self.render_profile,
        }

    @property
    def spec_hash(self) -> str:
        return digest_of(self.as_dict())


@dataclass(frozen=True)
class TeachingPlanV1:
    """教学计划：与 lesson 首次生成合并产出，不额外增加一次收费调用。"""

    unit_id: str
    source_hash: str
    objective: str
    prerequisites: tuple[str, ...]
    claims: tuple[dict, ...]
    steps: tuple[dict, ...]
    visual_requests: tuple[dict, ...]

    def claim_ids(self) -> set[str]:
        return {str(claim.get("claim_id")) for claim in self.claims}

    def as_dict(self) -> dict:
        return {"unit_id": self.unit_id, "source_hash": self.source_hash,
                "objective": self.objective, "prerequisites": list(self.prerequisites),
                "claims": [dict(c) for c in self.claims],
                "steps": [dict(s) for s in self.steps],
                "visual_requests": [dict(v) for v in self.visual_requests]}


# ── 解析入口 ───────────────────────────────────────────────────────────────
def parse_visual_spec(data: dict) -> VisualSpecV1:
    """解析并**完整校验**受限场景规格；任何非法输入都定位到具体字段。"""
    if not isinstance(data, dict):
        _fail("spec", "必须是对象")
    if data.get("schema_version") == "2.0":
        return parse_visual_spec_v2(data)
    if "unsupported" in data:
        raise UnsupportedVisualSpec(data.get("unsupported"))
    missing = [key for key in FROZEN_FIELDS if key not in data]
    if missing:
        _fail("spec", f"缺少固定字段 {missing}")
    extra = sorted(set(data) - set(FROZEN_FIELDS))
    if extra:
        _fail("spec", f"出现未定义字段 {extra}")

    if data["schema_version"] != SCHEMA_VERSION:
        _fail("schema_version", f"只支持 {SCHEMA_VERSION!r}，实际 {data['schema_version']!r}")
    request_id_text, _ = _text(data["request_id"], "request_id", limit=96)
    if not _REQUEST_ID_RE.match(request_id_text):
        _fail("request_id", "必须是字母开头的稳定标识符")
    request_id = request_id_text
    unit_id, _ = _text(data["unit_id"], "unit_id", limit=64)
    lesson_anchor, _ = _text(data["lesson_anchor"], "lesson_anchor", limit=MAX_TEXT_CHARS)
    objective, _ = _text(data["objective"], "objective", limit=MAX_TEXT_CHARS)
    if data["render_profile"] not in PROFILES:
        _fail("render_profile", f"只能是 {list(PROFILES)}，实际 {data['render_profile']!r}")

    source_refs = _sequence(data["source_refs"], "source_refs", low=0, high=MAX_SOURCE_REFS)
    source_refs = tuple(_text(item, f"source_refs[{i}]", limit=MAX_TEXT_CHARS)[0]
                        for i, item in enumerate(source_refs))
    source_claims = _sequence(data["source_claims"], "source_claims", low=0,
                              high=MAX_SOURCE_REFS)
    source_claims = tuple(_text(item, f"source_claims[{i}]", limit=MAX_TEXT_CHARS)[0]
                          for i, item in enumerate(source_claims))
    dependency_paths = _sequence(data["dependency_paths"], "dependency_paths", low=0,
                                 high=MAX_DEPENDENCY_PATHS)
    dependency_paths = tuple(_dependency_path(item, f"dependency_paths[{i}]")
                             for i, item in enumerate(dependency_paths))

    parameters_raw = _mapping(data["parameters"], "parameters")
    if len(parameters_raw) > MAX_PARAMETERS:
        _fail("parameters", f"参数个数超过上限 {MAX_PARAMETERS}")
    parameters: dict[str, Any] = {}
    for name, value in parameters_raw.items():
        if not _PARAM_RE.match(str(name)):
            _fail("parameters", f"参数名 {name!r} 不合法（小写字母开头）")
        if name in DIRECTION_VARS:
            _fail("parameters", f"参数名 {name!r} 与方向变量冲突")
        parameters[name] = _norm_number(value, f"parameters.{name}")

    for field_name in ("objects", "steps", "math_assertions"):
        if not isinstance(data[field_name], list):
            _fail(field_name, "必须是数组")

    spec = VisualSpecV1(
        schema_version=SCHEMA_VERSION,
        request_id=request_id,
        unit_id=unit_id,
        source_refs=source_refs,
        lesson_anchor=lesson_anchor,
        source_claims=source_claims,
        dependency_paths=dependency_paths,
        objective=objective,
        parameters=parameters,
        objects=tuple(data["objects"]),
        steps=tuple(data["steps"]),
        math_assertions=tuple(data["math_assertions"]),
        render_profile=data["render_profile"],
    )
    return validate_spec_structure(spec)


def validate_spec_structure(spec: VisualSpecV1) -> VisualSpecV1:
    """结构校验 + 规范化。解析与编译都走这里，伪造的数据类也逃不掉。"""
    if isinstance(spec, VisualSpecV2):
        return parse_visual_spec_v2(spec.as_dict())
    if not isinstance(spec, VisualSpecV1):
        _fail("spec", "必须是 VisualSpecV1 或 VisualSpecV2")
    params = set(spec.parameters)

    if len(spec.objects) > MAX_OBJECTS:
        _fail("objects", f"对象数 {len(spec.objects)} 超过上限 {MAX_OBJECTS}")
    if len(spec.steps) > MAX_STEPS:
        _fail("steps", f"步数 {len(spec.steps)} 超过上限 {MAX_STEPS}")
    if len(spec.math_assertions) > MAX_ASSERTIONS:
        _fail("math_assertions", f"断言数超过上限 {MAX_ASSERTIONS}")

    assertions = tuple(_assertion(item, i, params=params, spec=spec)
                       for i, item in enumerate(spec.math_assertions))
    assertion_ids = {item["assertion_id"] for item in assertions}
    if len(assertion_ids) != len(assertions):
        _fail("math_assertions", "assertion_id 必须唯一")

    objects = tuple(_object(item, i, params=params, spec=spec)
                    for i, item in enumerate(spec.objects))
    object_ids = {obj["object_id"] for obj in objects}
    if len(object_ids) != len(objects):
        _fail("objects", "object_id 必须唯一")
    if not objects:
        _fail("objects", "至少需要一个对象")

    by_id = {obj["object_id"]: obj for obj in objects}
    steps = tuple(_step(item, i, params=params, object_ids=object_ids,
                        by_id=by_id, assertion_ids=assertion_ids)
                  for i, item in enumerate(spec.steps))
    total_actions = sum(len(step["actions"]) for step in steps)
    if total_actions > MAX_ACTIONS:
        _fail("steps", f"动作总数 {total_actions} 超过上限 {MAX_ACTIONS}")
    if len({step["step_id"] for step in steps}) != len(steps):
        _fail("steps", "step_id 必须唯一")
    for i, step in enumerate(steps):
        if i and step["start"] < steps[i - 1]["end"]:
            _fail(f"steps[{i}]",
                  f"时段与上一步重叠或倒序（{step['start']} < {steps[i - 1]['end']}）")

    # 标签里的占位符也要指向真实断言
    for i, obj in enumerate(objects):
        if obj["kind"] == "label":
            for reference in _PLACEHOLDER_RE.findall(obj["text"]):
                if reference not in assertion_ids:
                    _fail(f"objects[{i}].text",
                          f"占位符引用了不存在的数学断言 {reference!r}")

    # 截面必须切在真实存在的曲面上
    for i, obj in enumerate(objects):
        if obj["kind"] != "section":
            continue
        target = by_id.get(obj["surface"])
        if target is None:
            _fail(f"objects[{i}].surface", f"引用了不存在的曲面 {obj['surface']!r}")
        if target["kind"] not in ("surface", "plane"):
            _fail(f"objects[{i}].surface",
                  f"{obj['surface']!r} 是 {target['kind']}，截面需要 surface 或 plane")

    # 数学断言引用到的对象必须在册（放在对象建好之后，报错才定位得准）
    for i, item in enumerate(assertions):
        _assertion_references(item, i, by_id=by_id)

    return replace(spec, parameters=dict(spec.parameters), objects=objects, steps=steps,
                   math_assertions=assertions)


# ── 对象 ───────────────────────────────────────────────────────────────────
def _object(item: Any, index: int, *, params: set[str], spec: VisualSpecV1) -> dict:
    path = f"objects[{index}]"
    data = _mapping(item, path)
    kind = data.get("kind")
    if kind not in KINDS:
        _fail(f"{path}.kind", f"未知对象类型 {kind!r}（首期支持 {list(KINDS)}）")
    extra = set(data) - _COMMON_OBJECT_KEYS - _KIND_KEYS[kind]
    if extra:
        _fail(path, f"对象类型 {kind!r} 不允许字段 {sorted(extra)}")
    object_id = _identifier(data.get("object_id"), f"{path}.object_id", _ID_RE)
    result: dict[str, Any] = {"object_id": object_id, "kind": kind}
    if "label" in data:
        result["label"] = _text(data["label"], f"{path}.label", limit=MAX_LABEL_CHARS)[0]
    if "color" in data:
        if data["color"] not in COLORS:
            _fail(f"{path}.color", f"颜色只能是 {list(COLORS)}")
        result["color"] = data["color"]

    if kind == "axes2d":
        result["x_range"] = _range(data.get("x_range"), f"{path}.x_range")
        result["y_range"] = _range(data.get("y_range"), f"{path}.y_range")
        for axis in ("x_label", "y_label"):
            if axis in data:
                result[axis] = _text(data[axis], f"{path}.{axis}", limit=MAX_LABEL_CHARS)[0]
    elif kind == "axes3d":
        for axis in ("x_range", "y_range", "z_range"):
            result[axis] = _range(data.get(axis), f"{path}.{axis}")
    elif kind in ("point", "vector", "arrow"):
        space = data.get("space")
        if space not in ("math2d", "math3d"):
            _fail(f"{path}.space", "必须是 math2d 或 math3d")
        result["space"] = space
        dims = 2 if space == "math2d" else 3
        if kind == "point":
            result["at"] = _coord_list(data.get("at"), f"{path}.at", params=params,
                                       length=dims)
        else:
            if "start" in data:
                result["start"] = _coord_list(data["start"], f"{path}.start", params=params,
                                              length=dims)
            if "end" not in data:
                _fail(f"{path}.end", "必须给出终点")
            result["end"] = _coord_list(data["end"], f"{path}.end", params=params,
                                        length=dims)
    elif kind in ("surface", "plane"):
        result["expr"] = _ast(data.get("expr"), f"{path}.expr", params=params,
                              free_vars=("x", "y"))
        result["domain"] = _domain(data.get("domain"), f"{path}.domain", params=params)
        result["resolution"] = _resolution(data.get("resolution"), f"{path}.resolution")
    elif kind == "curve":
        form = data.get("form")
        if form == "graph2d":
            if set(data) - _COMMON_OBJECT_KEYS - {"form", "expr", "x_range", "samples"}:
                _fail(path, "graph2d 曲线不允许 space／t_range 字段")
            result["form"] = "graph2d"
            result["expr"] = _ast(data.get("expr"), f"{path}.expr", params=params,
                                  free_vars=("x",))
            result["x_range"] = _range(data.get("x_range"), f"{path}.x_range")
        elif form == "parametric":
            if set(data) - _COMMON_OBJECT_KEYS - {"form", "expr", "t_range", "samples",
                                                  "space"}:
                _fail(path, "parametric 曲线不允许 x_range 字段")
            space = data.get("space")
            if space not in ("math2d", "math3d"):
                _fail(f"{path}.space", "必须是 math2d 或 math3d")
            result["space"] = space
            components = _mapping(data.get("expr"), f"{path}.expr")
            wanted = {"x", "y"} | ({"z"} if space == "math3d" else set())
            if set(components) != wanted:
                _fail(f"{path}.expr", f"参数曲线必须给出分量 {sorted(wanted)}")
            result["form"] = "parametric"
            result["expr"] = {name: _ast(components[name], f"{path}.expr.{name}",
                                         params=params, free_vars=("t",))
                              for name in sorted(wanted)}
            result["t_range"] = _range(data.get("t_range"), f"{path}.t_range")
        else:
            _fail(f"{path}.form", "曲线形式只能是 graph2d 或 parametric")
        result["samples"] = _samples(data.get("samples"), f"{path}.samples")
    elif kind == "section":
        result["surface"] = _identifier(data.get("surface"), f"{path}.surface", _ID_RE)
        variable = data.get("variable")
        if variable not in ("x", "y"):
            _fail(f"{path}.variable", "被固定的变量只能是 x 或 y")
        result["variable"] = variable
        result["at"] = _const_ast(data.get("at"), f"{path}.at", params=params)
        result["range"] = _range(data.get("range"), f"{path}.range")
        result["samples"] = _samples(data.get("samples"), f"{path}.samples")
    elif kind == "label":
        text, _ = _text(data.get("text"), f"{path}.text", limit=MAX_LABEL_CHARS,
                        allow_placeholders=True)
        result["text"] = text
        space = data.get("space", "math2d")
        if space not in ("math2d", "math3d"):
            _fail(f"{path}.space", "必须是 math2d 或 math3d")
        result["space"] = space
        dims = 2 if space == "math2d" else 3
        result["at"] = _coord_list(data.get("at", [0, 0]), f"{path}.at", params=params,
                                   length=dims)
    elif kind == "formula":
        result["expr"] = _ast(data.get("expr"), f"{path}.expr", params=params,
                              free_vars=("x", "y"))
    else:  # matrix2d
        rows = data.get("rows")
        if not isinstance(rows, list) or len(rows) != 2 or \
                any(not isinstance(row, list) or len(row) != 2 for row in rows):
            _fail(f"{path}.rows", "matrix2d 只允许 2x2")
        normalized = []
        for r, row in enumerate(rows):
            normalized.append([_const_ast(row[c], f"{path}.rows[{r}][{c}]", params=params)
                               for c in range(2)])
        for r, row in enumerate(normalized):
            for c, entry in enumerate(row):
                _number(eval_const_ast(entry, spec.parameters),
                        f"{path}.rows[{r}][{c}]")
        result["rows"] = normalized
    return result


# ── 步与动作 ───────────────────────────────────────────────────────────────
def _step(item: Any, index: int, *, params: set[str], object_ids: set[str],
          by_id: dict, assertion_ids: set[str]) -> dict:
    path = f"steps[{index}]"
    data = _mapping(item, path)
    extra = set(data) - _STEP_KEYS
    if extra:
        _fail(path, f"步骤不允许字段 {sorted(extra)}")
    for key in _STEP_KEYS:
        if key not in data:
            _fail(f"{path}.{key}", "必填字段缺失")
    step_id = _identifier(data["step_id"], f"{path}.step_id", _ID_RE)
    caption, references = _text(data["caption"], f"{path}.caption",
                                limit=MAX_CAPTION_CHARS, allow_placeholders=True)
    expected_change, _ = _text(data["expected_change"], f"{path}.expected_change",
                               limit=MAX_TEXT_CHARS)
    start = _number(data["start"], f"{path}.start")
    end = _number(data["end"], f"{path}.end")
    if start < 0:
        _fail(f"{path}.start", "时段必须非负")
    if end <= start:
        _fail(f"{path}.end", f"结束时间必须大于开始时间（{start} → {end}）")

    actions = _sequence(data["actions"], f"{path}.actions", low=0, high=MAX_ACTIONS)
    normalized_actions = [_action(action, j, path=f"{path}.actions[{j}]", params=params,
                                 object_ids=object_ids, by_id=by_id)
                          for j, action in enumerate(actions)]

    checkpoints = _sequence(data["checkpoints"], f"{path}.checkpoints", low=0, high=16)
    normalized_checkpoints = []
    for j, checkpoint in enumerate(checkpoints):
        cpath = f"{path}.checkpoints[{j}]"
        cdata = _mapping(checkpoint, cpath)
        if set(cdata) - {"at", "note"}:
            _fail(cpath, f"检查点不允许字段 {sorted(set(cdata) - {'at', 'note'})}")
        at = _number(cdata.get("at"), f"{cpath}.at")
        if not start <= at <= end:
            _fail(f"{cpath}.at", f"必须落在本步时段 [{start}, {end}] 内")
        entry = {"at": _norm_number(at, f"{cpath}.at")}
        if "note" in cdata:
            entry["note"] = _text(cdata["note"], f"{cpath}.note", limit=MAX_LABEL_CHARS)[0]
        normalized_checkpoints.append(entry)

    for reference in references:
        if reference not in assertion_ids:
            _fail(f"{path}.caption", f"占位符引用了不存在的数学断言 {reference!r}")
    _action_references({"actions": normalized_actions}, index, object_ids)
    return {"step_id": step_id, "caption": caption, "start": _norm_number(start, f"{path}.start"),
            "end": _norm_number(end, f"{path}.end"), "actions": normalized_actions,
            "checkpoints": normalized_checkpoints, "expected_change": expected_change}


def _action(item: Any, index: int, *, path: str, params: set[str], object_ids: set[str],
            by_id: dict) -> dict:
    data = _mapping(item, path)
    kind = data.get("action")
    if kind not in ACTIONS:
        _fail(f"{path}.action", f"未知动作 {kind!r}（允许 {list(ACTIONS)}）")
    extra = set(data) - _ACTION_KEYS[kind]
    if extra:
        _fail(path, f"动作 {kind!r} 不允许字段 {sorted(extra)}")
    result: dict[str, Any] = {"action": kind}
    if kind in ("reveal", "hide", "highlight"):
        result["object_id"] = _identifier(data.get("object_id"), f"{path}.object_id", _ID_RE)
    elif kind == "transform":
        object_id = _identifier(data.get("object_id"), f"{path}.object_id", _ID_RE)
        result["object_id"] = object_id
        result["expr"] = _ast(data.get("expr"), f"{path}.expr", params=params,
                              free_vars=("x", "y"))
        if "domain" in data:
            result["domain"] = _domain(data["domain"], f"{path}.domain", params=params)
    elif kind == "camera":
        for key in ("zoom", "phi", "theta"):
            if key in data:
                if key == "zoom":
                    zoom = _number(data[key], f"{path}.zoom")
                    if not MIN_CAMERA_ZOOM <= zoom <= MAX_CAMERA_ZOOM:
                        _fail(f"{path}.zoom",
                              f"缩放必须在 {MIN_CAMERA_ZOOM}–{MAX_CAMERA_ZOOM} 之间")
                    result["zoom"] = _norm_number(zoom, f"{path}.zoom")
                else:
                    angle = _number(data[key], f"{path}.{key}")
                    if abs(angle) > MAX_CAMERA_ANGLE:
                        _fail(f"{path}.{key}",
                              f"角度必须在 ±{MAX_CAMERA_ANGLE} 度以内")
                    result[key] = _norm_number(angle, f"{path}.{key}")
    else:  # pause
        seconds = _number(data.get("seconds"), f"{path}.seconds")
        if not 0 < seconds <= MAX_PAUSE_SECONDS:
            _fail(f"{path}.seconds", f"停顿时长必须在 0–{MAX_PAUSE_SECONDS} 秒之间")
        result["seconds"] = _norm_number(seconds, f"{path}.seconds")
    return result


def _action_references(step: dict, index: int, object_ids: set[str]) -> None:
    for j, action in enumerate(step["actions"]):
        object_id = action.get("object_id")
        if object_id is not None and object_id not in object_ids:
            _fail(f"steps[{index}].actions[{j}].object_id",
                  f"引用了不存在的对象 {object_id!r}")


# ── 数学断言 ───────────────────────────────────────────────────────────────
def _tolerance(value: Any, path: str) -> float:
    if value is None:
        return MATH_TOLERANCE
    result = _number(value, path)
    if not 0 < result <= MATH_TOLERANCE:
        _fail(path, f"容差必须 ≤ {MATH_TOLERANCE}（布局／像素误差不能用这个容差）")
    return result


def _assertion(item: Any, index: int, *, params: set[str], spec: VisualSpecV1) -> dict:
    path = f"math_assertions[{index}]"
    data = _mapping(item, path)
    kind = data.get("kind")
    if kind not in ASSERTION_KINDS:
        _fail(f"{path}.kind", f"未知数学断言类型 {kind!r}（允许 {list(ASSERTION_KINDS)}）")
    extra = set(data) - _ASSERTION_KEYS[kind]
    if extra:
        _fail(path, f"断言 {kind!r} 不允许字段 {sorted(extra)}")
    assertion_id = _identifier(data.get("assertion_id"), f"{path}.assertion_id", _ID_RE)
    result: dict[str, Any] = {"assertion_id": assertion_id, "kind": kind}

    if kind in ("partial_derivative", "derivative_sum", "polynomial",
                "surface_not_planar"):
        result["object_id"] = _identifier(data.get("object_id"), f"{path}.object_id", _ID_RE)
    if kind == "partial_derivative":
        variable = data.get("variable")
        if variable not in ("x", "y"):
            _fail(f"{path}.variable", "只能对 x 或 y 求偏导")
        result["variable"] = variable
        result["order"] = _integer(data.get("order"), f"{path}.order", 1, 2)
        if "at" in data:
            result["at"] = _point(data["at"], f"{path}.at")
        result["expected"] = _norm_number(data.get("expected"), f"{path}.expected")
        result["tolerance"] = _tolerance(data.get("tolerance"), f"{path}.tolerance")
    elif kind == "derivative_sum":
        terms = _sequence(data.get("terms"), f"{path}.terms", low=1, high=8)
        normalized = []
        for t, term in enumerate(terms):
            tpath = f"{path}.terms[{t}]"
            tdata = _mapping(term, tpath)
            if set(tdata) - {"variable", "order"}:
                _fail(tpath, f"项不允许字段 {sorted(set(tdata) - {'variable', 'order'})}")
            variable = tdata.get("variable")
            if variable not in ("x", "y"):
                _fail(f"{tpath}.variable", "只能对 x 或 y 求偏导")
            normalized.append({"variable": variable,
                               "order": _integer(tdata.get("order"), f"{tpath}.order", 1, 2)})
        result["terms"] = normalized
        if "at" in data:
            result["at"] = _point(data["at"], f"{path}.at")
        result["expected"] = _norm_number(data.get("expected"), f"{path}.expected")
        result["tolerance"] = _tolerance(data.get("tolerance"), f"{path}.tolerance")
    elif kind == "polynomial":
        coefficients = _mapping(data.get("expected_coefficients"),
                                f"{path}.expected_coefficients")
        if len(coefficients) > MAX_COEFFICIENTS:
            _fail(f"{path}.expected_coefficients", f"系数个数超过 {MAX_COEFFICIENTS}")
        normalized_coefficients = {}
        for monomial, value in coefficients.items():
            if not isinstance(monomial, str) or not _MONOMIAL_RE.match(monomial):
                _fail(f"{path}.expected_coefficients",
                      f"单项式写法 {monomial!r} 不合法（形如 x^2、x*y、y、1）")
            normalized_coefficients[monomial] = _norm_number(
                value, f"{path}.expected_coefficients.{monomial}")
        result["expected_coefficients"] = dict(sorted(normalized_coefficients.items()))
    elif kind == "surface_not_planar":
        pass
    elif kind == "section_is_trace":
        result["section"] = _identifier(data.get("section"), f"{path}.section", _ID_RE)
        result["surface"] = _identifier(data.get("surface"), f"{path}.surface", _ID_RE)
        variable = data.get("variable")
        if variable not in ("x", "y"):
            _fail(f"{path}.variable", "被固定的变量只能是 x 或 y")
        result["variable"] = variable
        if data.get("opens") not in OPEN_MODES:
            _fail(f"{path}.opens", f"开口方向只能是 {list(OPEN_MODES)}")
        result["opens"] = data["opens"]
    elif kind == "vector_sum":
        operands = _sequence(data.get("operands"), f"{path}.operands", low=2, high=8)
        result["operands"] = [_identifier(item_, f"{path}.operands[{i}]", _ID_RE)
                              for i, item_ in enumerate(operands)]
        result["result_object"] = _identifier(data.get("result_object"),
                                              f"{path}.result_object", _ID_RE)
        result["expected"] = _vector2(data.get("expected"), f"{path}.expected")
        result["tolerance"] = _tolerance(data.get("tolerance"), f"{path}.tolerance")
    elif kind == "matrix_apply":
        result["matrix"] = _identifier(data.get("matrix"), f"{path}.matrix", _ID_RE)
        result["vector"] = _identifier(data.get("vector"), f"{path}.vector", _ID_RE)
        result["result_object"] = _identifier(data.get("result_object"),
                                              f"{path}.result_object", _ID_RE)
        result["expected"] = _vector2(data.get("expected"), f"{path}.expected")
        result["tolerance"] = _tolerance(data.get("tolerance"), f"{path}.tolerance")
    return result


def _point(value: Any, path: str) -> dict:
    data = _mapping(value, path)
    if set(data) - {"x", "y"}:
        _fail(path, f"点不允许字段 {sorted(set(data) - {'x', 'y'})}")
    return {"x": _norm_number(data.get("x"), f"{path}.x"),
            "y": _norm_number(data.get("y"), f"{path}.y")}


def _vector2(value: Any, path: str) -> list:
    if not isinstance(value, list) or len(value) != 2:
        _fail(path, "必须是二维向量 [x, y]")
    return [_norm_number(value[0], f"{path}[0]"), _norm_number(value[1], f"{path}[1]")]


def _assertion_references(item: dict, index: int, *, by_id: dict) -> None:
    path = f"math_assertions[{index}]"
    kind = item["kind"]

    def need(object_id: str, field: str, kinds: tuple[str, ...]) -> dict:
        obj = by_id.get(object_id)
        if obj is None:
            _fail(f"{path}.{field}", f"引用了不存在的对象 {object_id!r}")
        if obj["kind"] not in kinds:
            _fail(f"{path}.{field}",
                  f"{object_id!r} 是 {obj['kind']}，这里需要 {list(kinds)}")
        return obj

    if kind in ("partial_derivative", "derivative_sum", "polynomial",
                "surface_not_planar"):
        need(item["object_id"], "object_id", ("surface", "plane", "formula"))
    elif kind == "section_is_trace":
        section = need(item["section"], "section", ("section",))
        surface = need(item["surface"], "surface", ("surface", "plane"))
        if section["surface"] != item["surface"]:
            _fail(f"{path}.surface",
                  f"截面 {item['section']!r} 切的是 {section['surface']!r}，不是 {item['surface']!r}")
        if section["variable"] != item["variable"]:
            _fail(f"{path}.variable",
                  f"截面固定的是 {section['variable']!r}，不是 {item['variable']!r}")
        del surface
    elif kind == "vector_sum":
        for i, operand in enumerate(item["operands"]):
            obj = need(operand, f"operands[{i}]", ("vector", "arrow", "point"))
            _require_2d(obj, f"{path}.operands[{i}]")
        result = need(item["result_object"], "result_object", ("vector", "arrow", "point"))
        _require_2d(result, f"{path}.result_object")
    elif kind == "matrix_apply":
        need(item["matrix"], "matrix", ("matrix2d",))
        vector = need(item["vector"], "vector", ("vector", "arrow", "point"))
        _require_2d(vector, f"{path}.vector")
        result = need(item["result_object"], "result_object", ("vector", "arrow", "point"))
        _require_2d(result, f"{path}.result_object")


def _require_2d(obj: dict, path: str) -> None:
    if obj["kind"] != "matrix2d" and obj.get("space") != "math2d":
        _fail(path, "首期只支持二维（math2d）向量")


# ── 教学计划 ───────────────────────────────────────────────────────────────
def parse_teaching_plan(data: dict) -> TeachingPlanV1:
    if not isinstance(data, dict):
        _fail("plan", "必须是对象")
    required = ("unit_id", "source_hash", "objective", "prerequisites", "claims", "steps",
                "visual_requests")
    missing = [key for key in required if key not in data]
    if missing:
        _fail("plan", f"缺少固定字段 {missing}")
    extra = sorted(set(data) - set(required))
    if extra:
        _fail("plan", f"出现未定义字段 {extra}")

    unit_id, _ = _text(data["unit_id"], "unit_id", limit=64)
    source_hash, _ = _text(data["source_hash"], "source_hash", limit=128)
    objective, _ = _plan_text(data["objective"], "objective", limit=MAX_TEXT_CHARS)

    prerequisites = _sequence(data["prerequisites"], "prerequisites", low=0, high=32)
    prerequisites = tuple(_plan_text(item, f"prerequisites[{i}]", limit=MAX_TEXT_CHARS)[0]
                          for i, item in enumerate(prerequisites))

    claims_raw = _sequence(data["claims"], "claims", low=1, high=64)
    claims = []
    for i, claim in enumerate(claims_raw):
        path = f"claims[{i}]"
        entry = _mapping(claim, path)
        if set(entry) - {"claim_id", "text"}:
            _fail(path, f"claim 不允许字段 {sorted(set(entry) - {'claim_id', 'text'})}")
        claim_id = _identifier(entry.get("claim_id"), f"{path}.claim_id", _ID_RE)
        text, _ = _plan_text(entry.get("text"), f"{path}.text", limit=MAX_TEXT_CHARS)
        claims.append({"claim_id": claim_id, "text": text})
    claim_ids = {claim["claim_id"] for claim in claims}
    if len(claim_ids) != len(claims):
        _fail("claims", "claim_id 必须唯一")

    steps_raw = _sequence(data["steps"], "steps", low=1, high=16)
    steps = []
    for i, step in enumerate(steps_raw):
        path = f"steps[{i}]"
        entry = _mapping(step, path)
        allowed = {"step_id", "question", "explanation_anchor", "claim_ids", "observe",
                   "pause_hint"}
        missing_step = sorted(allowed - set(entry))
        if missing_step:
            _fail(path, f"缺少字段 {missing_step}")
        extra_step = sorted(set(entry) - allowed)
        if extra_step:
            _fail(path, f"不允许字段 {extra_step}")
        step_id = _identifier(entry["step_id"], f"{path}.step_id", _ID_RE)
        question, _ = _plan_text(entry["question"], f"{path}.question", limit=MAX_TEXT_CHARS)
        anchor, _ = _plan_text(entry["explanation_anchor"], f"{path}.explanation_anchor",
                          limit=MAX_TEXT_CHARS)
        refs = _sequence(entry["claim_ids"], f"{path}.claim_ids", low=0, high=16)
        refs = [_identifier(item, f"{path}.claim_ids[{j}]", _ID_RE)
                for j, item in enumerate(refs)]
        unknown = [item for item in refs if item not in claim_ids]
        if unknown:
            _fail(f"{path}.claim_ids", f"引用了不存在的 claim {unknown}")
        observe, _ = _plan_text(entry["observe"], f"{path}.observe", limit=MAX_TEXT_CHARS)
        pause_hint, _ = _plan_text(entry["pause_hint"], f"{path}.pause_hint",
                              limit=MAX_TEXT_CHARS)
        steps.append({"step_id": step_id, "question": question,
                      "explanation_anchor": anchor, "claim_ids": refs,
                      "observe": observe, "pause_hint": pause_hint})
    if len({step["step_id"] for step in steps}) != len(steps):
        _fail("steps", "step_id 必须唯一")

    requests_raw = _sequence(data["visual_requests"], "visual_requests", low=0, high=32)
    visual_requests = []
    for i, request in enumerate(requests_raw):
        path = f"visual_requests[{i}]"
        entry = _mapping(request, path)
        allowed = {"learning_goal", "misconception", "observe", "claim_ids", "anchor"}
        missing_request = sorted(allowed - set(entry))
        if missing_request:
            _fail(path, f"缺少字段 {missing_request}")
        optional = {"request_id", "source_claims", "dependency_paths"}
        extra_request = sorted(set(entry) - allowed - optional)
        if extra_request:
            _fail(path, f"不允许字段 {extra_request}")
        refs = _sequence(entry["claim_ids"], f"{path}.claim_ids", low=0, high=16)
        refs = [_identifier(item, f"{path}.claim_ids[{j}]", _ID_RE)
                for j, item in enumerate(refs)]
        unknown = [item for item in refs if item not in claim_ids]
        if unknown:
            _fail(f"{path}.claim_ids", f"引用了不存在的 claim {unknown}")
        provenance = {}
        if "request_id" in entry:
            provenance["request_id"] = _identifier(entry["request_id"], f"{path}.request_id", _REQUEST_ID_RE)
        if "source_claims" in entry:
            sources = _sequence(entry["source_claims"], f"{path}.source_claims", low=0, high=64)
            if any(not isinstance(item, str) or item not in claim_ids for item in sources):
                _fail(f"{path}.source_claims", "必须引用已存在的 claim")
            provenance["source_claims"] = list(sources)
        if "dependency_paths" in entry:
            pointers = _sequence(entry["dependency_paths"], f"{path}.dependency_paths", low=0, high=64)
            for pointer in pointers:
                if (not isinstance(pointer, str) or not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)+", pointer)
                        or ".." in pointer):
                    _fail(f"{path}.dependency_paths", "必须是教案的 JSON Pointer")
            provenance["dependency_paths"] = list(pointers)
        visual_requests.append({
            **provenance,
            "learning_goal": _plan_text(entry["learning_goal"], f"{path}.learning_goal",
                                   limit=MAX_TEXT_CHARS)[0],
            "misconception": _plan_text(entry["misconception"], f"{path}.misconception",
                                   limit=MAX_TEXT_CHARS)[0],
            "observe": _plan_text(entry["observe"], f"{path}.observe", limit=MAX_TEXT_CHARS)[0],
            "claim_ids": refs,
            "anchor": _plan_text(entry["anchor"], f"{path}.anchor", limit=MAX_TEXT_CHARS)[0],
        })

    return TeachingPlanV1(unit_id=unit_id, source_hash=source_hash, objective=objective,
                          prerequisites=prerequisites, claims=tuple(claims),
                          steps=tuple(steps), visual_requests=tuple(visual_requests))


__all__ = [
    "SCHEMA_VERSION", "MAX_OBJECTS", "MAX_STEPS", "MAX_ACTIONS", "MAX_AST_DEPTH",
    "MAX_AST_NODES", "MAX_MESH", "MAX_LABEL_CHARS", "MAX_CAPTION_CHARS",
    "MAX_CURVE_SAMPLES", "MATH_TOLERANCE", "DIRECTION_VARS", "KINDS", "COLORS",
    "ACTIONS", "PROFILES", "ASSERTION_KINDS", "OPEN_MODES", "FROZEN_FIELDS",
    "UnsupportedVisualSpec", "VisualSpecV1", "TeachingPlanV1", "parse_visual_spec",
    "parse_teaching_plan", "validate_spec_structure", "normalize_ast", "ast_names",
    "eval_const_ast", "target_expression", "VisualSpecV2", "parse_spec", "parse_visual_spec_v2", "describe_visual_contract",
]

# V1 above is a historical reader/compiler contract. Production planners use V2.
# V2 never accepts a second AST or model-selected display profile.
V2_ROOT_FIELDS = frozenset(('schema_version','request_id','unit_id','goal_ids','claim_ids','math_model_refs','objects','steps'))
V2_OBJECT_FIELDS = {
    'axes2d': {'x_range','y_range','x_label','y_label'},
    'axes3d': {'x_range','y_range','z_range'},
    'formula': {'model_id','math_object_id'},
    'curve': {'model_id','math_object_id','x_range','samples'},
    'surface': {'model_id','math_object_id','domain','resolution'},
    'vector': {'model_id','math_object_id','space'},
    'matrix2d': {'model_id','math_object_id'},
    'label': {'text','at','space'},
}
V2_STEP_FIELDS = frozenset(('step_id','kind','goal_id','claim_ids','math_claim_refs','object_ids','observation','expected_change','caption','caption_claim_ids','start','end','actions','checkpoints'))
V2_ACTION_FIELDS = {name:{'action','object_id'} for name in ('reveal','hide','highlight')}
V2_ACTION_FIELDS['transform'] = {'action','object_id','target_object_id'}
V2_STEP_KINDS = ('change','observe')
V2_SPACES = ('math2d','math3d')


@dataclass(frozen=True)
class VisualSpecV2:
    """Canonical immutable references to approved mathematics, never model code."""
    _canonical: str

    def as_dict(self):
        return json.loads(self._canonical)

    def __getattr__(self, name):
        if name in V2_ROOT_FIELDS:
            return self.as_dict()[name]
        raise AttributeError(name)

    @property
    def spec_hash(self):
        return digest_of(self.as_dict())

    def object_by_id(self, object_id):
        for obj in self.objects:
            if obj['object_id']==object_id:
                return obj
        _fail('object_id', f'未知对象 {object_id!r}')


def _v2_fields(value, allowed, required, path):
    value=_mapping(value,path)
    if set(value)-set(allowed): _fail(path, f'不允许字段 {sorted(set(value)-set(allowed))}')
    if set(required)-set(value): _fail(path, f'缺少字段 {sorted(set(required)-set(value))}')
    return value


def _v2_ids(value, path, *, low=1):
    values=_sequence(value,path,low=low,high=MAX_OBJECTS)
    values=[_identifier(v,path,_REQUEST_ID_RE) for v in values]
    if len(values)!=len(set(values)): _fail(path,'标识符必须唯一')
    return values


def _v2_refs(value, path, *, digest=False):
    rows=_sequence(value,path,low=1,high=MAX_ASSERTIONS);out=[]
    fields={'model_id','digest' if digest else 'claim_id'}
    for i,row in enumerate(rows):
        _v2_fields(row,fields if digest else fields|{'relation_id'},fields,f'{path}[{i}]')
        _identifier(row['model_id'],path+'.model_id',_REQUEST_ID_RE)
        if digest:
            if not isinstance(row['digest'],str) or not re.fullmatch(r'sha256:[a-f0-9]{64}',row['digest']): _fail(path+'.digest','需要模型 sha256 摘要')
        else:
            _identifier(row['claim_id'],path+'.claim_id',_REQUEST_ID_RE)
            if 'relation_id' in row: _identifier(row['relation_id'],path+'.relation_id',_REQUEST_ID_RE)
        out.append(dict(row))
    if len({tuple(sorted(r.items())) for r in out})!=len(out): _fail(path,'引用重复')
    if digest and len({r['model_id'] for r in out})!=len(out): _fail(path,'model_id 重复')
    return out


def parse_visual_spec_v2(data):
    _v2_fields(data,V2_ROOT_FIELDS,V2_ROOT_FIELDS,'VisualSpecV2')
    if data['schema_version']!='2.0': _fail('schema_version','需要 2.0')
    result={k:data[k] for k in ('schema_version','request_id','unit_id')}
    for key in ('request_id','unit_id'): _identifier(data[key],key,_REQUEST_ID_RE)
    for key in ('goal_ids','claim_ids'): result[key]=_v2_ids(data[key],key)
    result['math_model_refs']=_v2_refs(data['math_model_refs'],'math_model_refs',digest=True)
    models={r['model_id'] for r in result['math_model_refs']}
    objects=[]
    for i,obj in enumerate(_sequence(data['objects'],'objects',low=1,high=MAX_OBJECTS)):
        path=f'objects[{i}]';obj=_mapping(obj,path);kind=obj.get('kind')
        if kind not in V2_OBJECT_FIELDS: _fail(path+'.kind',f'不支持 {kind!r}')
        required={'object_id','kind'}|V2_OBJECT_FIELDS[kind]
        optional={'color','label'} | ({'x_label','y_label'} if kind=='axes2d' else set())
        _v2_fields(obj,required|optional,required-optional,path)
        row=dict(obj);_identifier(row['object_id'],path+'.object_id',_REQUEST_ID_RE)
        if 'color' in row and row['color'] not in COLORS: _fail(path+'.color','未知颜色')
        if 'label' in row: row['label']=_text(row['label'],path+'.label',limit=MAX_LABEL_CHARS)[0]
        if 'model_id' in row:
            if row['model_id'] not in models: _fail(path+'.model_id','必须引用 math_model_refs')
            _identifier(row['math_object_id'],path+'.math_object_id',_REQUEST_ID_RE)
        if 'space' in row and row['space'] not in V2_SPACES: _fail(path+'.space','必须是 math2d 或 math3d')
        if kind=='label':
            row['text']=_text(row['text'],path+'.text',limit=MAX_LABEL_CHARS)[0]
            if re.search(r'[A-Za-z0-9][_^]|[=]',row['text']): _fail(path+'.text','数学表达式必须使用批准 AST 的 formula 对象')
            row['at']=[_number(v,path+'.at') for v in _sequence(row['at'],path+'.at',low=2,high=3)]
            if len(row['at'])!=(2 if row['space']=='math2d' else 3): _fail(path+'.at','坐标维数不符')
        for key in ('x_range','y_range','z_range'):
            if key in row: row[key]=_range(row[key],path+'.'+key)
        for key in ('x_label','y_label'):
            if key in row: row[key]=_text(row[key],path+'.'+key,limit=MAX_LABEL_CHARS)[0]
        if 'samples' in row: row['samples']=_integer(row['samples'],path+'.samples',2,MAX_CURVE_SAMPLES)
        if kind=='surface':
            row['domain']=_domain(row['domain'],path+'.domain',params=set())
            row['resolution']=[_integer(n,path+'.resolution',2,MAX_MESH) for n in _sequence(row['resolution'],path+'.resolution',low=2,high=2)]
        objects.append(row)
    by_id={o['object_id']:o for o in objects}
    if len(by_id)!=len(objects): _fail('objects','object_id 重复')
    result['objects']=objects;steps=[];visible=set();cursor=0;action_count=0
    for i,item in enumerate(_sequence(data['steps'],'steps',low=1,high=MAX_STEPS)):
        path=f'steps[{i}]';_v2_fields(item,V2_STEP_FIELDS,V2_STEP_FIELDS,path);step=dict(item)
        _identifier(step['step_id'],path+'.step_id',_REQUEST_ID_RE)
        if step['kind'] not in V2_STEP_KINDS: _fail(path+'.kind','必须是 change 或 observe')
        if step['goal_id'] not in result['goal_ids']: _fail(path+'.goal_id','目标不在当前规格')
        for key in ('claim_ids','caption_claim_ids','object_ids'): step[key]=_v2_ids(step[key],path+'.'+key)
        if not set(step['claim_ids'])<=set(result['claim_ids']): _fail(path+'.claim_ids','主张不在当前规格')
        if set(step['caption_claim_ids'])!=set(step['claim_ids']): _fail(path+'.caption_claim_ids','字幕必须绑定本步相同主张')
        if not set(step['object_ids'])<=set(by_id): _fail(path+'.object_ids','引用未知对象')
        step['math_claim_refs']=_v2_refs(step['math_claim_refs'],path+'.math_claim_refs')
        if any(r['model_id'] not in models for r in step['math_claim_refs']): _fail(path+'.math_claim_refs','引用未知模型')
        for key in ('observation','expected_change','caption'):
            step[key]=_text(step[key],path+'.'+key,limit=MAX_CAPTION_CHARS if key=='caption' else MAX_TEXT_CHARS)[0]
        step['start']=_number(step['start'],path+'.start');step['end']=_number(step['end'],path+'.end')
        validate_duration(step['end'])
        if step['start']!=cursor or step['end']<=cursor: _fail(path,'步骤必须连续、有序且具有正时长')
        cursor=step['end'];actions=_sequence(step['actions'],path+'.actions',low=0,high=MAX_ACTIONS)
        if step['kind']=='change' and not actions: _fail(path+'.change.actions','change 必须有非空 actions')
        if step['kind']=='observe':
            if actions: _fail(path+'.observe.actions','observe 只能观察，actions 必须为空')
            if not set(step['object_ids'])<=visible: _fail(path+'.observe','observe 只能阅读现存可见对象 visible')
        shown_during=set(visible);action_keys=set()
        for a in actions:
            a=_mapping(a,path+'.actions');op=a.get('action')
            if op not in V2_ACTION_FIELDS: _fail(path+'.actions','不支持的动作')
            _v2_fields(a,V2_ACTION_FIELDS[op],V2_ACTION_FIELDS[op],path+'.actions')
            oid=a['object_id']
            key=(op,oid)
            if key in action_keys: _fail(path+'.actions','同一步骤动作重复 duplicate，无法唯一绑定实帧')
            action_keys.add(key)
            if oid not in step['object_ids']: _fail(path+'.actions.object_id','动作必须绑定本步 object_ids')
            if op=='reveal':
                if oid in visible: _fail(path+'.reveal','reveal 对象已可见 visible，不构成变化')
                visible.add(oid);shown_during.add(oid)
            else:
                if oid not in visible: _fail(path+'.'+op,'动作对象必须现存可见')
                if op=='hide': visible.remove(oid)
                if op=='transform':
                    target=a['target_object_id']
                    if target not in step['object_ids'] or target==oid: _fail(path+'.transform','target_object_id 必须是本步另一个对象')
                    if by_id[target]['kind']!=by_id[oid]['kind'] or by_id[oid]['kind'] not in ('formula','curve','surface','vector','matrix2d'): _fail(path+'.transform','不支持此对象类型变换')
                    if target in visible: _fail(path+'.transform','目标对象已经可见')
                    visible.remove(oid);visible.add(target);shown_during.add(target)
        if not set(step['object_ids'])<=shown_during: _fail(path+'.object_ids','本步绑定对象必须实际可见 visible，不能用隐藏对象证明数学关系')
        action_count+=len(actions)
        step['actions']=[dict(a) for a in actions];checks=[]
        for c in _sequence(step['checkpoints'],path+'.checkpoints',low=0,high=16):
            _v2_fields(c,{'at','note'},{'at','note'},path+'.checkpoints')
            at=_number(c['at'],path+'.checkpoints.at')
            if not step['start']<=at<step['end']: _fail(path+'.checkpoints.at','观察时刻必须在本步骤内')
            checks.append({'at':at,'note':_text(c['note'],path+'.checkpoints.note',limit=MAX_LABEL_CHARS)[0]})
        step['checkpoints']=checks;steps.append(step)
    if action_count>MAX_ACTIONS: _fail('steps','动作总数超过上限')
    if len({s['step_id'] for s in steps})!=len(steps): _fail('steps','step_id 重复')
    if not any(s['kind']=='change' for s in steps): _fail('steps','静态内容应使用图，动画必须具有目标变化')
    if {s['goal_id'] for s in steps}!=set(result['goal_ids']): _fail('goal_ids','必须逐目标覆盖步骤')
    if set().union(*(set(s['claim_ids']) for s in steps))!=set(result['claim_ids']): _fail('claim_ids','必须逐主张覆盖步骤')
    result['steps']=steps
    return VisualSpecV2(json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(',',':')))


def parse_spec(data: dict) -> VisualSpecV2:
    """The production V2 entry point; historical parsers remain explicit."""
    return parse_visual_spec_v2(data)


def describe_visual_contract():
    """JSON Schema and parser use the same closed fields, kinds and limits."""
    from ..media.presentation_policy import MAX_ANIMATION_SECONDS
    ident={'type':'string','pattern':_REQUEST_ID_RE.pattern}
    number={'type':'number'}
    def ids(): return {'type':'array','items':ident,'minItems':1,'maxItems':MAX_OBJECTS,'uniqueItems':True}
    def text(limit=MAX_TEXT_CHARS): return {'type':'string','minLength':1,'maxLength':limit}
    def array(item,low,high): return {'type':'array','items':item,'minItems':low,'maxItems':high}
    def record(properties,required=None):
        return {'type':'object','properties':properties,'required':sorted(properties if required is None else required),'additionalProperties':False}
    rng=array(number,2,2)
    props={'object_id':ident,'kind':{'enum':list(V2_OBJECT_FIELDS)},'color':{'enum':list(COLORS)},'label':text(MAX_LABEL_CHARS),
      'model_id':ident,'math_object_id':ident,'x_range':rng,'y_range':rng,'z_range':rng,'x_label':text(MAX_LABEL_CHARS),'y_label':text(MAX_LABEL_CHARS),
      'domain':record({'x':rng,'y':rng}),'resolution':array({'type':'integer','minimum':2,'maximum':MAX_MESH},2,2),
      'samples':{'type':'integer','minimum':2,'maximum':MAX_CURVE_SAMPLES},'space':{'enum':list(V2_SPACES)},'at':array(number,2,3),'text':text(MAX_LABEL_CHARS)}
    objects=[]
    for kind,keys in V2_OBJECT_FIELDS.items():
        fields={'object_id','kind'}|keys
        objprops={k:props[k] for k in fields|{'color','label'}};objprops['kind']={'const':kind}
        objects.append(record(objprops,fields-({'x_label','y_label'} if kind=='axes2d' else set())))
    actions=[]
    for name,keys in V2_ACTION_FIELDS.items():
        actions.append(record({k:({'const':name} if k=='action' else ident) for k in keys}))
    math_refs=array(record({'model_id':ident,'claim_id':ident,'relation_id':ident}, {'model_id','claim_id'}),1,MAX_ASSERTIONS)
    step_props={'step_id':ident,'kind':{'enum':list(V2_STEP_KINDS)},'goal_id':ident,'claim_ids':ids(),
      'math_claim_refs':math_refs,'object_ids':ids(),'observation':text(),'expected_change':text(),
      'caption':text(MAX_CAPTION_CHARS),'caption_claim_ids':ids(),'start':{'type':'number','minimum':0,'exclusiveMaximum':MAX_ANIMATION_SECONDS},
      'end':{'type':'number','exclusiveMinimum':0,'maximum':MAX_ANIMATION_SECONDS},
      'actions':array({'oneOf':actions},0,MAX_ACTIONS),'checkpoints':array(record({'at':number,'note':text(MAX_LABEL_CHARS)}),0,16)}
    step=record(step_props,V2_STEP_FIELDS)
    step['allOf']=[{'if':{'properties':{'kind':{'const':kind}}},'then':{'properties':{'actions':{'minItems':1} if kind=='change' else {'maxItems':0}}}} for kind in V2_STEP_KINDS]
    schema=record({'schema_version':{'const':'2.0'},'request_id':ident,'unit_id':ident,'goal_ids':ids(),'claim_ids':ids(),
      'math_model_refs':array(record({'model_id':ident,'digest':{'type':'string','pattern':'^sha256:[a-f0-9]{64}$'}}),1,MAX_ASSERTIONS),
      'objects':array({'oneOf':objects},1,MAX_OBJECTS),'steps':array(step,1,MAX_STEPS)},V2_ROOT_FIELDS)
    return {'$schema':'https://json-schema.org/draft/2020-12/schema',**schema}
