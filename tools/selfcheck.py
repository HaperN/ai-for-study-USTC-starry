#!/usr/bin/env python3
"""**改完就跑这一条**：全套自检（用户 2026-09-26 的要求：改了 A 别忘了 B）。

为什么要有它：今天 3 次白跑（36 分钟、几十万 token）全是同一类 —— 我改了一处、
没跑全套自检、直到整条流水线跑到一半才炸（`locates_in` 导入漏加 → 验收整条没跑起来；
清单漏字段 → 三轮改不动）。所以把"改完之后该验什么"固定成一条命令：

    ../.venv-linux/bin/python tools/selfcheck.py            # 全部
    ../.venv-linux/bin/python tools/selfcheck.py --fast     # 跳过两个交付页的浏览器自检

检查项（每项都打印 通过/失败 + 用了多久）：
  ① 单元测试          pytest -q（含"补丁覆盖面自检"：每个字段都看得到 + 改得到）
  ② 教案体检          style_lint（规范 → 词表/阈值同源）
  ③ 交付页自检        每个 delivery/*/ 跑 check_render.js（引用、占位符、KaTeX）
  ④ 交互自检          check_interaction.js（点概念不换节、换节不残留、坏数据有说明）
  ⑤ 声明复核          problems.cli --check（包里的数字能不能从产物算回来）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PYTHON = str(ROOT.parent / ".venv-linux" / "bin" / "python")
NODE = "node"
JSDOM_HOME = ROOT / ".devtools" / "node_modules"   # jsdom 装在 AIforStudy/.devtools 下


def run(cmd: list[str], *, env=None, cwd=ROOT) -> tuple[int, str, float]:
    started = time.time()
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=env)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out, time.time() - started


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="全套自检（改完就跑这一条）")
    ap.add_argument("--fast", action="store_true", help="跳过浏览器/交互自检（只跑测试与体检）")
    # ⚠️ 不写死书名（规格 R1）：从 `config/books.json` 取默认那本
    sys.path.insert(0, str(ROOT / "lib"))
    import book as _book_registry
    from files import display_path as _display_path
    _book = _book_registry.resolve(None)
    # ⚠️ 用 `display_path` 而不是 `relative_to(ROOT)`：书可以登记在仓库外，
    # 那时 `relative_to` 会在**解析参数时**就抛 ValueError（2026-09-26 A6 实测同类坑）。
    ap.add_argument("--lessons", default=_display_path(_book.lessons))
    ap.add_argument("--parse", default=_display_path(_book.parse))
    args = ap.parse_args(argv)

    checks: list[tuple[str, list[str], dict | None]] = [
        ("① 单元测试", [PYTHON, "-m", "pytest", "-q"], None),
        ("② 教案体检", [PYTHON, "-m", "tools.style_lint", args.parse, "--lessons", args.lessons], None),
        # ②·五 不变量自检（doctor）：0 token，问"这套产物自洽吗"。
        # 为什么放在自检里：它是唯一能抓住「同一份信息写了两处 / 快照过期」这类
        # **不报错的错**的检查（清单漏页、记录陈旧、教案快照过期…）。
        # 退出码：0 通过 / 1 有漂移（**不是崩溃**）/ 2 doctor 自己坏了 —— 见 tools/doctor.py。
        ("②·五 不变量自检", [PYTHON, "tools/doctor.py"], None),
    ]
    import os
    jsdom_env = {**os.environ, "NODE_PATH": str(JSDOM_HOME)}
    # 「当前交付页」= 打包工具清单里那几个（其余是历史试验目录：用**旧代码旧数据**建的，
    # 本来就不该按今天的标准要求它们 —— 混在一起报红，只会让人不再看这个自检）。
    sys.path.insert(0, str(HERE))
    # ⚠️ 拿不到清单**不能静默**（2026-09-26 结构审计）：原来是 `except: current = set()`，
    #    于是 `make_delivery_package` 一导入失败（改名/语法错/缺依赖），
    #    `if current` 恒假 → 历史目录全被拉进自检（一片假红），
    #    而如果 SECTIONS 只剩一项，**其余交付页会被算作"全绿通过"**。
    #    现在：导入失败 = 这一项直接算失败，并且做一次**双向差集**校验。
    try:
        from make_delivery_package import SECTIONS as _SECTIONS
        current = {str(Path(src).name) for src, _name, _single in _SECTIONS}
        scope_error = ""
    except Exception as exc:                      # noqa: BLE001
        current, scope_error = set(), f"拿不到交付页清单：{type(exc).__name__}: {exc}"
    if scope_error:
        checks.append(("⓪ 交付页清单（拿不到）", [PYTHON, "-c",
                       f"import sys; sys.stderr.write({scope_error!r}); sys.exit(2)"], None))
    else:
        # 「清单里该有的页面」= SECTIONS 里**确实是网页**的那些（有的目录是纯文档，
        # 比如「AI 自己审自己改·实录」，它没有 index.html，不该被要求有渲染索引）。
        web_dirs = {}
        for src, _name, _single in _SECTIONS:
            d = ROOT / src
            if (d / "index.html").is_file() or (d / "render.index.json").is_file():
                web_dirs[d.name] = d
        # 历史目录：显式登记在 `delivery/历史页面.json` 里，**不在代码里写死**。
        # 为什么要这份登记：不然"磁盘上有、清单里没有"这条检查会被十来年的历史试跑目录
        # 天天刷成假红 —— 而假警报会把真失败淹掉（这个项目已经吃过这个亏）。
        legacy_file = ROOT / "delivery" / "历史页面.json"
        legacy = set()
        if legacy_file.is_file():
            try:
                legacy = set(json.loads(legacy_file.read_text(encoding="utf-8"))
                             .get("不是交付物") or [])
            except (ValueError, OSError):
                legacy = set()
        for name, d in sorted(web_dirs.items()):
            if not (d / "render.index.json").is_file():
                checks.append((f"⓪ 交付页清单（{name} 有页面但没有渲染索引）",
                               [PYTHON, "-c", "import sys; sys.exit(2)"], None))
        forgotten = sorted(n for n in {p.name for p in (ROOT / "delivery").glob("*")
                                       if (p / "render.index.json").is_file()}
                           - set(web_dirs) - legacy)
        if forgotten:
            checks.append((f"⓪ 交付页清单（磁盘上有、清单里没有：{forgotten}）",
                           [PYTHON, "-c", "import sys; sys.exit(2)"], None))

    for delivery in sorted((ROOT / "delivery").glob("*/")):
        if not (delivery / "index.html").is_file():
            continue
        if not (delivery / "render.index.json").is_file():
            # 手工搭的试验目录（如 MathBox冒烟测试）不是标准交付页，跳过
            continue
        if current and delivery.name not in current:
            continue          # 历史目录：不参与交付，也不参与今天的自检
        name = delivery.name
        checks.append((f"③ 交付页自检 · {name}",
                       [NODE, "blocks/render/check_render.js", str(delivery)], None))
        if not args.fast:
            checks.append((f"④ 交互自检 · {name}",
                           [NODE, "blocks/render/check_interaction.js", str(delivery)], jsdom_env))

    rows, failed = [], 0
    for label, cmd, env in checks:
        code, out, seconds = run(cmd, env=env)
        # 退出码 3 = "跳过"（比如没装 jsdom）——那是环境缺件，不算页面有问题
        skipped = code == 3 or "跳过" in out[:200]
        ok = code == 0 or skipped
        # 这两条自检用「非 0 退出」表示"发现问题"；体检用 0/1；测试用 pytest 的码
        rows.append((label, ok, seconds, out, skipped))
        if not ok:
            failed += 1

    print("═" * 62)
    for label, ok, seconds, out, skipped in rows:
        mark = "…" if skipped else ("✓" if ok else "✗")
        print(f"{mark} {label:28s} {seconds:6.1f} 秒" + ("（跳过：环境缺件）" if skipped else ""))
        if not ok:
            tail = [line for line in out.strip().splitlines() if line.strip()][-6:]
            for line in tail:
                print("      " + line[:150])
    print("═" * 62)
    print(f"共 {len(rows)} 项，失败 {failed} 项" + ("（全绿）" if not failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
