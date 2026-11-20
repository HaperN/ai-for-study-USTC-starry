#!/usr/bin/env python3
"""M8 出口验收：把"本地界面"的通过条件变成一条可复跑的命令。

方案 §14 的 M8：

| M8 本地界面 | ui/server/api、resources/control-ui |
| **导入/选章/计划/运行/取消/恢复/结果全流程，CLI/UI 计划等价；刷新页面可恢复状态** | M2接口冻结；正式交付依赖 M7 |

判据：
    M8-1 CLI 与 HTTP **共用校验器**，等价请求 plan digest 一致（A06）
    M8-2 同 idempotency_key 只创建一个任务（双击不建两个）（A06）
    M8-3 同书第二个任务**排队**而不是覆盖（A30）
    M8-4 刷新/关页**不取消**任务；状态在服务端（A30）
    M8-5 取消区分"正在取消"与"已取消"
    M8-6 重启后无存活 worker 的 running → interrupted，可续（A30）
    M8-7 写请求需要启动令牌；响应里出现密钥字段直接报错
    M8-8 产物按登记 ID 定位，**禁止路径穿越**
    M8-9 界面按钮可用性来自服务端 capability，不在前端复制业务规则
    M8-10 事件流短轮询游标可用（after=N）
    M8-11 `aifs ui` 子命令在位
    M8-12 界面静态资源不引用任何外部地址（离线可用）
    M8-13 全量测试退出码 0
"""
from __future__ import annotations

import argparse, json, re, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent.parent
REPO_ROOT = PKG_ROOT.parent
PYTHON = str(REPO_ROOT / ".venv-linux" / "bin" / "python")
M8_TESTS = "tests/integration/test_m8_ui.py"
UI_DIR = PKG_ROOT / "src/aiforstudy/resources/control-ui"


def _pytest(targets):
    proc = subprocess.run([PYTHON, "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider"],
                          cwd=str(PKG_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=1800)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def check() -> dict:
    results = []

    def add(cid, title, ok, evidence, detail=""):
        results.append({"id": cid, "判据": title, "通过": ok, "证据": evidence, "细节": detail})

    code, out = _pytest([M8_TESTS])
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    for cid, title in (
        ("M8-1", "CLI 与 HTTP 共用校验器，等价请求 plan digest 一致（A06）"),
        ("M8-2", "同 idempotency_key 只创建一个任务（A06）"),
        ("M8-3", "同书第二个任务排队而不是覆盖（A30）"),
        ("M8-4", "刷新/关页不取消任务（A30）"),
        ("M8-5", "取消区分'正在取消'与'已取消'"),
        ("M8-6", "重启后无存活 worker 的 running → interrupted，可续（A30）"),
        ("M8-7", "写请求需要启动令牌；响应含密钥字段直接报错"),
        ("M8-8", "产物按登记 ID 定位，禁止路径穿越"),
        ("M8-9", "按钮可用性来自服务端 capability"),
        ("M8-10", "事件流短轮询游标可用（after=N）"),
    ):
        add(cid, title, code == 0, M8_TESTS, tail[:160])

    # `aifs ui` 在位
    proc = subprocess.run([PYTHON, "-m", "aiforstudy", "ui", "--once", "--json",
                           "--workspace", str(PKG_ROOT)],
                          cwd=str(PKG_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=300)
    out_ui = proc.stdout.decode("utf-8", "replace").strip()
    ok_ui = proc.returncode == 0
    detail_ui = ""
    if ok_ui:
        try:
            payload = json.loads(out_ui.splitlines()[-1])
            ok_ui = bool(payload.get("url", "").startswith("http://127.0.0.1:")) and \
                bool(payload.get("token"))
            detail_ui = f"{payload.get('url')}｜令牌长度 {len(payload.get('token') or '')}"
        except ValueError:
            ok_ui = False
            detail_ui = out_ui[:200]
    add("M8-11", "`aifs ui` 子命令在位（默认只绑 127.0.0.1）", ok_ui,
        "aiforstudy.cli:cmd_ui", detail_ui)

    # 界面静态资源不引用任何外部地址
    offenders = []
    if UI_DIR.is_dir():
        for path in sorted(UI_DIR.iterdir()):
            if path.suffix not in (".html", ".js", ".css"):
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r"""["'(]\s*(?:https?:)?//""", text):
                offenders.append(path.name)
    add("M8-12", "界面静态资源不引用任何外部地址（断网可用）", not offenders,
        f"扫描 {UI_DIR.relative_to(PKG_ROOT)}",
        f"发现：{offenders}" if offenders else "没有外部地址")

    code, out = _pytest(["tests/", "-ra"])
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
    skipped = int(m.group(1)) if (m := re.search(r"(\d+) skipped", out)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", out)) else 0
    add("M8-13", "全量测试退出码 0、无 failed", code == 0 and failed == 0 and passed > 0,
        "pytest tests/ -q -ra",
        f"{passed} passed / {skipped} skipped / {failed} failed｜退出码 {code}")
    return {"_meta": {"file_type": "m8_exit_check", "schema_version": "1.0",
                      "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
            "通过": all(r["通过"] for r in results), "判据": results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M8 出口验收")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    payload = check()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for r in payload["判据"]:
            print(f"{'✓' if r['通过'] else '✗'} {r['id']} {r['判据']}")
            if r["细节"]:
                print(f"      {r['细节']}")
        print(f"\nM8 出口：{'通过' if payload['通过'] else '未通过'}")
    return 0 if payload["通过"] else 1


if __name__ == "__main__":
    sys.exit(main())
