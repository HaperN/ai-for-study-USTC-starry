"""生成 T06 静态夹具：规格 JSON + 可信源码 + 独立数学报告。

用法（cwd=AIforStudy，包未实现时用 `--specs-only` 只写规格）：

    PYTHONPATH=src:tests/support ../.venv-linux/bin/python \
        tests/fixtures/visual/build_fixtures.py [--specs-only]

产物（`tests/fixtures/visual/`）：

* `<name>.spec.json`        —— 受限场景规格（T07 的输入）
* `<name>.source.py`        —— 可信编译器输出（本任务不渲染视频）
* `<name>.source.sha256`    —— 可信源码 hash
* `<name>.math_report.json` —— 独立数学检查报告（不调用编译器答案函数）

测试 `test_fixtures_are_reproducible` 会重新编译／复核并与这些文件逐字节比较，
所以**不要手改**产物，改 builders.py 后重跑本脚本。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]                      # AIforStudy/
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import builders  # noqa: E402

SOURCE_SAMPLES = ("saddle-a1-h0-k0", "saddle-a0.5-h0.5-k-0.5",
                  "saddle-a2-h0.5-k-0.5", "pseudo-cancel-a2-b3",
                  "vector-sum", "matrix-apply")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    print(f"  写入 {path.relative_to(ROOT)}  ({len(text.encode('utf-8'))} bytes)")


def write_specs() -> dict:
    specs = builders.dump_specs()
    for name, spec in specs.items():
        _write(HERE / f"{name}.spec.json",
               json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _write(HERE / "teaching-plan.json",
           json.dumps(builders.teaching_plan_spec(), ensure_ascii=False, indent=2,
                      sort_keys=True) + "\n")
    return specs


def write_artifacts(specs: dict) -> None:
    from aiforstudy.blocks.visual.compiler import RenderProfile, compile_scene
    from aiforstudy.blocks.visual.math_check import verify_math
    from aiforstudy.blocks.visual.spec import parse_visual_spec

    for name in SOURCE_SAMPLES:
        spec = parse_visual_spec(specs[name])
        scene = compile_scene(spec, RenderProfile.DRAFT)
        _write(HERE / f"{name}.source.py", scene.source)
        _write(HERE / f"{name}.source.sha256", scene.source_hash + "\n")
        report = verify_math(spec)
        _write(HERE / f"{name}.math_report.json",
               json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        digest = hashlib.sha256(scene.source.encode("utf-8")).hexdigest()
        assert "sha256:" + digest == scene.source_hash, "源码 hash 与实际内容不一致"
        print(f"  {name}: source_hash={scene.source_hash}  数学状态={report.status}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--specs-only", action="store_true",
                        help="只写规格 JSON（实现尚不存在时用）")
    args = parser.parse_args(argv)
    print("T06 夹具生成：")
    specs = write_specs()
    if args.specs_only:
        print("（--specs-only：跳过可信源码与数学报告）")
        return 0
    write_artifacts(specs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
