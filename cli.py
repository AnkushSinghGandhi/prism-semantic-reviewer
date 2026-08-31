#!/usr/bin/env python3
"""
Pryti CLI Entrypoint
"""
import sys

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Pryti Semantic Reviewer")
        print("\nUsage: pryti <command> [options]")
        print("\nCommands:")
        print("  review    Run a semantic review of a PR or commit range")
        print("  post      Post a review to a PR (sticky comment, inline comments, label)")
        print("  serve     Start the Pryti web UI")
        print("  invariants Discover baseline invariants from a repository's history")
        print("  digest    Org-wide leadership roll-up from the labels Pryti applies to PRs")
        print("\nRun 'pryti <command> --help' for more information on a command.")
        sys.exit(0)

    command = sys.argv[1]
    # Remove the command from sys.argv so sub-parsers work correctly
    sys.argv.pop(1)

    if command == "review":
        import diff_pr
        # No args → review the current repo's branch vs. its default branch (diff_pr resolves it).
        diff_pr.main()
    elif command == "post":
        from ci import post_review
        if len(sys.argv) == 1:
            sys.argv.append("--help")
        post_review.main()
    elif command == "serve":
        import serve
        serve.main()
    elif command == "invariants":
        import invariants
        if len(sys.argv) == 1:
            sys.argv.append("--help")
        invariants.main()
    elif command == "digest":
        import digest
        if len(sys.argv) == 1:
            sys.argv.append("--help")
        digest.main()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)

if __name__ == "__main__":
    main()
