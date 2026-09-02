"""
Milestone 1 extractor — a crude static pass over a Django repo for the lens:

    HTTP endpoint -> auth -> DB tables -> external calls -> async -> PII egress

Pure stdlib `ast`. No imports of the target app (safe on any checkout). Emits per-endpoint
facts with an explicit resolution status per edge:  ✓ resolved / ⚠ potential / ? unknown.

The design principle baked in here: never render "not observed" as "doesn't exist".
Anything we cannot resolve is surfaced as ⚠ or ? and linked to source, never dropped.
"""
from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field, asdict
from typing import Optional

# ---- config --------------------------------------------------------------------------

SKIP_DIRS = {".git", "venv", ".venv", "node_modules", "__pycache__", "migrations",
             "site-packages", "tests", "test"}

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}

# outbound-call roots we recognise; value = how to label the destination
EXTERNAL_ROOTS = {"requests", "httpx", "urllib", "urllib2", "aiohttp",
                  "stripe", "razorpay", "boto3", "sendgrid", "smtplib"}
EXTERNAL_VERBS = {"get", "post", "put", "patch", "delete", "request", "send", "post_async"}

# ORM read vs write method names
ORM_READ = {"get", "filter", "all", "values", "values_list", "count", "exists",
            "first", "last", "aggregate", "annotate", "none", "get_or_none"}
ORM_WRITE = {"create", "update", "save", "delete", "bulk_create", "bulk_update",
             "get_or_create", "update_or_create", "insert", "add", "set", "remove"}

# receivers where a bare .save()/.delete() is NOT a DB write — cache, session, file/blob storage.
# Excluding these stops `cache.delete(key)` etc. from being mislabeled a table write (`<instance>`).
NON_ORM_RECEIVERS = {"cache", "caches", "session", "storage", "default_storage", "fs"}

# class-name suffixes that are NOT ORM tables (a `serializer.save()` writes to its Meta.model, not a
# table named "FooSerializer"). We resolve these to Meta.model when known, else fall back to unknown.
NON_MODEL_SUFFIXES = ("Serializer", "Form")

# Django cache ops — surfaced as the E7 cache lens (read vs mutate/invalidate), not DB writes.
CACHE_READ = {"get", "get_many", "has_key", "keys", "ttl"}
CACHE_WRITE = {"set", "set_many", "add", "delete", "delete_many", "clear",
               "incr", "decr", "touch", "get_or_set"}

# sensitive attribute names for the PII edge
SENSITIVE = {"email", "phone", "mobile", "password", "ssn", "aadhaar", "aadhar",
             "pan", "card", "card_number", "address", "dob", "date_of_birth",
             "contact", "contact_number", "user_email", "billing_email"}

# taint sinks (feature #9): where a tainted value "leaves". Egress to a third party / log / client.
LOG_ROOTS = {"logger", "logging", "log", "LOG", "logfire"}
LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical"}
RESPONSE_CALLS = {"Response", "JsonResponse", "HttpResponse", "HttpResponseBadRequest"}
# calls we propagate taint *through* (they serialize/wrap, they don't sanitize)
TAINT_WRAPPERS = {"dumps", "dump", "str", "format", "dict", "list", "tuple", "join"}

VERIFIED, POTENTIAL, UNKNOWN, NA = "✓", "⚠", "?", "n/a"


# ---- fact model ----------------------------------------------------------------------

@dataclass
class Edge:
    status: str                       # ✓ / ⚠ / ? / n/a
    items: list = field(default_factory=list)   # human-readable resolved facts
    note: str = ""


@dataclass
class Endpoint:
    route: str
    handler: str
    file: str = ""
    line: int = 0
    methods: list = field(default_factory=list)
    e1_route_handler: Edge = None
    e2_auth: Edge = None
    e3_db_tables: Edge = None
    e4_external: Edge = None
    e5_async: Edge = None
    e6_pii: Edge = None
    e7_cache: Edge = None
    tables: dict = field(default_factory=dict)   # model -> {"table": name, "explicit": bool}


# ---- repo index ----------------------------------------------------------------------

class RepoIndex:
    """Parse every .py once; index class + function defs by name, and url path() calls."""

    def __init__(self, root: str):
        self.root = root
        self.classes: dict[str, list] = {}     # name -> [(node, file)]
        self.funcs: dict[str, list] = {}       # name -> [(node, file)]
        self.url_files: list[str] = []
        self.drf_present = False               # REST_FRAMEWORK settings seen
        self.default_permissions = None        # DEFAULT_PERMISSION_CLASSES (list) or None
        self.module_consts: dict[str, dict] = {}   # file -> {NAME: folded string}
        self.global_consts: dict[str, str] = {}    # repo-wide URL constants (unambiguous only)
        self.class_models: dict[str, str] = {}     # Serializer/Form/etc. name -> its Meta.model table
        self.perm_consts: dict[str, list] = {}     # PERMISSION_CONSTANT -> [class names] (unambiguous)
        self.model_tables: dict[str, str] = {}     # Model -> explicit Meta.db_table (SQL table name)
        self._build()

    def _iter_py(self):
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)

    def _build(self):
        seen, ambiguous = {}, set()        # for the repo-wide string-constant table
        pseen, pamb = {}, set()            # for the repo-wide permission-list constant table
        for path in self._iter_py():
            try:
                src = open(path, encoding="utf-8").read()
                tree = ast.parse(src)
            except (SyntaxError, UnicodeDecodeError):
                continue
            if os.path.basename(path) == "urls.py" or os.sep + "urls" + os.sep in path:
                self.url_files.append(path)
            if "settings" in path:
                self._scan_drf(tree)
            mc = _module_str_consts(tree)              # this module's top-level string constants
            self.module_consts[path] = mc
            for name, val in mc.items():               # merge into the repo-wide table
                if name in seen and seen[name] != val:
                    ambiguous.add(name)                # same name, different value across files → drop
                seen[name] = val
            for name, lst in _module_perm_consts(tree).items():   # permission-list constants
                if name in pseen and pseen[name] != lst:
                    pamb.add(name)
                pseen[name] = lst
            self._index_defs(tree, path)
        self.global_consts = {n: v for n, v in seen.items() if n not in ambiguous}
        self.perm_consts = {n: v for n, v in pseen.items() if n not in pamb}

    def _index_defs(self, node, path):
        """Index ClassDefs and FunctionDefs, recursing through the module, class bodies, and
        control-flow (if/try/with) — but NOT into function bodies. Handlers and the helpers we
        follow are module-level or class methods, never nested inside a function, so pruning
        function bodies (the bulk of the AST) skips ~all the cost with no loss."""
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                self.classes.setdefault(child.name, []).append((child, path))
                m = _class_meta_model(child)                  # DRF/Django `class Meta: model = X`
                if m:
                    self.class_models[child.name] = m
                t = _class_db_table(child)                    # Django `class Meta: db_table = '...'`
                if t:
                    self.model_tables[child.name] = t
                self._index_defs(child, path)                 # into class body (methods, Meta)
            elif isinstance(child, ast.FunctionDef):
                self.funcs.setdefault(child.name, []).append((child, path))
                # prune: a function body holds no module-level handlers/helpers to index
            else:
                self._index_defs(child, path)                 # if/try/with/… may wrap defs

    def consts_for(self, file: str) -> dict:
        """String constants visible in `file`: the repo-wide table, with this module's own on top."""
        return {**self.global_consts, **self.module_consts.get(file, {})}

    def table_for(self, model: str):
        """(table, explicit) for a model: its declared `Meta.db_table` if present (explicit=True),
        else Django's default `{app_label}_{model_lower}` from where the class lives (explicit=False),
        or (None, False) if the model isn't in the repo."""
        if model in self.model_tables:
            return self.model_tables[model], True
        defs = self.classes.get(model)
        if defs:
            app = _app_label(defs[0][1])
            if app:
                return f"{app}_{model.lower()}", False
        return None, False

    def _scan_drf(self, tree):
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "REST_FRAMEWORK" for t in n.targets) \
                    and isinstance(n.value, ast.Dict):
                self.drf_present = True
                for k, v in zip(n.value.keys, n.value.values):
                    if _const_str(k) == "DEFAULT_PERMISSION_CLASSES" and isinstance(v, (ast.List, ast.Tuple)):
                        self.default_permissions = [
                            (_const_str(e) or _dotted(e) or "").split(".")[-1] for e in v.elts]

    def find_class(self, name: str, near: str = ""):
        return _pick(self.classes.get(name), near)

    def find_func(self, name: str, near: str = ""):
        return _pick(self.funcs.get(name), near)


def _pick(candidates, near: str):
    """Disambiguate a name collision by preferring the def defined nearest `near`."""
    if not candidates:
        return (None, None)
    if len(candidates) == 1 or not near:
        return candidates[0]
    near_parts = near.split(os.sep)

    def common(path):
        p = path.split(os.sep)
        n = 0
        for a, b in zip(near_parts, p):
            if a != b:
                break
            n += 1
        return n
    return max(candidates, key=lambda c: common(c[1]))


# ---- url parsing ---------------------------------------------------------------------

def _const_str(node) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _resolve_route(node, str_consts: dict) -> str:
    """Handle plain strings and f-strings like f'{prefix}/submit'."""
    s = _const_str(node)
    if s is not None:
        return s
    if isinstance(node, ast.JoinedStr):
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                out.append(str(part.value))
            elif isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name):
                out.append(str_consts.get(part.value.id, "{" + part.value.id + "}"))
            else:
                out.append("{?}")
        return "".join(out)
    return "?"


def _handler_name(node) -> Optional[str]:
    """From `X.as_view()` or `mod.Cls.as_view()` or bare `view` return the class/func name."""
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "as_view":
            base = f.value
            if isinstance(base, ast.Attribute):
                return base.attr
            if isinstance(base, ast.Name):
                return base.id
    if isinstance(node, ast.Name):          # bare function view
        return node.id
    if isinstance(node, ast.Attribute):     # mod.view
        return node.attr
    return None


def parse_endpoints(index: RepoIndex) -> list:
    endpoints = []
    for path in index.url_files:
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except Exception:
            continue
        # module-level string consts (for f-string route prefixes)
        str_consts = {}
        for n in tree.body:
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                s = _const_str(n.value)
                if s is not None:
                    str_consts[n.targets[0].id] = s
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
                fname = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                if fname not in {"path", "re_path", "url"} or len(node.args) < 2:
                    continue
                route = _resolve_route(node.args[0], str_consts)
                handler = _handler_name(node.args[1])
                if handler:
                    endpoints.append((route, handler))
    # dedupe, preserve order
    seen, out = set(), []
    for r, h in endpoints:
        if (r, h) not in seen:
            seen.add((r, h))
            out.append((r, h))
    return out


# ---- view analysis -------------------------------------------------------------------

class FactCollector(ast.NodeVisitor):
    """Walk a function body; collect lens facts. Records callees for 1-level follow."""

    def __init__(self, file: str = "", consts=None, class_models=None, self_type=None):
        self.file = file
        self.class_models = class_models or {}   # Serializer/Form name -> its Meta.model table
        self.self_type = self_type               # enclosing class, so `self.objects.x()` names its table
        self.db = []           # (model, kind, file, line)
        self.external = []     # (root, dest, file, line)
        self.async_ = []       # (mechanism, target, file, line)
        self.pii = []          # (attr, file, line)
        self.cache = []        # (kind, detail, file, line): cache read / mutate (E7)
        self.self_calls = []   # method names called as self.x(...)
        self.func_calls = []   # bare names called x(...)
        self.typed_calls = []  # (ClassName, method) called as Model.method(...)
        self.var_types = {}    # local var name -> Model (so `order.save()` → Order:write)
        # local var name -> resolved string (so `requests.post(url)` names the dest); seeded with
        # module/repo-level URL constants so an imported `requests.post(LEARN_API)` resolves too.
        self.str_vars = dict(consts or {})
        self.tainted = {}      # local var name -> {sensitive field labels it carries}
        self.leaks = []        # (field, sink, file, line): a tainted field that reaches an egress sink

    def _loc(self, node):
        return (self.file, getattr(node, "lineno", 0))

    def visit_Call(self, node: ast.Call):
        f = node.func
        fl, ln = self._loc(node)
        # ORM:  <Model>.objects.<method>(...)  and chained querysets
        model, method = _orm_model_method(f)
        if model and method:
            if model in ("self", "cls"):                 # `self.objects.x()` in a model method →
                model = self.self_type or "<instance>"   # the enclosing class, not a table named "self"
            kind = "write" if method in ORM_WRITE else ("read" if method in ORM_READ else "read")
            self.db.append((model, kind, fl, ln))
        # instance .save()/.delete() — resolve `var.save()` to its model when we know the type,
        # but skip non-ORM receivers (cache/session/storage) so they aren't mislabeled DB writes.
        if isinstance(f, ast.Attribute) and f.attr in {"save", "delete"} and not model \
                and not _is_non_orm_receiver(f.value):
            who = self.var_types.get(_receiver_key(f.value))
            who = self.class_models.get(who, who)            # FooSerializer.save() → its Meta.model
            if who and who.endswith(NON_MODEL_SUFFIXES):     # a serializer/form we couldn't map → unknown
                who = None
            self.db.append((who or "<instance>", "write", fl, ln))
        # cache read / mutate (E7): cache.get(k) / cache.set(k, …) / cache.delete(k) / caches['x'].clear()
        if isinstance(f, ast.Attribute) and _is_cache_receiver(f.value) \
                and f.attr in (CACHE_READ | CACHE_WRITE):
            kind = "read" if f.attr in CACHE_READ else "write"
            detail = f"{f.attr} {_cache_key(node, self.str_vars)}".strip()
            self.cache.append((kind, detail, fl, ln))
        # external calls
        if isinstance(f, ast.Attribute):
            root = _root_name(f.value)
            if root in EXTERNAL_ROOTS and f.attr in EXTERNAL_VERBS:
                self.external.append((f"{root}.{f.attr}", _dest_of(node, self.str_vars), fl, ln))
        # async: threading.Thread(target=fn)
        if _is_threading_thread(f):
            tgt = _kw(node, "target")
            self.async_.append(("threading.Thread", tgt or "?", fl, ln))
        # async: .delay(...) / .apply_async(...)
        if isinstance(f, ast.Attribute) and f.attr in {"delay", "apply_async"}:
            self.async_.append((f"celery.{f.attr}", _root_name(f.value) or "?", fl, ln))
        # async: send_task('name')
        if (isinstance(f, ast.Name) and f.id == "send_task") or \
           (isinstance(f, ast.Attribute) and f.attr == "send_task"):
            self.async_.append(("celery.send_task", _first_const(node) or "?", fl, ln))
        # follow targets
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "self":
            self.self_calls.append(f.attr)
        elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                and f.value.id[:1].isupper() and f.attr not in {"objects", "as_view"} \
                and f.attr not in ORM_READ and f.attr not in ORM_WRITE:
            # Model.classmethod(...)  e.g. OrderedItems.insert_ordered_item(...)
            self.typed_calls.append((f.value.id, f.attr))
        elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Call) \
                and isinstance(f.value.func, ast.Name) and f.value.func.id[:1].isupper():
            # Service().method(...)  e.g. CompanionProductService().get_product_details(...)
            self.typed_calls.append((f.value.func.id, f.attr))
        elif isinstance(f, ast.Name):
            self.func_calls.append(f.id)
        # taint sink: a tainted (sensitive) value passed into an egress call → a traced leak
        sink = _sink_label(f)
        if sink:
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                for label in self._taint_labels(arg):
                    self.leaks.append((label, sink, fl, ln))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        if node.attr in SENSITIVE:
            self.pii.append((node.attr, self.file, getattr(node, "lineno", 0)))
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign):
        # remember `x = <something that reveals a Model>` so a later `x.save()` names the table
        model = self._model_of(node.value)
        for tgt in node.targets:
            if isinstance(tgt, (ast.Name, ast.Attribute)) and model:   # x = … / self.x = Model(…)
                self.var_types[_receiver_key(tgt)] = model
            elif isinstance(tgt, (ast.Tuple, ast.List)) and tgt.elts \
                    and isinstance(tgt.elts[0], ast.Name) and isinstance(node.value, ast.Call):
                m, meth = _orm_model_method(node.value.func)           # obj, created = M.objects.get_or_create()
                if m and meth in {"get_or_create", "update_or_create"}:
                    self.var_types[tgt.elts[0].id] = m
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            val = _fold_str(node.value, self.str_vars)       # `url = 'x' + settings.Y` → resolve dest later
            if val is not None:
                self.str_vars[name] = val
            labels = self._taint_labels(node.value)          # `email = user.email` → var is tainted
            if labels:
                self.tainted[name] = set(labels)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        # a parameter annotated with a model type resolves `param.save()` inside the body
        for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
            m = _anno_model(a.annotation)
            if m:
                self.var_types[a.arg] = m
        self.generic_visit(node)
    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_For(self, node: ast.For):
        # `for order in Order.objects.filter(...)` → loop var is an Order (resolves `order.save()`)
        if isinstance(node.target, ast.Name):
            model = self._model_of(node.iter)
            if not model and isinstance(node.iter, ast.Name):
                model = self.var_types.get(node.iter.id)
            if model:
                self.var_types[node.target.id] = model
            labels = self._taint_labels(node.iter)           # iterating a tainted collection
            if labels:
                self.tainted[node.target.id] = set(labels)
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return):
        # returning a tainted value → it leaves via the HTTP response
        if node.value is not None:
            fl, ln = self._loc(node)
            for label in self._taint_labels(node.value):
                self.leaks.append((label, "response", fl, ln))
        self.generic_visit(node)

    def _taint_labels(self, node) -> set:
        """Sensitive field labels an expression carries — the taint half of the flow. Conservative:
        propagates through attribute/subscript reads of a sensitive name, request `.get('field')`,
        dict/list/f-string/concat construction, and serialize-only wrappers — but NOT arbitrary
        calls (which may sanitize), so a resulting leak is trustworthy, not a guess."""
        if node is None:
            return set()
        labels = set()
        if isinstance(node, ast.Attribute):
            if node.attr in SENSITIVE:
                labels.add(node.attr)
            if isinstance(node.value, ast.Name) and node.value.id in self.tainted:
                labels |= self.tainted[node.value.id]
        elif isinstance(node, ast.Name):
            labels |= self.tainted.get(node.id, set())
        elif isinstance(node, ast.Subscript):
            key = _subscript_key(node)
            if key in SENSITIVE:
                labels.add(key)
            labels |= self._taint_labels(node.value)
        elif isinstance(node, ast.Call):
            f = node.func
            fname = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if isinstance(f, ast.Attribute) and f.attr == "get" and node.args:
                if _const_str(node.args[0]) in SENSITIVE:
                    labels.add(_const_str(node.args[0]))
            if fname in TAINT_WRAPPERS:
                for a in list(node.args) + [k.value for k in node.keywords]:
                    labels |= self._taint_labels(a)
        elif isinstance(node, ast.Dict):
            labels |= {_const_str(k) for k in node.keys if _const_str(k) in SENSITIVE}
            for v in node.values:
                labels |= self._taint_labels(v)
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for e in node.elts:
                labels |= self._taint_labels(e)
        elif isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.FormattedValue):
                    labels |= self._taint_labels(part.value)
        elif isinstance(node, ast.BinOp):
            labels |= self._taint_labels(node.left) | self._taint_labels(node.right)
        return labels

    @staticmethod
    def _model_of(value):
        if not isinstance(value, ast.Call):
            return None
        f = value.func
        if isinstance(f, ast.Name) and f.id[:1].isupper():          # Model(...)
            return f.id
        m, _ = _orm_model_method(f)                                 # Model.objects.<method>(...)
        if m:
            return m
        if isinstance(f, ast.Name) and f.id in {"get_object_or_404", "get_list_or_404"} \
                and value.args and isinstance(value.args[0], ast.Name) \
                and value.args[0].id[:1].isupper():                 # get_object_or_404(Model, …)
            return value.args[0].id
        return None


def _orm_model_method(f):
    """Match Model.objects.method — return (Model, method)."""
    # f is Attribute like  <...>.method ; walk down looking for `.objects`
    if not isinstance(f, ast.Attribute):
        return None, None
    method = f.attr
    cur = f.value
    while isinstance(cur, ast.Call):
        cur = cur.func.value if isinstance(cur.func, ast.Attribute) else None
    # cur should be Attribute `<Model>.objects` or Name via manager
    if isinstance(cur, ast.Attribute) and cur.attr == "objects" and isinstance(cur.value, ast.Name):
        return cur.value.id, method
    return None, None


def _is_non_orm_receiver(node) -> bool:
    """True for `.save()/.delete()` receivers that are NOT the ORM: `cache`, `request.session`,
    `caches['default']`, storage — so we don't record them as DB writes."""
    if isinstance(node, ast.Name):
        return node.id in NON_ORM_RECEIVERS
    if isinstance(node, ast.Attribute):
        return node.attr in NON_ORM_RECEIVERS        # request.session, self.cache
    if isinstance(node, ast.Subscript):
        return _is_non_orm_receiver(node.value)       # caches['default']
    return False


def _is_cache_receiver(node) -> bool:
    """True for a Django cache receiver: `cache`, `caches['default']`, or `self.cache`."""
    if isinstance(node, ast.Name):
        return node.id in {"cache", "caches"}
    if isinstance(node, ast.Attribute):
        return node.attr in {"cache", "caches"}
    if isinstance(node, ast.Subscript):
        return _is_cache_receiver(node.value)
    return False


def _cache_key(node: ast.Call, str_vars: dict) -> str:
    """The cache key (first arg) folded to a string, `{var}` if it's a variable, or '' if none."""
    if not node.args:
        return ""
    return _fold_str(node.args[0], str_vars) or _placeholder(node.args[0])


def _receiver_key(node):
    """var_types lookup key for a `.save()/.delete()` receiver: a bare name, or a dotted `self.x`."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _dotted(node)                          # self.instance -> "self.instance"
    return None


def _anno_model(ann):
    """A model name from a type annotation (`obj: EntityLock`, `models.Foo`, or a `"Foo"` forward
    ref), or None. Uppercase-first heuristic — a builtin like `str`/`int` is ignored."""
    if isinstance(ann, ast.Name) and ann.id[:1].isupper():
        return ann.id
    if isinstance(ann, ast.Attribute) and ann.attr[:1].isupper():
        return ann.attr
    if isinstance(ann, ast.Constant) and isinstance(ann.value, str) and ann.value[:1].isupper():
        return ann.value.split("[", 1)[0].strip()     # "Foo" / "List[Foo]" forward refs
    return None


def _root_name(node):
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _subscript_key(node: ast.Subscript):
    """The constant string key of `x['key']`, tolerating the pre-3.9 ast.Index wrapper."""
    s = node.slice
    if s.__class__.__name__ == "Index":     # Python <3.9 compatibility
        s = s.value
    return _const_str(s)


def _sink_label(f):
    """If a call's callee `f` is a known egress sink, its label — else None.
    Sinks: an external client call, a Celery dispatch, a logging call, or an HTTP Response()."""
    if isinstance(f, ast.Attribute):
        root = _root_name(f.value)
        if root in EXTERNAL_ROOTS and f.attr in EXTERNAL_VERBS:
            return f"{root}.{f.attr}"
        if f.attr in {"delay", "apply_async"}:
            return f"celery.{f.attr}"
        if f.attr == "send_task":
            return "celery.send_task"
        if f.attr in LOG_METHODS and root in LOG_ROOTS:
            return f"log.{f.attr}"
    if isinstance(f, ast.Name):
        if f.id == "send_task":
            return "celery.send_task"
        if f.id == "print":
            return "log.print"
        if f.id in RESPONSE_CALLS:
            return "response"
    return None


def _fold_str(node, str_vars: dict) -> Optional[str]:
    """Best-effort static value of a string expression: literals, local vars (from `str_vars`),
    `a + b` concatenation, and f-strings. Parts we can't pin down (a `settings.X`, an unknown name)
    become a readable `{NAME}` placeholder — honest, not a guess. Returns None when nothing at all
    is resolvable, so callers can fall back to `?`."""
    if node is None:
        return None
    s = _const_str(node)
    if s is not None:
        return s
    if isinstance(node, ast.Name):
        return str_vars.get(node.id)
    if isinstance(node, ast.Attribute):               # settings.SESSION_DOMAIN_NAME → {SESSION_DOMAIN_NAME}
        return "{" + node.attr + "}"
    if isinstance(node, ast.JoinedStr):               # f'{prefix}/send'
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                out.append(str(part.value))
            elif isinstance(part, ast.FormattedValue):
                out.append(_fold_str(part.value, str_vars) or _placeholder(part.value))
            else:
                out.append("{?}")
        return "".join(out)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _fold_str(node.left, str_vars)
        right = _fold_str(node.right, str_vars)
        if left is None and right is None:            # no literal/known anchor → stay honest "?"
            return None
        return (left or _placeholder(node.left)) + (right or _placeholder(node.right))
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Name) and f.id in {"str", "int"} and node.args:
            return _fold_str(node.args[0], str_vars) or _placeholder(node.args[0])   # str(pk) → {pk}
        if isinstance(f, ast.Attribute) and f.attr in {"format", "strip", "rstrip", "lstrip",
                                                        "lower", "upper"}:
            return _fold_str(f.value, str_vars)       # URL.format(pk=pk) → keep the "…/{pk}/…" template
        # os.getenv("X") / os.environ.get("X") / getenv("X") → {X} (the env var name, a readable host)
        is_env = (isinstance(f, ast.Attribute) and f.attr == "getenv") \
            or (isinstance(f, ast.Name) and f.id == "getenv") \
            or (isinstance(f, ast.Attribute) and f.attr == "get"
                and isinstance(f.value, ast.Attribute) and f.value.attr == "environ")
        if is_env and node.args:
            name = _const_str(node.args[0])
            if name:
                return "{" + name + "}"
    return None


def _perm_list_of(node):
    """`[Perm1, Perm2]` / `(Perm1,)` → ['Perm1', 'Perm2']; None if not a list/tuple of names."""
    if isinstance(node, (ast.List, ast.Tuple)):
        return [e.id if isinstance(e, ast.Name) else _dotted(e) for e in node.elts]
    return None


def _module_perm_consts(tree):
    """Module-level `NAME = [ ... ]` list constants, so `permission_classes = SOME_CONSTANT`
    resolves to the real classes instead of showing empty."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            lst = _perm_list_of(node.value)
            if lst is not None:
                out[node.targets[0].id] = lst
    return out


def _class_meta_model(classdef):
    """The `model = X` inside a class's inner `class Meta:` — the table a ModelSerializer/ModelForm
    actually writes to. Returns the model name or None."""
    for n in classdef.body:
        if isinstance(n, ast.ClassDef) and n.name == "Meta":
            for s in n.body:
                if isinstance(s, ast.Assign) and any(
                        isinstance(t, ast.Name) and t.id == "model" for t in s.targets):
                    if isinstance(s.value, ast.Name):
                        return s.value.id
                    if isinstance(s.value, ast.Attribute):
                        return s.value.attr
    return None


def _class_db_table(classdef):
    """The explicit `db_table = '...'` inside a model's inner `class Meta:` — the real SQL table
    name. Returns the table string or None (then callers fall back to Django's app_label_model)."""
    for n in classdef.body:
        if isinstance(n, ast.ClassDef) and n.name == "Meta":
            for s in n.body:
                if isinstance(s, ast.Assign) and any(
                        isinstance(t, ast.Name) and t.id == "db_table" for t in s.targets):
                    return _const_str(s.value)
    return None


def _app_label(path):
    """Django app label from a file path: the segment right under an `apps/` directory, else None."""
    parts = path.replace("\\", "/").split("/")
    if "apps" in parts:
        i = parts.index("apps")
        if i + 1 < len(parts):
            return parts[i + 1]
    return None


def _module_str_consts(tree, base=None):
    """Top-level `NAME = <string>` constants in a module, folded to their static value (with
    {placeholder} for parts like settings.X). Accumulates so `B = A + '/x'` resolves via `A`, and
    seeds from `base` (the repo-wide table) so a constant built from an imported one resolves too."""
    consts = dict(base or {})
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            val = _fold_str(node.value, consts)
            if val is not None:
                consts[node.targets[0].id] = val
    return consts


def _placeholder(node) -> str:
    """A readable `{name}` stand-in for an expression part we can't fold to a literal."""
    if isinstance(node, ast.Name):
        return "{" + node.id + "}"
    if isinstance(node, ast.Attribute):
        return "{" + node.attr + "}"
    return "{?}"


def _dest_of(node: ast.Call, str_vars: dict = None) -> str:
    if node.args:
        a = node.args[0]
        if isinstance(a, ast.Attribute):          # settings.PAYTM_STATUS_URL
            return _dotted(a)
        s = _const_str(a)
        if s:
            return s
        folded = _fold_str(a, str_vars or {})     # variable / 'x'+settings.Y / f-string
        if folded:
            return folded
    url_kw = _kw(node, "url")
    return url_kw or "?"


def _dotted(node) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _kw(call: ast.Call, name: str):
    for k in call.keywords:
        if k.arg == name:
            if isinstance(k.value, ast.Name):
                return k.value.id
            if isinstance(k.value, ast.Attribute):
                return _dotted(k.value)
            s = _const_str(k.value)
            if s:
                return s
    return None


def _first_const(call: ast.Call):
    return _const_str(call.args[0]) if call.args else None


def _is_threading_thread(f):
    return (isinstance(f, ast.Attribute) and f.attr == "Thread") or \
           (isinstance(f, ast.Name) and f.id == "Thread")


def _auth_of(cls: ast.ClassDef, index=None, _seen=None):
    """Permission/authentication class names on the class or an inherited base; None if none."""
    found = None
    for n in cls.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id in {"permission_classes", "authentication_classes"}:
                    lst = _perm_list_of(n.value)                       # a literal [Perm, …]
                    if lst is None and isinstance(n.value, ast.Name) and index is not None:
                        lst = index.perm_consts.get(n.value.id)        # resolve `= SOME_CONSTANT`
                    if lst is not None:                                # resolved (possibly to empty)
                        found = (found or []) + [f"{t.id}={lst}"]
                    else:                                              # a constant/expr we can't read
                        ref = n.value.id if isinstance(n.value, ast.Name) else "?"
                        found = (found or []) + [f"{t.id}=<{ref}> unresolved"]
    if found is not None or index is None:
        return found
    # walk base classes defined in the repo (custom base views may set permissions)
    _seen = _seen or set()
    for base in cls.bases:
        bname = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
        if not bname or bname in _seen:
            continue
        _seen.add(bname)
        bcls, _ = index.find_class(bname)
        if bcls is not None:
            inherited = _auth_of(bcls, index, _seen)
            if inherited is not None:
                return [f"{x} (inherited from {bname})" for x in inherited]
    return None


def analyze_handler(name: str, index: RepoIndex) -> Endpoint:
    ep = Endpoint(route="", handler=name)
    cls, cfile = index.find_class(name)
    if cls is None:
        fn, ffile = index.find_func(name)
        if fn is None:
            ep.e1_route_handler = Edge(UNKNOWN, note="handler not found in repo")
            for a in ("e2_auth", "e3_db_tables", "e4_external", "e5_async", "e6_pii", "e7_cache"):
                setattr(ep, a, Edge(UNKNOWN))
            return ep
        ep.file, ep.line, ep.methods = _rel(index, ffile), fn.lineno, ["<func>"]
        methods = [fn]
        auth = None
        owner_file = ffile
    else:
        ep.file, ep.line = _rel(index, cfile), cls.lineno
        methods = [m for m in cls.body if isinstance(m, ast.FunctionDef) and m.name in HTTP_METHODS]
        ep.methods = [m.name.upper() for m in methods]
        auth = _auth_of(cls, index)
        owner_file = cfile

    ep.e1_route_handler = Edge(VERIFIED, [f"{name} @ {ep.file}:{ep.line}"])

    # E2 auth — explicit on class/base, else the resolved DRF default, else unknown
    if auth is not None:
        unresolved = any("unresolved" in a for a in auth)
        open_empty = any(a.startswith("permission_classes=[]") for a in auth)   # no perms → open
        allow_all = any("AllowAny" in a for a in auth) or open_empty
        if unresolved:
            ep.e2_auth = Edge(UNKNOWN, auth, note="permission_classes set to an unresolved constant — verify")
        else:
            ep.e2_auth = Edge(VERIFIED, auth, note="open — no permission classes" if open_empty
                              else ("explicitly open" if allow_all else ""))
    elif index.default_permissions is not None:
        dp = index.default_permissions
        ep.e2_auth = Edge(VERIFIED, [f"DEFAULT_PERMISSION_CLASSES={dp}"],
                          note="DRF settings default" + (" — open" if any("AllowAny" in x for x in dp) else ""))
    elif index.drf_present:
        # DRF with no DEFAULT_PERMISSION_CLASSES → framework default is AllowAny (open)
        ep.e2_auth = Edge(VERIFIED, ["AllowAny"],
                          note="DRF default (no DEFAULT_PERMISSION_CLASSES set → permits all)")
    else:
        ep.e2_auth = Edge(UNKNOWN, note="no permission_classes and no DRF default resolvable")

    # walk methods with 1-level follow into self-methods and module funcs
    owner_rel = _rel(index, owner_file)
    agg = FactCollector(owner_rel)
    followed = set()
    same_module_classmethods = {m.name: m for m in (cls.body if cls else []) if isinstance(m, ast.FunctionDef)}
    for m in methods:
        _walk_follow(m, agg, index, owner_file, same_module_classmethods, followed, depth=0,
                     self_type=(name if cls is not None else None))

    ep.e3_db_tables = _score_db(agg)
    ep.e4_external = _score_external(agg)
    ep.e5_async = _score_async(agg)
    ep.e6_pii = _score_pii(agg)
    ep.e7_cache = _score_cache(agg)
    # resolve each touched model to its real SQL table (declared db_table, else app convention)
    for model in {m for m, _k, _f, _l in agg.db if m and m != "<instance>"}:
        tbl, explicit = index.table_for(model)
        if tbl:
            ep.tables[model] = {"table": tbl, "explicit": explicit}
    return ep


MAX_FOLLOW_DEPTH = 2   # handler(0) → helper(1) → helper's helper(2). Capped: deeper loses precision.


def _walk_follow(fn, agg, index, owner_file, classmethods, followed, depth, self_type=None):
    """Collect facts in `fn`; recurse up to MAX_FOLLOW_DEPTH into the functions it calls.

    Facts in the handler itself (depth 0) are direct; anything reached through a call (depth ≥ 1)
    is tagged "(via call)" so the report stays honest about indirection. `self_type` is the class
    `fn` belongs to, so a `self.objects.x()` inside it resolves to that class's table.
    """
    owner_rel = _rel(index, owner_file)
    local = FactCollector(owner_rel, consts=index.consts_for(owner_file),
                          class_models=index.class_models, self_type=self_type)
    local.visit(fn)
    if depth == 0:
        agg.db += local.db
        agg.external += local.external
        agg.async_ += local.async_
        agg.pii += local.pii
        agg.leaks += local.leaks
    else:
        _tag_indirect(agg, local)      # reached via a call → mark it
    if depth >= MAX_FOLLOW_DEPTH:
        return

    # self.method(...) — resolve against the current class's own methods (same self_type)
    for mname in set(local.self_calls):
        key = f"self:{owner_rel}:{mname}"
        if mname in classmethods and key not in followed:
            followed.add(key)
            _walk_follow(classmethods[mname], agg, index, owner_file, classmethods, followed,
                         depth + 1, self_type=self_type)

    # module-level func(...) defined in the repo (no `self`)
    for fname in set(local.func_calls):
        key = f"func:{fname}"
        if key in followed:
            continue
        node, ffile = index.find_func(fname, near=owner_file)
        if node is not None:
            followed.add(key)
            _walk_follow(node, agg, index, ffile, {}, followed, depth + 1)

    # Model.classmethod(...) / Service().method(...) — recurse with THAT class's methods,
    # so a `self.x()` inside the service resolves correctly at the next level down
    for cname, mname in set(local.typed_calls):
        key = f"{cname}.{mname}"
        if key in followed:
            continue
        cls, cfile = index.find_class(cname, near=owner_file)
        if cls is None:
            continue
        method = next((m for m in cls.body if isinstance(m, ast.FunctionDef) and m.name == mname), None)
        if method is None:
            continue
        followed.add(key)
        cls_methods = {m.name: m for m in cls.body if isinstance(m, ast.FunctionDef)}
        _walk_follow(method, agg, index, cfile, cls_methods, followed, depth + 1, self_type=cname)


def _tag_indirect(agg, sub):
    # facts discovered one level down are still real, but flagged as inter-procedural
    agg.db += [(m, k + "*", f, ln) for (m, k, f, ln) in sub.db]
    agg.external += [(r, d + " (via call)", f, ln) for (r, d, f, ln) in sub.external]
    agg.async_ += [(mech + " (via call)", t, f, ln) for (mech, t, f, ln) in sub.async_]
    agg.cache += [(k + "*", d, f, ln) for (k, d, f, ln) in sub.cache]
    agg.pii += sub.pii
    agg.leaks += sub.leaks     # a leak fully inside a helper is still an intra-procedural leak


def _first_loc(seen, key, f, ln):
    """Keep the first source location seen for a fact key."""
    if key not in seen:
        seen[key] = f"{f}:{ln}"


def _score_db(agg) -> Edge:
    if not agg.db:
        return Edge(NA)
    seen, indirect, unknown = {}, False, False
    for model, kind, f, ln in agg.db:
        ind = kind.endswith("*")
        indirect = indirect or ind
        unknown = unknown or model == "<instance>"
        _first_loc(seen, f"{model}:{kind.rstrip('*')}{' (via call)' if ind else ''}", f, ln)
    items = [f"{fact} @ {loc}" for fact, loc in seen.items()]
    status = POTENTIAL if (indirect or unknown) else VERIFIED
    return Edge(status, items, note="write hidden in manager/service" if indirect else "")


def _score_external(agg) -> Edge:
    if not agg.external:
        return Edge(NA)
    seen = {}
    via = cfg = partial = unk = False
    for r, d, f, ln in agg.external:
        via = via or "via call" in d
        clean = d.replace(" (via call)", "")
        cfg = cfg or clean.startswith("settings.")
        unk = unk or clean == "?"           # nothing at all resolved → blunt ?
        partial = partial or "{" in clean   # host/path with a runtime {placeholder} → known-but-partial
        _first_loc(seen, f"{r} -> {d}", f, ln)
    items = [f"{fact} @ {loc}" for fact, loc in seen.items()]
    # never a clean ✓ when the destination isn't fully pinned down: verify by hand.
    status = POTENTIAL if (via or cfg or partial or unk) else VERIFIED
    note = "; ".join(x for x in ["indirect" if via else "", "host in settings" if cfg else "",
                                 "dest partially resolved" if partial else "",
                                 "dest unresolved" if unk else ""] if x)
    return Edge(status, items, note=note)


def _score_async(agg) -> Edge:
    if not agg.async_:
        return Edge(NA)
    seen = {}
    for mech, t, f, ln in agg.async_:
        _first_loc(seen, f"{mech} -> {t}", f, ln)
    items = [f"{fact} @ {loc}" for fact, loc in seen.items()]
    return Edge(POTENTIAL, items, note="pattern-detected; not in a Celery-only view")


def _score_cache(agg) -> Edge:
    if not agg.cache:
        return Edge(NA)
    seen, indirect, unk = {}, False, False
    for kind, detail, f, ln in agg.cache:
        ind = kind.endswith("*")
        indirect = indirect or ind
        unk = unk or "{" in detail or detail.endswith(" ?")   # key not fully pinned down
        _first_loc(seen, f"{kind.rstrip('*')} {detail}{' (via call)' if ind else ''}", f, ln)
    items = [f"{fact} @ {loc}" for fact, loc in seen.items()]
    status = POTENTIAL if (indirect or unk) else VERIFIED
    return Edge(status, items, note="via helper" if indirect else "")


PERSON_MODELS = {"User", "Profile", "UserProfile", "Student", "Customer", "Account",
                 "Applicant", "Lead", "Contact", "Address"}


def _score_pii(agg) -> Edge:
    seen = {}
    for attr, f, ln in agg.pii:
        _first_loc(seen, attr, f, ln)
    for model, kind, f, ln in agg.db:
        if model in PERSON_MODELS and kind.rstrip("*").startswith("read"):
            _first_loc(seen, f"reads {model}", f, ln)
    leaks = {}
    for field, sink, f, ln in agg.leaks:
        _first_loc(leaks, f"{field} → {sink}", f, ln)
    egress = bool(agg.external or agg.async_)
    if not seen and not leaks:
        return Edge(NA)
    src_items = [f"{fact} @ {loc}" for fact, loc in seen.items()]
    if leaks:
        # traced: a sensitive field actually flows into an egress sink (intra-procedural). The
        # verified-leak lines lead; the raw sources follow for context.
        leak_items = [f"{fact} @ {loc}" for fact, loc in leaks.items()]
        return Edge(VERIFIED, leak_items + src_items,
                    note="traced: a sensitive field flows into an egress sink (intra-procedural)")
    if egress:
        # source and an egress path co-occur but no flow was traced between them → honestly potential
        return Edge(POTENTIAL, src_items,
                    note="PII source co-occurs with an egress path — object-level taint not traced")
    return Edge(UNKNOWN, src_items, note="PII source read; downstream egress not proven")


def _rel(index, path):
    return os.path.relpath(path, index.root) if path else ""


# ---- top-level -----------------------------------------------------------------------

def analyze_repo(root: str):
    index = RepoIndex(root)
    endpoints = parse_endpoints(index)
    results = []
    for route, handler in endpoints:
        ep = analyze_handler(handler, index)
        ep.route = route
        results.append(ep)
    return results


def to_dict(ep: Endpoint) -> dict:
    d = asdict(ep)
    return d
