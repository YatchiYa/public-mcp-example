"""Create demo.db (SQLite) with a small e-commerce dataset:  python -m servers.database.seed"""

from __future__ import annotations

import os
import random
from datetime import date, timedelta

from sqlalchemy import create_engine, text

URL = os.environ.get("DATABASE_URL", "sqlite:///./demo.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, country TEXT NOT NULL, signup_date DATE NOT NULL);
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, price_eur REAL NOT NULL);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
  product_id INTEGER NOT NULL REFERENCES products(id), quantity INTEGER NOT NULL,
  order_date DATE NOT NULL, status TEXT NOT NULL);
"""

CUSTOMERS = [("Alice Martin", "FR"), ("Bob Chen", "SG"), ("Carla Rossi", "IT"), ("Dan Okafor", "NG"),
             ("Emma Schulz", "DE"), ("Farid Haddad", "MA"), ("Grace Kim", "KR"), ("Hugo Silva", "BR")]
PRODUCTS = [("Trail backpack 40L", "outdoor", 89.0), ("Travel adapter", "electronics", 19.9),
            ("Noise-cancelling headphones", "electronics", 249.0), ("Packing cubes x6", "luggage", 24.5),
            ("Merino t-shirt", "apparel", 45.0), ("Carry-on suitcase", "luggage", 159.0)]


def main() -> None:
    rng = random.Random(42)
    engine = create_engine(URL)
    with engine.begin() as conn:
        for stmt in filter(str.strip, SCHEMA.split(";")):
            conn.execute(text(stmt))
        if conn.execute(text("SELECT COUNT(*) FROM customers")).scalar():
            print("demo.db already seeded"); return
        for i, (n, c) in enumerate(CUSTOMERS, 1):
            conn.execute(text("INSERT INTO customers VALUES (:i,:n,:c,:d)"),
                         {"i": i, "n": n, "c": c, "d": date(2025, 1, 1) + timedelta(days=rng.randint(0, 300))})
        for i, (n, cat, p) in enumerate(PRODUCTS, 1):
            conn.execute(text("INSERT INTO products VALUES (:i,:n,:c,:p)"), {"i": i, "n": n, "c": cat, "p": p})
        for i in range(1, 61):
            conn.execute(text("INSERT INTO orders VALUES (:i,:c,:p,:q,:d,:s)"), {
                "i": i, "c": rng.randint(1, len(CUSTOMERS)), "p": rng.randint(1, len(PRODUCTS)),
                "q": rng.randint(1, 4), "d": date(2026, 1, 1) + timedelta(days=rng.randint(0, 240)),
                "s": rng.choice(["paid", "paid", "paid", "shipped", "refunded"])})
    print(f"Seeded {URL}: {len(CUSTOMERS)} customers, {len(PRODUCTS)} products, 60 orders")


if __name__ == "__main__":
    main()
