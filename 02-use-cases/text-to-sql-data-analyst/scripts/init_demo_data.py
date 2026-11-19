#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Sample Data Generator

Reads config/tables.yaml and generates sample data in Parquet format.
Customize the generate_* functions for your business domain.

Usage:
    python scripts/init_demo_data.py
    aws s3 cp data/demo/ s3://YOUR-BUCKET/data/ --recursive
"""

import os
import random
import yaml
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# Configuration
OUTPUT_DIR = "data/demo"
NUM_CUSTOMERS = 1000
NUM_PRODUCTS = 200
NUM_SALES = 5000


def load_tables_config():
    """Load table configuration."""
    config_path = Path(__file__).parent.parent / "config" / "tables.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def generate_customers(num_records: int) -> pd.DataFrame:
    """Generate sample customer data. Customize for your business."""
    print(f"Generating {num_records} customers...")

    first_names = [
        "Alice", "Bob", "Carol", "David", "Emma", "Frank", "Grace", "Henry",
        "Iris", "Jack", "Karen", "Leo", "Maria", "Noah", "Olivia", "Paul",
    ]
    last_names = [
        "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia",
        "Miller", "Davis", "Rodriguez", "Martinez", "Wilson", "Anderson",
    ]
    countries = ["United States", "Canada", "United Kingdom", "Germany", "France", "Spain"]
    segments = {"premium": 0.15, "regular": 0.60, "new": 0.25}

    data = []
    start_date = datetime.now() - timedelta(days=730)

    for i in range(1, num_records + 1):
        first = random.choice(first_names)
        last = random.choice(last_names)
        segment = random.choices(
            list(segments.keys()), weights=list(segments.values())
        )[0]
        reg_date = start_date + timedelta(days=random.randint(0, 730))

        data.append({
            "customer_id": i,
            "name": f"{first} {last}",
            "email": f"{first.lower()}.{last.lower()}{i}@example.com",
            "registration_date": reg_date.strftime("%Y-%m-%d"),
            "country": random.choice(countries),
            "segment": segment,
        })

    return pd.DataFrame(data)


def generate_products(num_records: int) -> pd.DataFrame:
    """Generate sample product data. Customize for your business."""
    print(f"Generating {num_records} products...")

    categories = {
        "electronics": {"min": 50, "max": 1500},
        "clothing": {"min": 15, "max": 200},
        "home": {"min": 10, "max": 500},
        "sports": {"min": 20, "max": 800},
        "food": {"min": 2, "max": 100},
    }
    suppliers = [
        "Supplier A", "Supplier B", "Supplier C", "Supplier D", "Supplier E",
    ]

    data = []
    pid = 1
    per_cat = num_records // len(categories)

    for cat, config in categories.items():
        for _ in range(per_cat):
            data.append({
                "product_id": pid,
                "name": f"{cat.title()} Product {pid}",
                "category": cat,
                "price": round(random.uniform(config["min"], config["max"]), 2),
                "stock": random.randint(0, 500),
                "supplier": random.choice(suppliers),
            })
            pid += 1

    return pd.DataFrame(data)


def generate_sales(
    num_records: int, num_customers: int, num_products: int
) -> pd.DataFrame:
    """Generate sample sales data."""
    print(f"Generating {num_records} sales...")

    payment_methods = {"credit_card": 0.60, "transfer": 0.30, "cash": 0.10}
    data = []
    start_date = datetime.now() - timedelta(days=365)

    for i in range(1, num_records + 1):
        sale_date = start_date + timedelta(days=random.randint(0, 365))
        quantity = random.randint(1, 10)
        base_price = random.uniform(20, 500)
        discount = round(random.uniform(0, 0.20), 2)
        total = round(base_price * quantity * (1 - discount), 2)
        method = random.choices(
            list(payment_methods.keys()),
            weights=list(payment_methods.values()),
        )[0]

        data.append({
            "sale_id": i,
            "customer_id": random.randint(1, num_customers),
            "product_id": random.randint(1, num_products),
            "sale_date": sale_date.strftime("%Y-%m-%d"),
            "quantity": quantity,
            "total_amount": total,
            "discount": discount,
            "payment_method": method,
        })

    return pd.DataFrame(data)


def save_parquet(df: pd.DataFrame, table_name: str):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"{table_name}.parquet")
    df.to_parquet(path, engine="pyarrow", index=False)
    print(f"  -> {path} ({len(df)} records)")


def main():
    config = load_tables_config()
    db_name = config.get("database_name", "demo")
    print(f"\nGenerating data for: {db_name}\n")

    customers = generate_customers(NUM_CUSTOMERS)
    products = generate_products(NUM_PRODUCTS)
    sales = generate_sales(NUM_SALES, NUM_CUSTOMERS, NUM_PRODUCTS)

    save_parquet(customers, "customers")
    save_parquet(products, "products")
    save_parquet(sales, "sales")

    bucket = os.environ.get("DEMO_S3_BUCKET", "my-company-text-to-sql-data")
    prefix = config.get("s3_data_prefix", "data")

    print(f"\nData generated in {OUTPUT_DIR}/")
    print(f"\nNext step — upload to S3:")
    print(f"  aws s3 cp {OUTPUT_DIR}/customers.parquet s3://{bucket}/{prefix}/customers/customers.parquet")
    print(f"  aws s3 cp {OUTPUT_DIR}/products.parquet s3://{bucket}/{prefix}/products/products.parquet")
    print(f"  aws s3 cp {OUTPUT_DIR}/sales.parquet s3://{bucket}/{prefix}/sales/sales.parquet\n")


if __name__ == "__main__":
    main()
