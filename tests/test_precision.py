"""Tier 1 #2 — the two precision upgrades:
  1. depth-2 follow: an outbound call two hops from the handler is still found.
  2. variable typing: `var = Model...; var.save()` names the table instead of `<instance>`.
"""
from conftest import BLOG, facts, by_route
from extractor import analyze_repo


def _checkout():
    return by_route(analyze_repo(BLOG), "api/checkout")


def test_instance_save_resolves_to_model():
    f = facts(_checkout(), "e3_db_tables")
    assert "Order:write" in f            # order.save() resolved via var typing
    assert "<instance>:write" not in f   # no vague placeholder left


def test_depth2_external_call_found():
    # handler → CheckoutService().complete() → self._notify() → requests.post(WEBHOOK_URL)
    f = facts(_checkout(), "e4_external")
    assert any("requests.post" in x for x in f)
    assert any("WEBHOOK_URL" in x for x in f)


def test_depth1_still_works():
    # regression: the one-hop case (handler → PaymentService().charge() → requests.post) still resolves
    f = facts(by_route(analyze_repo(BLOG), "api/orders"), "e4_external")
    assert any("PAYMENT_URL" in x for x in f)
