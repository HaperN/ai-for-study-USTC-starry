#!/bin/sh
# 用仓库自带的 Chromium 给已渲染的阅读页截整页 PNG（只读，离线）。
# 用法: tools/shot_reader.sh <index.html 绝对路径> <输出 png> [宽 高]
set -eu
html="$1"; out="$2"; w="${3:-1280}"; h="${4:-2400}"
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
chrome="$repo/.devtools/chrome/chrome-linux64/chrome"
[ -x "$chrome" ] || { echo "缺 Chromium: $chrome" >&2; exit 2; }
profile=$(mktemp -d)
trap 'rm -rf "$profile"' EXIT
"$chrome" --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
  --user-data-dir="$profile" --window-size="${w},${h}" \
  --screenshot="$out" "file://$html" >/dev/null 2>&1
ls -l "$out"
