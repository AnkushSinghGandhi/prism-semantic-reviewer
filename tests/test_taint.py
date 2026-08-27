"""Intra-procedural PII taint (#9): a sensitive field that actually *flows* into an egress sink is
✓ verified ("this field leaves here"); mere co-occurrence stays ⚠ potential. Also the remaining
precision item — resolving `<instance>.save()` to a concrete model via local variable types."""
import ast
from extractor.analyzer import FactCollector, _score_pii, _score_db


def _pii(code):
    fc = FactCollector("v.py")
    fc.visit(ast.parse(code))
    return _score_pii(fc)


def _db(code):
    fc = FactCollector("v.py")
    fc.visit(ast.parse(code))
    return _score_db(fc)


def test_traced_leak_is_verified_with_field_and_sink():
    e = _pii('def post(self, request):\n'
             '    email = request.data.get("email")\n'
             '    requests.post("https://x", json={"email": email})\n')
    assert e.status == "✓"
    assert any("email → requests.post" in it for it in e.items)   # specific field, specific sink


def test_co_occurrence_without_flow_stays_potential():
    """PII read AND an egress call, but the sensitive value never reaches it → honestly ⚠."""
    e = _pii('def post(self, request):\n'
             '    e = user.email\n'
             '    requests.post("https://x", json={"status": "ok"})\n')
    assert e.status == "⚠" and not any("→" in it for it in e.items)


def test_pii_read_without_egress_is_unknown():
    e = _pii('def get(self, request):\n    e = user.email\n    return e_local\n')
    assert e.status == "?"


def test_unknown_wrapper_call_does_not_forge_a_leak():
    """Passing PII through an unrecognized function (which might sanitize) must NOT become a
    verified leak — taint only propagates through serialize-only wrappers."""
    e = _pii('def post(self, request):\n'
             '    safe = anonymize(user.email)\n'
             '    requests.post("https://x", json={"v": safe})\n')
    assert e.status == "⚠"                                        # source present, but no traced flow


def test_async_and_log_and_return_sinks():
    delay = _pii('def post(self, request):\n    tasks.notify.delay(user.email)\n')
    ret = _pii('def get(self, request):\n    return user.phone\n')
    assert delay.status == "✓" and any("celery.delay" in it for it in delay.items)
    assert ret.status == "✓" and any("phone → response" in it for it in ret.items)


def test_instance_write_resolves_via_for_loop():
    e = _db('def post(self, request):\n'
            '    for order in Order.objects.filter(user=request.user):\n'
            '        order.save()\n')
    assert e.status == "✓" and any("Order:write" in it for it in e.items)
    assert not any("<instance>" in it for it in e.items)


def test_instance_write_stays_honest_when_unresolvable():
    e = _db('def post(self, request):\n    obj = self.get_object()\n    obj.save()\n')
    assert e.status == "⚠" and any("<instance>:write" in it for it in e.items)
