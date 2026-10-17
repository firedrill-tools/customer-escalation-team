"""Private model-free source commands; no runtime role/read capability is returned."""

import argparse
import json

from .managed import ManagedSource
from .strict import parse_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--db", required=True)
    init.add_argument("--fixture")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--db", required=True)
    intent = commands.add_parser("intent")
    intent.add_argument("--db", required=True)
    intent.add_argument("--case", required=True)
    intent.add_argument("--request", required=True)
    intent.add_argument("--credit-cents", type=int)
    intent.add_argument("--unlock", action="store_true")
    update = commands.add_parser("update")
    update.add_argument("--db", required=True)
    update.add_argument("--case", required=True)
    update.add_argument("--updates-json", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            if args.fixture:
                source = ManagedSource.seed(args.db, args.fixture)
            else:
                from .assets import read_asset

                source = ManagedSource.seed_bytes(args.db, read_asset("starter.json"))
        else:
            source = ManagedSource(args.db)
        try:
            if args.command == "update":
                result = source.update(
                    args.case,
                    parse_json(args.updates_json.encode("utf-8", errors="strict")),
                )
            elif args.command == "intent":
                result = source.intent(
                    args.case,
                    args.request,
                    credit_cents=args.credit_cents,
                    unlock=args.unlock,
                )
            else:
                result = source.snapshot()
        finally:
            cleanup = source.close()
            if not cleanup["database_closed"] or not cleanup["descriptor_closed"]:
                raise RuntimeError("private escalation source cleanup unconfirmed")
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
