"""The before/after graph: nodes/edges tagged added / removed / same."""
import copy

from conftest import BLOG, by_route
from extractor import analyze_repo
from diff_pr import build_diff_graph, build_review, diff


def test_same_endpoint_has_no_changes():
    ep = by_route(analyze_repo(BLOG), "api/orders")
    g = build_diff_graph(ep, ep)
    assert g["nodes"] and all(n["state"] == "same" for n in g["nodes"])
    assert all(e["state"] == "same" for e in g["edges"])


def test_added_fact_is_marked_added_others_same():
    head = by_route(analyze_repo(BLOG), "api/orders")
    base = copy.deepcopy(head)
    base.e5_async.items = []              # pretend the async dispatch didn't exist before
    g = build_diff_graph(base, head)
    async_nodes = [n for n in g["nodes"] if n["type"] == "async"]
    assert async_nodes and all(n["state"] == "added" for n in async_nodes)
    # a table that exists in both sides stays "same"
    assert any(n["state"] == "same" for n in g["nodes"] if n["type"] == "table")


def test_removed_fact_is_marked_removed():
    base = by_route(analyze_repo(BLOG), "api/orders")
    head = copy.deepcopy(base)
    head.e4_external.items = []           # external call removed in head
    g = build_diff_graph(base, head)
    assert any(n["type"] == "external" and n["state"] == "removed" for n in g["nodes"])


def test_new_endpoint_graph_is_all_added():
    head = analyze_repo(BLOG)
    base = [e for e in head if "checkout" not in e.route]      # checkout is introduced by the PR
    review = build_review(diff(base, head), [], {"title": "t", "base": "a", "head": "b", "shortstat": ""})
    ch = next(c for c in review["changes"] if "checkout" in c["route"])
    assert ch["kind"] == "NEW ENDPOINT"
    assert all(n["state"] == "added" for n in ch["graph"]["nodes"])
