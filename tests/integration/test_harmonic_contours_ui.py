"""Execute the production offline harmonic template with DOM and real Chromium."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_harmonic_contours_dom_contracts():
    node = shutil.which("node")
    if not node or not (ROOT / "node_modules/jsdom").is_dir():
        pytest.skip("Node/jsdom unavailable")
    result = subprocess.run([node, "--test", "tests/ui/harmonic_contours.test.cjs"],
                            cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_harmonic_contours_in_real_browser(tmp_path):
    node = shutil.which("node")
    if not node or not (ROOT / ".devtools/chrome/chrome-linux64/chrome").is_file():
        pytest.skip("Chromium/Node unavailable")
    result = subprocess.run([node, "tests/ui/harmonic_contours.browser.cjs", str(tmp_path)],
                            cwd=ROOT, text=True, capture_output=True, timeout=35)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "P6-harmonic-mobile.png").stat().st_size > 1000
