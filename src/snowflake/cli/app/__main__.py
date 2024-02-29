from __future__ import annotations

import sys

from snowflake.cli.app.cli_app import app


def main(*args):
    if len(sys.argv) == 1:
        sys.argv.append("-h")
    app(*args)


if __name__ == "__main__":
    main()

if getattr(sys, "frozen", False):
    main(sys.argv[1:])
