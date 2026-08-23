#!/usr/bin/env python3
"""
Live web service for Semantic Review — stdlib only, no dependencies.

    python3 serve.py [--repo <default-repo>] [--invariants <corpus.json>] [--port 8765]

Then open http://127.0.0.1:8765 : pick a PR, get the ranked review + an interactive
node-link graph of each change. Runs the analysis on demand and caches results.
"""
import argparse
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diff_pr  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = {}   # (repo, merge, base, head, inv) -> review


def make_handler(defaults):
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

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            g = lambda k, d=None: (q.get(k) or [d])[0]  # noqa: E731
            try:
                if u.path in ("/", "/index.html"):
                    return self._send(200, open(os.path.join(HERE, "web", "app.html"), "rb").read(),
                                      "text/html; charset=utf-8")
                if u.path == "/api/config":
                    return self._json(200, {"repo": defaults["repo"], "invariants": defaults["invariants"]})
                if u.path == "/api/prs":
                    repo = g("repo") or defaults["repo"]
                    if not repo:
                        return self._json(400, {"error": "no repo given"})
                    return self._json(200, {"repo": repo, "prs": diff_pr.list_merges(repo, int(g("n", "30")))})
                if u.path == "/api/review":
                    repo = g("repo") or defaults["repo"]
                    inv = g("invariants") or defaults["invariants"] or None
                    key = (repo, g("merge"), g("base"), g("head"), inv)
                    if key not in CACHE:
                        res = diff_pr.run_review(repo, base=g("base"), head=g("head"),
                                                 merge=g("merge"), invariants_path=inv)
                        CACHE[key] = res["review"]
                    return self._json(200, CACHE[key])
                return self._json(404, {"error": "not found"})
            except Exception as e:
                traceback.print_exc()
                return self._json(500, {"error": str(e)})
    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="")
    ap.add_argument("--invariants", default="")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    defaults = {"repo": os.path.abspath(args.repo) if args.repo else "",
                "invariants": os.path.abspath(args.invariants) if args.invariants else ""}
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(defaults))
    print(f"Semantic Review  →  http://{args.host}:{args.port}")
    print(f"  default repo: {defaults['repo'] or '(enter one in the UI)'}")
    if defaults["invariants"]:
        print(f"  enforcing:    {defaults['invariants']}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
