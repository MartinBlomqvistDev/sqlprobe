#!/usr/bin/env python
"""Create and populate the demo databases with synthetic data.

Two SQLite files are written, and the split between them is the point:

    demo.db       The analytics database the agent queries. Pseudonymous
                  customer references only, no names and no e-mail addresses.
    directory.db  The identity directory. Names and contact addresses, keyed
                  by the same reference. Never reachable from the agent, only
                  from the resolve endpoint.

Everything here is generated. The people, the products and the orders do not
correspond to anything real, addresses use the reserved .invalid domain so they
cannot be delivered to, and the run is deterministic: the same --seed always
produces the same database.

Usage:
    python scripts/seed_demo_db.py
    python scripts/seed_demo_db.py --orders 20000 --seed 7 --force
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

# --- Generation defaults ---------------------------------------------------
# Every figure below is a default of this script and nothing else. Change them
# freely; the tests assert on structure and referential integrity, not on size.
DEFAULT_SEED = 42
DEFAULT_CUSTOMERS = 400
DEFAULT_ORDERS = 5000
DEFAULT_MONTHS = 24
DEFAULT_END_DATE = "2026-06-30"

COUNTRIES = ["SE", "DE", "NL", "DK", "FI", "PL", "FR"]
CITIES = {
    "SE": ["Kristianstad", "Umeå", "Örebro"],
    "DE": ["Bremen", "Leipzig", "Freiburg"],
    "NL": ["Utrecht", "Groningen", "Breda"],
    "DK": ["Aarhus", "Odense", "Aalborg"],
    "FI": ["Tampere", "Turku", "Oulu"],
    "PL": ["Gdansk", "Wroclaw", "Poznan"],
    "FR": ["Nantes", "Rennes", "Grenoble"],
}
CHANNELS = ["web", "mobile", "marketplace"]
CHANNEL_WEIGHTS = [0.55, 0.32, 0.13]
STATUSES = ["completed", "cancelled", "returned"]
STATUS_WEIGHTS = [0.90, 0.06, 0.04]
TIERS = ["standard", "plus"]
TIER_WEIGHTS = [0.78, 0.22]

# Invented name pools. Combined pseudorandomly, so the directory reads like a
# customer list without any entry belonging to a real person.
FIRST_NAMES = [
    "Amara", "Beatriz", "Casimir", "Delphine", "Eamon", "Fabiola", "Gideon",
    "Hanne", "Ilaria", "Jarek", "Kiona", "Lorcan", "Maeve", "Nabil", "Oksana",
    "Perrine", "Quentin", "Rosalind", "Soren", "Thandiwe", "Ulla", "Vesna",
    "Wilhelmina", "Xavier", "Yannick", "Zofia",
]
LAST_NAMES = [
    "Adeyemi", "Backlund", "Corvino", "Delacroix", "Eriksdotter", "Fontaine",
    "Grimaldi", "Halvorsen", "Ivanova", "Jankowski", "Kowalczyk", "Lindqvist",
    "Moretti", "Nakamura", "Okafor", "Pettersen", "Quintero", "Rasmussen",
    "Sorensen", "Tikkanen", "Ustinov", "Valdes", "Waldemar", "Ximenes",
    "Yilmaz", "Zetterlund",
]

# (name, category, unit_price, cost_price)
PRODUCTS: list[tuple[str, str, float, float]] = [
    ("Copper Saucepan 2L", "kitchen", 79.00, 41.50),
    ("Bamboo Chopping Board", "kitchen", 24.50, 9.80),
    ("Ceramic Mixing Bowl", "kitchen", 32.00, 13.25),
    ("Cast Iron Skillet 26cm", "kitchen", 64.00, 30.00),
    ("Hand Trowel Set", "garden", 28.00, 11.40),
    ("Watering Can 5L", "garden", 19.50, 7.60),
    ("Pruning Shears", "garden", 36.00, 15.20),
    ("Compost Bin 120L", "garden", 88.00, 47.00),
    ("Desk Lamp Warm White", "office", 54.00, 24.30),
    ("Cork Notice Board", "office", 41.00, 17.90),
    ("Standing Desk Riser", "office", 149.00, 78.50),
    ("Insulated Flask 750ml", "outdoor", 34.00, 13.10),
    ("Folding Camp Chair", "outdoor", 59.00, 27.40),
    ("Trail Backpack 28L", "outdoor", 112.00, 52.80),
]

SCHEMA = """
CREATE TABLE customers (
    customer_id      INTEGER PRIMARY KEY,
    customer_ref     TEXT    NOT NULL UNIQUE,
    country          TEXT    NOT NULL,
    city             TEXT    NOT NULL,
    tier             TEXT    NOT NULL,
    signup_date      TEXT    NOT NULL,
    marketing_opt_in INTEGER NOT NULL
);

CREATE TABLE products (
    product_id   INTEGER PRIMARY KEY,
    sku          TEXT    NOT NULL UNIQUE,
    product_name TEXT    NOT NULL,
    category     TEXT    NOT NULL,
    unit_price   REAL    NOT NULL,
    cost_price   REAL    NOT NULL,
    launched_on  TEXT    NOT NULL
);

CREATE TABLE orders (
    order_id         INTEGER PRIMARY KEY,
    customer_id      INTEGER NOT NULL REFERENCES customers(customer_id),
    order_date       TEXT    NOT NULL,
    status           TEXT    NOT NULL,
    channel          TEXT    NOT NULL,
    shipping_country TEXT    NOT NULL,
    discount_pct     REAL    NOT NULL,
    order_total      REAL    NOT NULL
);

CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL REFERENCES orders(order_id),
    product_id    INTEGER NOT NULL REFERENCES products(product_id),
    quantity      INTEGER NOT NULL,
    unit_price    REAL    NOT NULL,
    line_total    REAL    NOT NULL
);

CREATE TABLE daily_sales (
    sales_date  TEXT    NOT NULL,
    channel     TEXT    NOT NULL,
    order_count INTEGER NOT NULL,
    units_sold  INTEGER NOT NULL,
    net_revenue REAL    NOT NULL,
    PRIMARY KEY (sales_date, channel)
);

CREATE INDEX idx_orders_date   ON orders(order_date);
CREATE INDEX idx_orders_cust   ON orders(customer_id);
CREATE INDEX idx_items_order   ON order_items(order_id);
CREATE INDEX idx_items_product ON order_items(product_id);
"""

DIRECTORY_SCHEMA = """
CREATE TABLE directory_entries (
    customer_ref  TEXT PRIMARY KEY,
    full_name     TEXT NOT NULL,
    contact_email TEXT NOT NULL
);
"""


@dataclass
class Options:
    """Parsed command line options."""

    db_path: Path
    directory_path: Path
    seed: int
    customers: int
    orders: int
    months: int
    end_date: date
    force: bool


def parse_args(argv: list[str] | None = None) -> Options:
    """Parse command line arguments into an Options object."""
    parser = argparse.ArgumentParser(
        description="Seed the demo analytics and directory databases."
    )
    parser.add_argument(
        "--db-path", type=Path, default=Path("data/demo.db"),
        help="Where to write the analytics database (default: data/demo.db).",
    )
    parser.add_argument(
        "--directory-path", type=Path, default=Path("data/directory.db"),
        help="Where to write the identity directory (default: data/directory.db).",
    )
    parser.add_argument(
        "--seed", type=int, default=DEFAULT_SEED,
        help=f"Random seed, for reproducible data (default: {DEFAULT_SEED}).",
    )
    parser.add_argument(
        "--customers", type=int, default=DEFAULT_CUSTOMERS,
        help=f"Number of customers to generate (default: {DEFAULT_CUSTOMERS}).",
    )
    parser.add_argument(
        "--orders", type=int, default=DEFAULT_ORDERS,
        help=f"Number of orders to generate (default: {DEFAULT_ORDERS}).",
    )
    parser.add_argument(
        "--months", type=int, default=DEFAULT_MONTHS,
        help=f"How many months of history to cover (default: {DEFAULT_MONTHS}).",
    )
    parser.add_argument(
        "--end-date", type=date.fromisoformat, default=date.fromisoformat(DEFAULT_END_DATE),
        help=f"Last day of the generated history (default: {DEFAULT_END_DATE}).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite the database files if they already exist.",
    )
    args = parser.parse_args(argv)
    return Options(
        db_path=args.db_path,
        directory_path=args.directory_path,
        seed=args.seed,
        customers=args.customers,
        orders=args.orders,
        months=args.months,
        end_date=args.end_date,
        force=args.force,
    )


def seed(options: Options) -> dict[str, int]:
    """Generate both databases.

    Args:
        options: Parsed command line options.

    Returns:
        A mapping of table name to the number of rows written.

    Raises:
        FileExistsError: If a target file exists and --force was not given.
    """
    for path in (options.db_path, options.directory_path):
        if path.exists() and not options.force:
            raise FileExistsError(f"{path} already exists. Pass --force to replace it.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)

    rng = random.Random(options.seed)
    start_date = options.end_date - timedelta(days=int(options.months * 30.44))

    customers, directory = _make_customers(rng, options.customers, start_date, options.end_date)
    products = _make_products(start_date)
    orders, items = _make_orders(
        rng, options.orders, customers, products, start_date, options.end_date
    )
    daily = _aggregate_daily(orders, items)

    with sqlite3.connect(options.db_path) as connection:
        connection.executescript(SCHEMA)
        connection.executemany(
            "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?)", customers
        )
        connection.executemany(
            "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", products
        )
        connection.executemany(
            "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)", orders
        )
        connection.executemany(
            "INSERT INTO order_items VALUES (?, ?, ?, ?, ?, ?)", items
        )
        connection.executemany(
            "INSERT INTO daily_sales VALUES (?, ?, ?, ?, ?)", daily
        )
        connection.commit()

    with sqlite3.connect(options.directory_path) as connection:
        connection.executescript(DIRECTORY_SCHEMA)
        connection.executemany(
            "INSERT INTO directory_entries VALUES (?, ?, ?)", directory
        )
        connection.commit()

    return {
        "customers": len(customers),
        "products": len(products),
        "orders": len(orders),
        "order_items": len(items),
        "daily_sales": len(daily),
        "directory_entries": len(directory),
    }


def _make_customers(
    rng: random.Random, count: int, start: date, end: date
) -> tuple[list[tuple], list[tuple]]:
    """Build the customer rows and their matching directory entries."""
    customers: list[tuple] = []
    directory: list[tuple] = []
    span = (end - start).days

    for index in range(1, count + 1):
        reference = f"cus_{index:05d}"
        country = rng.choice(COUNTRIES)
        city = rng.choice(CITIES[country])
        tier = rng.choices(TIERS, weights=TIER_WEIGHTS, k=1)[0]
        # Signups are spread over the window plus a year of pre-history, so the
        # oldest customers predate the order data.
        signup = start + timedelta(days=rng.randint(-365, max(span - 1, 1)))
        customers.append(
            (
                index,
                reference,
                country,
                city,
                tier,
                signup.isoformat(),
                1 if rng.random() < 0.61 else 0,
            )
        )

        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        directory.append(
            (
                reference,
                f"{first} {last}",
                f"{first.lower()}.{last.lower()}.{index}@example.invalid",
            )
        )

    return customers, directory


def _make_products(start: date) -> list[tuple]:
    """Build the product catalogue."""
    return [
        (
            index,
            f"SKU-{index:04d}",
            name,
            category,
            unit_price,
            cost_price,
            (start - timedelta(days=90 + index * 11)).isoformat(),
        )
        for index, (name, category, unit_price, cost_price) in enumerate(PRODUCTS, 1)
    ]


def _make_orders(
    rng: random.Random,
    count: int,
    customers: list[tuple],
    products: list[tuple],
    start: date,
    end: date,
) -> tuple[list[tuple], list[tuple]]:
    """Build orders and their line items.

    Order dates carry a mild upward trend and a weekend dip, so the demo data
    has something for a trend question to find without being a straight line.
    """
    orders: list[tuple] = []
    items: list[tuple] = []
    span = max((end - start).days, 1)
    item_id = 0

    for order_id in range(1, count + 1):
        # Bias towards recent dates: growth over the window.
        position = min(rng.random() ** 0.78, 0.999)
        order_date = start + timedelta(days=int(position * span))
        if order_date.weekday() >= 5 and rng.random() < 0.35:
            order_date -= timedelta(days=rng.randint(1, 2))
        if order_date < start:
            order_date = start

        customer = customers[rng.randrange(len(customers))]
        customer_id, _, country, _, tier, signup, _ = customer
        if order_date.isoformat() < signup:
            order_date = date.fromisoformat(signup)

        channel = rng.choices(CHANNELS, weights=CHANNEL_WEIGHTS, k=1)[0]
        status = rng.choices(STATUSES, weights=STATUS_WEIGHTS, k=1)[0]
        shipping_country = country if rng.random() < 0.93 else rng.choice(COUNTRIES)
        discount_pct = rng.choice([0.0, 0.0, 0.0, 5.0, 10.0, 15.0])
        if tier == "plus" and discount_pct == 0.0 and rng.random() < 0.30:
            discount_pct = 5.0

        gross = 0.0
        for product in rng.sample(products, k=rng.choices([1, 2, 3, 4], weights=[0.44, 0.31, 0.17, 0.08], k=1)[0]):
            product_id, _, _, _, list_price, _, _ = product
            quantity = rng.choices([1, 2, 3], weights=[0.72, 0.21, 0.07], k=1)[0]
            # Historical price drift, so order_items.unit_price is genuinely
            # not the same as the current list price.
            unit_price = round(list_price * rng.uniform(0.92, 1.0), 2)
            line_total = round(unit_price * quantity, 2)
            gross += line_total
            item_id += 1
            items.append(
                (item_id, order_id, product_id, quantity, unit_price, line_total)
            )

        order_total = round(gross * (1 - discount_pct / 100), 2)
        orders.append(
            (
                order_id,
                customer_id,
                order_date.isoformat(),
                status,
                channel,
                shipping_country,
                discount_pct,
                order_total,
            )
        )

    orders.sort(key=lambda row: row[2])
    return orders, items


def _aggregate_daily(orders: list[tuple], items: list[tuple]) -> list[tuple]:
    """Derive daily_sales from completed orders, so the two always agree."""
    units_by_order: dict[int, int] = {}
    for _, order_id, _, quantity, _, _ in items:
        units_by_order[order_id] = units_by_order.get(order_id, 0) + quantity

    buckets: dict[tuple[str, str], list[float]] = {}
    for order_id, _, order_date, status, channel, _, _, order_total in orders:
        if status != "completed":
            continue
        bucket = buckets.setdefault((order_date, channel), [0.0, 0.0, 0.0])
        bucket[0] += 1
        bucket[1] += units_by_order.get(order_id, 0)
        bucket[2] += order_total

    return [
        (sales_date, channel, int(count), int(units), round(revenue, 2))
        for (sales_date, channel), (count, units, revenue) in sorted(buckets.items())
    ]


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    options = parse_args(argv)
    try:
        counts = seed(options)
    except FileExistsError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {options.db_path} and {options.directory_path}")
    for table, rows in counts.items():
        print(f"  {table:<18} {rows:>7,}")
    print(f"\nSeed {options.seed}: rerunning with the same seed rebuilds this exactly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
