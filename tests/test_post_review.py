"""The CI label picker: one prism:* triage label per PR, chosen by the worst signal in the review
(semantic changes, invariant alerts, or intent contradictions). Pure function — no network."""
from ci.post_review import pick_label, LABELS


def test_label_picks_worst_severity_across_all_signals():
    review = {"changes": [{"sev": "🟡"}, {"sev": "🟢"}],
              "invariants": [{"kind": "NEW VIOLATION", "sev": "🔴"}],
              "contradictions": []}
    assert pick_label(review) == LABELS["🔴"]        # a 🔴 invariant alert dominates 🟡/🟢 changes


def test_label_counts_intent_contradiction():
    review = {"changes": [{"sev": "🟢"}], "invariants": [],
              "contradictions": [{"sev": "🔴", "why": "claims refactor, but adds an endpoint"}]}
    assert pick_label(review) == LABELS["🔴"]


def test_label_ignores_non_alert_invariants():
    review = {"changes": [{"sev": "🟡"}],
              "invariants": [{"kind": "HELD", "sev": "🟢"}, {"kind": "RESOLVED", "sev": "🟢"}],
              "contradictions": []}
    assert pick_label(review) == LABELS["🟡"]        # HELD/RESOLVED aren't alerts → don't raise it


def test_label_defaults_to_safe_refactor_when_no_signal():
    assert pick_label({"changes": [], "invariants": [], "contradictions": []}) == LABELS["🟢"]
    assert pick_label({}) == LABELS["🟢"]
