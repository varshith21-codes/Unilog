"""Serve the enriched catalogue over MCP so an agent can query it live.

Tier 3, item 21, and blueprint Part 4.2's fifth persona: an AI shopping agent needs machine-readable
product facts — typed values and units, not prose.

    # stdio, which is what an MCP host launches
    python scripts/mcp_server.py

    # a real port, for the Inspector or a remote host
    python scripts/mcp_server.py --http --port 3001

    # serve real pipeline output instead of the golden corpus
    python scripts/mcp_server.py --from-bundles

    # what the agent would see, without a transport
    python scripts/mcp_server.py --describe

Point an MCP host at it with:

    {"mcpServers": {"axiom": {"command": "python",
                              "args": ["scripts/mcp_server.py"]}}}

**stdout is the wire.** Under stdio the protocol owns stdout, so nothing here prints to it before
serving begins; ``--describe`` is the one mode that writes to stdout, and it never starts a server.

**The provenance contract.** Every value an agent receives carries its derivation method, whether
its citation was verified, and an explicit `usable_for_compliance` flag; and a compliance attribute
is never matched on unverified data. An agent restates what it is given as fact, so a catalogue that
hands it a bare boolean has published that boolean in prose everywhere the agent speaks.

Needs the optional extra:  pip install -e ".[mcp]"

Exit codes: 0 on a clean shutdown, 2 on a bad invocation, 4 when the extra is missing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from axiom.agents import list_classes
from axiom.resolve import load_catalogue
from axiom.schema import load_default

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLES = REPO_ROOT / "data" / "console"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--http",
        action="store_true",
        help="serve Streamable HTTP on a port instead of stdio",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3001)
    parser.add_argument(
        "--from-bundles",
        action="store_true",
        help="serve real pipeline output from data/console rather than the golden corpus",
    )
    parser.add_argument("--bundles", type=Path, default=DEFAULT_BUNDLES)
    parser.add_argument(
        "--describe",
        action="store_true",
        help="print the catalogue and schema an agent would see, then exit without serving",
    )
    args = parser.parse_args()

    registry = load_default()
    catalogue = load_catalogue(
        registry, bundles=args.bundles, prefer_bundles=args.from_bundles
    )

    if not len(catalogue):
        print("no records to serve", file=sys.stderr)
        return 2

    if args.describe:
        print(
            json.dumps(
                {
                    "catalogue": catalogue.summary(),
                    "schema": list_classes(registry),
                },
                indent=2,
                default=str,
            )
        )
        return 0

    try:
        from axiom.agents.server import build_server
    except ModuleNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 4

    try:
        server = build_server(catalogue, registry)
    except ModuleNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 4

    # stderr, because under stdio stdout is the protocol wire.
    print(
        f"axiom-catalogue: serving {len(catalogue)} records "
        f"({catalogue.source.value}, measured={catalogue.source.is_measured})",
        file=sys.stderr,
    )

    if args.http:
        server.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
