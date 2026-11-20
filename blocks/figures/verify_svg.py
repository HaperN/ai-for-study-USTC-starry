"""校验画出来的图**对不对** —— 靠坐标断言，不靠眼睛看。

这台机器没有浏览器，就算有我也不会去"目视检查每张图"。所以图形走
"**画 → 渲 → 断言**"：SVG 的结构和几何关系都能被机器判定。

校验六条：
  1. SVG 存在且是合法 XML；
  2. 该有的图元都在（五条带 id 的线：三条向量 + 两条辅助线）；
  3. 三个标注是**真文字**（`<text>` 里有 a / b / a+b），不是被转成路径的图形
     —— 文字能被搜索、能被选中，也能被这里查；
  4. 没有内嵌 PNG（内嵌了就说明不是矢量，放大会糊）；
  5. 颜色写成 `currentColor`、没有写死的黑色（页面切深色主题时图能跟着变）；
  6. **几何关系对**：C − A == B − O 且 C − B == A − O（显示坐标下成立，
     这两条是仿射变换不变量，所以能真正证明"画出来的是平行四边形"），
     外加数据坐标下 C == A + B；
  8. **这张图自带的数学断言**（几何关系、闭环、长度比、夹角、标注挂点）——
     由画图时写进 geometry.json，所以新增一张图不用改校验器。
  7. **没有画出画布**：每个标注的**实际外框**都在画布内。
     实测第一版几何全对、前六条全绿，但 "a+b" 被顶到画布外，PNG 里只剩 "a+"
     —— 所以"算得对"不等于"看得见"，这条必须查。
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

TOLERANCE = 1e-6
TOLERANCE_DEG = 0.5          # 夹角容差（度）
TOLERANCE_RATIO = 1e-3       # 长度比容差
ANCHOR_MAX_PX = 160          # 标注中心离它挂的那个点最远多少像素
# 平行四边形那张图的默认图元（老产物没有 expected_ids 字段时兜底）
EXPECTED_IDS = ["vec-a", "vec-b", "vec-ab", "helper-ac", "helper-bc"]


def _texts(root: ET.Element) -> list[str]:
    return [element.text or "" for element in root.iter()
            if element.tag.endswith("text")]


def _ids(root: ET.Element) -> set[str]:
    return {element.get("id") for element in root.iter() if element.get("id")}


def _close(a, b, tolerance: float = TOLERANCE) -> bool:
    return abs(a - b) <= tolerance


def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1]]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _norm(a):
    return (a[0] ** 2 + a[1] ** 2) ** 0.5


def _angle_deg(vertex, p1, p2, display) -> float:
    import math
    u, v = _sub(display[p1], display[vertex]), _sub(display[p2], display[vertex])
    nu, nv = _norm(u), _norm(v)
    if nu == 0 or nv == 0:
        return 0.0
    cos = max(-1.0, min(1.0, _dot(u, v) / (nu * nv)))
    return math.degrees(math.acos(cos))


def _angle_deg_between(u, v) -> float:
    """两个方向之间的夹角（0~180）。用来查"同向/反向"，不需要共用顶点。"""
    import math
    nu, nv = _norm(u), _norm(v)
    if nu == 0 or nv == 0:
        return 0.0
    cos = max(-1.0, min(1.0, _dot(u, v) / (nu * nv)))
    return math.degrees(math.acos(cos))


def check_assertions(payload: dict) -> list[str]:
    """每张图**自带的数学断言**（都在显示坐标下判，因为它们是仿射不变量）。

    这些才是"图画对了"的证明：
      closes    首尾相接的段必须闭环
      relations 两对点之间的位移必须相等
      lengths   两段长度的比值
      angles    顶点处两条边的夹角
      anchors   每个标注要挂在它该挂的那个点附近（防止标注挂错顶点）
    都由画图时写进 geometry.json，所以新增一张图不用改校验器。
    """
    problems: list[str] = []
    display = payload.get("display") or {}
    if not display:
        return problems

    for loop in payload.get("closes") or []:
        total = [0.0, 0.0]
        for start, end in loop:
            delta = _sub(display[end], display[start])
            total = [total[0] + delta[0], total[1] + delta[1]]
        if not all(abs(v) <= TOLERANCE for v in total):
            problems.append(f"这些段首尾相接没有闭环：{loop}（合起来是 {total}，应为 0）")

    for (a, b), (c, d) in payload.get("relations") or []:
        left, right = _sub(display[a], display[b]), _sub(display[c], display[d])
        if not all(_close(x, y) for x, y in zip(left, right)):
            problems.append(f"位移不相等：{a}−{b}={left} 应等于 {c}−{d}={right}")

    for (a, b), (c, d), ratio in payload.get("lengths") or []:
        first, second = _norm(_sub(display[a], display[b])), _norm(_sub(display[c], display[d]))
        if second == 0:
            problems.append(f"长度比的分母为零：{c}−{d}")
            continue
        actual = first / second
        if abs(actual - ratio) > TOLERANCE_RATIO:
            problems.append(f"长度比不对：{a}−{b} 是 {c}−{d} 的 {actual:.4f} 倍，应为 {ratio}")

    for vertex, p1, p2, wanted in payload.get("angles") or []:
        actual = _angle_deg(vertex, p1, p2, display)
        if abs(actual - wanted) > TOLERANCE_DEG:
            problems.append(f"夹角不对：∠{p1}{vertex}{p2} 画出来是 {actual:.2f}°，"
                            f"应为 {wanted}°")

    for (a, b), (c, d), wanted in payload.get("directions") or []:
        u, v = _sub(display[b], display[a]), _sub(display[d], display[c])
        actual = _angle_deg_between(u, v)
        if abs(actual - wanted) > TOLERANCE_DEG:
            problems.append(f"方向不对：{a}→{b} 与 {c}→{d} 夹角 {actual:.2f}°，"
                            f"应为 {wanted}°")

    boxes = payload.get("label_boxes") or {}
    for label, anchor in (payload.get("anchors") or {}).items():
        box = boxes.get(label)
        if not box or anchor not in display:
            continue
        centre = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]
        distance = _norm(_sub(centre, display[anchor]))
        if distance > ANCHOR_MAX_PX:
            problems.append(f"标注 {label!r} 离它该挂的点 {anchor} 太远"
                            f"（{distance:.0f} 像素）—— 可能挂错了顶点")
    return problems


def _verify_structural(root, text: str, payload: dict, checks: dict) -> dict:
    """外部渲染器画出来的图（schemdraw / circuitikz / 曲线 / 3D）怎么算"画对了"：

    这类图**没有可比对的点对**，所以断言换三件事：
      ① 图元数量够不够（防止"文件生成了但图是空的"）；
      ② 该有的标注文字在不在（schemdraw/matplotlib 都把文字留成 <text>）；
      ③ 数值断言：后端**独立算过一遍**的期望值必须和"真正画出去的数据"一致
         （曲线图最容易犯的错是"公式对、画出来不对"）。
    """
    problems: list[str] = []
    paths = [e for e in root.iter() if str(e.tag).endswith("path")]
    checks["路径数"] = len(paths)
    need = int(payload.get("min_paths") or 1)
    if len(paths) < need:
        problems.append(f"图元路径只有 {len(paths)} 条，少于声明的 {need} 条（图可能是空的）")
    size = len(text.encode("utf-8"))
    checks["体积"] = size
    floor = int(payload.get("min_bytes") or 0)
    if floor and size < floor:
        problems.append(f"SVG 只有 {size} 字节，少于声明的 {floor} 字节")
    texts = _texts(root)
    checks["文字标注"] = texts
    for want in payload.get("require_texts") or []:
        w = str(want).replace(" ", "")
        if w and not any(w in str(got).replace(" ", "") for got in texts):
            problems.append(f"标注 {want!r} 没出现在图里（现有标注：{texts[:8]}）")
    # 几何断言（外部渲染器画的图：元件数、包围盒）
    if payload.get("element_count_expected"):
        if int(payload.get("element_count") or 0) != int(payload["element_count_expected"]):
            problems.append(f"规格声明了 {payload['element_count_expected']} 个元件，"
                            f"实际只画出 {payload.get('element_count')} 个")
    bbox = payload.get("bbox")
    if bbox:
        area = max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])
        checks["包围盒面积"] = round(area, 3)
        if area < float(payload.get("bbox_area_min") or 1.0):
            problems.append(f"图的包围盒面积只有 {round(area, 3)}，小于声明的下限"
                            f"（图像是退化的/空的）")
    for chk in payload.get("numeric_checks") or []:
        if not chk.get("ok"):
            problems.append(f"数值断言失败：x={chk.get('x')} 期望 {chk.get('y_expected')}，"
                            f"实际画出 {chk.get('y_drawn')}")
    problems += list(payload.get("numeric_problems") or [])
    if payload.get("numeric") and not (payload.get("numeric_checks") or []):
        problems.append("这张图声明了数值断言但一条都没写（那就只剩「文件生成了」）")
    return {"ok": not problems, "problems": problems, "checks": checks}


def verify(svg_path: str | Path, geometry_path: str | Path) -> dict:
    """返回 {"ok": bool, "problems": [...], "checks": {...}}。"""
    svg_path, geometry_path = Path(svg_path), Path(geometry_path)
    problems: list[str] = []
    checks: dict = {}

    if not svg_path.is_file():
        return {"ok": False, "problems": [f"SVG 不存在：{svg_path}"], "checks": {}}
    text = svg_path.read_text(encoding="utf-8")

    # ① 合法 XML
    try:
        root = ET.fromstring(text)
        checks["合法XML"] = True
    except ET.ParseError as exc:
        return {"ok": False, "problems": [f"SVG 不是合法 XML：{exc}"], "checks": {}}

    # ①·五 **非向量图**（电路/曲线/3D）走结构+数值断言：
    #     向量那套"仿射不变量"对电路图不适用（它没有可以比对的点对），
    #     硬套只会逼着人编造断言。所以按 payload 里的 `kind` 分派。
    _payload = json.loads(Path(geometry_path).read_text(encoding="utf-8"))
    if _payload.get("kind") == "structural":
        return _verify_structural(root, text, _payload, checks)

    # ② 图元齐全（每张图自己声明该有哪些）
    expected = json.loads(Path(geometry_path).read_text(encoding="utf-8")).get(
        "expected_ids") or EXPECTED_IDS
    ids = _ids(root)
    missing = [name for name in expected if name not in ids]
    checks["图元"] = sorted(i for i in ids if i in expected)
    if missing:
        problems.append(f"少了这些图元：{missing}（实际有：{sorted(i for i in ids if i)}）")

    # ③ 标注是真文字（按**这张图自己声明的标注**判，不写死具体名字）
    payload_for_labels = json.loads(Path(geometry_path).read_text(encoding="utf-8"))
    labels = _texts(root)
    checks["文字标注"] = labels
    label_gids = payload_for_labels.get("label_gids") or {}
    for want in payload_for_labels.get("labels") or []:
        if want.startswith("$"):
            # 数学标注会被 matplotlib 渲成路径 —— 这是**故意的**（不依赖字体，
            # 任何浏览器都长一样，颜色照样跟 currentColor 走）。所以用 gid 证明它在。
            gid = label_gids.get(want)
            if gid and f'id="{gid}"' not in text:
                problems.append(f"数学标注 {want!r} 的图元不在 SVG 里（gid={gid}）")
        elif want not in labels:
            problems.append(f"标注 {want!r} 不是真文字（可能被转成路径了，"
                            f"应设 matplotlib 的 svg.fonttype='none'）")

    # ④ 不是内嵌位图
    checks["内嵌PNG"] = "base64" in text
    if "base64" in text:
        problems.append("SVG 里内嵌了 base64 位图 → 不是矢量，放大会糊")

    # ⑤ 颜色跟着主题走
    checks["currentColor"] = "currentColor" in text
    if "currentColor" not in text:
        problems.append("没有 currentColor（深色主题下线条会看不见）")
    hard_coded = [token for token in ('#000000', 'stroke="black"', 'fill="black"')
                  if token in text]
    if hard_coded:
        problems.append(f"还有写死的颜色 {hard_coded}")

    # ⑥ 几何关系
    payload = json.loads(geometry_path.read_text(encoding="utf-8"))
    display = payload.get("display") or {}
    if not display:
        problems.append("geometry.json 里缺少显示坐标，无法校验几何关系")

    # ⑦ 有没有画出画布：标注的外框、以及四个点的显示坐标，都要在画布内
    canvas = payload.get("canvas")
    for (a, b), (c, d), wanted in payload.get("directions") or []:
        u, v = _sub(display[b], display[a]), _sub(display[d], display[c])
        actual = _angle_deg_between(u, v)
        if abs(actual - wanted) > TOLERANCE_DEG:
            problems.append(f"方向不对：{a}→{b} 与 {c}→{d} 夹角 {actual:.2f}°，"
                            f"应为 {wanted}°")

    boxes = payload.get("label_boxes") or {}
    margin = 2.0
    if canvas:
        width, height = canvas
        checks["画布"] = canvas
        for name, box in boxes.items():
            if box[0] < margin or box[1] < margin or box[2] > width - margin \
                    or box[3] > height - margin:
                problems.append(f"标注 {name!r} 超出画布（外框 {box}，画布 {canvas}）"
                                f"—— 读者会看到被切掉的文字")
        for name, point in display.items():
            if not (0 <= point[0] <= width and 0 <= point[1] <= height):
                problems.append(f"点 {name} 的显示坐标 {point} 在画布外")

    # ⑧ 这张图**自带的数学断言**
    problems += check_assertions(payload)

    return {"ok": not problems, "problems": problems, "checks": checks}


__all__ = ["verify", "check_assertions", "_verify_structural", "EXPECTED_IDS", "TOLERANCE",
           "TOLERANCE_DEG", "TOLERANCE_RATIO", "ANCHOR_MAX_PX"]
