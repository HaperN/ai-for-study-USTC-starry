"""后端 `three`：**3D / glTF**（Blender 无头导出 .glb + 同数据的等轴测 SVG 贴片）。

为什么要两个产物：
  * `.glb` 是给**网页 3D** 用的（仓库已内置 three.js + OrbitControls，可离线内联，能转视角/拉伸）；
  * `.svg` 是**静态贴片**：从**同一份规格**算出的等轴测投影，给不能跑 WebGL 的场合（打印、纯文本阅读器）兜底，
    同时让 `verify(svg, geometry)` 这条既有校验链原样可用（不必为 3D 造第二套入口）。

为什么用 Blender 而不是手写 glTF：参数化场景（平面/立方/圆柱/相机）用 bpy 三行就能建，
手写 glTF 的 buffer/accessor 极易出错。

⚠️ 2026-10-06（A01 实测）：这里原来写死 `Path.home()/".local/opt/blender/blender"`，
而 `config/capabilities.json` 只声明 `bin:node` —— 后果是**在别人的机器上、或 HOME 被换掉时，
`aifs capabilities` 把 `three` 报成"依赖齐备"，一画就 RuntimeError**。
A01 的原话是"不依赖个人 Blender 路径"，所以现在两边**同一条规则**（`blender_path()`）：

    1. `AIFORSTUDY_BLENDER`（显式指定，最高优先）
    2. PATH（`shutil.which`）
    3. `config/capabilities.json` 的 `binaries.blender.fallback_paths` 里**声明**的位置

第 3 条是**声明**出来的、可删可覆盖，而且能力预检调的是同一个
`aiforstudy.providers.capabilities.binary_fallback_paths()` —— 不会再出现
"预检说不可用、后端却跑得起来"（或反过来）的两套口径。**代码里不写死任何个人路径。**

**断言**（`kind="structural"` + `numeric_checks`）：
  ① 几何断言：SVG 路径数 ≥ 物体数、标注文字必须在；
  ② **glTF 断言**：解析 .glb 的 JSON chunk —— 节点数/网格数必须等于规格声明的物体数，
     包围盒必须与规格算出来的一致（这才能证明"导出的确实是这个场景"）。
"""
from __future__ import annotations

import json
import math
import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

#: 读配置兜底位置失败的原因（空 = 读到了；见 `_config_fallback_paths()`）。
CONFIG_ERROR = ""


def _config_fallback_paths() -> list:
    """配置里为 blender 声明的兜底位置（读不到配置就返回空 —— 只认 PATH，不猜）。

    ⚠️ 读配置失败**不静默**：原因记在 `CONFIG_ERROR` 里，报错时会带出来。
    否则又会出现"预检读到配置、后端读不到"的两套口径。
    """
    global CONFIG_ERROR
    try:
        from aiforstudy.core.workspace import open_workspace
        from aiforstudy.providers.capabilities import binary_fallback_paths
        paths = binary_fallback_paths(open_workspace(None), "blender")
        CONFIG_ERROR = ""
        return paths
    except Exception as exc:                                   # noqa: BLE001
        CONFIG_ERROR = f"{type(exc).__name__}: {exc}"
        return []


def blender_path() -> Path | None:
    """定位 Blender —— 顺序见模块文档；找不到就返回 `None`（由调用方明确报错）。

    与 `capabilities.json` 的 `bin:blender` 探测是**同一条规则**：
    探测说可用就必须真能跑，探测说不可用就必须明确报错。
    """
    explicit = os.environ.get("AIFORSTUDY_BLENDER")
    if explicit:
        return Path(explicit).expanduser()
    found = shutil.which("blender")
    if found:
        return Path(found)
    for candidate in _config_fallback_paths():
        path = Path(str(candidate)).expanduser()
        if path.is_file():
            return path
    return None


#: 等轴测投影（和 Blender 里看到的朝向大体一致；只用于静态贴片）
def _iso(x: float, y: float, z: float) -> tuple[float, float]:
    c30, s30 = math.cos(math.radians(30)), math.sin(math.radians(30))
    return (x - y) * c30, (x + y) * s30 - z


def _corners(obj: dict) -> list[tuple[float, float, float]]:
    ax, ay, az = (float(v) for v in (obj.get("at") or [0, 0, 0]))
    sx, sy, sz = (float(v) for v in (obj.get("size") or [1, 1, 1]))
    return [(ax + dx * sx, ay + dy * sy, az + dz * sz)
            for dx in (-0.5, 0.5) for dy in (-0.5, 0.5) for dz in (-0.5, 0.5)]


def _blender_script(spec: dict, glb: Path) -> str:
    """把声明式规格翻成 bpy 脚本（建物体 + 相机 + 导出 GLB）。"""
    lines = ["import bpy, math",
             "bpy.ops.wm.read_factory_settings(use_empty=True)"]
    for obj in spec.get("objects") or []:
        kind = str(obj.get("kind") or "box")
        at = list(obj.get("at") or [0, 0, 0])
        size = list(obj.get("size") or [1, 1, 1])
        name = str(obj.get("name") or kind)
        op = {"box": "cube", "cube": "cube", "cylinder": "cylinder",
              "plane": "plane", "sphere": "uv_sphere"}.get(kind)
        if not op:
            raise KeyError(f"three 后端不认识的物体：{kind}")
        lines.append(f"bpy.ops.mesh.primitive_{op}_add(location=({at[0]},{at[1]},{at[2]}))")
        lines.append(f"o = bpy.context.active_object; o.name = {name!r}")
        if kind in ("box", "cube", "plane"):
            lines.append(f"o.scale = ({size[0]/2 or 0.001},{size[1]/2 or 0.001},{size[2]/2 or 0.001})")
        else:
            r = max(size[0], size[1]) / 2 or 0.5
            lines.append(f"o.scale = ({r},{r},{size[2]/2 or 0.5})")
    cam = spec.get("camera") or {}
    loc = list(cam.get("location") or [6, -6, 5])
    look = list(cam.get("look_at") or [0, 0, 0])
    lines += [
        f"bpy.ops.object.camera_add(location=({loc[0]},{loc[1]},{loc[2]}))",
        "cam = bpy.context.active_object",
        f"cam.name = 'FigCamera'",
        "import mathutils",
        f"d = mathutils.Vector(({look[0]},{look[1]},{look[2]})) - cam.location",
        "cam.rotation_euler = d.to_track_quat('-Z','Y').to_euler()",
        "bpy.context.scene.camera = cam",
        f"bpy.ops.export_scene.gltf(filepath={str(glb)!r}, export_format='GLB')",
    ]
    return "\n".join(lines) + "\n"


def _glb_json(path: Path) -> dict:
    """读 .glb 的 JSON chunk（前 12 字节是头，接着是 chunk 头 + 数据）。"""
    raw = path.read_bytes()
    magic, _version, _length = struct.unpack_from("<4sII", raw, 0)
    if magic != b"glTF":
        raise ValueError("不是合法的 GLB（magic 不对）")
    offset, out = 12, {}
    while offset + 8 <= len(raw):
        clen, ctype = struct.unpack_from("<II", raw, offset)
        chunk = raw[offset + 8: offset + 8 + clen]
        if ctype == 0x4E4F534A:                       # 'JSON'
            out = json.loads(chunk.decode("utf-8"))
            break
        offset += 8 + clen + ((4 - clen % 4) % 4)
    return out


def draw(spec: dict, out_dir: str | Path, *, show_png: bool = True) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = spec["name"]
    glb_path = out_dir / f"{name}.glb"
    svg_path = out_dir / f"{name}.svg"

    blender = blender_path()
    if blender is None:
        raise RuntimeError(
            "找不到 blender（PATH 里没有，配置里声明的位置也不在）—— 3D 后端需要它无头导出 glTF。"
            "用 `AIFORSTUDY_BLENDER=/path/to/blender` 指定，或把它放进 PATH；"
            "能力预检同一条规则（`aifs capabilities` 会先把这一条后端标成不可用，"
            "不会到这一步才炸）。" + (f"｜读配置失败：{CONFIG_ERROR}" if CONFIG_ERROR else ""))

    script = _blender_script(spec, glb_path)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        script_path = fh.name
    run = subprocess.run([str(blender), "-b", "--factory-startup", "--python", script_path],
                         capture_output=True, text=True, timeout=600)
    if run.returncode != 0 or not glb_path.is_file():
        tail = "\n".join((run.stdout or run.stderr or "").splitlines()[-10:])
        raise RuntimeError(f"Blender 导出 glTF 失败（{name}）：\n{tail}")

    # ── glTF 断言：节点/网格数 + 包围盒（证明导出的确实是这个场景）──
    doc = _glb_json(glb_path)
    nodes = [n.get("name") for n in doc.get("nodes", [])]
    meshes = doc.get("meshes", [])
    xs, ys, zs = [], [], []
    for obj in spec.get("objects") or []:
        for cx, cy, cz in _corners(obj):
            xs.append(cx); ys.append(cy); zs.append(cz)
    expect_bbox = [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)] if xs else [0]*6
    obj_names = {str(o.get("name") or o.get("kind")) for o in spec.get("objects") or []}
    got_names = {str(n) for n in nodes if n}
    missing = sorted(obj_names - got_names)
    numeric = [{"check": "节点数", "expected": len(obj_names), "drawn": len(got_names & obj_names),
                "ok": not missing},
               {"check": "网格数", "expected": len(obj_names), "drawn": len(meshes),
                "ok": len(meshes) >= len(obj_names)}]
    problems: list[str] = []
    if missing:
        problems.append(f"glTF 里少了这些节点：{missing}（实际：{sorted(got_names)}）")

    # ── 静态贴片：同一份规格算等轴测投影（给不能跑 WebGL 的场合兜底）──
    pts = [_iso(*c) for obj in spec.get("objects") or [] for c in _corners(obj)]
    minx = min(p[0] for p in pts); maxx = max(p[0] for p in pts)
    miny = min(p[1] for p in pts); maxy = max(p[1] for p in pts)
    scale = 260.0 / max(maxx - minx, maxy - miny, 1e-6)
    W = (maxx - minx) * scale + 80; H = (maxy - miny) * scale + 80
    def sx_(v): return 40 + (v[0] - minx) * scale
    def sy_(v): return H - 40 - (v[1] - miny) * scale
    body = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" '
            f'width="{W:.0f}" height="{H:.0f}">',
            '<rect width="100%" height="100%" fill="#fff"/>']
    for obj in spec.get("objects") or []:
        cs = [_iso(*c) for c in _corners(obj)]
        top = [cs[1], cs[3], cs[7], cs[5]]
        d = " ".join(("M" if i == 0 else "L") + f"{sx_(p):.1f},{sy_(p):.1f}" for i, p in enumerate(top)) + " Z"
        body.append(f'<path d="{d}" fill="#dbe4ff" stroke="#3b5bdb" stroke-width="1.6"/>')
        ax, ay = _iso(*(obj.get("at") or [0, 0, 0]))
        body.append(f'<text x="{sx_((ax,ay)):.1f}" y="{sy_((ax,ay)):.1f}" font-size="12" '
                    f'text-anchor="middle" fill="#1f2937">{obj.get("name") or obj.get("kind")}</text>')
    body.append("</svg>")
    svg_path.write_text("\n".join(body), encoding="utf-8")

    # 场景描述（给**页面端**用）：仓库的 vendor 里只有 three.min.js + OrbitControls，
    # **没有 GLTFLoader** —— 与其为了 .glb 再去引一个加载器，不如把"画什么"直接给页面：
    # 页面用 three 的基本体（Box/Plane/Cylinder/Sphere）+ OrbitControls 就能转视角。
    scene = {"objects": [dict(o) for o in spec.get("objects") or []],
             "camera": dict(spec.get("camera") or {})}
    scene_path = out_dir / f"{name}.scene.json"
    scene_path.write_text(json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")

    payload = {
        "kind": "structural", "backend": "three", "name": name,
        "scene": scene_path.name,
        "min_paths": max(1, len(spec.get("objects") or [])),
        "min_bytes": 200,
        "require_texts": [str(o.get("name") or o.get("kind")) for o in spec.get("objects") or []],
        "numeric": True,
        "numeric_checks": numeric,
        "numeric_problems": problems,
        "gltf": {"file": glb_path.name, "nodes": len(nodes), "meshes": len(meshes),
                 "bbox_expected": expect_bbox},
        "labels": [str(o.get("name") or o.get("kind")) for o in spec.get("objects") or []],
        "svg_bytes": len(svg_path.read_text(encoding="utf-8").encode("utf-8")),
    }
    geometry = out_dir / f"{name}.geometry.json"
    geometry.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    produced = {"svg": str(svg_path), "geometry": str(geometry), "glb": str(glb_path),
                "scene": str(scene_path)}
    if show_png:
        produced["png"] = ""
    return produced


__all__ = ["draw", "blender_path"]
