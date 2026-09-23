"""Create everything, in order, from an empty database.

    python scripts/setup.py                   # schema, seed, views, RLS
    python scripts/setup.py --no-rls          # leave security off for now
    python scripts/setup.py --accounts 50000  # a bigger book

This is a convenience wrapper. Every step it runs is a plain file you can open
and run yourself in the Fabric query editor or SQL Server Management Studio:

    sql/01-schema.sql              tables and reference data
    scripts/seed.py                synthetic customers and accounts
    sql/02-views.sql               the three published views
    sql/03-row-level-security.sql  the fail-closed security policy

If any step fails, fix the cause and re-run - all four are idempotent.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

from db import connect, run_sql_file  # noqa: E402


def step(number: int, total: int, title: str) -> None:
    print()
    print(f"[{number}/{total}] {title}")
    print("-" * (len(title) + 8))
    # Flush before any subprocess writes to the same stdout, otherwise the
    # child's output appears above this header and the log reads backwards.
    sys.stdout.flush()


def main() -> None:
    ap = argparse.ArgumentParser(description="Set up the database from nothing.")
    ap.add_argument("--accounts", type=int, default=8000)
    ap.add_argument("--customers", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-rls", action="store_true",
                    help="skip row-level security (needed for the stage-three "
                         "data agent comparison)")
    args = ap.parse_args()

    total = 3 if args.no_rls else 4

    print("Checking the connection ...")
    sys.stdout.flush()
    try:
        conn = connect()
        cur = conn.cursor()
        cur.execute("SELECT DB_NAME(), SUSER_SNAME()")
        db, who = cur.fetchone()
        cur.close()
        conn.close()
        print(f"  connected to {db} as {who}")
    except Exception as exc:  # noqa: BLE001
        print(f"\nCould not connect: {exc}")
        print("\nSee docs/05-troubleshooting.md")
        sys.exit(1)

    step(1, total, "Creating tables and reference data")
    run_sql_file(os.path.join(ROOT, "sql", "01-schema.sql"))

    step(2, total, "Generating the synthetic book")
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "seed.py"),
         "--accounts", str(args.accounts),
         "--customers", str(args.customers),
         "--seed", str(args.seed)],
        cwd=ROOT,
    )
    if result.returncode != 0:
        sys.exit(result.returncode)

    step(3, total, "Creating the published views")
    run_sql_file(os.path.join(ROOT, "sql", "02-views.sql"))

    if not args.no_rls:
        step(4, total, "Enabling row-level security")
        run_sql_file(os.path.join(ROOT, "sql", "03-row-level-security.sql"))

    print()
    print("=" * 62)
    print("Database ready.")
    print("=" * 62)
    if args.no_rls:
        print()
        print("Row-level security is OFF. Enable it with:")
        print("    python -c \"import sys;sys.path.insert(0,'src');"
              "from db import run_sql_file;run_sql_file('sql/03-row-level-security.sql')\"")
    else:
        print()
        print("Row-level security is ON and fail-closed. A connection with no")
        print("role claim sees zero rows - that is correct, not a failure.")
    print()
    print("Next:")
    print("    python scripts/verify.py      confirm the data looks right")
    print("    dab start -c dab/dab-config.json")
    print()
    print("Then open docs/02-configuring-dab.md to point it at your own data.")


if __name__ == "__main__":
    main()
