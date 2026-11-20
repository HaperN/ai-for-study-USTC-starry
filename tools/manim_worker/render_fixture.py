#!/usr/bin/env python3
"""把一个 **T06 固定规格**渲染成真视频（T07 证据与人工复核用）。

     PYTHONPATH=src ../.venv-linux/bin/python tools/manim_worker/render_fixture.py \\
         --spec tests/fixtures/visual/saddle-a1-h0-k0.spec.json --profile final \\
         --out /tmp/t07-final

它做的事只有一件：**走生产路径**（`compile_scene` → `manim_adapter.render_scene` → 隔离 worker），
然后把可复核的原始材料一起写进 `--out`：

    video.mp4 poster.png keyframes/ captions.vtt transcript.txt
    animation_result.json   # §5.3 的 AnimationResult 全文
    measurements.json       # 墙钟／峰值 RSS／输出字节／上限
    ffprobe.json            # ffprobe -show_format -show_streams 原文
    ffmpeg-decode.log       # `ffmpeg -f null -` 完整解码的原始输出与退出码
    environment.json        # 环境指纹（工具版本、字体 hash、MathTex 自检、隔离配置）

为什么值得留一个入口：证据里的视频必须能**一条命令重放**，否则"我渲过"这句话
只能靠相信。脚本本身不做任何判断，判断都在适配器与测试里。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT / "src", ROOT / "tools" / "manim_worker"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from aiforstudy.blocks.visual import manim_adapter as adapter  # noqa: E402
from aiforstudy.blocks.visual import worker as worker_module  # noqa: E402
from aiforstudy.blocks.visual.compiler import compile_scene  # noqa: E402
from aiforstudy.blocks.visual.spec import parse_visual_spec  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="渲染一份 T06 规格（真实 Manim worker）")
    parser.add_argument("--spec", required=True, help="T06 规格 JSON")
    parser.add_argument("--profile", default="final", choices=("draft", "final"))
    parser.add_argument("--out", required=True, help="输出目录")
    parser.add_argument("--workspace", default=str(ROOT), help="工作区根（默认本仓库）")
    parser.add_argument("--scratch", default="", help="worker 临时根（默认 <out>/scratch）")
    parser.add_argument("--interpreter", default="", help="独立渲染解释器（默认按配置解析）")
    args = parser.parse_args(argv)

    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    spec = parse_visual_spec(json.loads(Path(args.spec).read_text(encoding="utf-8")))
    scene = compile_scene(spec, args.profile)
    services = adapter.default_services(workspace_root=args.workspace,
                                        scratch_root=Path(args.scratch) if args.scratch else None)
    if args.interpreter:
        services = adapter.ManimServices(
            worker_dir=services.worker_dir, interpreter=args.interpreter,
            scratch_root=services.scratch_root or (out / "scratch"),
            cache_dir=out / "cache")
    limits = adapter.limits_for_profile(services.worker_dir, args.profile)
    result = adapter.render_scene(scene, limits, services, spec=spec, profile=args.profile,
                                  output_dir=out)
    (out / "animation_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    worker_report = result["technical_report"]["worker"]
    (out / "measurements.json").write_text(json.dumps({
        "unit": spec.unit_id, "request_id": spec.request_id, "profile": args.profile,
        "status": result["status"],
        "wall_seconds": worker_report["wall_seconds"],
        "peak_rss_bytes": worker_report["peak_rss_bytes"],
        "output_bytes": worker_report["output_bytes"],
        "limits": result["technical_report"]["limits"],
        "isolation_backend": worker_report["isolation"].get("backend"),
        "cache_committed": worker_report["cache_committed"],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    environment = result["technical_report"].get("environment") or {}
    (out / "environment.json").write_text(
        json.dumps(environment, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    ffprobe = "ffprobe"
    probe = subprocess.run([ffprobe, "-v", "error", "-print_format", "json", "-show_format",
                            "-show_streams", str(out / "video.mp4")],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (out / "ffprobe.json").write_bytes(probe.stdout)
    decode = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(out / "video.mp4"),
                             "-f", "null", "-"], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    (out / "ffmpeg-decode.log").write_text(
        f"# ffmpeg -i video.mp4 -f null -  → 退出码 {decode.returncode}\n"
        f"{decode.stdout.decode('utf-8', 'replace')}\n", encoding="utf-8")
    worker_log = Path(worker_report["log_path"])
    if worker_log.is_file():
        (out / "worker.log").write_bytes(worker_log.read_bytes())
    print(json.dumps({"status": result["status"], "out": str(out),
                      "video_seconds": worker_report["wall_seconds"],
                      "resources": len(result["resources"]),
                      "ffmpeg_decode_exit": decode.returncode}, ensure_ascii=False))
    return 0 if result["status"] == "fulfilled" else 1


if __name__ == "__main__":
    raise SystemExit(main())
