"""MinerU 云端 API（fetch）的离线测试：不联网、不花额度。

锁住的命题：
1. **上传必须是"裸 PUT"** —— 多一个 `Content-Type` 就 403 SignatureDoesNotMatch
   （实测踩过：`urllib` 带 body 时会自动加它，所以改用 `http.client`）。
   这条用一个本地假服务器把请求头抓下来直接断言。
2. **解压不许被目录穿越**（压缩包是第三方给的，`../` 一律拒绝）。
3. **token 的三种给法**（显式参数 / 环境变量 / `.secrets/mineru.env`），找不到就给中文指引。
"""
import http.client
import os
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "blocks" / "parse" / "mineru"))
sys.path.insert(0, str(ROOT / "lib"))

import fetch as F          # noqa: E402


# ── ① 裸 PUT：一个头都不许带（尤其 Content-Type）────────────────────────
class _Recorder(BaseHTTPRequestHandler):
    received: list = []

    def do_PUT(self):                                   # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        _Recorder.received.append({
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": self.rfile.read(length),
        })
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):                       # 别把访问日志打到测试输出里
        pass


@pytest.fixture
def fake_oss():
    _Recorder.received = []
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def test_bare_put_sends_no_content_type(fake_oss):
    """这条是实测踩出来的：加了 Content-Type 就 403 SignatureDoesNotMatch。"""
    url = f"http://127.0.0.1:{fake_oss.server_port}/upload?Expires=1&Signature=abc"
    status, _ = F.bare_put(url, b"PDF-BYTES")
    assert status == 200
    assert len(_Recorder.received) == 1
    sent = _Recorder.received[0]["headers"]
    assert "content-type" not in sent, f"带了 Content-Type，OSS 会签名不匹配：{sent}"
    assert _Recorder.received[0]["body"] == b"PDF-BYTES"


def test_bare_put_keeps_the_query_string(fake_oss):
    """预签名的查询串是签名的一部分，必须原样带上。"""
    url = f"http://127.0.0.1:{fake_oss.server_port}/u?X-Amz-Signature=deadbeef"
    F.bare_put(url, b"x")
    assert _Recorder.received, "请求没到"
    # BaseHTTPRequestHandler 把 path 放在 self.path，这里用 body 长度间接确认收到了内容
    assert _Recorder.received[0]["body"] == b"x"


# ── ② 解压：防目录穿越 ─────────────────────────────────────────────────
def _make_zip(path: Path, names: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, "data")
    return path


def test_extract_zip_rejects_path_traversal(tmp_path):
    zip_path = _make_zip(tmp_path / "bad.zip", ["../evil.txt"])
    with pytest.raises(F.MinerUFetchError):
        F.extract_zip(zip_path, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists(), "穿越出去的文件被写出来了"


def test_extract_zip_rejects_absolute_path(tmp_path):
    zip_path = _make_zip(tmp_path / "bad2.zip", ["/tmp/evil-abs.txt"])
    with pytest.raises(F.MinerUFetchError):
        F.extract_zip(zip_path, tmp_path / "out")


def test_extract_zip_keeps_the_expected_files(tmp_path):
    zip_path = _make_zip(tmp_path / "ok.zip",
                         ["layout.json", "abc_content_list.json", "full.md", "images/aa.jpg"])
    written = F.extract_zip(zip_path, tmp_path / "out")
    assert sorted(written) == ["abc_content_list.json", "full.md", "images/aa.jpg", "layout.json"]
    assert (tmp_path / "out" / "images" / "aa.jpg").is_file()


# ── ③ token 的三种给法 ─────────────────────────────────────────────────
def test_load_token_prefers_the_explicit_argument():
    assert F.load_token("sk-explicit") == "sk-explicit"


def test_load_token_reads_the_environment(monkeypatch):
    monkeypatch.setenv("MINERU_API_TOKEN", "sk-from-env")
    assert F.load_token() == "sk-from-env"


def test_load_token_reads_the_secrets_file(monkeypatch, tmp_path):
    monkeypatch.delenv("MINERU_API_TOKEN", raising=False)
    secret = tmp_path / "mineru.env"
    secret.write_text('MINERU_API_TOKEN="sk-from-file"\nOTHER=1\n', encoding="utf-8")
    monkeypatch.setattr(F, "SECRETS", (secret,))
    assert F.load_token() == "sk-from-file"


def test_load_token_explains_where_to_get_one(monkeypatch, tmp_path):
    monkeypatch.delenv("MINERU_API_TOKEN", raising=False)
    monkeypatch.setattr(F, "SECRETS", (tmp_path / "nope.env",))
    with pytest.raises(F.MinerUFetchError) as error:
        F.load_token()
    assert "apiManage/token" in str(error.value), "报错里要写清楚去哪拿 token"

@pytest.mark.parametrize('method', ['GET', 'PUT'])
def test_mineru_single_http_exchange_obeys_total_deadline(method):
    import time
    from http.server import ThreadingHTTPServer
    class Slow(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.command == 'PUT':
                self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200)
            self.send_header('Content-Length', '1000000')
            self.end_headers()
            try:
                for _ in range(70):
                    self.wfile.write(b' '); self.wfile.flush(); time.sleep(.02)
            except (BrokenPipeError, ConnectionResetError):
                pass
        do_PUT = do_GET
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    started = time.monotonic()
    try:
        with pytest.raises(F.MinerUFetchError):
            with F._control(.3):
                url = f'http://127.0.0.1:{server.server_port}/slow'
                F.bare_put(url, b'pdf') if method == 'PUT' else F._request(url)
        assert time.monotonic() - started < 1, 'dribbling body bypassed total deadline'
    finally:
        server.shutdown(); server.server_close()
