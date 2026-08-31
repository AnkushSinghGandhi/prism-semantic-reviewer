"""pryti digest — offline unit tests. The GitHub reads are injected, so no network."""
from urllib.parse import quote

import digest


def test_severity_counts_maps_each_tier():
    def fake(url):
        if "security" in url:
            return {"total_count": 3}
        if "payment" in url:
            return {"total_count": 2}
        return {"total_count": 0}
    counts = digest.severity_counts("acme", fetch=fake)
    assert counts == {"🔴": 3, "🟠": 2, "🟡": 0}


def test_unfetchable_tier_is_none_not_zero():
    def boom(url):
        raise RuntimeError("network down")
    counts = digest.severity_counts("acme", fetch=boom)
    assert counts["🔴"] is None
    # honesty rule: an unknown renders as ? in the digest, never a fake 0
    assert "?" in digest.build_digest("acme", counts)
    assert "0" not in digest.build_digest("acme", counts)


def test_build_digest_shows_counts_and_adoption():
    text = digest.build_digest("careers360", {"🔴": 4, "🟠": 9, "🟡": 12},
                               adoption={"total": 20, "live": 6, "moat": 2})
    assert "careers360" in text
    assert "security / PII / unauth write" in text
    assert "6/20 repos live" in text
    assert "confirmed in 2" in text


def test_adoption_counts_checks_both_markers():
    have = {("acme/live", digest.WORKFLOW_PATH),
            ("acme/full", digest.WORKFLOW_PATH),
            ("acme/full", digest.INVARIANTS_PATH)}

    def fake(url):
        for repo, path in have:
            if f"repos/{repo}/contents/{quote(path)}" in url:
                return {"ok": True}
        raise RuntimeError("404")
    a = digest.adoption_counts(["acme/live", "acme/full", "acme/none"], fetch=fake)
    assert a == {"total": 3, "live": 2, "moat": 1}


def test_slack_payload():
    assert digest.slack_payload("hi") == {"text": "hi"}
