"""HTTP boundaries for the video UI, with model work replaced by a service double."""
import io
import json
import threading
import urllib.error
import urllib.request

import pytest

from aiforstudy.core.workspace import Workspace
from aiforstudy.ui.server import ControlServer


class VideoService:
    def __init__(self, root):
        self.root = root
        self.job = {"job_id": "video-1", "state": "queued", "stage": "probe", "progress": 0}

    def capabilities(self):
        return {"available": True, "sample_available": True, "max_upload_bytes": 32}

    def register_upload(self, stream, length, filename):
        assert not isinstance(stream, (bytes, io.BytesIO)), "HTTP upload must be streamed"
        self.root.joinpath("received.mp4").write_bytes(stream.read(length))
        return {"id": "source-1", "filename": filename, "bytes": length}

    def sample(self):
        return {"id": "source-sample", "filename": "第一讲.mp4", "bytes": 12}

    def submit(self, body):
        assert body["source_id"] == "source-1"
        return self.job

    def list_jobs(self):
        return [self.job]

    def get(self, job_id):
        if job_id != "video-1":
            raise KeyError("missing job")
        return self.job

    def cancel(self, job_id):
        self.job["state"] = "cancelled"
        return self.get(job_id)

    def resume(self, job_id):
        self.job["state"] = "queued"
        return self.get(job_id)

    def reconcile(self, job_id):
        self.job.update(state='queued',revision={'state':'queued','stage':'reconcile','parent_result':'result.json'})
        return self.get(job_id)

    def artifact(self, job_id, relative):
        return self.root / "received.mp4" if job_id == "video-1" and relative == "source.mp4" else None


@pytest.fixture
def client(tmp_path):
    control = ControlServer(Workspace(root=tmp_path))
    control._video_service = VideoService(tmp_path)
    httpd = control.make_server()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def request(path, body=None, headers=None, method=None, authorized=True):
        head = {"Origin": control.origin}
        if authorized:
            head["X-Aifs-Token"] = control.token
        head.update(headers or {})
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            head["Content-Type"] = "application/json"
        req = urllib.request.Request(control.origin + path, body, head, method=method)
        try:
            response = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    yield request, tmp_path
    httpd.shutdown()
    httpd.server_close()


def test_entry_and_assets_are_served(client):
    request, _ = client
    assert b'href="/video"' in request("/")[2]
    for path in ("/video", "/video-result.html", "/video.js", "/video-analysis/result.js",
                 "/video-assets/audible-audio.js", "/video-assets/katex.min.js", "/video-assets/katex.min.css"):
        assert request(path)[0] == 200, path


def test_reconciliation_uses_the_same_authorized_write_boundary(client):
    request,_=client
    assert request('/api/videos/video-1/reconcile',{},authorized=False)[0]==400
    status,_,body=request('/api/videos/video-1/reconcile',{})
    assert status==200 and json.loads(body)['revision']['stage']=='reconcile'


def test_installed_package_assets_work_without_workspace_blocks(client, monkeypatch):
    request, workspace = client
    package = workspace / "site-packages" / "aiforstudy"
    render = package / "_legacy_tree" / "blocks" / "render"
    assets = {
        "audible-audio.js": ("assets/audible-audio.js", b"/* installed audio driver */"),
        "katex.min.js": ("vendor/katex.min.js", b"/* installed katex */"),
        "katex.min.css": ("vendor/katex.min.css", b".katex { color: black; }"),
        "fonts/KaTeX_Main-Regular.woff2": ("vendor/fonts/KaTeX_Main-Regular.woff2", b"wOF2-test-font"),
    }
    for relative, content in assets.values():
        target = render / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (render / "vendor" / "private.js").write_text("not public")
    assert not (workspace / "blocks").exists()
    monkeypatch.setattr("aiforstudy.ui.server.__file__", str(package / "ui" / "server.py"))
    for name, (_, content) in assets.items():
        status, headers, body = request("/video-assets/" + name)
        assert (status, body) == (200, content), name
        assert headers["X-Content-Type-Options"] == "nosniff"
    for name in ("private.js", "fonts/%2e%2e/private.js", "fonts/../../private.js"):
        assert request("/video-assets/" + name)[0] == 404


def test_installed_assets_take_precedence_over_source_fallback(client, monkeypatch):
    request, workspace = client
    package = workspace / "site-packages" / "aiforstudy"
    for prefix, content in (("_legacy_tree/blocks", "installed"), ("blocks", "source fallback")):
        target = package / prefix / "render/assets/audible-audio.js"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    monkeypatch.setattr("aiforstudy.ui.server.__file__", str(package / "ui/server.py"))
    assert request("/video-assets/audible-audio.js")[2] == b"installed"


def test_upload_stream_and_submission(client):
    request, root = client
    status, _, data = request("/api/videos/upload", b"video-content", {"X-Video-Filename": "lesson.mp4"})
    assert status == 201
    assert json.loads(data)["id"] == "source-1"
    assert (root / "received.mp4").read_bytes() == b"video-content"
    assert request("/api/videos", {"source_id": "source-1"})[0] == 202
    assert json.loads(request("/api/videos")[2])["jobs"][0]["state"] == "queued"
    assert json.loads(request("/api/videos/video-1/cancel", {})[2])["state"] == "cancelled"
    assert json.loads(request("/api/videos/video-1/resume", {})[2])["state"] == "queued"
    assert request("/api/videos/missing")[0] == 404


@pytest.mark.parametrize("headers,authorized", [({}, False), ({"Origin": "https://foreign.example"}, True)])
def test_write_gate_covers_upload_and_sample(client, headers, authorized):
    request, root = client
    assert request("/api/videos/upload", b"video", {"X-Video-Filename": "x.mp4", **headers}, authorized=authorized)[0] == 400
    assert not (root / "received.mp4").exists()
    assert request("/api/videos/sample", {}, headers, authorized=authorized)[0] == 400


@pytest.mark.parametrize("filename,body", [("../evil.mp4", b"video"), ("%2fetc%2fvideo.mp4", b"video"), ("x.html", b"video"), ("x.mp4", b""), ("x.mp4", b"x" * 33)])
def test_upload_rejects_unsafe_or_invalid_input(client, filename, body):
    request, root = client
    assert request("/api/videos/upload", body, {"X-Video-Filename": filename})[0] == 400
    assert not (root / "received.mp4").exists()


@pytest.mark.parametrize("payload", [{}, {"source_path": "/etc/passwd"}, {"source_id": "source-1", "source_path": "/etc/passwd"}])
def test_submit_requires_registered_source_and_no_paths(client, payload):
    assert client[0]("/api/videos", payload)[0] == 400


def test_artifact_is_readonly_range_and_head(client):
    request, root = client
    (root / "received.mp4").write_bytes(b"0123456789")
    status, headers, data = request("/api/videos/video-1/artifacts/source.mp4", headers={"Range": "bytes=2-5"})
    assert (status, data, headers["Content-Range"]) == (206, b"2345", "bytes 2-5/10")
    assert "sandbox allow-scripts" in headers["Content-Security-Policy"]
    assert request("/api/videos/video-1/artifacts/source.mp4", method="HEAD")[2] == b""
    assert request("/api/videos/video-1/artifacts/source.mp4", {})[0] == 405
    assert request("/api/videos/video-1/artifacts/../outside")[0] == 404
