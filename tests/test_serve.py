"""The web-UI invariants endpoints: GET /api/invariants lists candidates, POST /api/invariants/
confirm writes the (server-configured) corpus. Spins a real handler on an ephemeral port so the
HTTP wiring is exercised, not mocked."""
import json
import subprocess
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import pytest
import serve
from conftest import BLOG


def _git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A one-commit git repo built from the blog fixture (enough for discovery to run)."""
    d = str(tmp_path / "r")
    subprocess.run(["cp", "-r", BLOG, d], check=True)
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t.t")
    _git(d, "config", "user.name", "t")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "init")
    return d


@pytest.fixture
def server(repo, tmp_path):
    """Serve with a writable corpus configured; yields (base_cfg-less) host:port and corpus path."""
    corpus = tmp_path / "corpus.json"
    corpus.write_text("[]")
    cfg = {"repo": repo, "invariants": str(corpus), "token": "", "allowed": [],
           "base_path": "", "preload": {}}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve.make_handler(cfg))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    serve.INV_CACHE.clear()
    try:
        yield httpd.server_address, str(corpus)
    finally:
        httpd.shutdown()


def _req(addr, method, path, body=None):
    conn = HTTPConnection(*addr, timeout=30)
    payload = json.dumps(body) if body is not None else None
    conn.request(method, path, body=payload,
                 headers={"Content-Type": "application/json"} if payload else {})
    r = conn.getresponse()
    data = json.loads(r.read() or "{}")
    conn.close()
    return r.status, data


def test_get_invariants_lists_candidates(server):
    addr, _ = server
    status, d = _req(addr, "GET", "/api/invariants")   # no repo param → defaults to cfg repo
    assert status == 200 and d["can_confirm"] is True
    assert d["candidates"] and d["confirmed_ids"] == []


def test_post_confirm_writes_corpus(server):
    addr, corpus = server
    _, listing = _req(addr, "GET", "/api/invariants")
    cid = listing["candidates"][0]["id"]
    status, d = _req(addr, "POST", "/api/invariants/confirm", {"id": cid, "owner": "sec"})
    assert status == 200 and d["ok"] and cid in d["confirmed_ids"]
    on_disk = json.loads(open(corpus).read())
    row = next(c for c in on_disk if c["id"] == cid)
    assert row["confirmed"] is True and row["owner"] == "sec"


def test_blame_endpoint_returns_a_verdict(server):
    addr, _ = server
    status, d = _req(addr, "GET", "/api/blame?id=auth-before-write")   # repo defaults to cfg repo
    assert status == 200 and d["id"] == "auth-before-write" and "text" in d


def test_blame_endpoint_requires_id(server):
    addr, _ = server
    status, d = _req(addr, "GET", "/api/blame")
    assert status == 400 and "id" in d["error"]


def test_confirm_requires_configured_corpus(repo):
    """With no corpus configured, confirming is refused (never writes a client-chosen path)."""
    cfg = {"repo": repo, "invariants": "", "token": "", "allowed": [], "base_path": "", "preload": {}}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve.make_handler(cfg))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        status, d = _req(httpd.server_address, "POST", "/api/invariants/confirm", {"id": "x"})
        assert status == 400 and "corpus" in d["error"]
    finally:
        httpd.shutdown()
