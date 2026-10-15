import argparse
import json

from .team import run_team
from .workspace import Workspace


def main():
    parser = argparse.ArgumentParser(description="Synthetic customer escalation team")
    parser.add_argument("--state", default="workspace.json")
    parser.add_argument("--case", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--model")
    parser.add_argument("--allow-writes", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(run_team(Workspace(args.state), args.case, args.request,
                                  args.model, args.allow_writes), indent=2))
    except (ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")
