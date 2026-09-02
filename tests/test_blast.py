"""Hotspot / blast-radius tie-breaker — fan-in scoring and same-tier ordering."""
import diff_pr
from extractor.analyzer import Endpoint, Edge, VERIFIED, UNKNOWN, NA


def mk(route, handler, *, auth="?", tables=(), ext=(), pii=False, cache=()):
    """A minimal but complete Endpoint (all seven edges set) for exercising diff()."""
    ep = Endpoint(route=route, handler=handler, file="v.py", line=1, methods=["POST"])
    ep.e1_route_handler = Edge(VERIFIED, [f"{handler} @ v.py:1"])
    ep.e2_auth = Edge(UNKNOWN) if auth == "?" else Edge(VERIFIED, [auth])
    ep.e3_db_tables = Edge(VERIFIED, [f"{t} @ v.py:2" for t in tables]) if tables else Edge(NA)
    ep.e4_external = Edge(VERIFIED, [f"{x} @ v.py:3" for x in ext]) if ext else Edge(NA)
    ep.e5_async = Edge(NA)
    ep.e6_pii = Edge(VERIFIED, ["email @ v.py:4"]) if pii else Edge(NA)
    ep.e7_cache = Edge(VERIFIED, [f"{c} @ v.py:5" for c in cache]) if cache else Edge(NA)
    return ep


def test_resources_names_tables_ext_async():
    ep = mk("/x", "X", tables=["User:write", "Order:read"], ext=["requests.post -> stripe.com"])
    assert diff_pr._resources(ep) == {"table:User", "table:Order", "ext:stripe.com"}


def test_blast_radius_counts_other_routes_sharing_a_resource():
    eps = [mk(f"/read{i}", f"R{i}", auth="IsAuthenticated", tables=["User:read"]) for i in range(5)]
    idx = diff_pr.fanin_index(eps)
    hot = mk("/hot", "Hot", tables=["User:write"])       # shares the User table with 5 readers
    cold = mk("/cold", "Cold", tables=["Log:write"])     # isolated table, nobody else touches it
    assert diff_pr.blast_radius(hot, idx) == 5
    assert diff_pr.blast_radius(cold, idx) == 0


def test_diff_breaks_severity_ties_by_blast():
    shared = [mk(f"/read{i}", f"R{i}", auth="IsAuthenticated", tables=["User:read"]) for i in range(5)]
    hot = mk("/hot", "Hot", auth="?", tables=["User:write"])     # 🔴 unauth write to a hot table
    cold = mk("/cold", "Cold", auth="?", tables=["Log:write"])   # 🔴 unauth write to an isolated one
    base = shared
    head = shared + [hot, cold]
    changes = diff_pr.diff(base, head)
    news = [c for c in changes if c["kind"] == "NEW ENDPOINT"]
    assert {c["sev"] for c in news} == {diff_pr.CRIT}            # same tier, so blast decides order
    assert [c["route"] for c in news] == ["/hot", "/cold"]
    assert news[0]["blast"] > news[1]["blast"]


def test_blast_does_not_cross_severity_tiers():
    # a low-severity but high-blast change must never outrank a high-severity one
    shared = [mk(f"/read{i}", f"R{i}", auth="IsAuthenticated", tables=["User:read"]) for i in range(9)]
    crit = mk("/danger", "D", auth="?", tables=["Log:write"])            # 🔴, blast 0
    base = shared
    head = shared + [crit, mk("/danger2", "D2", auth="?", tables=["User:write"])]
    changes = diff_pr.diff(base, head)
    assert changes[0]["sev"] == diff_pr.CRIT                    # severity still leads the whole list
