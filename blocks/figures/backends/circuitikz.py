"""后端 `circuitikz`：**TeX 级**电路图（`pdflatex` → `pdftocairo -svg`）。

为什么还要这个后端（已经有 schemdraw 了）：schemdraw 快、免 TeX，但画出来的元件样式是
matplotlib 味；教材/论文里那种"印刷级"电路图用 **CircuiTikZ** 才像样。两者并存，按图的
规格选后端 —— 这就是"一处入口 + 多后端"的意义。

规格里放**TikZ 源码**（TikZ 本身就是一门声明式语言，硬拆成 points/arrows 反而更难写）：
    {"backend": "circuitikz",
     "tikz": "\\draw (0,0) to[V,l=$u_{S1}$] (0,3) ...;"}

**坑（实测）**：
  * 本机 **没有 dvisvgm 可用**（它不认系统 Ghostscript 10.02）→ 一律走 `pdftocairo -svg`；
  * TeX 输出的文字会被**转成字形轮廓**，SVG 里没有 <text> → 断言只能走几何（路径数/体积/画布）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

#: TinyTeX 装在家目录（无 sudo），所以编译前要把它挂进 PATH。
TINYTEX_BIN = Path.home() / ".TinyTeX" / "bin" / "x86_64-linux"

DOC = (
    "\\documentclass[border=4pt]{standalone}\n"
    "\\usepackage[siunitx]{circuitikz}\n"
    "\\begin{document}\n"
    "\\begin{circuitikz}[american]\n"
    "__BODY__\n"
    "\\end{circuitikz}\n"
    "\\end{document}\n"
)


def _env() -> dict:
    env = dict(os.environ)
    if TINYTEX_BIN.is_dir():
        env["PATH"] = str(TINYTEX_BIN) + os.pathsep + env.get("PATH", "")
    return env


def _tool(name: str) -> str:
    found = shutil.which(name, path=_env()["PATH"])
    if not found:
        raise RuntimeError(f"没找到 {name}（circuitikz 后端需要 TeX + pdftocairo；"
                           f"TeX 在 {TINYTEX_BIN}）")
    return found


def draw(spec: dict, out_dir: str | Path, *, show_png: bool = True) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = spec["name"]
    tex_path = out_dir / f"{name}.tex"
    pdf_path = out_dir / f"{name}.pdf"
    svg_path = out_dir / f"{name}.svg"

    body = str(spec.get("tikz") or "").strip()
    if not body:
        raise ValueError("circuitikz 后端需要 spec['tikz']（TikZ 源码）")
    tex_path.write_text(DOC.replace("__BODY__", body), encoding="utf-8")

    log = subprocess.run([_tool("pdflatex"), "-interaction=nonstopmode", "-halt-on-error",
                          "-output-directory", str(out_dir), str(tex_path)],
                         capture_output=True, env=_env(), timeout=300)
    if log.returncode != 0 or not pdf_path.is_file():
        # TeX can insert line breaks into the middle of multibyte characters.
        tail = "\n".join(line for stream in (log.stdout, log.stderr)
                         for line in stream.decode("utf-8", errors="replace").splitlines()[-12:])
        raise RuntimeError(f"pdflatex 编译失败（{name}）：\n{tail}")
    conv = subprocess.run([_tool("pdftocairo"), "-svg", str(pdf_path), str(svg_path)],
                          capture_output=True, env=_env(), timeout=300)
    if conv.returncode != 0 or not svg_path.is_file():
        diagnostic = conv.stderr.decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"pdftocairo 转换失败（{name}）：{diagnostic}")

    svg_text = svg_path.read_text(encoding="utf-8")
    paths = len(re.findall(r"<path", svg_text))
    dims = re.search(r'width="([0-9.]+)pt"\s+height="([0-9.]+)pt"', svg_text)
    canvas = [float(dims.group(1)), float(dims.group(2))] if dims else [0.0, 0.0]
    payload = {
        "kind": "structural",
        "backend": "circuitikz",
        "name": name,
        "min_paths": int((spec.get("assertions") or {}).get("min_paths") or 8),
        "min_bytes": int((spec.get("assertions") or {}).get("min_bytes") or 1500),
        # TeX 的文字是字形轮廓，所以**不查 <text>**；改查路径数与画布尺寸
        "paths": paths,
        "canvas_pt": canvas,
        "canvas_min_pt": float((spec.get("assertions") or {}).get("canvas_min_pt") or 60.0),
        "tikz_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()[:16],
        "labels": list((spec.get("assertions") or {}).get("labels") or []),
        "svg_bytes": len(svg_text.encode("utf-8")),
    }
    geometry = out_dir / f"{name}.geometry.json"
    geometry.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    produced = {"svg": str(svg_path), "geometry": str(geometry), "pdf": str(pdf_path)}
    if show_png:
        produced["png"] = ""
    return produced


__all__ = ["draw", "TINYTEX_BIN"]
