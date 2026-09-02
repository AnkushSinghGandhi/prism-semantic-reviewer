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


def test_external_dest_unresolvable_stays_unknown():
    # a destination built from a function call is not guessed → honest "?"
    src = ("import requests\n"
           "def send():\n"
           "    requests.post(build_url())\n")
    assert _external_dests(src) == ["?"]
