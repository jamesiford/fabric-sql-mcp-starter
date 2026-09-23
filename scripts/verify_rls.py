"""Prove that row-level security filters rows per caller.

    python scripts/verify_rls.py

Presents several different role claims on the same connection and reports what
each one can see. The query is identical every time - only the claim changes.

This is the evidence that per-user entitlement is enforced by the database
rather than by the application. It is also a regression test: run it after
changing the predicate, the view, or the config.

Expected shape:

    svc-all         8,000 accounts   (break-glass service role)
    B. Nakamura       812 accounts   (one relationship manager)
    I. Petrosyan      794 accounts   (a different manager, a different book)
    (no claim)          0 accounts   (fail-closed)

If every row shows the same number, the security policy is not enabled - run
sql/03-row-level-security.sql.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

from db import connect  # noqa: E402

# The full WS-Federation claim URI. This is what Data API builder actually
# writes into session context - not a short name like 'role'. The predicate in
# sql/03-row-level-security.sql reads this exact key.
ROLE_CLAIM = "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"


def count_for(cursor, role: str | None) -> int:
    """Rows visible to a caller presenting this role claim."""
    if role is None:
        # Clear any claim from a previous iteration - otherwise the "no claim"
        # case silently inherits the last role and the test proves nothing.
        cursor.execute(f"EXEC sp_set_session_context '{ROLE_CLAIM}', NULL")
    else:
        cursor.execute(f"EXEC sp_set_session_context '{ROLE_CLAIM}', ?", (role,))
    cursor.execute("SELECT COUNT(*) FROM dbo.vw_deposits")
    return cursor.fetchone()[0]


def main() -> int:
    conn = connect()
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) FROM sys.security_policies "
        "WHERE name = 'rls_rm_book' AND is_enabled = 1"
    )
    if not cur.fetchone()[0]:
        print("Row-level security is NOT enabled.")
        print()
        print("Every caller currently sees every row. To enable it:")
        print("    sql/03-row-level-security.sql")
        cur.close()
        conn.close()
        return 1

    # Pick two real relationship managers out of the data rather than hardcoding
    # names, so this works against a re-seeded or customised book.
    cur.execute(f"EXEC sp_set_session_context '{ROLE_CLAIM}', 'svc-all'")
    cur.execute(
        "SELECT TOP 2 relationship_manager, COUNT(*) AS n "
        "FROM dbo.vw_deposits GROUP BY relationship_manager ORDER BY n DESC"
    )
    managers = [row[0] for row in cur.fetchall()]

    cases: list[str | None] = ["svc-all", *managers, None]

    print("The same query, run four times. Only the caller's claim changes.")
    print()
    print(f"  {'role claim':<26} {'rows visible':>13}")
    print(f"  {'-' * 26} {'-' * 13}")

    results: dict[str, int] = {}
    for role in cases:
        n = count_for(cur, role)
        label = role if role else "(no claim)"
        results[label] = n
        print(f"  {label:<26} {n:>13,}")

    # Leave the session clean for whatever runs next.
    cur.execute(f"EXEC sp_set_session_context '{ROLE_CLAIM}', NULL")
    cur.close()
    conn.close()

    unrestricted = results.get("svc-all", 0)
    scoped = [results[m] for m in managers if m in results]
    anonymous = results.get("(no claim)", -1)

    print()
    ok = (
        unrestricted > 0
        and all(0 < s < unrestricted for s in scoped)
        and anonymous == 0
    )

    if ok:
        print("PASS")
        print()
        print(f"  The service role saw all {unrestricted:,} accounts.")
        print(f"  Each relationship manager saw only their own book "
              f"({', '.join(f'{s:,}' for s in scoped)}).")
        print("  A caller with no role claim saw nothing.")
        print()
        print("  The filter is applied by the database, below the API. No caller")
        print("  can bypass it - by the time the query runs, the identity is")
        print("  already bound to the session.")
        return 0

    print("FAIL - filtering did not behave as expected.")
    print()
    if unrestricted and scoped and all(s == unrestricted for s in scoped):
        print("  Every role saw the same number of rows, which means the claim")
        print("  is not reaching the predicate. Check that dab-config.json has:")
        print("      \"options\": { \"set-session-context\": true }")
        print("  and restart the server - config is read at startup only.")
    elif anonymous != 0:
        print(f"  A caller with no claim saw {anonymous:,} rows. The predicate is")
        print("  not fail-closed. Compare it against sql/03-row-level-security.sql.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
