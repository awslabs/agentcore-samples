"""Generate the sample data. Every value is made up; the same seed always gives the same files.

- Shared market data (.cache/market/): synthetic New York City rental listings, one Parquet file per
  month, plus a neighborhood lookup table.
- One private portfolio per agency in config.AGENCIES (.cache/users/<agency>/): the agency's own
  listings, in its boroughs and at its own pricing, with private columns (owner, commission) that
  the market data doesn't have.

    python infra/data_gen.py            # deploy.py runs this when files are missing

The numbers follow simple, documented patterns so questions have interesting answers: rent depends on
neighborhood, size and amenities, peaks in summer and rises 2-9% a year depending on the neighborhood; listings priced above their
neighborhood's going rate stay on the market longer. Faker supplies the names and street addresses.
"""

import argparse
import calendar
import datetime as dt
import time
from pathlib import Path

import duckdb
import numpy as np
from config import AGENCIES, DATA_GEN, ROOT
from faker import Faker

OUT = ROOT / ".cache" / "market"
USERS_OUT = ROOT / ".cache" / "users"

# (neighborhood, borough, 1-bedroom base rent in USD at the start of the data, share of listings,
#  size factor, doorman probability, share of pre-war buildings). Names are real places; every
#  number is invented for this sample.
NEIGHBORHOODS = [
    ("Upper East Side", "Manhattan", 3900, 4.0, 1.00, 0.45, 0.55),
    ("Upper West Side", "Manhattan", 4000, 3.8, 1.00, 0.40, 0.65),
    ("Midtown", "Manhattan", 4300, 3.0, 0.90, 0.70, 0.20),
    ("Chelsea", "Manhattan", 4700, 2.2, 0.95, 0.45, 0.40),
    ("Greenwich Village", "Manhattan", 4900, 1.8, 0.85, 0.30, 0.70),
    ("East Village", "Manhattan", 4200, 2.5, 0.80, 0.10, 0.80),
    ("Lower East Side", "Manhattan", 4000, 2.0, 0.80, 0.15, 0.70),
    ("SoHo", "Manhattan", 5600, 0.8, 1.10, 0.35, 0.50),
    ("Tribeca", "Manhattan", 6200, 0.8, 1.25, 0.60, 0.40),
    ("Financial District", "Manhattan", 4400, 2.4, 0.95, 0.80, 0.15),
    ("Harlem", "Manhattan", 2900, 3.0, 1.05, 0.15, 0.70),
    ("Washington Heights", "Manhattan", 2500, 2.6, 1.05, 0.05, 0.85),
    ("Williamsburg", "Brooklyn", 4100, 3.2, 0.95, 0.35, 0.30),
    ("Greenpoint", "Brooklyn", 3600, 1.8, 0.95, 0.15, 0.50),
    ("DUMBO", "Brooklyn", 5000, 0.7, 1.05, 0.65, 0.20),
    ("Brooklyn Heights", "Brooklyn", 4300, 1.2, 1.00, 0.30, 0.75),
    ("Park Slope", "Brooklyn", 3700, 2.4, 1.05, 0.10, 0.70),
    ("Bushwick", "Brooklyn", 3000, 3.0, 1.00, 0.05, 0.40),
    ("Bedford-Stuyvesant", "Brooklyn", 2900, 3.2, 1.05, 0.05, 0.65),
    ("Crown Heights", "Brooklyn", 2800, 2.8, 1.05, 0.05, 0.70),
    ("Prospect Heights", "Brooklyn", 3500, 1.4, 1.00, 0.20, 0.55),
    ("Downtown Brooklyn", "Brooklyn", 4000, 1.8, 0.95, 0.75, 0.10),
    ("Bay Ridge", "Brooklyn", 2300, 1.6, 1.10, 0.05, 0.60),
    ("Flatbush", "Brooklyn", 2300, 2.2, 1.10, 0.05, 0.70),
    ("Long Island City", "Queens", 3800, 2.4, 0.95, 0.75, 0.10),
    ("Astoria", "Queens", 2700, 3.0, 1.05, 0.10, 0.55),
    ("Sunnyside", "Queens", 2400, 1.4, 1.10, 0.05, 0.60),
    ("Jackson Heights", "Queens", 2300, 1.8, 1.15, 0.05, 0.75),
    ("Forest Hills", "Queens", 2500, 1.6, 1.15, 0.25, 0.50),
    ("Flushing", "Queens", 2200, 2.0, 1.10, 0.15, 0.30),
    ("Ridgewood", "Queens", 2500, 1.4, 1.05, 0.03, 0.70),
    ("Mott Haven", "Bronx", 2400, 1.6, 1.05, 0.30, 0.40),
    ("Riverdale", "Bronx", 2200, 1.2, 1.20, 0.35, 0.40),
    ("Fordham", "Bronx", 1900, 1.4, 1.10, 0.05, 0.75),
    ("Pelham Bay", "Bronx", 1900, 0.9, 1.15, 0.05, 0.50),
    ("St. George", "Staten Island", 2000, 0.8, 1.15, 0.10, 0.50),
    ("Tottenville", "Staten Island", 2000, 0.4, 1.30, 0.02, 0.20),
]
BEDROOM_MIX = {  # probability of 0 (studio) to 4 bedrooms
    "Manhattan": [0.24, 0.38, 0.25, 0.10, 0.03],
    "Brooklyn": [0.12, 0.34, 0.32, 0.17, 0.05],
    "Queens": [0.10, 0.36, 0.32, 0.17, 0.05],
    "Bronx": [0.08, 0.32, 0.34, 0.20, 0.06],
    "Staten Island": [0.04, 0.24, 0.34, 0.28, 0.10],
}
# Yearly rent growth where it differs from the 4.5% default: fast-rising and flat neighborhoods.
GROWTH = {
    "Bushwick": 0.085,
    "Bedford-Stuyvesant": 0.08,
    "Crown Heights": 0.075,
    "Ridgewood": 0.075,
    "Mott Haven": 0.07,
    "Long Island City": 0.065,
    "Harlem": 0.06,
    "Astoria": 0.055,
    "Greenpoint": 0.055,
    "Tribeca": 0.02,
    "SoHo": 0.025,
    "DUMBO": 0.025,
    "Upper East Side": 0.03,
    "Brooklyn Heights": 0.03,
    "St. George": 0.035,
    "Riverdale": 0.035,
    "Tottenville": 0.03,
}
BEDROOM_RENT = np.array([0.78, 1.00, 1.38, 1.80, 2.25])  # multiplier over a 1-bedroom
BROKER_FEE = {"Manhattan": 0.55, "Brooklyn": 0.40, "Queens": 0.30, "Bronx": 0.25, "Staten Island": 0.20}
STATUS = ["rented", "active", "withdrawn"]


def month_starts(first: str, months: int) -> list[dt.date]:
    y, m = map(int, first.split("-"))
    out = []
    for _ in range(months):
        out.append(dt.date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def people(seed: int) -> tuple[dict, dict]:
    """Faker-made agents (each at one agency) and street addresses, sampled per row later. Calling
    Faker once per row would take minutes; pools of a few thousand values are enough."""
    fake = Faker("en_US")
    Faker.seed(seed)
    agencies = sorted(
        {
            f"{fake.last_name()} {s}"
            for s in fake.random_choices(
                ["Realty", "Properties", "Residential", "Homes", "& Partners", "Group"], length=400
            )
        }
    )[:300]
    rng = np.random.default_rng(seed)
    agents = {
        "agent_id": np.arange(2000),
        "agent_name": np.array([fake.name() for _ in range(2000)]),
        "agency": np.array(agencies)[rng.integers(0, len(agencies), 2000)],
    }
    addresses = {
        "address_id": np.arange(20000),
        "street_address": np.array([f"{fake.building_number()} {fake.street_name()}" for _ in range(20000)]),
    }
    return agents, addresses


def month_rows(
    rng: np.random.Generator,
    start: dt.date,
    index: int,
    n: int,
    data_end: dt.date,
    boroughs: list[str] | None = None,
    price: float = 1.0,
) -> dict:
    """One month of listings. boroughs and price narrow and shift them for an agency's portfolio."""
    nb = np.array(NEIGHBORHOODS, dtype=object)
    weights = nb[:, 3].astype(float) * (np.isin(nb[:, 1], boroughs) if boroughs else 1)
    weights = weights / weights.sum()
    hood = rng.choice(len(NEIGHBORHOODS), n, p=weights)
    borough = nb[hood, 1]
    base, size, doorman_p, prewar_p = (nb[hood, k].astype(float) for k in (2, 4, 5, 6))

    bedrooms = np.empty(n, dtype=np.int64)
    for b, mix in BEDROOM_MIX.items():
        sel = borough == b
        bedrooms[sel] = rng.choice(5, sel.sum(), p=mix)
    bathrooms = np.maximum(1.0, np.round((bedrooms * 0.6 + rng.normal(0.4, 0.35, n)) * 2) / 2)
    sqft = np.round((380 + 290 * bedrooms) * size * rng.lognormal(0, 0.15, n), -1).astype(np.int32)
    prewar = rng.random(n) < prewar_p
    year_built = np.where(prewar, rng.integers(1890, 1940, n), rng.integers(1940, 2025, n)).astype(np.int16)
    doorman = rng.random(n) < doorman_p
    elevator = doorman | (rng.random(n) < np.where(prewar, 0.35, 0.70))
    laundry = rng.random(n) < np.where(year_built >= 2000, 0.65, 0.18)
    pets = rng.random(n) < 0.55
    broker = rng.random(n) < np.vectorize(BROKER_FEE.get)(borough).astype(float)

    month = start.month
    season = 1 + 0.035 * np.cos(2 * np.pi * (month - 7.5) / 12)  # peaks in July-August
    growth = np.array([GROWTH.get(h[0], 0.045) for h in NEIGHBORHOODS])[hood]
    trend = (1 + growth) ** (index / 12)
    going_rate = (
        base
        * BEDROOM_RENT[bedrooms]
        * (sqft / ((380 + 290 * bedrooms) * size)) ** 0.35
        * season
        * trend
        * np.where(doorman, 1.09, 1)
        * np.where(laundry, 1.06, 1)
        * np.where(elevator, 1.03, 1)
        * np.where(year_built >= 2010, 1.05, 1)
    )
    over = rng.lognormal(0, 0.10, n) * price  # how far the asking rent sits from the going rate
    rent = (np.round(going_rate * over / 25) * 25).astype(np.int32)

    days = calendar.monthrange(start.year, month)[1]
    day = rng.integers(0, days, n).astype(np.int32)  # days after the 1st; dates are built in SQL
    listed = np.datetime64(start) + day.astype("timedelta64[D]")
    dom = np.maximum(1, np.round(rng.gamma(2.0, 9.0, n) * over**6 * np.where(broker, 1.15, 1))).astype(np.int32)
    gone = listed + dom.astype("timedelta64[D]") <= np.datetime64(data_end)
    withdrawn = rng.random(n) < 0.06
    status = np.where(~gone, 1, np.where(withdrawn, 2, 0)).astype(np.int8)
    return {
        "listing_id": index * 1_000_000 + np.arange(n),
        "day": day,
        "neighborhood_id": hood.astype(np.int16),
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "sqft": sqft,
        "rent_usd": rent,
        "broker_fee": broker,
        "doorman": doorman,
        "elevator": elevator,
        "laundry_in_unit": laundry,
        "pets_allowed": pets,
        "year_built": year_built,
        "status": status,
        "days_on_market": dom,
        "agent_id": rng.integers(0, 2000, n),
        "address_id": rng.integers(0, 20000, n),
    }


def _calendar() -> tuple[list[dt.date], dt.date]:
    starts = month_starts(DATA_GEN["first_month"], DATA_GEN["months"])
    last = starts[-1]
    return starts, dt.date(last.year, last.month, calendar.monthrange(last.year, last.month)[1])


def generate(force: bool = False) -> list[Path]:
    cfg = DATA_GEN
    starts, data_end = _calendar()
    listings_dir = OUT / "listings"
    listings_dir.mkdir(parents=True, exist_ok=True)
    agents, addresses = people(cfg["seed"])
    con = duckdb.connect()
    con.register("agents", agents)
    con.register("addresses", addresses)
    con.register("statuses", {"status": np.arange(3, dtype=np.int8), "status_name": np.array(STATUS)})
    files = []
    t0 = time.monotonic()
    for i, start in enumerate(starts):
        path = listings_dir / f"listings_{start:%Y-%m}.parquet"
        files.append(path)
        if path.exists() and not force:
            continue
        rows = month_rows(np.random.default_rng([cfg["seed"], i]), start, i, cfg["rows_per_month"], data_end)
        con.register("rows", rows)
        con.execute(f"""COPY (
            WITH r AS (SELECT *, DATE '{start}' + day AS listed_date FROM rows)
            SELECT r.listing_id, r.listed_date, r.neighborhood_id, ad.street_address, r.bedrooms, r.bathrooms,
                   r.sqft, r.rent_usd, r.broker_fee, r.doorman, r.elevator, r.laundry_in_unit, r.pets_allowed,
                   r.year_built, s.status_name AS status, r.days_on_market,
                   CASE WHEN s.status_name = 'rented' THEN r.listed_date + r.days_on_market END AS rented_date,
                   ag.agent_name, ag.agency
            FROM r JOIN agents ag USING (agent_id) JOIN addresses ad USING (address_id) JOIN statuses s USING (status)
            ORDER BY r.listed_date, r.listing_id
        ) TO '{path}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 122880)""")
        con.unregister("rows")
        print(
            f"[data_gen] {path.relative_to(ROOT)}  {cfg['rows_per_month']:,} rows  {path.stat().st_size / 1e6:,.1f} MB"
        )
    lookup = OUT / "neighborhoods.csv"
    with lookup.open("w") as f:
        f.write("neighborhood_id,neighborhood,borough\n")
        f.writelines(f'{i},"{n[0]}",{n[1]}\n' for i, n in enumerate(NEIGHBORHOODS))
    files.append(lookup)
    print(f"[data_gen] {len(files)} files in {OUT.relative_to(ROOT)}/ ({time.monotonic() - t0:,.1f} s)")
    return files


def generate_portfolios(force: bool = False) -> dict[str, list[Path]]:
    """Each agency's own listings, spread over the same months as the market data. The agency's agents,
    the owners and the commission are private: they exist only in this file."""
    starts, data_end = _calendar()
    out = {}
    for n_agency, (key, agency) in enumerate(AGENCIES.items()):
        path = USERS_OUT / key / "my_listings" / "portfolio.parquet"
        out[key] = [path]
        if path.exists() and not force:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        seed = [DATA_GEN["seed"], 1000 + n_agency]
        fake = Faker("en_US")
        Faker.seed(seed[-1])
        rng = np.random.default_rng(seed)
        con = duckdb.connect()
        con.register("agents", {"agent_id": np.arange(12), "agent_name": np.array([fake.name() for _ in range(12)])})
        con.register(
            "owners",
            {
                "owner_id": np.arange(400),
                "owner_name": np.array([fake.name() for _ in range(400)]),
                "owner_email": np.array([fake.email() for _ in range(400)]),
            },
        )
        con.register(
            "addresses",
            {
                "address_id": np.arange(5000),
                "street_address": np.array([f"{fake.building_number()} {fake.street_name()}" for _ in range(5000)]),
            },
        )
        con.register("statuses", {"status": np.arange(3, dtype=np.int8), "status_name": np.array(STATUS)})
        con.execute("CREATE TABLE p AS SELECT * FROM (SELECT 1) WHERE false")
        per_month = agency["listings"] // len(starts)
        for i, start in enumerate(starts):
            rows = month_rows(rng, start, i, per_month, data_end, agency["boroughs"], agency["price_vs_market"])
            rows["listing_id"] = (900 + n_agency) * 1_000_000 + i * 10_000 + np.arange(per_month)
            rows["agent_id"] = rng.integers(0, 12, per_month)
            rows["address_id"] = rng.integers(0, 5000, per_month)
            rows["owner_id"] = rng.integers(0, 400, per_month)
            rows["commission_pct"] = rng.choice([8.33, 10.0, 12.0, 15.0], per_month)
            con.register("rows", rows)
            select = f"""
                WITH r AS (SELECT *, DATE '{start}' + day AS listed_date FROM rows)
                SELECT r.listing_id, r.listed_date, r.neighborhood_id, ad.street_address, r.bedrooms, r.bathrooms,
                       r.sqft, r.rent_usd, r.broker_fee, r.doorman, r.elevator, r.laundry_in_unit, r.pets_allowed,
                       r.year_built, s.status_name AS status, r.days_on_market,
                       CASE WHEN s.status_name = 'rented' THEN r.listed_date + r.days_on_market END AS rented_date,
                       ag.agent_name, '{agency["name"]}' AS agency, o.owner_name, o.owner_email, r.commission_pct
                FROM r JOIN agents ag USING (agent_id) JOIN addresses ad USING (address_id)
                       JOIN owners o USING (owner_id) JOIN statuses s USING (status)"""
            con.execute(f"CREATE OR REPLACE TABLE p AS {select}" if i == 0 else f"INSERT INTO p {select}")
            con.unregister("rows")
        con.execute(
            f"COPY (SELECT * FROM p ORDER BY listed_date, listing_id) TO '{path}' (FORMAT parquet, COMPRESSION zstd)"
        )
        print(f"[data_gen] {path.relative_to(ROOT)}  {per_month * len(starts):,} rows ({agency['name']})")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--force", action="store_true", help="regenerate files that already exist")
    force = ap.parse_args().force
    generate(force)
    generate_portfolios(force)
