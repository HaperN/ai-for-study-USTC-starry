#!/usr/bin/env bash
# Rebuild the isolated renderer. The project interpreter stays independent.
# macOS: brew install cairo pango pkg-config ffmpeg
#        brew install --cask font-noto-sans-cjk-sc
# Linux: install cairo/pango/Python development headers, pkg-config, ffmpeg,
#        TeX, dvisvgm and Noto Sans CJK through the OS package manager.
# Neither path disables kernel isolation. Verification never calls model APIs.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLATFORM="$(uname -s)"
case "$PLATFORM" in
  Darwin) SUFFIX=macos ;;
  Linux) SUFFIX=linux ;;
  *) echo "Unsupported renderer platform: $PLATFORM" >&2; exit 2 ;;
esac
TARGET="${1:-$(dirname "$REPO")/.venv-manim-$SUFFIX}"
PYTHON="${AIFORSTUDY_BOOTSTRAP_PYTHON:-python3}"
BOOTSTRAP_PIP="${AIFORSTUDY_BOOTSTRAP_PIP:-$(dirname "$REPO")/.venv-linux/bin/python}"
MANIM_VERSION="${MANIM_VERSION:-0.21.0}"
for tool in pkg-config cc ffmpeg ffprobe latex dvisvgm; do
  command -v "$tool" >/dev/null || { echo "Missing dependency: $tool" >&2; exit 2; }
done
pkg-config --exists cairo pango || { echo 'Missing cairo/pango development packages' >&2; exit 2; }
"$PYTHON" -c 'import sysconfig,pathlib; assert (pathlib.Path(sysconfig.get_path("include"))/"Python.h").is_file(), "Missing Python headers"'
"$PYTHON" -m venv --without-pip "$TARGET"
if [ -x "$BOOTSTRAP_PIP" ]; then
  "$BOOTSTRAP_PIP" -m pip --python "$TARGET/bin/python" install --upgrade pip wheel
else
  "$TARGET/bin/python" -m ensurepip --upgrade
fi
if [ "$PLATFORM" = Darwin ]; then
  "$TARGET/bin/python" -m pip install -r "$REPO/tools/manim_worker/requirements-macos.txt"
else
  "$TARGET/bin/python" -m pip install "manim==$MANIM_VERSION" matplotlib sympy
fi
if [ "$PLATFORM" = Darwin ] && [ -d "$TARGET/native/lib" ]; then
  export DYLD_LIBRARY_PATH="$TARGET/native/lib"
fi
IMPORT_LOG="$(mktemp /tmp/aifs-render-import.XXXXXX)"
trap 'rm -f "$IMPORT_LOG"' EXIT
if ! "$TARGET/bin/python" -c 'import manim,cairo,manimpango; print("Manim",manim.__version__)' 2>"$IMPORT_LOG"; then
  if [ "$PLATFORM" = Darwin ] && "$PYTHON" -c 'import sys; raise SystemExit("libhvf.dylib" not in open(sys.argv[1]).read())' "$IMPORT_LOG"; then
    echo 'Building a private FreeType library for this macOS version; system libraries stay untouched.'
    "$PYTHON" "$REPO/tools/manim_worker/repair_macos_freetype.py" "$TARGET"
    export DYLD_LIBRARY_PATH="$TARGET/native/lib"
    "$TARGET/bin/python" -c 'import manim,cairo,manimpango; print("Manim",manim.__version__)'
  else
    cat "$IMPORT_LOG" >&2
    exit 3
  fi
fi
"$TARGET/bin/python" -m pip freeze > "$TARGET/requirements-installed.txt"
echo "Installed: $TARGET/bin/python"
echo 'Run the real isolated Chinese + MathTex + audio/video verification:'
echo "PYTHONPATH=src AIFORSTUDY_MANIM_PYTHON='$TARGET/bin/python' '$BOOTSTRAP_PIP' tools/manim_worker/verify_local.py --out /tmp/aiforstudy-render-check"
