#!/usr/bin/env python3
"""
Prism live web service — stdlib only, no dependencies.

Local:
    python3 serve.py [--repo <default-repo-or-url>] [--invariants <corpus.json>] [--port 8765]

Deployed (e.g. Render) it reads these env vars:
    PORT                 port to bind (Render sets this)                     [default 8765]
    HOST                 interface to bind                                   [default 0.0.0.0]
    PRISM_TOKEN          if set, /api/* requires ?token=... (lock down public deploys)
    PRISM_ALLOWED_REPOS  comma-separated substrings; only matching repos may be reviewed
    PRISM_BASE_PATH      serve under a sub-path, e.g. /prism (behind a reverse proxy)
    GITHUB_TOKEN         for private repos / GitHub API rate limits
    PRISM_DEFAULT_REPO   default repo shown in the UI
    PRISM_INVARIANTS     default invariant corpus path
"""
import argparse
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diff_pr           # noqa: E402
from gitutil import is_url  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = {}   # (repo, merge, base, head, pr, inv) -> review


def make_handler(cfg):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

        def _authorized(self, g):
            if not cfg["token"]:
                return True
            return (g("token") or self.headers.get("X-Prism-Token")) == cfg["token"]

        def _repo_ok(self, repo):
            if not cfg["allowed"]:
                return True
            return any(a and a in repo for a in cfg["allowed"])

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            g = lambda k, d=None: (q.get(k) or [d])[0]  # noqa: E731
            path = u.path
            bp = cfg["base_path"]
            if bp and path.startswith(bp):
                path = path[len(bp):] or "/"
            try:
                if path in ("/", "/index.html", ""):
                    return self._send(200, open(os.path.join(HERE, "web", "app.html"), "rb").read(),
                                      "text/html; charset=utf-8")
                if path == "/healthz":
                    return self._json(200, {"ok": True})

                if path.startswith("/api/") and not self._authorized(g):
                    return self._json(401, {"error": "unauthorized — append ?token=…"})

                if path == "/api/config":
                    return self._json(200, {"repo": cfg["repo"], "invariants": cfg["invariants"],
                                            "locked": bool(cfg["allowed"])})
                if path == "/api/prs":
                    repo = g("repo") or cfg["repo"]
                    if not repo:
                        return self._json(400, {"error": "no repo given"})
                    if not self._repo_ok(repo):
                        return self._json(403, {"error": "repo not in allowlist"})
                    return self._json(200, {"repo": repo, "prs": diff_pr.list_merges(repo, int(g("n", "30")))})
                if path == "/api/review":
                    repo = g("repo") or cfg["repo"]
                    if not self._repo_ok(repo):
                        return self._json(403, {"error": "repo not in allowlist"})
                    inv = g("invariants") or cfg["invariants"] or None
                    key = (repo, g("merge"), g("base"), g("head"), g("pr"), inv)
                    if key not in CACHE:
                        res = diff_pr.run_review(repo, base=g("base"), head=g("head"),
                                                 merge=g("merge"), pr=g("pr"), invariants_path=inv)
                        CACHE[key] = res["review"]
                    return self._json(200, CACHE[key])
                return self._json(404, {"error": "not found"})
            except Exception as e:
                traceback.print_exc()
                return self._json(500, {"error": str(e)})
    return Handler


def _norm_repo(r):
    return r if (not r or is_url(r)) else os.path.abspath(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.environ.get("PRISM_DEFAULT_REPO", ""))
    ap.add_argument("--invariants", default=os.environ.get("PRISM_INVARIANTS", ""))
    ap.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    args = ap.parse_args()

    cfg = {
        "repo": _norm_repo(args.repo),
        "invariants": _norm_repo(args.invariants),
        "token": os.environ.get("PRISM_TOKEN", ""),
        "allowed": [s.strip() for s in os.environ.get("PRISM_ALLOWED_REPOS", "").split(",") if s.strip()],
        "base_path": os.environ.get("PRISM_BASE_PATH", "").rstrip("/"),
    }
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(cfg))
    print(f"Prism  →  http://{args.host}:{args.port}{cfg['base_path'] or ''}")
    print(f"  default repo: {cfg['repo'] or '(enter one in the UI)'}")
    if cfg["token"]:
        print("  access:       token required (?token=…)")
    elif args.host == "0.0.0.0":
        print("  WARNING: bound publicly with no PRISM_TOKEN — anyone can trigger repo clones.")
    if cfg["allowed"]:
        print(f"  allowlist:    {cfg['allowed']}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
