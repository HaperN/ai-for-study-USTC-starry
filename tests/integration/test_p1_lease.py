"""Book ownership is proved by a live OS lock, including across processes."""
import importlib
import subprocess
import sys
from pathlib import Path

import pytest


def lease_api():
    try:
        return importlib.import_module("aiforstudy.pipeline.lease")
    except ModuleNotFoundError:
        pytest.fail("BookLease and is_book_active are not implemented")


def test_book_lease_excludes_other_process_and_releases_after_exit(tmp_path):
    api = lease_api()
    root = tmp_path / "book"
    assert not api.is_book_active(root)
    assert not root.exists(), "observing idle state must be read-only"
    script = """
import sys
from pathlib import Path
from aiforstudy.pipeline.lease import BookLease
with BookLease(Path(sys.argv[1])):
    print('locked', flush=True)
    sys.stdin.readline()
"""
    child = subprocess.Popen([sys.executable, "-c", script, str(root)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "locked"
        assert api.is_book_active(root)
        with pytest.raises(api.BookBusyError):
            with api.BookLease(root):
                pytest.fail("second process acquired occupied book")
        with api.BookLease(tmp_path / "independent-book"):
            assert api.is_book_active(tmp_path / "independent-book")
        assert api.is_book_active(root)
    finally:
        child.communicate("done\n", timeout=5)
    assert not api.is_book_active(root)
    with api.BookLease(root):
        assert api.is_book_active(root)


def test_same_process_and_symlink_share_lease(tmp_path):
    api = lease_api()
    root = tmp_path / "book"
    with api.BookLease(root):
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        with pytest.raises(api.BookBusyError):
            with api.BookLease(alias):
                pytest.fail("same-process alias acquired occupied book")
    assert not api.is_book_active(root)


def test_process_crash_does_not_leave_stale_active_state(tmp_path):
    api = lease_api()
    script = """
import os,sys
from aiforstudy.pipeline.lease import BookLease
with BookLease(sys.argv[1]):
    os._exit(9)
"""
    child = subprocess.run([sys.executable, "-c", script, str(tmp_path / "book")], timeout=5)
    assert child.returncode == 9
    assert not api.is_book_active(tmp_path / "book")
    with api.BookLease(tmp_path / "book"):
        assert api.is_book_active(tmp_path / "book")
