"""Turn row-level security on or off.

    python scripts/rls.py --off     # see data in the demo
    python scripts/rls.py --on      # put the policy back
    python scripts/rls.py           # report the current state

setup.py applies the policy by default, which is the right default for the
database and the wrong one for a first look at the demo app: the predicate is
claim-driven and fail-closed, a local session presents no claim, and so every
query correctly returns nothing.

This is the switch for that, so you are not pasting multi-line python at a shell
prompt to flip it. It resolves paths from the repo root, so it works from any
working directory.
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

from db import connect, run_sql_file  # noqa: E402


def visible_rows() -> int:
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM dbo.vw_deposits")
    n = int(cur.fetchone()[0])
    cur.close()
    conn.close()
    return n


def policy_enabled() -> bool:
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM sys.security_policies WHERE is_enabled = 1")
    n = int(cur.fetchone()[0])
    cur.close()
    conn.close()
    return n > 0


def report() -> None:
    on = policy_enabled()
    print(f"\n  Row-level security is {'ON' if on else 'OFF'}.")
    print(f"  This connection can see {visible_rows():,} rows of dbo.vw_deposits.\n")
    if on:
        print("  Seeing zero is correct: this session presents no role claim.")
        print("  Run 'python scripts/rls.py --off' to see data in the demo.\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--on", action="store_true", help="apply the security policy")
    group.add_argument("--off", action="store_true", help="drop the security policy")
    args = ap.parse_args()

    if args.on:
        run_sql_file(os.path.join(ROOT, "sql", "03-row-level-security.sql"))
    elif args.off:
        run_sql_file(os.path.join(ROOT, "sql", "04-disable-row-level-security.sql"))

    report()


if __name__ == "__main__":
    main()
