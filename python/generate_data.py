"""
generate_data.py — Agent Eval Demo synthetic data generator.

Produces Parquet files for all AGENT_EVAL_DEMO tables:
  FULFILLMENT_INTELLIGENCE: orders, order_lines, waves, exceptions
  INVENTORY_INTELLIGENCE: item_master, on_hand, movements
  LABOR_INTELLIGENCE: pick_tasks, labor_standards
  SHIPPING_INTELLIGENCE: shipments, carrier_scans, zone_rate_cards
  + fiscal_calendar_445 dimension (in FULFILLMENT_INTELLIGENCE)

Six tenants, ~18 months (2025-01-01 to 2026-06-30), ~40K orders.
All six ambiguity traps are structurally present in the schema.
"""

import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

SEED = 42
np.random.seed(SEED)

OUTPUT_DIR = Path(__file__).parent.parent / "data"
OUTPUT_DIR.mkdir(exist_ok=True)

# --- Constants ---
START_DATE = pd.Timestamp("2025-01-01")
END_DATE = pd.Timestamp("2026-06-30")

TENANTS = [
    {"id": "T001", "name": "Alderwood Logistics", "profile": "high_volume_low_complexity"},
    {"id": "T002", "name": "Bellweather Freight", "profile": "heavy_freight"},
    {"id": "T003", "name": "Cobalt Apparel", "profile": "apparel_seasonal"},
    {"id": "T004", "name": "Dunmore Distribution", "profile": "ecomm_dth"},
    {"id": "T005", "name": "Everline Medical", "profile": "healthcare_regulated"},
    {"id": "T006", "name": "Foxglove Foods", "profile": "cold_chain_perishable"},
]

CARRIERS = ["FEDEX", "UPS", "USPS", "DHL", "XPO", "ONTRAC"]
ZONES = [1, 2, 3, 4, 5, 6, 7, 8]
WEIGHT_BREAKS = ["0-1LB", "1-5LB", "5-20LB", "20-50LB", "50-150LB"]

WAREHOUSES = ["ATL-DC1", "ATL-DC2", "CHI-DC1", "DAL-DC1", "LAX-DC1"]


def generate_fiscal_calendar_445():
    """4-4-5 retail fiscal calendar. Fiscal year starts Feb 1."""
    rows = []
    # Generate for 2025 and 2026 fiscal years
    for fy_start_year in [2024, 2025, 2026]:
        # Fiscal year starts on the Sunday closest to Feb 1
        feb1 = pd.Timestamp(f"{fy_start_year + 1}-02-01")
        # Find nearest Sunday
        fy_start = feb1 - pd.Timedelta(days=(feb1.weekday() + 1) % 7)

        fiscal_year = fy_start_year + 1  # FY2025 starts Jan/Feb 2025
        week_in_year = 0
        # 4-4-5 pattern per quarter
        weeks_pattern = [4, 4, 5] * 4  # 12 periods

        current = fy_start
        for period_idx, weeks_in_period in enumerate(weeks_pattern):
            fiscal_period = period_idx + 1
            fiscal_quarter = (period_idx // 3) + 1
            for week_in_period in range(1, weeks_in_period + 1):
                week_in_year += 1
                for day_offset in range(7):
                    cal_date = current + pd.Timedelta(days=day_offset)
                    if cal_date < START_DATE or cal_date > END_DATE:
                        continue
                    rows.append({
                        "CALENDAR_DATE": cal_date,
                        "FISCAL_YEAR": fiscal_year,
                        "FISCAL_QUARTER": fiscal_quarter,
                        "FISCAL_PERIOD": fiscal_period,
                        "FISCAL_WEEK": week_in_year,
                        "FISCAL_WEEK_IN_PERIOD": week_in_period,
                        "CALENDAR_YEAR": cal_date.year,
                        "CALENDAR_MONTH": cal_date.month,
                        "CALENDAR_WEEK": cal_date.isocalendar()[1],
                        "DAY_OF_WEEK": cal_date.strftime("%A"),
                    })
                current += pd.Timedelta(days=7)

    df = pd.DataFrame(rows)
    return df


def generate_item_master(n_skus=8000):
    """Item master with categories, each/carton conversion, and activity flags."""
    categories = [
        "Electronics", "Apparel", "Home Goods", "Food & Beverage",
        "Health & Beauty", "Automotive Parts", "Office Supplies",
        "Pet Supplies", "Sporting Goods", "Toys & Games"
    ]
    subcategories = {
        "Electronics": ["Cables", "Adapters", "Batteries", "Chargers", "Cases"],
        "Apparel": ["Shirts", "Pants", "Outerwear", "Shoes", "Accessories"],
        "Home Goods": ["Kitchen", "Bedding", "Cleaning", "Storage", "Decor"],
        "Food & Beverage": ["Snacks", "Drinks", "Condiments", "Grains", "Canned"],
        "Health & Beauty": ["Vitamins", "Skincare", "Haircare", "OTC Medicine", "First Aid"],
        "Automotive Parts": ["Filters", "Bulbs", "Wipers", "Fluids", "Mounts"],
        "Office Supplies": ["Paper", "Pens", "Binders", "Tape", "Labels"],
        "Pet Supplies": ["Food", "Toys", "Beds", "Grooming", "Treats"],
        "Sporting Goods": ["Fitness", "Outdoor", "Team Sports", "Water", "Winter"],
        "Toys & Games": ["Action Figures", "Board Games", "Puzzles", "Dolls", "Building"],
    }

    rows = []
    for i in range(n_skus):
        cat = np.random.choice(categories)
        subcat = np.random.choice(subcategories[cat])
        # AMBIGUITY TRAP 3: QTY_EACHES vs QTY_CARTONS — carton_qty varies by SKU
        carton_qty = np.random.choice([6, 8, 12, 16, 24])
        # AMBIGUITY TRAP 6: "active SKU" — only ~60% have recent movement
        rows.append({
            "SKU": f"SKU-{i+1:06d}",
            "DESCRIPTION": f"{subcat} {cat} Item {i+1}",
            "CATEGORY": cat,
            "SUBCATEGORY": subcat,
            "UNIT_WEIGHT_LB": round(np.random.exponential(2.5) + 0.1, 2),
            "EACHES_PER_CARTON": carton_qty,
            "UNIT_COST": round(np.random.uniform(1.50, 150.00), 2),
            "HAZMAT_FLAG": "Y" if (cat == "Automotive Parts" and np.random.random() < 0.3) else "N",
            "TEMPERATURE_SENSITIVE": "Y" if cat == "Food & Beverage" else "N",
            "CREATED_DATE": START_DATE + pd.Timedelta(days=np.random.randint(0, 90)),
        })
    return pd.DataFrame(rows)


def generate_zone_rate_cards():
    """Carrier zone rate cards. AMBIGUITY TRAP 4: cost requires this join."""
    rows = []
    for carrier in CARRIERS:
        base_mult = {"FEDEX": 1.0, "UPS": 0.95, "USPS": 0.7, "DHL": 1.3, "XPO": 1.5, "ONTRAC": 0.65}[carrier]
        for zone in ZONES:
            for wb in WEIGHT_BREAKS:
                base_rate = {
                    "0-1LB": 4.50, "1-5LB": 7.80, "5-20LB": 12.50,
                    "20-50LB": 22.00, "50-150LB": 45.00
                }[wb]
                rate = round(base_rate * base_mult * (1 + zone * 0.08) + np.random.uniform(-0.5, 0.5), 2)
                rows.append({
                    "CARRIER": carrier,
                    "ZONE": zone,
                    "WEIGHT_BREAK": wb,
                    "RATE_PER_PACKAGE": rate,
                    "FUEL_SURCHARGE_PCT": round(np.random.uniform(5.0, 18.0), 1),
                    "EFFECTIVE_DATE": pd.Timestamp("2025-01-01"),
                    "EXPIRY_DATE": pd.Timestamp("2026-12-31"),
                })
    return pd.DataFrame(rows)


def generate_orders_and_lines(item_master, n_orders=40000):
    """Orders and order lines with all ambiguity traps embedded."""
    skus = item_master["SKU"].values
    sku_weights = item_master.set_index("SKU")["UNIT_WEIGHT_LB"].to_dict()
    sku_carton_qtys = item_master.set_index("SKU")["EACHES_PER_CARTON"].to_dict()

    orders = []
    lines = []
    line_id = 0

    date_range_days = (END_DATE - START_DATE).days

    for i in range(n_orders):
        tenant = TENANTS[i % len(TENANTS)]
        order_date = START_DATE + pd.Timedelta(days=np.random.randint(0, date_range_days))
        # AMBIGUITY TRAP 1: "on time" — three different date concepts
        ship_by_date = order_date + pd.Timedelta(days=np.random.choice([1, 2, 3, 5]))
        promised_delivery_date = ship_by_date + pd.Timedelta(days=np.random.choice([2, 3, 5, 7]))

        n_lines = np.random.choice([2, 3, 4, 5, 6, 8, 10, 15], p=[0.10, 0.15, 0.20, 0.20, 0.15, 0.10, 0.07, 0.03])
        # AMBIGUITY TRAP 2: fill rate — some lines are short-picked
        lines_filled = np.random.binomial(n_lines, 0.88)
        warehouse = np.random.choice(WAREHOUSES)
        priority = np.random.choice(["STANDARD", "EXPEDITED", "NEXT_DAY"], p=[0.7, 0.2, 0.1])

        order_id = f"ORD-{i+1:07d}"
        orders.append({
            "ORDER_ID": order_id,
            "TENANT_ID": tenant["id"],
            "ORDER_DATE": order_date,
            "SHIP_BY_DATE": ship_by_date,
            "PROMISED_DELIVERY_DATE": promised_delivery_date,
            "WAREHOUSE_ID": warehouse,
            "PRIORITY": priority,
            "TOTAL_LINES": n_lines,
            "LINES_FILLED": lines_filled,
            "STATUS": "SHIPPED" if np.random.random() < 0.92 else np.random.choice(["PARTIAL", "CANCELLED", "EXCEPTION"]),
            "CHANNEL": np.random.choice(["B2B", "DTC", "MARKETPLACE"], p=[0.4, 0.35, 0.25]),
        })

        selected_skus = np.random.choice(skus, size=n_lines, replace=False)
        for j, sku in enumerate(selected_skus):
            # AMBIGUITY TRAP 3: qty_eaches vs qty_cartons
            qty_eaches = np.random.choice([1, 2, 3, 6, 12, 24, 48])
            carton_qty = sku_carton_qtys.get(sku, 12)
            qty_cartons = max(1, qty_eaches // carton_qty)
            filled = j < lines_filled

            line_id += 1
            # AMBIGUITY TRAP (fill rate): a SHORT line is a short-PICK, which in a
            # real WMS usually ships partially rather than not at all. ~70% of short
            # lines get a partial quantity. This is what makes the three fill-rate
            # definitions diverge (order 53% / line 88% / unit 92%); if short lines
            # were all-or-nothing, unit fill would exactly equal line fill and the
            # trap would be degenerate.
            if filled:
                qty_shipped = qty_eaches
            elif qty_eaches > 1 and np.random.random() < 0.70:
                qty_shipped = max(1, int(qty_eaches * np.random.uniform(0.20, 0.90)))
            else:
                qty_shipped = 0

            lines.append({
                "ORDER_LINE_ID": f"OL-{line_id:08d}",
                "ORDER_ID": order_id,
                "TENANT_ID": tenant["id"],
                "SKU": sku,
                "QTY_ORDERED_EACHES": qty_eaches,
                "QTY_SHIPPED_EACHES": qty_shipped,
                "QTY_CARTONS": qty_cartons,
                "UNIT_PRICE": round(np.random.uniform(5.0, 200.0), 2),
                "LINE_STATUS": "SHIPPED" if filled else "SHORT",
                "WAVE_ID": f"W-{order_date.strftime('%Y%m%d')}-{warehouse}-{np.random.randint(1,20):02d}",
            })

    return pd.DataFrame(orders), pd.DataFrame(lines)


def generate_shipments(orders_df):
    """Shipments with carrier scan timestamps. AMBIGUITY TRAP 1 lives here."""
    shipped = orders_df[orders_df["STATUS"].isin(["SHIPPED", "PARTIAL"])].copy()
    rows = []
    for _, order in shipped.iterrows():
        carrier = np.random.choice(CARRIERS, p=[0.3, 0.25, 0.2, 0.1, 0.1, 0.05])
        zone = np.random.choice(ZONES, p=[0.05, 0.1, 0.15, 0.2, 0.2, 0.15, 0.1, 0.05])
        total_weight = round(np.random.exponential(8.0) + 0.5, 2)
        # Determine weight break
        if total_weight <= 1:
            wb = "0-1LB"
        elif total_weight <= 5:
            wb = "1-5LB"
        elif total_weight <= 20:
            wb = "5-20LB"
        elif total_weight <= 50:
            wb = "20-50LB"
        else:
            wb = "50-150LB"

        ship_date = order["ORDER_DATE"] + pd.Timedelta(days=np.random.choice([0, 1, 2, 3]))
        # AMBIGUITY TRAP 1: carrier_first_scan_ts may differ from ship_by_date
        # Sometimes scanned same day, sometimes next day, occasionally late
        scan_delay_hours = np.random.choice([0, 2, 4, 8, 24, 48], p=[0.3, 0.25, 0.2, 0.15, 0.07, 0.03])
        carrier_first_scan = ship_date + pd.Timedelta(hours=scan_delay_hours + np.random.randint(6, 18))

        rows.append({
            "SHIPMENT_ID": f"SHP-{len(rows)+1:07d}",
            "ORDER_ID": order["ORDER_ID"],
            "TENANT_ID": order["TENANT_ID"],
            "CARRIER": carrier,
            "ZONE": zone,
            "WEIGHT_BREAK": wb,
            "TOTAL_WEIGHT_LB": total_weight,
            "PACKAGE_COUNT": np.random.choice([1, 2, 3, 4], p=[0.6, 0.25, 0.1, 0.05]),
            "SHIP_DATE": ship_date,
            "CARRIER_FIRST_SCAN_TS": carrier_first_scan,
            "SHIP_BY_DATE": order["SHIP_BY_DATE"],
            "PROMISED_DELIVERY_DATE": order["PROMISED_DELIVERY_DATE"],
            "WAREHOUSE_ID": order["WAREHOUSE_ID"],
            "TRACKING_NUMBER": f"TRK{np.random.randint(100000000, 999999999)}",
        })
    return pd.DataFrame(rows)


def generate_carrier_scans(shipments_df):
    """Carrier scan events for shipment tracking."""
    rows = []
    scan_types = ["PICKED_UP", "IN_TRANSIT", "OUT_FOR_DELIVERY", "DELIVERED", "EXCEPTION"]
    for _, shp in shipments_df.iterrows():
        n_scans = np.random.choice([3, 4, 5, 6], p=[0.2, 0.4, 0.3, 0.1])
        current_ts = shp["CARRIER_FIRST_SCAN_TS"]
        for s in range(n_scans):
            scan_type = scan_types[min(s, len(scan_types) - 1)]
            if s == n_scans - 1:
                scan_type = "DELIVERED" if np.random.random() < 0.95 else "EXCEPTION"
            rows.append({
                "SCAN_ID": f"SC-{len(rows)+1:09d}",
                "SHIPMENT_ID": shp["SHIPMENT_ID"],
                "TENANT_ID": shp["TENANT_ID"],
                "SCAN_TYPE": scan_type,
                "SCAN_TIMESTAMP": current_ts,
                "LOCATION": f"HUB-{np.random.randint(1,50):02d}",
                "CARRIER": shp["CARRIER"],
            })
            current_ts += pd.Timedelta(hours=np.random.randint(4, 36))
    return pd.DataFrame(rows)


def generate_waves(orders_df):
    """Wave planning records."""
    wave_ids = orders_df["ORDER_ID"].map(
        lambda _: f"W-PLACEHOLDER"  # we'll use lines for real wave IDs
    )
    # Aggregate from lines data — generate basic wave records
    # Waves are per-warehouse per-day batches
    dates = pd.date_range(START_DATE, END_DATE, freq='D')
    rows = []
    for wh in WAREHOUSES:
        for d in dates:
            n_waves = np.random.choice([0, 1, 2, 3], p=[0.1, 0.4, 0.35, 0.15])
            for w in range(n_waves):
                cutoff_hour = np.random.choice([10, 14, 17, 20])
                rows.append({
                    "WAVE_ID": f"W-{d.strftime('%Y%m%d')}-{wh}-{w+1:02d}",
                    "WAREHOUSE_ID": wh,
                    "WAVE_DATE": d,
                    "CUTOFF_TIME": f"{cutoff_hour:02d}:00",
                    "STATUS": "COMPLETED" if np.random.random() < 0.93 else "MISSED_CUTOFF",
                    "TOTAL_ORDERS": np.random.randint(5, 80),
                    "TOTAL_LINES": np.random.randint(10, 400),
                    "TOTAL_UNITS": np.random.randint(20, 2000),
                })
    return pd.DataFrame(rows)


def generate_exceptions(orders_df):
    """Order exceptions (short picks, damaged, address issues)."""
    exception_types = ["SHORT_PICK", "DAMAGED", "ADDRESS_INVALID", "CARRIER_REJECT", "HAZMAT_HOLD", "TEMP_EXCURSION"]
    rows = []
    # ~5% of orders get an exception
    exc_orders = orders_df.sample(frac=0.05, random_state=SEED)
    for _, order in exc_orders.iterrows():
        rows.append({
            "EXCEPTION_ID": f"EXC-{len(rows)+1:06d}",
            "ORDER_ID": order["ORDER_ID"],
            "TENANT_ID": order["TENANT_ID"],
            "EXCEPTION_TYPE": np.random.choice(exception_types),
            "EXCEPTION_DATE": order["ORDER_DATE"] + pd.Timedelta(days=np.random.randint(0, 3)),
            "RESOLUTION": np.random.choice(["RESOLVED", "PENDING", "CANCELLED"], p=[0.7, 0.2, 0.1]),
            "WAREHOUSE_ID": order["WAREHOUSE_ID"],
            "NOTES": "Auto-generated exception record",
        })
    return pd.DataFrame(rows)


def generate_on_hand(item_master, tenants):
    """Current on-hand inventory by SKU/warehouse/tenant."""
    rows = []
    # Not all SKUs stocked everywhere — sample ~40% per warehouse/tenant combo
    for tenant in tenants:
        for wh in WAREHOUSES:
            stocked_skus = item_master.sample(frac=0.4, random_state=hash(tenant["id"] + wh) % 2**31)
            for _, item in stocked_skus.iterrows():
                rows.append({
                    "SKU": item["SKU"],
                    "WAREHOUSE_ID": wh,
                    "TENANT_ID": tenant["id"],
                    "QTY_ON_HAND_EACHES": np.random.randint(0, 500),
                    "QTY_ALLOCATED": np.random.randint(0, 100),
                    "QTY_AVAILABLE": None,  # will compute
                    "LAST_COUNT_DATE": END_DATE - pd.Timedelta(days=np.random.randint(0, 30)),
                    "LOCATION_ID": f"{wh}-{np.random.choice(['A','B','C','D'])}{np.random.randint(1,50):02d}-{np.random.randint(1,5)}",
                })
    df = pd.DataFrame(rows)
    df["QTY_AVAILABLE"] = (df["QTY_ON_HAND_EACHES"] - df["QTY_ALLOCATED"]).clip(lower=0)
    return df


def generate_movements(item_master, tenants):
    """Inventory movements. AMBIGUITY TRAP 6: 'active SKU' = has movement in trailing 30d."""
    rows = []
    movement_types = ["RECEIPT", "PICK", "ADJUSTMENT", "TRANSFER", "RETURN"]
    # Only ~60% of SKUs get movement in last 30 days
    active_skus = item_master.sample(frac=0.60, random_state=SEED + 1)["SKU"].values
    inactive_skus = item_master[~item_master["SKU"].isin(active_skus)]["SKU"].values

    # Generate movements over 18 months — but cluster recent ones in active SKUs
    dates = pd.date_range(START_DATE, END_DATE, freq='D')
    for tenant in tenants:
        # Active SKUs: movements throughout
        for sku in np.random.choice(active_skus, size=min(2000, len(active_skus)), replace=False):
            n_moves = np.random.randint(3, 20)
            for _ in range(n_moves):
                move_date = START_DATE + pd.Timedelta(days=np.random.randint(0, (END_DATE - START_DATE).days))
                rows.append({
                    "MOVEMENT_ID": f"MV-{len(rows)+1:08d}",
                    "SKU": sku,
                    "TENANT_ID": tenant["id"],
                    "WAREHOUSE_ID": np.random.choice(WAREHOUSES),
                    "MOVEMENT_TYPE": np.random.choice(movement_types, p=[0.3, 0.35, 0.1, 0.15, 0.1]),
                    "QTY_EACHES": np.random.randint(1, 100) * (1 if np.random.random() < 0.6 else -1),
                    "MOVEMENT_DATE": move_date,
                    "REFERENCE_ID": f"REF-{np.random.randint(100000, 999999)}",
                })
        # Inactive SKUs: movements only before 60+ days ago (trap 6)
        for sku in np.random.choice(inactive_skus, size=min(500, len(inactive_skus)), replace=False):
            n_moves = np.random.randint(1, 5)
            for _ in range(n_moves):
                # Only old movements
                move_date = START_DATE + pd.Timedelta(days=np.random.randint(0, max(1, (END_DATE - START_DATE).days - 60)))
                rows.append({
                    "MOVEMENT_ID": f"MV-{len(rows)+1:08d}",
                    "SKU": sku,
                    "TENANT_ID": tenant["id"],
                    "WAREHOUSE_ID": np.random.choice(WAREHOUSES),
                    "MOVEMENT_TYPE": np.random.choice(movement_types, p=[0.3, 0.35, 0.1, 0.15, 0.1]),
                    "QTY_EACHES": np.random.randint(1, 50),
                    "MOVEMENT_DATE": move_date,
                    "REFERENCE_ID": f"REF-{np.random.randint(100000, 999999)}",
                })
    return pd.DataFrame(rows)


def generate_pick_tasks(order_lines_df):
    """Pick tasks for labor tracking."""
    rows = []
    shipped_lines = order_lines_df[order_lines_df["LINE_STATUS"] == "SHIPPED"]
    for _, line in shipped_lines.iterrows():
        pick_time_sec = np.random.exponential(45) + 10  # seconds per pick
        rows.append({
            "PICK_TASK_ID": f"PT-{len(rows)+1:08d}",
            "ORDER_LINE_ID": line["ORDER_LINE_ID"],
            "ORDER_ID": line["ORDER_ID"],
            "TENANT_ID": line["TENANT_ID"],
            "SKU": line["SKU"],
            "QTY_PICKED": line["QTY_SHIPPED_EACHES"],
            "PICKER_ID": f"EMP-{np.random.randint(1, 200):04d}",
            "PICK_START_TS": pd.NaT,  # would need order date context
            "PICK_DURATION_SEC": round(pick_time_sec, 1),
            "ZONE": f"ZONE-{np.random.choice(['A','B','C','D','E'])}",
            "WAVE_ID": line["WAVE_ID"],
        })
    return pd.DataFrame(rows)


def generate_labor_standards():
    """Expected pick rates by zone and method."""
    rows = []
    methods = ["DISCRETE", "BATCH", "ZONE_PICK", "CLUSTER"]
    zones = ["ZONE-A", "ZONE-B", "ZONE-C", "ZONE-D", "ZONE-E"]
    for zone in zones:
        for method in methods:
            rows.append({
                "ZONE": zone,
                "PICK_METHOD": method,
                "STANDARD_PICKS_PER_HOUR": np.random.randint(60, 180),
                "STANDARD_UNITS_PER_HOUR": np.random.randint(100, 500),
                "EFFECTIVE_DATE": pd.Timestamp("2025-01-01"),
            })
    return pd.DataFrame(rows)


# Columns declared DATE (not TIMESTAMP_NTZ) in 01_load_data.sql. Parquet must
# carry these as date32, otherwise COPY INTO fails with:
#   100071 (22000): Failed to cast variant value "2025-01-01 00:00:00.000" to DATE
# Note that coerce_timestamps='us' does NOT do this — it only sets timestamp
# precision. The dtype itself has to become a date.
DATE_ONLY_COLUMNS = {
    "fiscal_calendar_445": ["CALENDAR_DATE"],
    "waves": ["WAVE_DATE"],
    "labor_standards": ["EFFECTIVE_DATE"],
    "zone_rate_cards": ["EFFECTIVE_DATE", "EXPIRY_DATE"],
}


def to_date32(df, name):
    """Coerce the DATE-declared columns of `name` to true dates before writing."""
    for col in DATE_ONLY_COLUMNS.get(name, []):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col]).dt.date
    return df


def main():
    print("Generating synthetic 3PL fulfilment data...")

    # 1. Fiscal calendar
    print("  fiscal_calendar_445...")
    fiscal_cal = generate_fiscal_calendar_445()
    to_date32(fiscal_cal, "fiscal_calendar_445").to_parquet(OUTPUT_DIR / "fiscal_calendar_445.parquet", index=False, coerce_timestamps='us')

    # 2. Item master
    print("  item_master...")
    item_master = generate_item_master(n_skus=8000)
    item_master.to_parquet(OUTPUT_DIR / "item_master.parquet", index=False, coerce_timestamps='us')

    # 3. Zone rate cards
    print("  zone_rate_cards...")
    zone_rates = generate_zone_rate_cards()
    to_date32(zone_rates, "zone_rate_cards").to_parquet(OUTPUT_DIR / "zone_rate_cards.parquet", index=False, coerce_timestamps='us')

    # 4. Orders and lines
    print("  orders + order_lines...")
    orders, order_lines = generate_orders_and_lines(item_master, n_orders=40000)
    orders.to_parquet(OUTPUT_DIR / "orders.parquet", index=False, coerce_timestamps='us')
    order_lines.to_parquet(OUTPUT_DIR / "order_lines.parquet", index=False, coerce_timestamps='us')
    print(f"    {len(orders)} orders, {len(order_lines)} lines")

    # 5. Shipments
    print("  shipments...")
    shipments = generate_shipments(orders)
    shipments.to_parquet(OUTPUT_DIR / "shipments.parquet", index=False, coerce_timestamps='us')
    print(f"    {len(shipments)} shipments")

    # 6. Carrier scans
    print("  carrier_scans...")
    carrier_scans = generate_carrier_scans(shipments)
    carrier_scans.to_parquet(OUTPUT_DIR / "carrier_scans.parquet", index=False, coerce_timestamps='us')
    print(f"    {len(carrier_scans)} scans")

    # 7. Waves
    print("  waves...")
    waves = generate_waves(orders)
    to_date32(waves, "waves").to_parquet(OUTPUT_DIR / "waves.parquet", index=False, coerce_timestamps='us')

    # 8. Exceptions
    print("  exceptions...")
    exceptions = generate_exceptions(orders)
    exceptions.to_parquet(OUTPUT_DIR / "exceptions.parquet", index=False, coerce_timestamps='us')

    # 9. On-hand inventory
    print("  on_hand...")
    on_hand = generate_on_hand(item_master, TENANTS)
    on_hand.to_parquet(OUTPUT_DIR / "on_hand.parquet", index=False, coerce_timestamps='us')

    # 10. Movements
    print("  movements...")
    movements = generate_movements(item_master, TENANTS)
    movements.to_parquet(OUTPUT_DIR / "movements.parquet", index=False, coerce_timestamps='us')
    print(f"    {len(movements)} movements")

    # 11. Pick tasks (sample — full set would be huge)
    print("  pick_tasks (sampled)...")
    # Sample to keep reasonable size
    sampled_lines = order_lines.sample(frac=0.3, random_state=SEED)
    pick_tasks = generate_pick_tasks(sampled_lines)
    pick_tasks.to_parquet(OUTPUT_DIR / "pick_tasks.parquet", index=False, coerce_timestamps='us')
    print(f"    {len(pick_tasks)} pick tasks")

    # 12. Labor standards
    print("  labor_standards...")
    labor_standards = generate_labor_standards()
    to_date32(labor_standards, "labor_standards").to_parquet(OUTPUT_DIR / "labor_standards.parquet", index=False, coerce_timestamps='us')

    print(f"\nAll files written to {OUTPUT_DIR}/")
    print("Files:")
    for f in sorted(OUTPUT_DIR.glob("*.parquet")):
        size_mb = f.stat().st_size / 1024 / 1024
        print(f"  {f.name}: {size_mb:.2f} MB")


if __name__ == "__main__":
    main()
