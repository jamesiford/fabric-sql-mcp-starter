"""Check that the database looks the way the documentation says it does.

    python scripts/verify.py

Run this after scripts/setup.py. It is a sanity check, not a test suite: it
confirms the seed produced a coherent book and that the published views return
what the MCP server will be describing to a model.

If row-level security is enabled, this script sets the break-glass service role
first so it can see the whole book. That is why it reports on every branch even
though a plain connection would see nothing.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

from db import connect  # noqa: E402

ROLE_CLAIM = "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"


def main() -> int:
    conn = connect()
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM sys.security_policies WHERE name = 'rls_rm_book' AND is_enabled = 1")
    rls_on = bool(cur.fetchone()[0])
    print(f"row-level security : {'ON (fail-closed)' if rls_on else 'OFF'}")

    if rls_on:
        # Present the break-glass role so the rest of the checks can see data.
        cur.execute(f"EXEC sp_set_session_context '{ROLE_CLAIM}', 'svc-all'")
        print("                     (using svc-all to inspect the whole book)")

    print()
    checks: list[tuple[str, str, object]] = []

    cur.execute("SELECT COUNT(*) FROM dbo.vw_deposits")
    accounts = cur.fetchone()[0]
    checks.append(("accounts", f"{accounts:,}", accounts > 0))

    cur.execute("SELECT COUNT(DISTINCT customer_id) FROM dbo.vw_deposits")
    customers = cur.fetchone()[0]
    checks.append(("customers", f"{customers:,}", customers > 0))

    cur.execute("SELECT COUNT(DISTINCT branch_name) FROM dbo.vw_deposits")
    branches = cur.fetchone()[0]
    checks.append(("branches", str(branches), branches == 9))

    cur.execute("SELECT COUNT(DISTINCT relationship_manager) FROM dbo.vw_deposits")
    rms = cur.fetchone()[0]
    checks.append(("relationship managers", str(rms), rms > 1))

    cur.execute("SELECT COUNT(DISTINCT currency) FROM dbo.vw_deposits")
    ccy = cur.fetchone()[0]
    checks.append(("currencies", str(ccy), ccy >= 4))

    cur.execute("SELECT MAX(as_of_date) FROM dbo.vw_deposits")
    as_of = cur.fetchone()[0]
    checks.append(("snapshot date", str(as_of), as_of is not None))

    cur.execute("SELECT COUNT(*) FROM dbo.vw_maturity_ladder")
    ladder = cur.fetchone()[0]
    checks.append(("maturity ladder rows", f"{ladder:,}", ladder > 0))

    cur.execute("SELECT COUNT(*) FROM dbo.vw_customer_exposure")
    exposure = cur.fetchone()[0]
    checks.append(("exposure rows", f"{exposure:,}", exposure > 0))

    # Only CDs should carry a maturity date. If this is non-zero the seed or the
    # view is wrong, and maturity questions will quietly return nonsense.
    cur.execute("SELECT COUNT(*) FROM dbo.vw_deposits WHERE product_code <> 'CD' AND matures_on IS NOT NULL")
    stray = cur.fetchone()[0]
    checks.append(("non-CD with maturity", str(stray), stray == 0))

    # Group relationships are what make roll-up questions interesting.
    cur.execute("SELECT COUNT(*) FROM dbo.vw_deposits WHERE parent_customer_id IS NOT NULL")
    grouped = cur.fetchone()[0]
    checks.append(("accounts in a group", f"{grouped:,}", grouped > 0))

    width = max(len(name) for name, _, _ in checks)
    ok = True
    for name, value, passed in checks:
        mark = "PASS" if passed else "FAIL"
        if not passed:
            ok = False
        print(f"  [{mark}] {name:<{width}}  {value}")

    print()
    print("Balance by branch - what a caller will see:")
    cur.execute(
        "SELECT TOP 20 branch_name, SUM(balance) AS total, COUNT(*) AS n "
        "FROM dbo.vw_deposits GROUP BY branch_name ORDER BY total DESC"
    )
    for branch, total, n in cur.fetchall():
        print(f"    {branch:<32} {float(total):>18,.2f}  ({n:,} accounts)")

    cur.close()
    conn.close()

    print()
    if ok:
        print("Everything checks out. Start the server with:")
        print("    dab start -c dab/dab-config.json")
        return 0

    print("Something is off. Re-run: python scripts/setup.py")
    return 1


if __name__ == "__main__":
    sys.exit(main())
