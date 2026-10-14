"""Verify every source in config/sources.yaml (see gold_agent.ops.verify_cli for options)."""

import sys

from gold_agent.ops.verify_cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
