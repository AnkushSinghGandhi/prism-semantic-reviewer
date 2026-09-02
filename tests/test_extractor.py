"""The 6-edge lens on a fixture Django app. Pryti parses the fixtures with `ast`; it never
imports or runs them, so Django/DRF don't need to be installed."""
import ast

from conftest import BLOG, facts, by_route
from extractor import analyze_repo
from extractor.analyzer import FactCollector


def eps():
    return analyze_repo(BLOG)


def test_endpoints_discovered():
    routes = {e.route for e in eps()}
    assert any("api/orders" in r for r in routes)
    assert any("api/stats" in r for r in routes)
    assert any("open-write" in r for r in routes)


def test_route_and_auth_resolved():
    co = by_route(eps(), "api/orders")
    assert co.e1_route_handler.status == "✓"          # E1: route → handler
    assert co.handler == "CreateOrder"
    assert co.e2_auth.status == "✓"                    # E2: auth
    assert "IsAuthenticated" in " ".join(co.e2_auth.items)


def test_db_edge_direct_and_via_call():
    co = by_route(eps(), "api/orders")
    f = facts(co, "e3_db_tables")
    assert "Order:write" in f                          # direct ORM write
    assert "OrderItem:write" in f                      # write hidden in a model helper (follow)
    assert "User:read" in f                            # read (also the PII source)
    # a write reached via a call keeps the endpoint honest (⚠, not a clean ✓)
    assert co.e3_db_tables.status in ("⚠", "✓")


def test_external_via_service_resolves_settings_name():
    co = by_route(eps(), "api/orders")
    f = facts(co, "e4_external")
    assert any("requests.post" in x for x in f)        # E4: external call (found via service)
    assert any("settings.PAYMENT_URL" in x for x in f)  # destination resolved to the config name


def test_async_dispatch_detected():
    co = by_route(eps(), "api/orders")
    assert any("threading.Thread" in x for x in facts(co, "e5_async"))  # E5


def test_pii_flagged_potential_not_safe():
    co = by_route(eps(), "api/orders")
    # PII source (email / User) co-occurs with an egress path → potential, never a false "safe"
    assert co.e6_pii.status in ("⚠", "?")
    assert co.e6_pii.status != "✓"


def test_permission_classes_constant_resolves(tmp_path):
    # `permission_classes = SOME_CONSTANT` (even imported) resolves to the real classes, not empty
    from diff_pr import is_open
    (tmp_path / "perms.py").write_text("PERMS = [ApiKeyPermission, IsAuthenticated]\nOPEN_PERMS = []\n")
    (tmp_path / "views.py").write_text(
        "from rest_framework.views import APIView\nfrom .perms import PERMS, OPEN_PERMS\n"
        "class Guarded(APIView):\n    permission_classes = PERMS\n    def get(self, r): return None\n"
        "class Wide(APIView):\n    permission_classes = OPEN_PERMS\n    def get(self, r): return None\n")
    (tmp_path / "urls.py").write_text(
        "from django.urls import path\nfrom .views import Guarded, Wide\n"
        "urlpatterns = [path('g', Guarded.as_view()), path('w', Wide.as_view())]\n")
    eps = {e.route: e for e in analyze_repo(str(tmp_path))}
    guarded = " ".join(eps["g"].e2_auth.items)
    assert "ApiKeyPermission" in guarded and "IsAuthenticated" in guarded
    assert not is_open(eps["g"])                       # real perms → not open
    assert is_open(eps["w"])                           # constant resolves to [] → open


def test_permission_classes_unresolved_constant_is_unknown(tmp_path):
    # a permission constant defined nowhere → honest UNKNOWN, never a silent empty/✓
    (tmp_path / "views.py").write_text(
        "from rest_framework.views import APIView\n"
        "class V(APIView):\n    permission_classes = MYSTERY_PERMS\n    def get(self, r): return None\n")
    (tmp_path / "urls.py").write_text(
        "from django.urls import path\nfrom .views import V\nurlpatterns = [path('v', V.as_view())]\n")
    v = next(e for e in analyze_repo(str(tmp_path)) if e.route == "v")
    assert v.e2_auth.status == "?"
    assert any("unresolved" in x for x in v.e2_auth.items)


def test_drf_default_permission_is_open():
    ps = by_route(eps(), "api/stats")
    # no permission_classes + REST_FRAMEWORK without DEFAULT_PERMISSION_CLASSES → AllowAny (open)
    assert "AllowAny" in " ".join(ps.e2_auth.items)


def test_explicit_allow_any_write_is_open():
    ow = by_route(eps(), "open-write")
    assert "AllowAny" in " ".join(ow.e2_auth.items)
    assert "Order:write" in facts(ow, "e3_db_tables")


def _external_dests(src):
    fc = FactCollector("m.py")
    fc.visit(ast.parse(src))
    return [dest for _root, dest, _f, _l in fc.external]


def _db_models(src):
    fc = FactCollector("m.py")
    fc.visit(ast.parse(src))
    return {model for model, _kind, _f, _l in fc.db}


def test_cache_and_session_ops_are_not_db_writes():
    # `cache.delete(k)` / `request.session.save()` are not ORM writes → no <instance> table
    src = ("from django.core.cache import cache\n"
           "class H:\n"
           "    def f(self, request):\n"
           "        cache.delete('k')\n"
           "        request.session.save()\n")
    assert _db_models(src) == set()


def _cache_edge(src):
    from extractor.analyzer import _score_cache
    fc = FactCollector("m.py")
    fc.visit(ast.parse(src))
    agg = FactCollector("m.py")
    agg.cache = fc.cache
    return _score_cache(agg)


def test_cache_lens_reads_and_writes_surface_as_e7():
    src = ("from django.core.cache import cache\n"
           "class H:\n"
           "    def f(self, pk):\n"
           "        v = cache.get('stats')\n"
           "        cache.set('user:' + str(pk), 1)\n"
           "        cache.delete('lock')\n")
    e = _cache_edge(src)
    joined = " | ".join(e.items)
    assert "read get stats" in joined
    assert "write set user:{pk}" in joined     # str(pk) unwrapped to {pk}
    assert "write delete lock" in joined


def test_cache_edge_is_na_when_no_cache_used():
    assert _cache_edge("class H:\n    def f(self):\n        return 1\n").status == "n/a"


def test_instance_write_resolves_via_param_annotation():
    src = ("class H:\n"
           "    def f(self, obj: EntityLock):\n"
           "        obj.save()\n")
    assert _db_models(src) == {"EntityLock"}


def test_instance_write_resolves_via_self_attr_and_tuple():
    src = ("class H:\n"
           "    def f(self):\n"
           "        self.row = Widget()\n"
           "        self.row.save()\n"
           "        obj, created = Gadget.objects.get_or_create(id=1)\n"
           "        obj.save()\n")
    assert _db_models(src) == {"Widget", "Gadget"}


def test_model_db_table_resolves_declared_and_convention(tmp_path):
    # each model resolves to its real SQL table: declared Meta.db_table, else app_label_model
    app = tmp_path / "apps" / "shop"
    app.mkdir(parents=True)
    (app / "models.py").write_text(
        "class Thing(models.Model):\n    class Meta:\n        db_table = 'custom_thing'\n"
        "class Plain(models.Model):\n    pass\n")
    (app / "views.py").write_text(
        "from rest_framework.views import APIView\n"
        "class V(APIView):\n    def get(self, r):\n        Thing.objects.all()\n        Plain.objects.all()\n        return None\n")
    (app / "urls.py").write_text(
        "from django.urls import path\nfrom .views import V\nurlpatterns = [path('v', V.as_view())]\n")
    ep = next(e for e in analyze_repo(str(tmp_path)) if e.route == "v")
    assert ep.tables["Thing"] == {"table": "custom_thing", "explicit": True}      # declared
    assert ep.tables["Plain"] == {"table": "shop_plain", "explicit": False}       # Django convention


def test_serializer_save_resolves_to_meta_model_not_class():
    # `s = WebinarSerializer(...); s.save()` writes to the serializer's Meta.model, not a
    # table named "WebinarSerializer"
    fc = FactCollector("m.py", class_models={"WebinarSerializer": "Webinar"})
    fc.visit(ast.parse("class H:\n"
                       "    def f(self):\n"
                       "        s = WebinarSerializer(data=1)\n"
                       "        s.save()\n"))
    assert {m for m, _k, _f, _l in fc.db} == {"Webinar"}


def test_unmapped_serializer_is_unknown_not_a_fake_table():
    # a *Serializer we can't map to a model must not appear as a table named "...Serializer"
    fc = FactCollector("m.py", class_models={})
    fc.visit(ast.parse("class H:\n"
                       "    def f(self):\n"
                       "        s = MysterySerializer(data=1)\n"
                       "        s.save()\n"))
    assert {m for m, _k, _f, _l in fc.db} == {"<instance>"}


def test_self_objects_resolves_to_enclosing_model(tmp_path):
    # `cls.objects.filter()` inside a model method → the model's table, not a fake table named "self"
    (tmp_path / "models.py").write_text(
        "class Question(models.Model):\n"
        "    class Meta:\n        db_table = 'questions'\n"
        "    @classmethod\n    def recent(cls):\n        return cls.objects.filter(active=1)\n")
    (tmp_path / "views.py").write_text(
        "from rest_framework.views import APIView\n"
        "class V(APIView):\n    def get(self, r):\n        Question.recent()\n        return None\n")
    (tmp_path / "urls.py").write_text(
        "from django.urls import path\nfrom .views import V\nurlpatterns = [path('v', V.as_view())]\n")
    v = next(e for e in analyze_repo(str(tmp_path)) if e.route == "v")
    models = {x.split(":")[0] for x in v.e3_db_tables.items}
    assert "Question" in models and "self" not in models and "cls" not in models


def test_self_objects_unknown_enclosing_is_instance_not_self():
    # no enclosing class known → honest <instance>, never a table literally named "self"
    fc = FactCollector("m.py", self_type=None)
    fc.visit(ast.parse("class H:\n    def f(self):\n        self.objects.all()\n"))
    assert {m for m, _k, _f, _l in fc.db} == {"<instance>"}


def test_unresolved_instance_write_stays_honest():
    # a receiver we genuinely can't type is still surfaced as <instance>:write, never dropped
    src = ("class H:\n"
           "    def f(self):\n"
           "        obj = build_thing()\n"
           "        obj.save()\n")
    assert "<instance>" in _db_models(src)


def test_external_dest_folds_var_and_concat():
    # `requests.post(url)` where url = 'lit' + settings.X + 'lit' → resolved, not a bare "?"
    src = ("import requests\n"
           "from django.conf import settings\n"
           "def send():\n"
           "    url = 'https://mailer-service' + settings.SESSION_DOMAIN_NAME + '/api/1/send'\n"
           "    requests.post(url, data=1)\n")
    assert _external_dests(src) == ["https://mailer-service{SESSION_DOMAIN_NAME}/api/1/send"]


def test_external_dest_folds_fstring_var():
    src = ("import requests\n"
           "def send(base):\n"
           "    url = f'{base}/hook'\n"
           "    requests.post(url)\n")
    assert _external_dests(src) == ["{base}/hook"]


def test_external_dest_resolves_format_and_getenv():
    # URL = os.getenv('X') + '/path/{id}' ; requests.post(URL.format(id=id)) → readable, not "?"
    src = ("import os, requests\n"
           "BASE = os.getenv('API_URL', '')\n"
           "URL = BASE + '/v1/item/{id}/'\n"
           "def f(id):\n"
           "    requests.post(URL.format(id=id))\n")
    assert _external_dests(src) == ["{API_URL}/v1/item/{id}/"]


def test_module_str_consts_accumulate():
    from extractor.analyzer import _module_str_consts
    tree = ast.parse("base = 'https://x'\nAPI = base + '/v1'\n")
    assert _module_str_consts(tree) == {"base": "https://x", "API": "https://x/v1"}


def test_imported_url_constant_resolves_across_modules(tmp_path):
    # the real cnext shape: a URL constant defined in one module, imported and used in another
    (tmp_path / "service_urls.py").write_text(
        "import os\nBASE = os.getenv('API_URL', '')\nSET_KW = BASE + '/api/1/kw/{pk}/set/'\n")
    (tmp_path / "settings.py").write_text(
        "REST_FRAMEWORK = {'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.AllowAny']}\n")
    (tmp_path / "views.py").write_text(
        "import requests\nfrom rest_framework.views import APIView\nfrom .service_urls import SET_KW\n"
        "class V(APIView):\n    def post(self, r, pk):\n        requests.post(SET_KW.format(pk=pk))\n        return None\n")
    (tmp_path / "urls.py").write_text(
        "from django.urls import path\nfrom .views import V\nurlpatterns = [path('api/v/<int:pk>', V.as_view())]\n")
    eps = analyze_repo(str(tmp_path))
    v = next(e for e in eps if "api/v" in e.route)
    dests = [x.split(" -> ", 1)[-1].split(" @ ")[0] for x in v.e4_external.items]
    assert "{API_URL}/api/1/kw/{pk}/set/" in dests     # imported constant + .format resolved


def test_external_dest_unresolvable_stays_unknown():
    # a destination built from a function call is not guessed → honest "?"
    src = ("import requests\n"
           "def send():\n"
           "    requests.post(build_url())\n")
    assert _external_dests(src) == ["?"]


def _external_status(src):
    from extractor.analyzer import _score_external
    fc = FactCollector("m.py")
    fc.visit(ast.parse(src))
    agg = FactCollector("m.py")
    agg.external = fc.external
    return _score_external(agg).status


def test_external_status_partial_dest_is_not_verified():
    # a {placeholder} host (runtime-dependent) is known-but-partial → ⚠, never a clean ✓
    src = ("import requests\n"
           "from django.conf import settings\n"
           "def send():\n"
           "    url = 'https://x' + settings.Y + '/send'\n"
           "    requests.post(url)\n")
    assert _external_status(src) == "⚠"


def test_external_status_literal_dest_is_verified():
    src = ("import requests\n"
           "def send():\n"
           "    requests.post('https://api.stripe.com/v1/charges')\n")
    assert _external_status(src) == "✓"
