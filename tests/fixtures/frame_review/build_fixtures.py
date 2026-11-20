"""生成 T08 画面缺陷夹具（故意遮挡／错误公式／错误高亮）并打印 sha256。

用法（cwd=AIforStudy）：

    PYTHONPATH=src:tests/support:tests/fixtures/frame_review \
      ../.venv-linux/bin/python tests/fixtures/frame_review/build_fixtures.py [out_dir]

默认写到本目录（`tests/fixtures/frame_review/`）；测试自己在 `tmp_path` 里现场生成，
所以这些 PNG **不必**进版本库 —— 本脚本存在的意义是"证据可以一条命令重放"。

⚠️ 更名过：原来叫 `builders.py`，与 `tests/fixtures/visual/builders.py` 在
`sys.modules` 里**同名**，`pytest tests/` 整套跑时会拿到 T06 那一份（单跑文件不会暴露）。
产物：`correct.png`、`occlusion.png`、`wrong-formula.png`、`wrong-highlight.png`、
`defects.json`（每个缺陷的 bbox／object_id／sha256）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import frame_review_builders as builders  # noqa: E402


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else HERE
    manifest = builders.build(out)
    for entry in manifest["frames"].values():
        entry["bytes"] = Path(entry["path"]).stat().st_size
    (out / "defects.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, entry in manifest["frames"].items():
        print(f"{name:>16}  {entry['sha256']}  {entry['bytes']:>6}B  {entry['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
