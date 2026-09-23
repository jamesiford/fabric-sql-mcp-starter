"""Generate the synthetic commercial deposit book.

Run this AFTER sql/01-schema.sql. It writes customers and deposit accounts
directly into the database; the views and security policy come afterwards.

    python scripts/seed.py                    # 2,000 customers, ~8,000 accounts
    python scripts/seed.py --accounts 50000   # a larger book
    python scripts/seed.py --seed 99          # a different but reproducible book

Why a script rather than a .sql file of INSERTs: the row count needs to be a
dial. A laptop demo wants 8,000 accounts; a capacity-sizing test wants a
million. Generating in Python keeps one source of truth for the distributions.

DETERMINISM: everything derives from --seed (default 42). The same seed always
produces the same book, so screenshots, documentation and test assertions stay
valid across re-runs. Do not replace random.Random(seed) with the module-level
random functions - that reintroduces global state and breaks reproducibility.

THE DATA IS FAKE. Names are assembled from word lists. Any resemblance to a
real institution's customers is coincidental.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from db import connect  # noqa: E402

# ── shape of the generated book ──────────────────────────────────────────────

BRANCHES = [
    # code, weight - Los Angeles Main and Monterey Park carry more of the book,
    # which gives branch aggregations something to actually rank.
    ("LA-001", 14), ("LA-002", 13), ("LA-003", 10), ("LA-004", 11),
    ("SF-001", 12), ("SF-002", 12), ("MOD-001", 12), ("HK-001", 8), ("CN-001", 8),
]

# product_code, weight, rate range, term in months (None = no maturity)
PRODUCTS = [
    ("DDA", 30, (0.10, 0.75), None),
    ("NOW", 20, (0.50, 1.60), None),
    ("MMA", 20, (0.80, 2.40), None),
    ("CD",  19, (3.20, 5.10), (3, 60)),
    ("FX",  11, (0.05, 2.20), None),
]

# Currency by branch country. Shanghai and Hong Kong Central deliberately hold
# no USD, so "USD only, by branch" returns 7 rows not 9 - a small correctness
# trap that catches a model inventing rows.
CURRENCY_BY_COUNTRY = {
    "US": [("USD", 88), ("EUR", 5), ("JPY", 3), ("CNY", 2), ("HKD", 2)],
    "HK": [("HKD", 62), ("CNY", 24), ("JPY", 8), ("EUR", 6)],
    "CN": [("CNY", 74), ("HKD", 14), ("JPY", 7), ("EUR", 5)],
}
BRANCH_COUNTRY = {
    "LA-001": "US", "LA-002": "US", "LA-003": "US", "LA-004": "US",
    "SF-001": "US", "SF-002": "US", "MOD-001": "US", "HK-001": "HK", "CN-001": "CN",
}

RELATIONSHIP_MANAGERS = [
    "B. Nakamura", "I. Petrosyan", "C. Delgado", "M. Okonkwo", "S. Lindqvist",
    "R. Chaudhary", "T. Abadi", "L. Fontaine", "D. Vasquez", "K. Yamamoto",
]

INDUSTRIES = [
    "Electronics Components", "Food Distribution", "Commercial Real Estate",
    "Textiles and Apparel", "Logistics and Freight", "Medical Devices",
    "Specialty Chemicals", "Industrial Machinery", "Packaging Materials",
    "Construction Materials",
]

# Company names are assembled from these, which keeps them obviously synthetic
# while still looking plausible in a demo.
NAME_FIRST = [
    "Sterling", "Harbor", "Meridian", "Pinnacle", "Cascade", "Summit", "Anchor",
    "Vertex", "Keystone", "Granite", "Beacon", "Cardinal", "Copper", "Ironwood",
    "Lakeshore", "Northgate", "Redwood", "Silverpeak", "Tidewater", "Westfield",
]
NAME_MID = [
    "Supply", "Industrial", "Trading", "Logistics", "Components", "Materials",
    "Distribution", "Manufacturing", "Holdings", "Partners", "Enterprises",
    "Resources", "Systems", "Solutions", "Group", "Works",
]
NAME_SUFFIX = ["LLC", "Inc.", "Corp.", "LP", "Co.", "Holdings LLC", "Group Inc."]


def weighted(rng: random.Random, pairs: list[tuple]) -> str:
    """Pick the first element of a (value, weight) pair list."""
    values = [p[0] for p in pairs]
    weights = [p[1] for p in pairs]
    return rng.choices(values, weights=weights, k=1)[0]


def make_customers(rng: random.Random, count: int) -> list[tuple]:
    """Build the customer dimension, including group parent relationships."""
    customers: list[tuple] = []
    used_names: set[str] = set()

    for i in range(count):
        cid = f"C{100000 + i}"
        for _ in range(40):  # retry loop for a unique name
            name = (
                f"{rng.choice(NAME_FIRST)} {rng.choice(NAME_MID)} "
                f"{rng.choice(NAME_SUFFIX)}"
            )
            if name not in used_names:
                used_names.add(name)
                break
        else:
            name = f"{name} ({i})"  # guaranteed unique fallback

        customers.append((
            cid,
            name,
            None,  # parent assigned below
            "commercial",
            rng.choice(INDUSTRIES),
            rng.choice(RELATIONSHIP_MANAGERS),
        ))

    # About 18% of customers roll up into a group parent. This is what makes
    # "total exposure for the whole group" a meaningful question, and it is why
    # vw_customer_exposure exposes parent_customer_id.
    n_children = int(count * 0.18)
    if count > 10 and n_children:
        indices = list(range(count))
        rng.shuffle(indices)
        children = indices[:n_children]
        parents = indices[n_children: n_children * 2]
        for child, parent in zip(children, parents):
            if child != parent:
                c = list(customers[child])
                c[2] = customers[parent][0]
                # A subsidiary inherits its parent's relationship manager -
                # otherwise group roll-ups split across RMs and the RLS demo
                # produces confusing numbers.
                c[5] = customers[parent][5]
                customers[child] = tuple(c)

    return customers


def make_accounts(
    rng: random.Random, customers: list[tuple], target: int, as_of: date
) -> list[tuple]:
    """Build the deposit_account fact table."""
    accounts: list[tuple] = []
    n = 0
    # Accounts per customer is skewed: most hold a few, a handful hold many.
    while len(accounts) < target:
        cust = customers[rng.randrange(len(customers))]
        for _ in range(rng.choices([1, 2, 3, 4, 8], weights=[42, 28, 16, 9, 5], k=1)[0]):
            if len(accounts) >= target:
                break
            branch = weighted(rng, BRANCHES)
            country = BRANCH_COUNTRY[branch]
            product = weighted(rng, [(p[0], p[1]) for p in PRODUCTS])
            spec = next(p for p in PRODUCTS if p[0] == product)

            # FX accounts are never USD - that is what makes them FX.
            currency = weighted(rng, CURRENCY_BY_COUNTRY[country])
            if product == "FX" and currency == "USD":
                currency = weighted(rng, [c for c in CURRENCY_BY_COUNTRY[country] if c[0] != "USD"])

            # Log-normal-ish balances: many small accounts, a few very large.
            magnitude = rng.choices([4, 5, 6, 7], weights=[30, 42, 22, 6], k=1)[0]
            balance = round(rng.uniform(1, 10) * (10 ** magnitude), 2)

            lo, hi = spec[2]
            rate = round(rng.uniform(lo, hi), 3)

            opened = as_of - timedelta(days=rng.randrange(30, 2200))
            matures = None
            if spec[3] is not None:
                months = rng.randrange(spec[3][0], spec[3][1] + 1)
                matures = opened + timedelta(days=months * 30)
                # Roll expired CDs forward so the maturity ladder has a future.
                while matures < as_of:
                    matures += timedelta(days=months * 30)

            n += 1
            accounts.append((
                f"A{2000000 + n}", cust[0], branch, product, currency,
                balance, rate, opened, matures, as_of,
            ))
    return accounts


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed the synthetic deposit book.")
    ap.add_argument("--customers", type=int, default=2000)
    ap.add_argument("--accounts", type=int, default=8000,
                    help="approximate target; the generator overshoots slightly")
    ap.add_argument("--seed", type=int, default=42,
                    help="same seed produces the same book, every time")
    ap.add_argument("--as-of", default=None,
                    help="snapshot date, YYYY-MM-DD. Defaults to last Sunday.")
    args = ap.parse_args()

    rng = random.Random(args.seed)

    if args.as_of:
        as_of = date.fromisoformat(args.as_of)
    else:
        today = date.today()
        as_of = today - timedelta(days=today.weekday() + 1)

    print(f"Generating {args.customers:,} customers ...")
    customers = make_customers(rng, args.customers)

    print(f"Generating ~{args.accounts:,} accounts as of {as_of} ...")
    accounts = make_accounts(rng, customers, args.accounts, as_of)

    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()
    try:
        print("Clearing existing rows ...")
        cur.execute("DELETE FROM dbo.deposit_account")
        cur.execute("DELETE FROM dbo.customer")
        conn.commit()

        # fast_executemany turns thousands of round trips into a handful.
        cur.fast_executemany = True

        print(f"Inserting {len(customers):,} customers ...")
        cur.executemany(
            "INSERT INTO dbo.customer (customer_id, customer_name, parent_customer_id, "
            "segment, industry, relationship_manager) VALUES (?, ?, ?, ?, ?, ?)",
            customers,
        )
        conn.commit()

        print(f"Inserting {len(accounts):,} accounts ...")
        for i in range(0, len(accounts), 5000):
            chunk = accounts[i: i + 5000]
            cur.executemany(
                "INSERT INTO dbo.deposit_account (account_number, customer_id, branch_code, "
                "product_code, currency, balance, rate, opened_on, matures_on, as_of_date) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                chunk,
            )
            conn.commit()
            print(f"  {min(i + 5000, len(accounts)):,} / {len(accounts):,}")

        cur.execute("SELECT COUNT(*) FROM dbo.deposit_account")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT branch_code) FROM dbo.deposit_account")
        branches = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM dbo.deposit_account "
            "WHERE product_code = 'CD' AND matures_on >= ?", (as_of,)
        )
        maturing = cur.fetchone()[0]
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    print()
    print(f"  accounts        {total:,}")
    print(f"  customers       {len(customers):,}")
    print(f"  branches        {branches}")
    print(f"  CDs not matured {maturing:,}")
    print(f"  snapshot        {as_of}")
    print()
    print("Next: run sql/02-views.sql, then sql/03-row-level-security.sql")


if __name__ == "__main__":
    main()
