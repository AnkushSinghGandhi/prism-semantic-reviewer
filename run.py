#!/usr/bin/env python3
"""
Run the Milestone 1 extractor over a Django repo.

    python run.py <repo_root> [--json facts.json] [--csv scored.csv] [--filter substr]

Prints a per-edge coverage summary (the Experiment B feasibility signal), and writes
facts.json (the product artifact that Milestone 2 will diff) + a scored CSV comparable to
the hand-scored experiment sheet.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extractor import analyze_repo, to_dict  # noqa: E402

EDGES = [("e1_route_handler", "E1 route→handler"), ("e2_auth", "E2 auth"),
         ("e3_db_tables", "E3 db"), ("e4_external", "E4 external"),
         ("e5_async", "E5 async"), ("e6_pii", "E6 pii"), ("e7_cache", "E7 cache")]
STATUSES = ["✓", "⚠", "?", "n/a"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--json", default="facts.json")
    ap.add_argument("--csv", default="scored.csv")
    ap.add_argument("--filter", default="", help="only endpoints whose route contains this")
    ap.add_argument("--show", type=int, default=8, help="print N sample endpoints in full")
    args = ap.parse_args()

    eps = analyze_repo(args.root)
    if args.filter:
        eps = [e for e in eps if args.filter in e.route]

    # coverage table
    counts = {ek: {s: 0 for s in STATUSES} for ek, _ in EDGES}
    for e in eps:
        for ek, _ in EDGES:
            edge = getattr(e, ek)
            counts[ek][edge.status] = counts[ek].get(edge.status, 0) + 1

    n = len(eps)
    print(f"\nAnalyzed {n} endpoints in {args.root}\n")
    print(f"{'edge':<20}{'✓':>6}{'⚠':>6}{'?':>6}{'n/a':>6}   {'✓% of applicable':>18}")
    print("-" * 68)
    for ek, label in EDGES:
        c = counts[ek]
        applicable = n - c["n/a"]
        pct = (100 * c["✓"] / applicable) if applicable else 0.0
        print(f"{label:<20}{c['✓']:>6}{c['⚠']:>6}{c['?']:>6}{c['n/a']:>6}   {pct:>16.0f}%")

    # sample detail
    print("\n" + "=" * 68)
    print("SAMPLE ENDPOINTS")
    print("=" * 68)
    for e in eps[:args.show]:
        print(f"\n{','.join(e.methods) or '?'}  {e.route}")
        print(f"    handler: {e.handler}  ({e.file}:{e.line})")
        for ek, label in EDGES[1:]:
            edge = getattr(e, ek)
            extra = f"  {edge.items}" if edge.items else ""
            note = f"  — {edge.note}" if edge.note else ""
            print(f"    {label:<16} {edge.status}{extra}{note}")

    # write json
    with open(args.json, "w") as f:
        json.dump([to_dict(e) for e in eps], f, ensure_ascii=False, indent=2)
    # write csv
    with open(args.csv, "w") as f:
        f.write("route,handler,methods,E1,E2,E3,E4,E5,E6\n")
        for e in eps:
            row = [e.route, e.handler, "|".join(e.methods)]
            for ek, _ in EDGES:
                row.append(getattr(e, ek).status)
            f.write(",".join('"' + str(c).replace('"', "'") + '"' for c in row) + "\n")
    print(f"\nWrote {args.json} and {args.csv}")


if __name__ == "__main__":
    main()
