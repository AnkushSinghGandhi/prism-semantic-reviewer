"""pryti invariants --bootstrap — the pure corpus builder. No git, no IO."""
import invariants


def _discovered():
    """Stand-in for one repo's `corpus(discover(...))` output."""
    return [
        {"id": "auth-before-write", "kind": "near-miss", "severity": "critical",
         "observed": {"ok": 8, "total": 10, "ratio": 0.8},
         "baseline_exceptions": [{"route": "/pay", "why": "auth unspecified", "location": "v.py:9"}],
         "confirmed": False, "owner": None},
        {"id": "external-egress-allowlist", "kind": "boundary", "severity": "critical",
         "observed": {"destinations": ["stripe.com", "settings.PAYTM_URL"]},
         "baseline_exceptions": [], "confirmed": False, "owner": None},
        {"id": "auth-group:/companions", "kind": "universal", "severity": "high",
         "observed": {"ok": 6, "total": 6, "ratio": 1.0},
         "baseline_exceptions": [], "confirmed": False, "owner": None},
        {"id": "auth-group:/coupons", "kind": "near-miss", "severity": "high",
         "observed": {"ok": 4, "total": 5, "ratio": 0.8},
         "baseline_exceptions": [{"route": "/coupons/x", "why": "open", "location": "c.py:3"}],
         "confirmed": False, "owner": None},
    ]


def _baseline():
    return [
        {"id": "auth-before-write", "statement": "Auth before any DB write",
         "severity": "critical", "confirmed": True, "owner": "security", "baseline_exceptions": []},
        {"id": "external-egress-allowlist", "statement": "Egress allowlist",
         "severity": "critical", "confirmed": True, "owner": "security",
         "observed": {"destinations": []}},
    ]


def test_baseline_rule_freezes_this_repos_current_exceptions():
    out = invariants.bootstrap_corpus(_discovered(), _baseline())
    awrite = next(c for c in out if c["id"] == "auth-before-write")
    # today's violator is baselined → enforce() will only fire on a NEW one (ratchet)
    assert awrite["baseline_exceptions"] == [
        {"route": "/pay", "why": "auth unspecified", "location": "v.py:9"}]
    assert awrite["confirmed"] is True
    assert awrite["owner"] == "security"          # org owner preserved, not overwritten


def test_boundary_allowlist_filled_from_discovery():
    out = invariants.bootstrap_corpus(_discovered(), _baseline())
    egress = next(c for c in out if c["id"] == "external-egress-allowlist")
    # the empty baseline allowlist is populated with the repo's current destinations
    assert egress["observed"]["destinations"] == ["stripe.com", "settings.PAYTM_URL"]


def test_high_confidence_extra_is_auto_confirmed_and_tagged():
    out = invariants.bootstrap_corpus(_discovered(), _baseline())
    grp = next((c for c in out if c["id"] == "auth-group:/companions"), None)
    assert grp is not None                         # a persistent universal is safe to include
    assert grp["confirmed"] is True
    assert grp["owner"] == "auto-bootstrap"        # tagged so a human can tell it apart


def test_shaky_near_miss_extra_is_left_for_a_human():
    out = invariants.bootstrap_corpus(_discovered(), _baseline())
    assert not any(c["id"] == "auth-group:/coupons" for c in out)


def test_no_baseline_still_yields_confirmed_high_confidence_rules():
    out = invariants.bootstrap_corpus(_discovered(), baseline=[])
    ids = {c["id"] for c in out}
    assert ids == {"external-egress-allowlist", "auth-group:/companions"}   # universals/boundaries only
    assert all(c["confirmed"] for c in out)


def test_extras_can_be_disabled():
    out = invariants.bootstrap_corpus(_discovered(), _baseline(), include_extras=False)
    assert {c["id"] for c in out} == {"auth-before-write", "external-egress-allowlist"}
    assert all(c["confirmed"] for c in out)
