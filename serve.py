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
import invariants as inv_mod                          # noqa: E402
from gitutil import is_url, git_toplevel, ensure_local  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = {}       # (repo, merge, base, head, pr, inv) -> review
INV_CACHE = {}   # (repo, n) -> [candidate dict]  (discovery is expensive; cache per repo)


def _discovered(repo, n):
    """Discovered invariant candidates (corpus dicts) for `repo`, cached per (repo, n)."""
    key = (repo, n)
    if key not in INV_CACHE:
        _, cands = inv_mod.discover(ensure_local(repo), n)
        INV_CACHE[key] = inv_mod.corpus(cands)
    return INV_CACHE[key]


def _read_corpus(path):
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return []


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
                if path in ("/", "/index.html", "/app", "/app.html", ""):
                    # `prism serve` goes straight to the usage page (the review UI)
                    return self._send(200, open(os.path.join(HERE, "web", "app.html"), "rb").read(),
                                      "text/html; charset=utf-8")
                if path == "/healthz":
                    return self._json(200, {"ok": True})

                if path.startswith("/api/") and not self._authorized(g):
                    return self._json(401, {"error": "unauthorized — append ?token=…"})

                if path == "/api/config":
                    return self._json(200, {"repo": cfg["repo"], "invariants": cfg["invariants"],
                                            "locked": bool(cfg["allowed"]),
                                            "preload": cfg["preload"]})
                if path == "/api/prs":
                    repo = g("repo") or cfg["repo"]
                    if not repo:
                        return self._json(400, {"error": "no repo given"})
                    if not self._repo_ok(repo):
                        return self._json(403, {"error": "repo not in allowlist"})
                    return self._json(200, {"repo": repo, "prs": diff_pr.list_merges(repo, int(g("n", "30")))})
                if path == "/api/refs":
                    repo = g("repo") or cfg["repo"]
                    if not repo:
                        return self._json(400, {"error": "no repo given"})
                    if not self._repo_ok(repo):
                        return self._json(403, {"error": "repo not in allowlist"})
                    return self._json(200, {"repo": repo,
                                            "branches": diff_pr.list_branches(repo),
                                            "commits": diff_pr.list_commits(repo, int(g("n", "50")))})
                if path == "/api/review":
                    repo = g("repo") or cfg["repo"]
                    if not self._repo_ok(repo):
                        return self._json(403, {"error": "repo not in allowlist"})
                    inv = g("invariants") or cfg["invariants"] or None
                    key = (repo, g("merge"), g("base"), g("head"), g("pr"), g("commit"), inv)
                    if key not in CACHE:
                        res = diff_pr.run_review(repo, base=g("base"), head=g("head"),
                                                 merge=g("merge"), pr=g("pr"), commit=g("commit"),
                                                 invariants_path=inv)
                        CACHE[key] = res["review"]
                    return self._json(200, CACHE[key])
                if path == "/api/invariants":
                    repo = g("repo") or cfg["repo"]
                    if not repo:
                        return self._json(400, {"error": "no repo given"})
                    if not self._repo_ok(repo):
                        return self._json(403, {"error": "repo not in allowlist"})
                    cands = _discovered(repo, int(g("snapshots", "6")))
                    confirmed_ids = [c["id"] for c in _read_corpus(cfg["invariants"])
                                     if c.get("confirmed")]
                    return self._json(200, {"repo": repo, "candidates": cands,
                                            "confirmed_ids": confirmed_ids,
                                            "corpus": cfg["invariants"],
                                            "can_confirm": bool(cfg["invariants"])})
                if path == "/api/blame":
                    repo = g("repo") or cfg["repo"]
                    if not repo:
                        return self._json(400, {"error": "no repo given"})
                    if not self._repo_ok(repo):
                        return self._json(403, {"error": "repo not in allowlist"})
                    inv_id = g("id")
                    if not inv_id:
                        return self._json(400, {"error": "no invariant id (?id=…)"})
                    result = inv_mod.blame_invariant(ensure_local(repo), inv_id, route=g("route"),
                                                     n=int(g("snapshots", "8")), exact=g("exact") == "1")
                    return self._json(200, {"repo": repo, "id": inv_id, "result": result,
                                            "text": inv_mod.render_blame(result, inv_id, g("route"))})
                return self._json(404, {"error": "not found"})
            except Exception as e:
                traceback.print_exc()
                return self._json(500, {"error": str(e)})

        def do_POST(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            g = lambda k, d=None: (q.get(k) or [d])[0]  # noqa: E731
            path = u.path
            bp = cfg["base_path"]
            if bp and path.startswith(bp):
                path = path[len(bp):] or "/"
            try:
                if not self._authorized(g):
                    return self._json(401, {"error": "unauthorized — append ?token=…"})
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or "{}") if length else {}

                if path == "/api/invariants/confirm":
                    # Confirm one discovered candidate into the *server-configured* corpus only —
                    # the client never supplies a write path (no arbitrary file writes).
                    corpus_path = cfg["invariants"]
                    if not corpus_path or is_url(corpus_path):
                        return self._json(400, {"error": "confirming needs a local corpus file — "
                                                "start `prism serve --invariants <path.json>`"})
                    repo = body.get("repo") or cfg["repo"]
                    if not repo:
                        return self._json(400, {"error": "no repo given"})
                    if not self._repo_ok(repo):
                        return self._json(403, {"error": "repo not in allowlist"})
                    inv_id = body.get("id")
                    cands = _discovered(repo, int(body.get("snapshots", 6)))
                    match = next((c for c in cands if c["id"] == inv_id), None)
                    if not match:
                        return self._json(404, {"error": f"no discovered candidate {inv_id!r}"})
                    confirmed = inv_mod.confirm_candidate(
                        match, owner=body.get("owner"), keep_baseline=body.get("keep_baseline", True))
                    merged = inv_mod.merge_corpus(_read_corpus(corpus_path), [confirmed])
                    with open(corpus_path, "w", encoding="utf-8") as f:
                        json.dump(merged, f, indent=2, ensure_ascii=False)
                    return self._json(200, {"ok": True, "id": inv_id,
                                            "confirmed_ids": [c["id"] for c in merged
                                                              if c.get("confirmed")]})
                return self._json(404, {"error": "not found"})
            except Exception as e:
                traceback.print_exc()
                return self._json(500, {"error": str(e)})
    return Handler


def _norm_repo(r):
    return r if (not r or is_url(r)) else os.path.abspath(r)


def main():
    ap = argparse.ArgumentParser(description="Prism web UI. Run inside a git repo to auto-select it.")
    ap.add_argument("--repo", default=os.environ.get("PRISM_DEFAULT_REPO", ""),
                    help="repo path or URL (default: the git repo of the current directory)")
    ap.add_argument("--invariants", default=os.environ.get("PRISM_INVARIANTS", ""))
    ap.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    # preload a review straight into the UI:
    ap.add_argument("--pr", help="open this GitHub PR number on load")
    ap.add_argument("--commit", help="open this commit's diff on load")
    ap.add_argument("--merge", help="open this merge commit on load")
    ap.add_argument("--base", help="open this base..head on load (with --head)")
    ap.add_argument("--head")
    ap.add_argument("--no-open", action="store_true", help="don't open a browser window")
    args = ap.parse_args()

    # auto-select the current git repo when no repo was given
    repo = args.repo or git_toplevel(os.getcwd())

    preload = {k: v for k, v in {"pr": args.pr, "commit": args.commit, "merge": args.merge,
                                 "base": args.base, "head": args.head}.items() if v}

    cfg = {
        "repo": _norm_repo(repo),
        "invariants": _norm_repo(args.invariants),
        "token": os.environ.get("PRISM_TOKEN", ""),
        "allowed": [s.strip() for s in os.environ.get("PRISM_ALLOWED_REPOS", "").split(",") if s.strip()],
        "base_path": os.environ.get("PRISM_BASE_PATH", "").rstrip("/"),
        "preload": preload,
    }
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(cfg))
    shown = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
    url = f"http://{shown}:{args.port}{cfg['base_path'] or ''}/"
    print(f"Prism  →  {url}")
    print(f"  repo: {cfg['repo'] or '(enter one in the UI)'}" + ("  [auto-detected]" if not args.repo and cfg['repo'] else ""))
    if preload:
        print(f"  preloading: {preload}")
    if cfg["token"]:
        print("  access:       token required (?token=…)")
    elif args.host == "0.0.0.0":
        print("  note: bound on 0.0.0.0 with no PRISM_TOKEN — fine locally, set a token if exposed.")
    if cfg["allowed"]:
        print(f"  allowlist:    {cfg['allowed']}")
    if not args.no_open and not os.environ.get("PORT"):   # local run, not a hosted deploy
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
