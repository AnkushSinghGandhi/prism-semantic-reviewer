#!/usr/bin/env python3
"""
Behavioral dependency capability-delta (feature #5) — the 6-edge lens on a new surface.

When a PR bumps a dependency, the honest question isn't "what version" — it's *"what can the new
version now DO that the old one couldn't?"* We answer it the same way we answer it for app code:
statically, from the package's own source. Parse the manifest change (requirements*.txt /
Pipfile.lock / poetry.lock) into version bumps, fetch each version's source, scan it for capability
signals (network / subprocess / native code / dynamic exec / filesystem-write / env access), and
surface the *newly-gained* capabilities as a semantic change:

    requests 2.28→2.31: no new capabilities            🟢
    leftpad  1.0→2.0:  now spawns a subprocess          🔴

Stay honest: a version we can't fetch/parse is reported as ⚠ *unresolved*, never as "safe".

The pure pieces (parse / scan / delta / severity / render) take plain strings and dirs so they are
fully unit-testable offline; the network fetch is an injectable `source_provider`.
"""
import ast
import io
import json
import os
import re
import tarfile
import tempfile
import urllib.request
import zipfile

CRIT, HIGH, MED, LOW, WARN = "🔴", "🟠", "🟡", "🟢", "⚠"

# ---- capability model -------------------------------------------------------------------------
# Top-level module name → capability it grants. Importing the module is the signal.
_MODULE_CAP = {}
for _m in ("socket", "ssl", "http", "urllib", "ftplib", "smtplib", "telnetlib", "poplib",
           "imaplib", "requests", "httpx", "aiohttp", "urllib3", "websocket", "websockets",
           "paramiko", "pycurl", "grpc"):
    _MODULE_CAP[_m] = "network"
for _m in ("subprocess", "pty"):
    _MODULE_CAP[_m] = "subprocess"
for _m in ("ctypes", "cffi", "_ctypes"):
    _MODULE_CAP[_m] = "native-code"

# Newly-gained capability → severity of the change. Code-execution surface is the worst.
CAP_SEVERITY = {"subprocess": CRIT, "dynamic-exec": CRIT, "native-code": CRIT,
                "network": HIGH, "filesystem-write": MED, "env-access": MED}
_ORDER = [CRIT, HIGH, MED, LOW]


def severity_for(caps):
    """Worst severity among a set of gained capabilities; 🟢 when nothing new was gained."""
    sevs = [CAP_SEVERITY.get(c, MED) for c in caps]
    return min(sevs, key=_ORDER.index) if sevs else LOW


# ---- capability scanner (AST over a package's source tree) ------------------------------------

def _attr_chain(node):
    """Dotted name for an Attribute/Name expression, e.g. `os.system` → 'os.system'."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _open_writes(call):
    """True if a builtin open() call requests a write mode (w/a/x/+)."""
    mode = None
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
        mode = call.args[1].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    return isinstance(mode, str) and any(ch in mode for ch in "wax+")


def scan_source_caps(src_dir):
    """The set of capability tags a Python source tree exercises. Static + conservative: import of
    a capability module, or a call to a known capability builtin/stdlib function. Unparseable files
    (e.g. py2) are skipped, not guessed."""
    caps = set()
    for root, _dirs, files in os.walk(src_dir):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            try:
                tree = ast.parse(open(os.path.join(root, fn), encoding="utf-8", errors="ignore").read())
            except (SyntaxError, ValueError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        _add_module(caps, a.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.level == 0:
                        _add_module(caps, node.module)
                elif isinstance(node, ast.Call):
                    _add_call(caps, node)
    return caps


def _add_module(caps, dotted):
    top = (dotted or "").split(".")[0]
    if top in _MODULE_CAP:
        caps.add(_MODULE_CAP[top])


def _add_call(caps, call):
    f = call.func
    if isinstance(f, ast.Name):
        if f.id in ("eval", "exec", "compile", "__import__"):
            caps.add("dynamic-exec")
        elif f.id == "open" and _open_writes(call):
            caps.add("filesystem-write")
        return
    chain = _attr_chain(f)
    if chain in ("os.system", "os.popen") or chain.startswith(("os.exec", "os.spawn", "os.fork")):
        caps.add("subprocess")
    elif chain in ("os.remove", "os.unlink", "os.rmdir", "os.chmod", "os.rename", "os.replace",
                   "shutil.rmtree", "shutil.move", "pathlib.Path.write_text", "pathlib.Path.write_bytes"):
        caps.add("filesystem-write")
    elif chain == "os.getenv" or chain.startswith("os.environ"):
        caps.add("env-access")
    elif chain in ("importlib.import_module", "importlib.__import__"):
        caps.add("dynamic-exec")


def capability_delta(old_caps, new_caps):
    """Capabilities the new version has that the old one didn't."""
    return set(new_caps) - set(old_caps)


# ---- manifest parsing -------------------------------------------------------------------------

def _norm_name(n):
    return re.sub(r"[-_.]+", "-", n.strip().lower())


def parse_requirements(text):
    """{normalized-name: version-or-spec} from a requirements.txt. Records the pinned version for
    `==`, else the raw specifier; skips comments, blanks, options, editable/URL/VCS lines."""
    out = {}
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-") or "://" in line or line.startswith(("git+", "http")):
            continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$", line)
        if not m:
            continue
        name, spec = m.group(1), m.group(3).strip()
        pin = re.match(r"^==\s*([^\s;,]+)", spec)
        out[_norm_name(name)] = pin.group(1) if pin else (spec or "*")
    return out


def parse_pipfile_lock(text):
    """{name: version} from a Pipfile.lock (default + develop), version stripped of a `==` pin."""
    out = {}
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError:
        return out
    for section in ("default", "develop"):
        for name, meta in (data.get(section) or {}).items():
            ver = (meta or {}).get("version", "") if isinstance(meta, dict) else ""
            out[_norm_name(name)] = ver[2:] if ver.startswith("==") else (ver or "*")
    return out


def parse_poetry_lock(text):
    """{name: version} from a poetry.lock. Regex-scans `[[package]]` blocks (no tomllib on 3.10)."""
    out = {}
    for block in re.split(r"(?m)^\[\[package\]\]\s*$", text or "")[1:]:
        block = block.split("\n[[", 1)[0]                 # stop at the next top-level table
        name = re.search(r'(?m)^\s*name\s*=\s*"([^"]+)"', block)
        ver = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', block)
        if name and ver:
            out[_norm_name(name.group(1))] = ver.group(1)
    return out


MANIFEST_PARSERS = {"pipfile.lock": parse_pipfile_lock, "poetry.lock": parse_poetry_lock}


def is_manifest(path):
    base = os.path.basename(path).lower()
    return base in MANIFEST_PARSERS or (base.startswith("requirement") and base.endswith(".txt"))


def parse_manifest(path, text):
    base = os.path.basename(path).lower()
    if base in MANIFEST_PARSERS:
        return MANIFEST_PARSERS[base](text)
    return parse_requirements(text)


def diff_manifests(old_map, new_map):
    """{name: (old_version_or_None, new_version_or_None)} for every package added, removed, or whose
    version string changed. Unchanged packages are omitted."""
    out = {}
    for name in set(old_map) | set(new_map):
        o, n = old_map.get(name), new_map.get(name)
        if o != n:
            out[name] = (o, n)
    return out


# ---- PyPI source provider (network; injectable) -----------------------------------------------

_SRC_CACHE = {}   # (name, version) -> extracted dir | None


def pypi_source_provider(name, version, timeout=8):
    """Fetch `name==version` from PyPI, extract the sdist/wheel, and return the extracted source
    dir — or None on any failure (offline, no matching file, yanked). Cached per (name, version).
    A concrete pinned version is required; ranges/`*` return None (nothing to scan)."""
    if not version or not re.match(r"^\d", version):
        return None
    key = (_norm_name(name), version)
    if key in _SRC_CACHE:
        return _SRC_CACHE[key]
    dest = None
    try:
        api = f"https://pypi.org/pypi/{name}/{version}/json"
        req = urllib.request.Request(api, headers={"User-Agent": "pryti-deps"})
        data = json.load(urllib.request.urlopen(req, timeout=timeout))
        url = _pick_dist(data.get("urls", []))
        if url:
            blob = urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "pryti-deps"}),
                timeout=timeout).read()
            dest = tempfile.mkdtemp(prefix="pryti-dep-")
            _extract(url, blob, dest)
    except Exception:
        dest = None
    _SRC_CACHE[key] = dest
    return dest


def _pick_dist(urls):
    sdists = [u["url"] for u in urls if u.get("packagetype") == "sdist"
              and u["url"].endswith((".tar.gz", ".tgz", ".zip"))]
    if sdists:
        return sdists[0]
    wheels = [u["url"] for u in urls if u.get("packagetype") == "bdist_wheel"]
    return wheels[0] if wheels else None


def _extract(url, blob, dest):
    if url.endswith(".zip") or url.endswith(".whl"):
        zipfile.ZipFile(io.BytesIO(blob)).extractall(dest)
    else:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
            tf.extractall(dest)


# ---- top-level analysis ------------------------------------------------------------------------

def _caps_for(provider, name, version):
    """Capability set for one package version, or None if it can't be resolved/scanned."""
    if provider is None or not version:
        return None
    src = provider(name, version)
    return scan_source_caps(src) if src else None


def analyze_dependency_changes(old_map, new_map, *, source_provider=pypi_source_provider):
    """Finding dicts for every dependency version change. Each is one of:
      - gained capabilities (severity by the worst one),
      - a bump with no new capabilities (🟢),
      - an addition (all of the new package's caps count as gained),
      - ⚠ unresolved when a version's source can't be fetched/scanned (never assumed safe).
    Removals are skipped (they shrink surface). `source_provider=None` → everything is ⚠ unresolved
    (pure/offline mode)."""
    findings = []
    for name, (old_v, new_v) in sorted(diff_manifests(old_map, new_map).items()):
        if new_v is None:                                  # removed → less surface, not a risk
            continue
        added = old_v is None
        new_caps = _caps_for(source_provider, name, new_v)
        old_caps = set() if added else _caps_for(source_provider, name, old_v)
        base = dict(name=name, old=old_v, new=new_v, added=added)
        if new_caps is None or old_caps is None:
            findings.append(dict(base, sev=MED, unresolved=True, gained=[],
                                 why="capabilities not statically resolved (offline or no sdist)"))
            continue
        gained = sorted(capability_delta(old_caps, new_caps))
        findings.append(dict(base, sev=severity_for(gained), unresolved=False, gained=gained,
                             why=("now has: " + ", ".join(gained)) if gained else "no new capabilities"))
    findings.sort(key=lambda f: _ORDER.index(f["sev"]))
    return findings


def render_deps_section(findings):
    """Markdown block for the report — dependency capability changes, sits with the other panels."""
    if not findings:
        return ""
    risky = [f for f in findings if f["gained"] or f["unresolved"]]
    L = ["## 📦 Dependency capability changes"]
    if risky:
        L.append(f"**{len(risky)} dependency change(s) widen or can't confirm the capability "
                 "surface** — the same behavioral lens, applied to a bumped package:\n")
    for f in findings:
        arrow = f"`{f['name']}` {f['old'] or '(new)'}→{f['new']}"
        if f["unresolved"]:
            L.append(f"- {WARN} {arrow} — {f['why']}")
        elif f["added"] and f["gained"]:
            L.append(f"- {f['sev']} {arrow} — new dependency; capabilities: {', '.join(f['gained'])}")
        else:
            L.append(f"- {f['sev']} {arrow} — {f['why']}")
    L.append("\n_Capabilities are read statically from each version's own source; an unfetchable "
             "version is ⚠ unresolved, never assumed safe._\n")
    return "\n".join(L)
