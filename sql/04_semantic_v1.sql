/*=============================================================================
  04_semantic_v1.sql — CANONICAL BUILD SCRIPT for the initial demo state.

  Creates: Weak FULFILLMENT_SV (v1) + competent SHIPPING_SV
  + 20 verified queries on FULFILLMENT_SV + 2 on SHIPPING_SV as eval ground truth.

  NOTE: This file creates FULFILLMENT_SV as the WEAK v1 definition. Running
  06_semantic_v2.sql later does CREATE OR REPLACE to upgrade it to v2 (0.650).
  The frozen v1 snapshot lives in 04b_semantic_v1_frozen.sql as FULFILLMENT_SV_V1
  (a separate object) for live baseline re-runs. Do NOT run both expecting the
  same object — they target different names.

  REBALANCED (Step F2): Fixed composition from BASELINE_V1_R4 (0.34 → target 0.45-0.65):
    1. MOVED 2 carrier-specific VQs (on-time UPS, late by carrier) to SHIPPING_SV
       where carrier data is properly described with metrics
    2. REDUCED tenant-name trap from 5 to 2 questions (Alderwood, Bellweather) — the rest now
       filter by warehouse or use no tenant filter
    3. REBALANCED: 3 VQs per trap × 6 traps + 2 easy general = 20 total
    4. FIXED ground truth SQL to use TENANT_ID='T001'/'T002' (actual data values)
       instead of business names that don't exist in the column

  The FULFILLMENT_SV is intentionally weak:
    - Vague column descriptions (e.g. "Date" for all timestamp columns)
    - No metrics defined
    - No filters defined
    - No custom instructions (no fiscal calendar guidance, no fill-rate definitions)
    - ZONE_RATE_CARDS table omitted entirely (cost queries impossible)

  The SHIPPING_SV is competent for carrier on-time and late-shipment questions
  (clear descriptions, metrics, CARRIER_SCANS joined to SHIPMENTS), so multi-view
  routing by the agent is meaningful. Note it declares ZONE_RATE_CARDS but has no
  cost verified query, so it declines cost questions: cost lives on v2.

  Verified queries use ABSOLUTE dates only (no "last month") to prevent score
  drift between runs. VQs reference logical table aliases, not physical paths.

  Eval config YAML uploaded uncompressed to @EVAL.CONFIGS.
=============================================================================*/

USE ROLE ACCOUNTADMIN;
USE WAREHOUSE AGENT_EVAL_DEMO_WH;
USE DATABASE AGENT_EVAL_DEMO;

------------------------------------------------------------
-- 1. WEAK FULFILLMENT_SV (v1) with 20 rebalanced verified queries
--    Key weaknesses:
--    - All date columns described as just "Date" (on-time trap)
--    - QTY columns described as just "Quantity" (units trap)
--    - No metrics (fill rate trap - no defined formula)
--    - No custom instructions (fiscal calendar trap)
--    - ZONE_RATE_CARDS not included (cost trap)
------------------------------------------------------------
CREATE OR REPLACE SEMANTIC VIEW AI.FULFILLMENT_SV
  TABLES (
    ORDERS as AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDERS primary key (ORDER_ID) comment='Orders table',
    ORDER_LINES as AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDER_LINES primary key (ORDER_LINE_ID) comment='Order line items',
    WAVES as AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.WAVES primary key (WAVE_ID) comment='Picking waves',
    EXCEPTIONS as AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.EXCEPTIONS primary key (EXCEPTION_ID) comment='Exceptions',
    FISCAL_CALENDAR as AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.FISCAL_CALENDAR_445 primary key (CALENDAR_DATE) comment='Calendar',
    ITEM_MASTER as AGENT_EVAL_DEMO.INVENTORY_INTELLIGENCE.ITEM_MASTER primary key (SKU) comment='Products',
    MOVEMENTS as AGENT_EVAL_DEMO.INVENTORY_INTELLIGENCE.MOVEMENTS primary key (MOVEMENT_ID) comment='Inventory movements',
    SHIPMENTS as AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.SHIPMENTS primary key (SHIPMENT_ID) comment='Shipments',
    -- Must be declared even though this is the WEAK v1 view: 3 of the 20 verified
    -- queries below join ZONE_RATE_CARDS by its bare logical name, and bare names
    -- resolve against the logical model. Omitting the table does not just make cost
    -- unanswerable, it makes the whole model fail to load with
    -- "Object 'ZONE_RATE_CARDS' does not exist or not authorized".
    -- v1 stays weak via vague comments, no relationship and no cost metric.
    ZONE_RATE_CARDS as AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.ZONE_RATE_CARDS comment='Rate cards'
  )
  RELATIONSHIPS (
    LINES_TO_ORDERS as ORDER_LINES(ORDER_ID) references ORDERS(ORDER_ID),
    LINES_TO_WAVES as ORDER_LINES(WAVE_ID) references WAVES(WAVE_ID),
    EXCEPTIONS_TO_ORDERS as EXCEPTIONS(ORDER_ID) references ORDERS(ORDER_ID),
    LINES_TO_ITEMS as ORDER_LINES(SKU) references ITEM_MASTER(SKU),
    MOVEMENTS_TO_ITEMS as MOVEMENTS(SKU) references ITEM_MASTER(SKU),
    SHIPMENTS_TO_ORDERS as SHIPMENTS(ORDER_ID) references ORDERS(ORDER_ID)
    -- NOTE: deliberately NO relationship to ZONE_RATE_CARDS. The table is declared
    -- (it has to be), but with no relationship, no cost metric and vague comments,
    -- so v1 has no guidance for cost questions. A relationship could not express
    -- this join anyway: it needs SHIP_DATE BETWEEN EFFECTIVE_DATE AND EXPIRY_DATE,
    -- and relationships are equality-only.
  )
  FACTS (
    ORDERS.TOTAL_LINES as TOTAL_LINES comment='Lines count',
    ORDERS.LINES_FILLED as LINES_FILLED comment='Lines filled',
    ORDER_LINES.QTY_ORDERED_EACHES as QTY_ORDERED_EACHES comment='Quantity',
    ORDER_LINES.QTY_SHIPPED_EACHES as QTY_SHIPPED_EACHES comment='Quantity',
    ORDER_LINES.QTY_CARTONS as QTY_CARTONS comment='Quantity',
    ORDER_LINES.UNIT_PRICE as UNIT_PRICE comment='Price',
    ITEM_MASTER.UNIT_WEIGHT_LB as UNIT_WEIGHT_LB comment='Weight',
    ITEM_MASTER.EACHES_PER_CARTON as EACHES_PER_CARTON comment='Pack size',
    ITEM_MASTER.UNIT_COST as UNIT_COST comment='Cost',
    MOVEMENTS.QTY_EACHES as QTY_EACHES comment='Quantity',
    SHIPMENTS.TOTAL_WEIGHT_LB as TOTAL_WEIGHT_LB comment='Weight',
    SHIPMENTS.PACKAGE_COUNT as PACKAGE_COUNT comment='Package count',
    ZONE_RATE_CARDS.RATE_PER_PACKAGE as RATE_PER_PACKAGE comment='Rate',
    ZONE_RATE_CARDS.FUEL_SURCHARGE_PCT as FUEL_SURCHARGE_PCT comment='Pct'
  )
  DIMENSIONS (
    ORDERS.ORDER_ID as ORDER_ID comment='ID',
    ORDERS.TENANT_ID as TENANT_ID comment='Tenant',
    ORDERS.ORDER_DATE as ORDER_DATE comment='Date',
    ORDERS.SHIP_BY_DATE as SHIP_BY_DATE comment='Date',
    ORDERS.PROMISED_DELIVERY_DATE as PROMISED_DELIVERY_DATE comment='Date',
    ORDERS.WAREHOUSE_ID as WAREHOUSE_ID comment='Warehouse',
    ORDERS.PRIORITY as PRIORITY comment='Priority',
    ORDERS.STATUS as STATUS comment='Status',
    ORDERS.CHANNEL as CHANNEL comment='Channel',
    ORDER_LINES.ORDER_LINE_ID as ORDER_LINE_ID comment='ID',
    ORDER_LINES.ORDER_ID as ORDER_ID comment='Order ref',
    ORDER_LINES.SKU as SKU comment='Product code',
    ORDER_LINES.LINE_STATUS as LINE_STATUS comment='Status',
    ORDER_LINES.WAVE_ID as WAVE_ID comment='Wave ref',
    WAVES.WAVE_ID as WAVE_ID comment='ID',
    WAVES.WAVE_DATE as WAVE_DATE comment='Date',
    WAVES.CUTOFF_TIME as CUTOFF_TIME comment='Time',
    EXCEPTIONS.EXCEPTION_ID as EXCEPTION_ID comment='ID',
    EXCEPTIONS.ORDER_ID as ORDER_ID comment='Order ref',
    EXCEPTIONS.TENANT_ID as TENANT_ID comment='Tenant',
    EXCEPTIONS.EXCEPTION_TYPE as EXCEPTION_TYPE comment='Type',
    EXCEPTIONS.EXCEPTION_DATE as EXCEPTION_DATE comment='Date',
    EXCEPTIONS.RESOLUTION as RESOLUTION comment='Resolution',
    FISCAL_CALENDAR.CALENDAR_DATE as CALENDAR_DATE comment='Date',
    FISCAL_CALENDAR.FISCAL_YEAR as FISCAL_YEAR comment='Year',
    FISCAL_CALENDAR.FISCAL_QUARTER as FISCAL_QUARTER comment='Quarter',
    FISCAL_CALENDAR.FISCAL_PERIOD as FISCAL_PERIOD comment='Period',
    FISCAL_CALENDAR.FISCAL_WEEK as FISCAL_WEEK comment='Week',
    ITEM_MASTER.SKU as SKU comment='Product code',
    ITEM_MASTER.DESCRIPTION as DESCRIPTION comment='Description',
    ITEM_MASTER.CATEGORY as CATEGORY comment='Category',
    ITEM_MASTER.SUBCATEGORY as SUBCATEGORY comment='Subcategory',
    MOVEMENTS.MOVEMENT_ID as MOVEMENT_ID comment='ID',
    MOVEMENTS.SKU as SKU comment='Product',
    MOVEMENTS.MOVEMENT_TYPE as MOVEMENT_TYPE comment='Type',
    MOVEMENTS.MOVEMENT_DATE as MOVEMENT_DATE comment='Date',
    SHIPMENTS.SHIPMENT_ID as SHIPMENT_ID comment='ID',
    SHIPMENTS.ORDER_ID as ORDER_ID comment='Order ref',
    SHIPMENTS.TENANT_ID as TENANT_ID comment='Tenant',
    SHIPMENTS.CARRIER as CARRIER comment='Carrier',
    SHIPMENTS.ZONE as ZONE comment='Zone',
    SHIPMENTS.WEIGHT_BREAK as WEIGHT_BREAK comment='Weight category',
    SHIPMENTS.SHIP_DATE as SHIP_DATE comment='Date',
    SHIPMENTS.CARRIER_FIRST_SCAN_TS as CARRIER_FIRST_SCAN_TS comment='Date',
    -- REQUIRED. Three verified queries reference s.SHIP_BY_DATE (s = SHIPMENTS).
    -- Verified query SQL resolves against the LOGICAL model, so omitting this
    -- makes the whole view invalid even though the physical column exists:
    --   "Invalid semantic model yaml ... invalid identifier 'S.SHIP_BY_DATE'"
    -- Comment left vague on purpose -- this is the WEAK v1 baseline.
    SHIPMENTS.SHIP_BY_DATE as SHIP_BY_DATE comment='Date',
    SHIPMENTS.WAREHOUSE_ID as WAREHOUSE_ID comment='Warehouse',
    SHIPMENTS.TRACKING_NUMBER as TRACKING_NUMBER comment='Tracking',
    -- Needed by the 3 cost verified queries. Vague comments on purpose.
    ZONE_RATE_CARDS.CARRIER as CARRIER comment='Carrier',
    ZONE_RATE_CARDS.ZONE as ZONE comment='Zone',
    ZONE_RATE_CARDS.WEIGHT_BREAK as WEIGHT_BREAK comment='Weight category',
    ZONE_RATE_CARDS.EFFECTIVE_DATE as EFFECTIVE_DATE comment='Date',
    ZONE_RATE_CARDS.EXPIRY_DATE as EXPIRY_DATE comment='Date'
  )
  COMMENT='Fulfillment intelligence - v1 baseline (pre-optimization)'
  AI_VERIFIED_QUERIES (
    -- TRAP 1: On-time shipping (3 VQs)
    -- Correct definition: CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE
    -- v1 describes all 3 date columns as just "Date" → model picks wrong comparison
    VQ_ON_TIME_ALL AS (
      QUESTION 'What is the on-time shipping rate for all tenants between 2025-06-01 and 2025-12-31?'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION TRUE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT COUNT(CASE WHEN s.CARRIER_FIRST_SCAN_TS <= s.SHIP_BY_DATE THEN 1 END)::FLOAT / NULLIF(COUNT(*), 0) AS on_time_rate FROM SHIPMENTS s WHERE s.SHIP_DATE BETWEEN ''2025-06-01'' AND ''2025-12-31'''
    ),
    VQ_ON_TIME_BY_WAREHOUSE AS (
      QUESTION 'Show the on-time shipping rate by warehouse for shipments between 2025-03-01 and 2025-09-30.'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION FALSE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT s.WAREHOUSE_ID, COUNT(CASE WHEN s.CARRIER_FIRST_SCAN_TS <= s.SHIP_BY_DATE THEN 1 END)::FLOAT / NULLIF(COUNT(*), 0) AS on_time_rate FROM SHIPMENTS s WHERE s.SHIP_DATE BETWEEN ''2025-03-01'' AND ''2025-09-30'' GROUP BY s.WAREHOUSE_ID ORDER BY on_time_rate'
    ),
    VQ_LATE_SHIPMENTS_COUNT AS (
      QUESTION 'How many shipments missed their ship-by date between 2025-06-01 and 2025-12-31?'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION FALSE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT COUNT(*) AS late_shipments FROM SHIPMENTS s WHERE s.CARRIER_FIRST_SCAN_TS > s.SHIP_BY_DATE AND s.SHIP_DATE BETWEEN ''2025-06-01'' AND ''2025-12-31'''
    ),
    -- TRAP 2: Fill rate (3 VQs)
    -- order fill vs line fill vs unit fill — three different answers, no metrics defined
    VQ_ORDER_FILL_RATE AS (
      QUESTION 'What is the order fill rate for tenant Alderwood between 2025-04-01 and 2025-10-31?'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION TRUE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT COUNT(CASE WHEN o.LINES_FILLED = o.TOTAL_LINES THEN 1 END)::FLOAT / NULLIF(COUNT(*), 0) AS order_fill_rate FROM ORDERS o WHERE o.TENANT_ID = ''T001'' AND o.ORDER_DATE BETWEEN ''2025-04-01'' AND ''2025-10-31'''
    ),
    VQ_LINE_FILL_RATE AS (
      QUESTION 'What is the line fill rate across all tenants for orders placed between 2025-01-26 and 2025-12-31?'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION FALSE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT SUM(o.LINES_FILLED)::FLOAT / NULLIF(SUM(o.TOTAL_LINES), 0) AS line_fill_rate FROM ORDERS o WHERE o.ORDER_DATE BETWEEN ''2025-01-26'' AND ''2025-12-31'''
    ),
    VQ_UNIT_FILL_RATE AS (
      QUESTION 'What is the unit fill rate (eaches shipped divided by eaches ordered) for orders between 2025-01-26 and 2025-12-31?'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION FALSE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT SUM(ol.QTY_SHIPPED_EACHES)::FLOAT / NULLIF(SUM(ol.QTY_ORDERED_EACHES), 0) AS unit_fill_rate FROM ORDER_LINES ol JOIN ORDERS o ON ol.ORDER_ID = o.ORDER_ID WHERE o.ORDER_DATE BETWEEN ''2025-01-26'' AND ''2025-12-31'''
    ),
    -- TRAP 3: Units ambiguity (3 VQs)
    -- eaches vs cartons vs lines — all described as "Quantity" in v1
    VQ_TOTAL_UNITS_SHIPPED AS (
      QUESTION 'How many total units were shipped between 2025-03-01 and 2025-09-30?'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION FALSE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT SUM(ol.QTY_SHIPPED_EACHES) AS total_units_shipped FROM ORDER_LINES ol JOIN ORDERS o ON ol.ORDER_ID = o.ORDER_ID WHERE o.ORDER_DATE BETWEEN ''2025-03-01'' AND ''2025-09-30'''
    ),
    VQ_CARTONS_BY_WAREHOUSE AS (
      QUESTION 'How many cartons were shipped from warehouse ATL-DC1 between 2025-06-01 and 2025-12-31?'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION FALSE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT SUM(ol.QTY_CARTONS) AS total_cartons FROM ORDER_LINES ol JOIN ORDERS o ON ol.ORDER_ID = o.ORDER_ID WHERE o.WAREHOUSE_ID = ''ATL-DC1'' AND o.ORDER_DATE BETWEEN ''2025-06-01'' AND ''2025-12-31'''
    ),
    VQ_ORDER_LINES_COUNT AS (
      QUESTION 'How many distinct order lines were there between 2025-02-01 and 2025-08-31?'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION FALSE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT COUNT(*) AS total_order_lines FROM ORDER_LINES ol JOIN ORDERS o ON ol.ORDER_ID = o.ORDER_ID WHERE o.ORDER_DATE BETWEEN ''2025-02-01'' AND ''2025-08-31'''
    ),
    -- TRAP 4: Cost per shipment (3 VQs)
    -- Requires ZONE_RATE_CARDS join — MISSING from v1, so ALL cost queries fail
    VQ_AVG_COST_BY_CARRIER AS (
      QUESTION 'What is the average shipping cost per shipment by carrier between 2025-06-01 and 2025-12-31?'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION FALSE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT s.CARRIER, AVG(zrc.RATE_PER_PACKAGE * (1 + zrc.FUEL_SURCHARGE_PCT / 100.0) * s.PACKAGE_COUNT) AS avg_cost FROM SHIPMENTS s JOIN ZONE_RATE_CARDS zrc ON s.CARRIER = zrc.CARRIER AND s.ZONE = zrc.ZONE AND s.WEIGHT_BREAK = zrc.WEIGHT_BREAK AND s.SHIP_DATE::DATE BETWEEN zrc.EFFECTIVE_DATE AND zrc.EXPIRY_DATE WHERE s.SHIP_DATE BETWEEN ''2025-06-01'' AND ''2025-12-31'' GROUP BY s.CARRIER ORDER BY avg_cost DESC'
    ),
    VQ_TOTAL_COST_BY_ZONE AS (
      QUESTION 'What is the total shipping cost by zone between 2025-06-01 and 2025-12-31?'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION FALSE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT s.ZONE, SUM(zrc.RATE_PER_PACKAGE * (1 + zrc.FUEL_SURCHARGE_PCT / 100.0) * s.PACKAGE_COUNT) AS total_cost FROM SHIPMENTS s JOIN ZONE_RATE_CARDS zrc ON s.CARRIER = zrc.CARRIER AND s.ZONE = zrc.ZONE AND s.WEIGHT_BREAK = zrc.WEIGHT_BREAK AND s.SHIP_DATE::DATE BETWEEN zrc.EFFECTIVE_DATE AND zrc.EXPIRY_DATE WHERE s.SHIP_DATE BETWEEN ''2025-06-01'' AND ''2025-12-31'' GROUP BY s.ZONE ORDER BY s.ZONE'
    ),
    VQ_HIGHEST_COST_CARRIER AS (
      QUESTION 'Which carrier has the highest average cost per shipment between 2025-06-01 and 2025-12-31?'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION FALSE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT s.CARRIER, AVG(zrc.RATE_PER_PACKAGE * (1 + zrc.FUEL_SURCHARGE_PCT / 100.0) * s.PACKAGE_COUNT) AS avg_cost FROM SHIPMENTS s JOIN ZONE_RATE_CARDS zrc ON s.CARRIER = zrc.CARRIER AND s.ZONE = zrc.ZONE AND s.WEIGHT_BREAK = zrc.WEIGHT_BREAK AND s.SHIP_DATE::DATE BETWEEN zrc.EFFECTIVE_DATE AND zrc.EXPIRY_DATE WHERE s.SHIP_DATE BETWEEN ''2025-06-01'' AND ''2025-12-31'' GROUP BY s.CARRIER ORDER BY avg_cost DESC LIMIT 1'
    ),
    -- TRAP 5: 4-4-5 fiscal calendar (3 VQs)
    -- No custom instruction explaining fiscal vs calendar month difference
    VQ_FISCAL_LOOKUP AS (
      QUESTION 'What fiscal period and fiscal year does the date 2025-08-15 fall in?'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION FALSE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT FISCAL_YEAR, FISCAL_PERIOD, FISCAL_QUARTER, FISCAL_WEEK FROM FISCAL_CALENDAR WHERE CALENDAR_DATE = ''2025-08-15'''
    ),
    VQ_ORDERS_IN_FISCAL_PERIOD AS (
      QUESTION 'How many orders were placed in fiscal period 7 of fiscal year 2025?'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION FALSE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT COUNT(*) AS order_count FROM ORDERS o JOIN FISCAL_CALENDAR fc ON o.ORDER_DATE::DATE = fc.CALENDAR_DATE WHERE fc.FISCAL_YEAR = 2025 AND fc.FISCAL_PERIOD = 7'
    ),
    VQ_FISCAL_PERIOD_REVENUE AS (
      QUESTION 'What is the total revenue in fiscal period 8 of fiscal year 2025?'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION FALSE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT SUM(ol.QTY_SHIPPED_EACHES * ol.UNIT_PRICE) AS revenue FROM ORDER_LINES ol JOIN ORDERS o ON ol.ORDER_ID = o.ORDER_ID JOIN FISCAL_CALENDAR fc ON o.ORDER_DATE::DATE = fc.CALENDAR_DATE WHERE fc.FISCAL_YEAR = 2025 AND fc.FISCAL_PERIOD = 8'
    ),
    -- TRAP 6: Active SKU (3 VQs)
    -- "Active" means movement in trailing 30 days, not merely existing in item_master
    VQ_ACTIVE_SKU_COUNT AS (
      QUESTION 'How many active SKUs are there as of December 2025?'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION FALSE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT COUNT(DISTINCT m.SKU) AS active_sku_count FROM MOVEMENTS m WHERE m.MOVEMENT_DATE BETWEEN ''2025-12-01'' AND ''2025-12-31'''
    ),
    VQ_ACTIVE_SKU_LIST AS (
      QUESTION 'Which SKUs had inventory movement in the 30 days ending 2025-12-31?'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION FALSE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT DISTINCT m.SKU FROM MOVEMENTS m WHERE m.MOVEMENT_DATE BETWEEN ''2025-12-01'' AND ''2025-12-31'' ORDER BY m.SKU'
    ),
    VQ_INACTIVE_SKU_COUNT AS (
      QUESTION 'How many SKUs in the item master had zero inventory movement in the 30 days ending 2025-12-31?'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION FALSE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT COUNT(*) AS inactive_skus FROM ITEM_MASTER im WHERE NOT EXISTS (SELECT 1 FROM MOVEMENTS m WHERE m.SKU = im.SKU AND m.MOVEMENT_DATE BETWEEN ''2025-12-01'' AND ''2025-12-31'')'
    ),
    -- General / easy (2 VQs) — these should pass reliably as control group
    VQ_ORDERS_BY_WH AS (
      QUESTION 'How many orders per warehouse were placed between 2025-06-01 and 2025-09-30?'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION FALSE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT WAREHOUSE_ID, COUNT(*) AS order_count FROM ORDERS WHERE ORDER_DATE BETWEEN ''2025-06-01'' AND ''2025-09-30'' GROUP BY WAREHOUSE_ID ORDER BY order_count DESC'
    ),
    VQ_EXCEPTIONS_BY_TYPE AS (
      QUESTION 'What is the total number of exceptions by type for tenant Bellweather between 2025-05-01 and 2025-11-30?'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION FALSE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT EXCEPTION_TYPE, COUNT(*) AS exception_count FROM EXCEPTIONS WHERE TENANT_ID = ''T002'' AND EXCEPTION_DATE BETWEEN ''2025-05-01'' AND ''2025-11-30'' GROUP BY EXCEPTION_TYPE ORDER BY exception_count DESC'
    )
  )
;

------------------------------------------------------------
-- 2. COMPETENT SHIPPING_SV
--    Clear descriptions, metrics, proper zone_rate_cards join
------------------------------------------------------------
CREATE OR REPLACE SEMANTIC VIEW AI.SHIPPING_SV
  TABLES (
    SHIPMENTS as AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.SHIPMENTS primary key (SHIPMENT_ID) comment='Outbound shipments with carrier details and delivery timing. Each shipment fulfills one order. CARRIER_FIRST_SCAN_TS is when the carrier physically accepted the package.',
    CARRIER_SCANS as AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.CARRIER_SCANS primary key (SCAN_ID) comment='Carrier scan events tracking package movement through the delivery network (pickup, transit, delivery, exception).',
    ZONE_RATE_CARDS as AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.ZONE_RATE_CARDS comment='Carrier rate cards defining cost per package by carrier, zone, and weight tier. Join to SHIPMENTS on (CARRIER, ZONE, WEIGHT_BREAK) with effective date range. Total cost = RATE_PER_PACKAGE * (1 + FUEL_SURCHARGE_PCT/100) * PACKAGE_COUNT.'
  )
  RELATIONSHIPS (
    SCANS_TO_SHIPMENTS as CARRIER_SCANS(SHIPMENT_ID) references SHIPMENTS(SHIPMENT_ID)
  )
  FACTS (
    SHIPMENTS.TOTAL_WEIGHT_LB as TOTAL_WEIGHT_LB comment='Actual total shipment weight in pounds',
    SHIPMENTS.PACKAGE_COUNT as PACKAGE_COUNT comment='Number of packages/parcels in this shipment',
    SHIPMENTS.SHIPMENT_RECORD as 1 comment='Record counter for shipment aggregations',
    ZONE_RATE_CARDS.RATE_PER_PACKAGE as RATE_PER_PACKAGE comment='Base rate per package in USD for this carrier/zone/weight combination',
    ZONE_RATE_CARDS.FUEL_SURCHARGE_PCT as FUEL_SURCHARGE_PCT comment='Fuel surcharge as a percentage added to the base rate'
  )
  DIMENSIONS (
    SHIPMENTS.SHIPMENT_ID as SHIPMENT_ID comment='Unique shipment identifier',
    SHIPMENTS.ORDER_ID as ORDER_ID comment='Source order this shipment fulfills',
    SHIPMENTS.TENANT_ID as TENANT_ID comment='Tenant (3PL client) identifier',
    SHIPMENTS.CARRIER as CARRIER comment='Carrier code: USPS, UPS, FEDX, DHL',
    SHIPMENTS.ZONE as ZONE comment='Carrier shipping zone (1-8). Higher zone = longer distance = higher cost.',
    SHIPMENTS.WEIGHT_BREAK as WEIGHT_BREAK comment='Weight tier for rate lookup: LT1LB, 1-5LB, 5-20LB, 20-50LB, GT50LB',
    SHIPMENTS.SHIP_DATE as SHIP_DATE comment='Date shipment was created/manifested in the WMS',
    SHIPMENTS.CARRIER_FIRST_SCAN_TS as CARRIER_FIRST_SCAN_TS comment='Timestamp when carrier first scanned the package (actual tender to carrier). On-time = CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE.',
    SHIPMENTS.SHIP_BY_DATE as SHIP_BY_DATE comment='Customer SLA deadline: package must be tendered to carrier by this date. This is the on-time shipping definition.',
    SHIPMENTS.PROMISED_DELIVERY_DATE as PROMISED_DELIVERY_DATE comment='Carrier-estimated delivery date shown to end consumer. NOT the on-time SLA.',
    SHIPMENTS.WAREHOUSE_ID as WAREHOUSE_ID comment='Originating fulfillment center identifier',
    SHIPMENTS.TRACKING_NUMBER as TRACKING_NUMBER comment='Carrier tracking number',
    -- SCAN_ID is the primary key of CARRIER_SCANS, so it MUST also be declared
    -- as a logical column. CREATE SEMANTIC VIEW accepts it either way, but
    -- Cortex Analyst rejects the ENTIRE view with error 392700 the moment an
    -- agent routes a carrier question here. This was a real latent defect:
    -- it was fixed on the live object first, and omitting it here would have
    -- silently reintroduced the bug on any fresh rebuild.
    CARRIER_SCANS.SHIPMENT_ID as SHIPMENT_ID comment='FK to SHIPMENTS. Declared as a logical column because it is used in a relationship. Harmless, and removes any risk of the 392700 class of rejection.',
    CARRIER_SCANS.SCAN_ID as SCAN_ID comment='Unique scan event id (primary key).',
    CARRIER_SCANS.SCAN_TYPE as SCAN_TYPE comment='Scan event type: PICKUP, IN_TRANSIT, OUT_FOR_DELIVERY, DELIVERED, EXCEPTION, RETURN_TO_SENDER',
    CARRIER_SCANS.SCAN_TIMESTAMP as SCAN_TIMESTAMP comment='When the scan occurred',
    CARRIER_SCANS.LOCATION as LOCATION comment='Facility or city where the scan occurred',
    ZONE_RATE_CARDS.EFFECTIVE_DATE as EFFECTIVE_DATE comment='Rate card effective start date',
    ZONE_RATE_CARDS.EXPIRY_DATE as EXPIRY_DATE comment='Rate card expiration date'
  )
  METRICS (
    SHIPMENTS.ON_TIME_SHIP_RATE as COUNT(CASE WHEN CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE THEN 1 END) / NULLIF(COUNT(SHIPMENT_RECORD), 0) comment='Percentage of shipments tendered to carrier on or before the SLA (SHIP_BY_DATE). On-time = CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE.',
    SHIPMENTS.TOTAL_SHIPMENTS as COUNT(SHIPMENT_RECORD) comment='Total number of shipments',
    SHIPMENTS.LATE_SHIPMENTS as COUNT(CASE WHEN CARRIER_FIRST_SCAN_TS > SHIP_BY_DATE THEN 1 END) comment='Number of shipments that missed the SLA deadline'
  )
  COMMENT='Shipping intelligence with carrier performance, on-time delivery, and cost analytics. Use SHIP_BY_DATE for on-time SLA calculations, not PROMISED_DELIVERY_DATE.'
  AI_VERIFIED_QUERIES (
    -- Moved from FULFILLMENT_SV: carrier-specific queries belong here where
    -- carrier dimensions and on-time metrics are properly described
    VQ_ON_TIME_UPS AS (
      QUESTION 'What is the on-time rate for UPS shipments between 2025-07-01 and 2025-12-31?'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION FALSE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT COUNT(CASE WHEN CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE THEN 1 END)::FLOAT / NULLIF(COUNT(*), 0) AS ups_on_time_rate FROM SHIPMENTS WHERE CARRIER = ''UPS'' AND SHIP_DATE BETWEEN ''2025-07-01'' AND ''2025-12-31'''
    ),
    VQ_LATE_BY_CARRIER AS (
      QUESTION 'How many shipments were late (missed the SLA) by carrier between 2025-06-01 and 2025-12-31?'
      VERIFIED_AT 1723488000
      ONBOARDING_QUESTION FALSE
      VERIFIED_BY '( STEWARD = data_engineering )'
      SQL 'SELECT CARRIER, COUNT(*) AS late_shipments FROM SHIPMENTS WHERE CARRIER_FIRST_SCAN_TS > SHIP_BY_DATE AND SHIP_DATE BETWEEN ''2025-06-01'' AND ''2025-12-31'' GROUP BY CARRIER ORDER BY late_shipments DESC'
    )
  )
;

------------------------------------------------------------
-- 3. GROUND TRUTH TABLE (backup / documentation)
--    Maps each verified query to its trap category
------------------------------------------------------------
CREATE TABLE IF NOT EXISTS EVAL.ANALYST_GROUND_TRUTH (
    QUESTION VARCHAR(2000) NOT NULL,
    SQL_ANSWER VARCHAR(16000) NOT NULL,
    TRAP_CATEGORY VARCHAR(50),
    COMPLEXITY VARCHAR(20)
);

------------------------------------------------------------
-- 4. EVALUATION CONFIG YAML → @EVAL.CONFIGS (uncompressed)
--    Upload via:
--      PUT 'file:///tmp/analyst_evaluation_config.yaml'
--          @AGENT_EVAL_DEMO.EVAL.CONFIGS
--          AUTO_COMPRESS=FALSE OVERWRITE=TRUE
--
--    YAML content:
--      analyst_params:
--        analyst_name: "AGENT_EVAL_DEMO.AI.FULFILLMENT_SV"
--        analyst_type: "SEMANTIC VIEW"
--      source_metadata:
--        type: "verified_queries"
--      metrics:
--        - "sql_correctness"
------------------------------------------------------------

-- Verify objects
SHOW SEMANTIC VIEWS IN SCHEMA AI;
LIST @EVAL.CONFIGS;
