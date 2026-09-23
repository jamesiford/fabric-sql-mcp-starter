"""Show what your MCP server actually advertises.

    python scripts/inspect_server.py
    python scripts/inspect_server.py --url https://my-app.azurecontainerapps.io/mcp

Connects over the real MCP protocol and prints the tool surface plus the entity
catalog. Use it to answer two questions with certainty rather than assumption:

    1. Is the write surface really absent?
       Expect exactly three tools. If create_record, update_record,
       delete_record or execute_entity appears, your dml-tools config is wrong.

    2. What does the model actually see?
       describe_entities returns exactly the descriptions you wrote. If a field
       has no description, the model is guessing about it.

This is the fastest way to check a config change did what you intended.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamablehttp_client  # noqa: E402

WRITE_TOOLS = {"create_record", "update_record", "delete_record", "execute_entity"}


async def inspect(url: str, show_fields: bool) -> int:
    print(f"Connecting to {url}")
    print()

    async with streamablehttp_client(url, timeout=60) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"  server   : {init.serverInfo.name} {init.serverInfo.version}")
            print(f"  protocol : {init.protocolVersion}")
            print()

            listing = await session.list_tools()
            names = [t.name for t in listing.tools]

            print(f"Tools advertised ({len(names)})")
            for tool in listing.tools:
                marker = "  !!  " if tool.name in WRITE_TOOLS else "      "
                print(f"{marker}{tool.name}")
            print()

            exposed_writes = WRITE_TOOLS.intersection(names)
            if exposed_writes:
                print("  *** WRITE TOOLS ARE EXPOSED ***")
                print(f"  {', '.join(sorted(exposed_writes))}")
                print("  Set these to false under runtime.mcp.dml-tools and restart.")
                print()
                return 1

            print("  Write surface is absent, as intended.")
            print()

            # describe_entities is the model's entire map of your data.
            result = await session.call_tool("describe_entities", {})
            text = "".join(
                b.text for b in result.content if getattr(b, "type", None) == "text"
            )
            try:
                catalog = json.loads(text)
            except json.JSONDecodeError:
                print(text[:2000])
                return 0

            entities = catalog.get("entities", [])
            print(f"Entities published ({len(entities)})")
            print()
            for ent in entities:
                print(f"  {ent.get('name')}")
                desc = (ent.get("description") or "").strip()
                if desc:
                    wrapped = desc if len(desc) <= 150 else desc[:150] + " ..."
                    print(f"      {wrapped}")
                else:
                    print("      (no description - the model is guessing about this entity)")

                fields = ent.get("fields") or []
                if fields:
                    missing = [
                        f.get("name") for f in fields
                        if not (f.get("description") or "").strip()
                    ]
                    print(f"      {len(fields)} fields", end="")
                    if missing:
                        print(f", {len(missing)} without a description: "
                              f"{', '.join(str(m) for m in missing[:6])}"
                              f"{' ...' if len(missing) > 6 else ''}")
                    else:
                        print(", all described")

                    if show_fields:
                        for f in fields:
                            fd = (f.get("description") or "").strip() or "(none)"
                            print(f"        - {f.get('name')}: {fd[:110]}")
                print()

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Inspect an MCP server's tool surface.")
    ap.add_argument("--url", default=os.environ.get("DAB_MCP_URL", "http://localhost:5000/mcp"))
    ap.add_argument("--fields", action="store_true", help="print every field description")
    args = ap.parse_args()

    try:
        return asyncio.run(inspect(args.url, args.fields))
    except Exception as exc:  # noqa: BLE001
        print(f"\nCould not reach the server: {type(exc).__name__}: {exc}")
        print()
        print("Is it running?   dab start -c dab/dab-config.json")
        print("See docs/05-troubleshooting.md")
        return 1


if __name__ == "__main__":
    sys.exit(main())
