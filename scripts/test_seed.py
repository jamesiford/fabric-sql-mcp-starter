"""Offline tests for the seed generator.

These exercise the pure-Python generation logic with no database involved, so
they run anywhere and catch the class of bug that would otherwise only surface
after a slow insert into Fabric.

    python scripts/test_seed.py

What is being protected here:
  - determinism, because the docs quote specific numbers
  - the invariants the published views and the MCP tool descriptions assert
  - the deliberate data traps that make the demo worth doing
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import random  # noqa: E402

# Import without triggering seed.py's database imports.
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "seedmod", os.path.join(ROOT, "scripts", "seed.py")
)
seedmod = importlib.util.module_from_spec(spec)
# seed.py imports db at module level for main(); stub it so the import succeeds
# without an ODBC driver or a .env present.
sys.modules.setdefault("db", type(sys)("db"))
sys.modules["db"].connect = lambda: None  # type: ignore[attr-defined]
spec.loader.exec_module(seedmod)  # type: ignore[union-attr]


FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f"  {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def main() -> int:
    as_of = date(2026, 9, 20)

    print("Generating with seed=42 ...")
    rng = random.Random(42)
    customers = seedmod.make_customers(rng, 2000)
    accounts = seedmod.make_accounts(rng, customers, 8000, as_of)
    print(f"  {len(customers):,} customers, {len(accounts):,} accounts")
    print()

    # ── determinism ──────────────────────────────────────────────────────────
    print("Determinism")
    rng2 = random.Random(42)
    customers2 = seedmod.make_customers(rng2, 2000)
    accounts2 = seedmod.make_accounts(rng2, customers2, 8000, as_of)
    check("same seed reproduces customers exactly", customers == customers2)
    check("same seed reproduces accounts exactly", accounts == accounts2)

    rng3 = random.Random(43)
    customers3 = seedmod.make_customers(rng3, 2000)
    check("a different seed produces a different book", customers != customers3)
    print()

    # ── primary keys ─────────────────────────────────────────────────────────
    print("Keys and referential integrity")
    cust_ids = [c[0] for c in customers]
    acct_ids = [a[0] for a in accounts]
    check("customer_id unique", len(set(cust_ids)) == len(cust_ids),
          f"{len(set(cust_ids)):,} distinct")
    check("account_number unique", len(set(acct_ids)) == len(acct_ids),
          f"{len(set(acct_ids)):,} distinct")
    check("customer_name unique", len({c[1] for c in customers}) == len(customers))

    known = set(cust_ids)
    check("every account references a real customer",
          all(a[1] in known for a in accounts))
    parents = [c[2] for c in customers if c[2] is not None]
    check("every parent_customer_id references a real customer",
          all(p in known for p in parents), f"{len(parents):,} grouped")
    check("no customer is its own parent",
          all(c[2] != c[0] for c in customers))
    print()

    # ── invariants the views and tool descriptions depend on ─────────────────
    print("Invariants asserted by the published surface")
    non_cd_with_maturity = [a for a in accounts if a[3] != "CD" and a[8] is not None]
    check("only CD carries a maturity date", not non_cd_with_maturity,
          f"{len(non_cd_with_maturity)} violations")

    cd_no_maturity = [a for a in accounts if a[3] == "CD" and a[8] is None]
    check("every CD has a maturity date", not cd_no_maturity,
          f"{len(cd_no_maturity)} violations")

    expired = [a for a in accounts if a[8] is not None and a[8] < as_of]
    check("no CD matured before the snapshot", not expired,
          f"{len(expired)} violations")

    opened_after = [a for a in accounts if a[7] > as_of]
    check("no account opened after the snapshot", not opened_after,
          f"{len(opened_after)} violations")

    fx_usd = [a for a in accounts if a[3] == "FX" and a[4] == "USD"]
    check("no FX account is denominated in USD", not fx_usd,
          f"{len(fx_usd)} violations")

    check("all balances positive", all(a[5] > 0 for a in accounts))
    check("all rates positive", all(a[6] > 0 for a in accounts))
    check("one snapshot date", len({a[9] for a in accounts}) == 1)
    print()

    # ── the deliberate traps ─────────────────────────────────────────────────
    print("Deliberate data traps (these make the demo worth doing)")
    branches = {a[2] for a in accounts}
    check("all 9 branches present", len(branches) == 9, f"{len(branches)} present")

    usd_branches = {a[2] for a in accounts if a[4] == "USD"}
    check("Shanghai holds no USD", "CN-001" not in usd_branches)
    check("Hong Kong Central holds no USD", "HK-001" not in usd_branches)
    check("USD-by-branch returns 7 rows, not 9", len(usd_branches) == 7,
          f"{len(usd_branches)} branches hold USD")

    check("all 5 products present", len({a[3] for a in accounts}) == 5)
    check("all 5 currencies present", len({a[4] for a in accounts}) == 5)
    check("every customer is segment 'commercial'",
          {c[3] for c in customers} == {"commercial"})
    check("10 relationship managers", len({c[5] for c in customers}) == 10)
    check("10 industries", len({c[4] for c in customers}) == 10)
    print()

    # ── group relationships, which drive the roll-up questions ───────────────
    print("Group relationships")
    grouped = [c for c in customers if c[2] is not None]
    pct = 100 * len(grouped) / len(customers)
    check("some customers roll up to a parent", 10 <= pct <= 25, f"{pct:.1f}%")

    by_id = {c[0]: c for c in customers}
    mismatched = [c for c in grouped if by_id[c[2]][5] != c[5]]
    check("a subsidiary inherits its parent's relationship manager",
          not mismatched, f"{len(mismatched)} mismatches")
    print()

    # ── distribution sanity, so aggregations are interesting ─────────────────
    print("Distributions")
    per_branch = Counter(a[2] for a in accounts)
    spread = max(per_branch.values()) / min(per_branch.values())
    check("branch volumes vary but none is empty", 1.2 < spread < 4.0,
          f"max/min = {spread:.2f}")

    per_rm = Counter(by_id[a[1]][5] for a in accounts)
    check("every relationship manager holds a book", len(per_rm) == 10,
          f"{min(per_rm.values()):,}-{max(per_rm.values()):,} accounts each")

    balances = sorted(a[5] for a in accounts)
    median = balances[len(balances) // 2]
    check("balances span several orders of magnitude",
          balances[-1] / balances[0] > 1000,
          f"{balances[0]:,.0f} to {balances[-1]:,.0f}, median {median:,.0f}")

    rates_by_product: dict[str, list[float]] = {}
    for a in accounts:
        rates_by_product.setdefault(a[3], []).append(float(a[6]))
    cd_avg = sum(rates_by_product["CD"]) / len(rates_by_product["CD"])
    dda_avg = sum(rates_by_product["DDA"]) / len(rates_by_product["DDA"])
    check("CDs pay materially more than checking", cd_avg > dda_avg * 3,
          f"CD {cd_avg:.2f}% vs DDA {dda_avg:.2f}%")
    print()

    # ── scale ────────────────────────────────────────────────────────────────
    print("Scaling")
    rng4 = random.Random(42)
    small_c = seedmod.make_customers(rng4, 50)
    small_a = seedmod.make_accounts(rng4, small_c, 100, as_of)
    check("a tiny book generates without error", len(small_a) >= 100,
          f"{len(small_a)} accounts from 50 customers")

    rng5 = random.Random(42)
    big_c = seedmod.make_customers(rng5, 5000)
    big_a = seedmod.make_accounts(rng5, big_c, 40000, as_of)
    check("a larger book generates without error", len(big_a) >= 40000,
          f"{len(big_a):,} accounts")
    check("account numbers still unique at scale",
          len({a[0] for a in big_a}) == len(big_a))
    print()

    print("=" * 62)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
