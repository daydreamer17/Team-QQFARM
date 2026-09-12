from __future__ import annotations

import argparse

from .backend.checkpoints import setup_checkpoints
from .backend.settings import settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Initialize LangGraph checkpoint tables")
    parser.add_argument("command", choices=("setup",))
    args = parser.parse_args(argv)
    if args.command == "setup":
        setup_checkpoints(settings.database_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
