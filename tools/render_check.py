#!/usr/bin/env python3
"""把网页丢给无头浏览器渲染，然后：① 截一张图 ② 取出页面里所有 .status 行的文字。
   —— 这是"我自己看图"的固定入口，模型页以后都走它。

用法：
  python3 tools/render_check.py <网页路径> [--width 1400] [--height 4300] [--out /tmp/x.png]
  没装无头浏览器时以 3 退出（算跳过，不算失败）。
"""
import argparse, os, re, subprocess, sys, tempfile

CHROME_CANDIDATES = [
    ".devtools/chrome/chrome-linux64/chrome",
    os.path.expanduser("~/.cache/ms-playwright/chromium/chrome-linux/chrome"),
]


def find_chrome(root):
    for c in CHROME_CANDIDATES:
        p = os.path.join(root, c) if not os.path.isabs(c) else c
        if os.path.exists(p):
            return p
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page")
    ap.add_argument("--width", type=int, default=1400)
    ap.add_argument("--height", type=int, default=4300)
    ap.add_argument("--out", default="/tmp/render_check.png")
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--wait", type=int, default=22000)
    args = ap.parse_args()

    chrome = find_chrome(args.root)
    if not chrome:
        print("没有无头浏览器，跳过（装法见 docs/交互建模与排版方案.md 附录 B.5）")
        return 3
    page = os.path.abspath(args.page)
    url = "file://" + page
    base = [chrome, "--headless=old", "--no-sandbox", "--disable-dev-shm-usage",
            "--user-data-dir=" + tempfile.mkdtemp(prefix="rc_"),
            "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
            "--hide-scrollbars", "--virtual-time-budget=%d" % args.wait,
            "--window-size=%d,%d" % (args.width, args.height)]

    dom_run = subprocess.run(base + ["--dump-dom", url], capture_output=True, text=True, timeout=300)
    dom = dom_run.stdout
    # ⚠️ 两个退出码都要看（2026-09-26 结构审计）：原来截图那条的返回码被丢掉，
    #    失败时只打印一句 "(没出来)"，没有任何人消费它 —— 于是"截图没生成"看起来像正常。
    shot = subprocess.run(base + ["--screenshot=" + args.out, url],
                          capture_output=True, text=True, timeout=300)
    if dom_run.returncode != 0:
        print(f"✗ 取 DOM 失败（退出码 {dom_run.returncode}）：{(dom_run.stderr or '')[-200:]}")
    if not os.path.exists(args.out):
        print(f"✗ 截图没生成（退出码 {shot.returncode}）：{(shot.stderr or '')[-200:]}")
        return 1

    print("截图:", args.out, "(%.0f KB)" % (os.path.getsize(args.out) / 1024))
    for m in re.finditer(r'<p class="status" id="status-([a-z0-9]+)">(.*?)</p>', dom, re.S):
        text = re.sub(r"<[^>]+>", "", m.group(2))
        print("  %s: %s" % (m.group(1), text.strip()))
    # 页面里自己报的其它数字
    for m in re.finditer(r'id="(glinfo|copy-msg)"[^>]*>(.*?)</', dom, re.S):
        text = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if text:
            print("  %s: %s" % (m.group(1), text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
