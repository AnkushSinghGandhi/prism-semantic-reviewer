#!/usr/bin/env python3
"""
Prism CLI Entrypoint
"""
import sys

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Prism Semantic Reviewer")
        print("\nUsage: prism <command> [options]")
        print("\nCommands:")
        print("  review    Run a semantic review of a PR or commit range")
        print("  serve     Start the Prism web UI")
        print("  invariants Discover baseline invariants from a repository's history")
        print("\nRun 'prism <command> --help' for more information on a command.")
        sys.exit(0)

    command = sys.argv[1]
    # Remove the command from sys.argv so sub-parsers work correctly
    sys.argv.pop(1)

    if command == "review":
        import diff_pr
        # If no arguments provided, trigger diff_pr's help
        if len(sys.argv) == 1:
            sys.argv.append("--help")
        diff_pr.main()
    elif command == "serve":
        import serve
        serve.main()
    elif command == "invariants":
        import invariants
        if len(sys.argv) == 1:
            sys.argv.append("--help")
        invariants.main()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)

if __name__ == "__main__":
    main()
