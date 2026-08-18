/*=============================================================================
  06_semantic_v2.sql — OPTIMIZED v2: AGENT_EVAL_DEMO.AI.FULFILLMENT_SV

  This file previously contained only a PLACEHOLDER COMMENT instead of the DDL,
  meaning the optimized view existed solely as a live object and would have been
  lost on any teardown. The full CREATE OR REPLACE is now captured below via
  GET_DDL so v2 is genuinely reproducible from source.

  MEASURED RESULT (authoritative, verified on 20 IDENTICAL verified queries):
    BASELINE_V1_FINAL   sql_correctness = 0.450  (on FULFILLMENT_SV_V1; band 0.40-0.45)
    OPTIMIZED_V2_FINAL  sql_correctness = 0.700  (on FULFILLMENT_SV)
    => +55.6%; 7 improved, 0 regressed, 13 flat. All post-fix runs scored 0.700.
  Do NOT quote the earlier 0.3421 -> 0.5263 / "+53.8%" figures: those were
  computed across two DIFFERENT question sets and are not comparable.

  What v2 adds over v1 (the eval told us which of these mattered):
    - explicit on-time metric anchored on CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE
    - all three fill-rate definitions as named metrics (order/line/unit)
    - the ZONE_RATE_CARDS table plus 3 cost verified queries, which teach the
      composite rate-card join.
    - TENANT_NAME so business names resolve to TENANT_ID.

  JOIN-PATH DEFECT -- FOUND BY THE EVAL, NOW FIXED (2026-08-17):
  TENANT_MAP and ZONE_RATE_CARDS were declared with NO relationship, so Cortex
  Analyst planned joins it could not compile and returned HTTP 500. Measured on the
  broken model: 14 of 100 attempts (5 runs x 20 questions) failed that way and
  scored a hard 0.0, and 2 were questions the WEAK v1 answered correctly -- v2 was
  actively worse than v1 on those.
  A declared table with no join path is worse than an absent table: it fails at
  plan time instead of being ignored.

  HOW IT WAS FIXED, and the trap in fixing it:
  Adding all three candidate relationships at once (ORDERS_TO_TENANT +
  SHIPMENTS_TO_TENANT + SHIPMENTS_TO_RATE_CARDS) made things WORSE -- the whole
  eval died with STATUS = FAILED / "Invocation failed", because SHIPMENTS then had
  TWO routes to TENANT_MAP (direct, and via ORDERS). Interactive Analyst queries
  still worked, so ONLY the eval exposed it.
  The fix is ONE route per table, added and measured one at a time:
    step 1  ORDERS_TO_TENANT only        -> eval COMPLETED, 500s 3 -> 1, score 0.575
    step 2  + SHIPMENTS_TO_RATE_CARDS    -> eval COMPLETED, 500s 1 -> 0, score 0.700
  Result: 0.450 -> 0.700 (+55.6%), 7 improved, 0 regressed, 13 flat, and all three
  post-fix runs scored exactly 0.700 -- removing the 500s also removed most of the
  run-to-run variance.
  If you add another relationship: add ONE, re-run the eval, check STATUS is
  COMPLETED and that join-path 500s stayed at zero.
    - custom instructions incl. the 4-4-5 retail fiscal calendar
=============================================================================*/

USE ROLE ACCOUNTADMIN;
USE DATABASE AGENT_EVAL_DEMO;
USE SCHEMA AI;
USE WAREHOUSE AGENT_EVAL_DEMO_WH;

create or replace semantic view FULFILLMENT_SV
	tables (
		AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDERS primary key (ORDER_ID) with synonyms=('sales orders','customer orders') comment='Customer fulfillment orders. One row per order. Contains SLA dates and fill metrics. ORDER_DATE is when the order was placed. SHIP_BY_DATE is the customer SLA deadline for carrier tender.',
		AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDER_LINES primary key (ORDER_LINE_ID) comment='Individual line items within orders. Each line has a SKU, quantity ordered in eaches, quantity shipped, and carton count. LINE_STATUS is SHIPPED or SHORT (short-picked, may be partial or zero-fill).',
		AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.WAVES primary key (WAVE_ID) comment='Picking waves that group order lines for warehouse execution. CUTOFF_TIME is the daily cutoff after which orders roll to the next wave.',
		AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.EXCEPTIONS primary key (EXCEPTION_ID) comment='Fulfillment exceptions requiring investigation or resolution (short picks, damage, mispicks, address issues).',
		FISCAL_CALENDAR as AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.FISCAL_CALENDAR_445 primary key (CALENDAR_DATE) comment='4-4-5 retail fiscal calendar. Join on ORDER_DATE::DATE = CALENDAR_DATE to get fiscal periods. Fiscal year starts last Sunday of January. Periods are 4-4-5 week pattern within each quarter. IMPORTANT: when a user says "last month" or "this month", they mean the FISCAL PERIOD (4-4-5), NOT a calendar month. Always join to this table for any time-period-based aggregation.',
		AGENT_EVAL_DEMO.INVENTORY_INTELLIGENCE.ITEM_MASTER primary key (SKU) comment='Product catalog with weights, pack sizes, and costs. EACHES_PER_CARTON defines the conversion factor between eaches and cartons for each SKU.',
		AGENT_EVAL_DEMO.INVENTORY_INTELLIGENCE.MOVEMENTS primary key (MOVEMENT_ID) comment='Inventory movements (receipts, picks, adjustments, transfers). An ACTIVE SKU is one with at least one movement in the trailing 30 days — presence in ITEM_MASTER alone does NOT make a SKU active.',
		AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.SHIPMENTS primary key (SHIPMENT_ID) comment='Outbound shipments. CARRIER_FIRST_SCAN_TS is when the carrier physically accepted the package. On-time shipping = CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE. PROMISED_DELIVERY_DATE is the carrier estimate shown to the customer (NOT the SLA).',
    AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.ZONE_RATE_CARDS primary key (CARRIER,ZONE,WEIGHT_BREAK) comment='Carrier rate cards by (CARRIER, ZONE, WEIGHT_BREAK) with EFFECTIVE_DATE/EXPIRY_DATE validity window. To compute shipping cost: JOIN SHIPMENTS s to ZONE_RATE_CARDS zrc ON s.CARRIER=zrc.CARRIER AND s.ZONE=zrc.ZONE AND s.WEIGHT_BREAK=zrc.WEIGHT_BREAK AND s.SHIP_DATE::DATE BETWEEN zrc.EFFECTIVE_DATE AND zrc.EXPIRY_DATE. Cost per shipment = RATE_PER_PACKAGE * (1 + FUEL_SURCHARGE_PCT/100) * PACKAGE_COUNT.',
		TENANT_MAP as AGENT_EVAL_DEMO.OPS.TENANT_ROLE_MAPPING primary key (TENANT_ID) comment='Maps TENANT_ID codes (T001-T006) to business names. Use this to resolve tenant names in questions: Alderwood Logistics=T001, Bellweather Freight=T002, Cobalt Apparel=T003, Dunmore Distribution=T004, Everline Medical=T005, Foxglove Foods=T006.'
	)
  relationships (
    -- ORDER MATTERS: this block is a GET_DDL capture and Snowflake returns
    -- ORDERS_TO_TENANT first. Keep this order or test_08_repro::test_v2_matches_live
    -- fails on ordering alone, with a diff that looks like missing columns.
    --
    -- ORDERS_TO_TENANT and SHIPMENTS_TO_RATE_CARDS were added 2026-08-17 to fix the
    -- join-path defect the eval found. ONE route per table, deliberately: adding
    -- SHIPMENTS_TO_TENANT as well gives SHIPMENTS two routes to TENANT_MAP (direct
    -- and via ORDERS), and that ambiguity killed the ENTIRE eval run with
    -- STATUS = FAILED / "Invocation failed" -- interactive queries still worked.
    -- Both target keys verified UNIQUE first, so neither can fan out:
    --   TENANT_ROLE_MAPPING   6 rows / 6 distinct TENANT_ID
    --   ZONE_RATE_CARDS     240 rows / 240 distinct (CARRIER, ZONE, WEIGHT_BREAK)
    ORDERS_TO_TENANT as ORDERS(TENANT_ID) references TENANT_MAP(TENANT_ID),
    LINES_TO_ITEMS as ORDER_LINES(SKU) references ITEM_MASTER(SKU),
    LINES_TO_ORDERS as ORDER_LINES(ORDER_ID) references ORDERS(ORDER_ID),
    LINES_TO_WAVES as ORDER_LINES(WAVE_ID) references WAVES(WAVE_ID),
    EXCEPTIONS_TO_ORDERS as EXCEPTIONS(ORDER_ID) references ORDERS(ORDER_ID),
    MOVEMENTS_TO_ITEMS as MOVEMENTS(SKU) references ITEM_MASTER(SKU),
    SHIPMENTS_TO_ORDERS as SHIPMENTS(ORDER_ID) references ORDERS(ORDER_ID),
    SHIPMENTS_TO_RATE_CARDS as SHIPMENTS(CARRIER,ZONE,WEIGHT_BREAK) references ZONE_RATE_CARDS(CARRIER,ZONE,WEIGHT_BREAK)
  )
	facts (
		ORDERS.TOTAL_LINES as TOTAL_LINES comment='Total number of order lines on this order',
		ORDERS.LINES_FILLED as LINES_FILLED comment='Number of lines fully shipped. Order fill rate = COUNT where LINES_FILLED = TOTAL_LINES / COUNT(*)',
		ORDER_LINES.QTY_ORDERED_EACHES as QTY_ORDERED_EACHES comment='Quantity ordered in individual units (eaches). Not cartons.',
		ORDER_LINES.QTY_SHIPPED_EACHES as QTY_SHIPPED_EACHES comment='Quantity actually shipped in eaches. Unit fill rate = SUM(QTY_SHIPPED_EACHES) / SUM(QTY_ORDERED_EACHES)',
		ORDER_LINES.QTY_CARTONS as QTY_CARTONS comment='Number of cartons shipped for this line. One carton = EACHES_PER_CARTON units (varies by SKU, typically 6-24).',
		ORDER_LINES.UNIT_PRICE as UNIT_PRICE comment='Price per each in USD',
		ITEM_MASTER.UNIT_WEIGHT_LB as UNIT_WEIGHT_LB comment='Weight per each in pounds',
		ITEM_MASTER.EACHES_PER_CARTON as EACHES_PER_CARTON comment='Number of eaches per carton for this SKU. Converts between unit types.',
		ITEM_MASTER.UNIT_COST as UNIT_COST comment='Cost per each in USD',
		MOVEMENTS.QTY_EACHES as QTY_EACHES comment='Quantity moved in eaches',
		SHIPMENTS.TOTAL_WEIGHT_LB as TOTAL_WEIGHT_LB comment='Total shipment weight in pounds',
		SHIPMENTS.PACKAGE_COUNT as PACKAGE_COUNT comment='Number of packages in the shipment',
		ZONE_RATE_CARDS.RATE_PER_PACKAGE as RATE_PER_PACKAGE comment='Base shipping rate per package in USD',
		ZONE_RATE_CARDS.FUEL_SURCHARGE_PCT as FUEL_SURCHARGE_PCT comment='Fuel surcharge percentage added to base rate'
	)
	dimensions (
		ORDERS.ORDER_ID as ORDER_ID comment='Unique order identifier',
		ORDERS.TENANT_ID as TENANT_ID comment='Tenant code (T001-T006). Join to TENANT_MAP for business name.',
		ORDERS.ORDER_DATE as ORDER_DATE comment='Date and time the order was placed',
		ORDERS.SHIP_BY_DATE as SHIP_BY_DATE comment='Customer SLA deadline for carrier tender. On-time = carrier scanned by this date.',
		ORDERS.PROMISED_DELIVERY_DATE as PROMISED_DELIVERY_DATE comment='Carrier-estimated delivery date shown to customer. NOT the on-time SLA.',
		ORDERS.WAREHOUSE_ID as WAREHOUSE_ID comment='Fulfillment center handling this order (ATL-DC1, CHI-DC1, DAL-DC1, etc.)',
		ORDERS.PRIORITY as PRIORITY comment='Order priority: STANDARD, EXPRESS, RUSH, SAME_DAY',
		ORDERS.STATUS as STATUS comment='Order status: OPEN, SHIPPED, PARTIAL, CANCELLED',
		ORDERS.CHANNEL as CHANNEL comment='Sales channel: ECOMMERCE, RETAIL, WHOLESALE, MARKETPLACE',
		ORDER_LINES.ORDER_LINE_ID as ORDER_LINE_ID comment='Unique line item identifier',
		ORDER_LINES.ORDER_ID as ORDER_ID comment='Parent order reference',
		ORDER_LINES.SKU as SKU comment='Stock keeping unit - product identifier',
		ORDER_LINES.LINE_STATUS as LINE_STATUS comment='SHIPPED = fully filled, SHORT = short-picked (may be partial fill or zero)',
		ORDER_LINES.WAVE_ID as WAVE_ID comment='Picking wave this line was assigned to',
		WAVES.WAVE_ID as WAVE_ID comment='Wave identifier',
		WAVES.WAVE_DATE as WAVE_DATE comment='Date the wave was released for picking',
		WAVES.CUTOFF_TIME as CUTOFF_TIME comment='Daily cutoff time for wave inclusion (e.g. 14:00)',
		EXCEPTIONS.EXCEPTION_ID as EXCEPTION_ID comment='Exception identifier',
		EXCEPTIONS.ORDER_ID as ORDER_ID comment='Order that generated this exception',
		EXCEPTIONS.TENANT_ID as TENANT_ID comment='Tenant code',
		EXCEPTIONS.EXCEPTION_TYPE as EXCEPTION_TYPE comment='SHORT_PICK, DAMAGE, MISPICK, ADDRESS_ISSUE, CARRIER_REJECT, HAZMAT_HOLD',
		EXCEPTIONS.EXCEPTION_DATE as EXCEPTION_DATE comment='When the exception was raised',
		EXCEPTIONS.RESOLUTION as RESOLUTION comment='RESOLVED, PENDING, CANCELLED',
		FISCAL_CALENDAR.CALENDAR_DATE as CALENDAR_DATE comment='Calendar date (join on ORDER_DATE::DATE)',
		FISCAL_CALENDAR.FISCAL_YEAR as FISCAL_YEAR comment='Fiscal year in 4-4-5 calendar (starts last Sunday of January)',
		FISCAL_CALENDAR.FISCAL_QUARTER as FISCAL_QUARTER comment='Fiscal quarter (1-4)',
		FISCAL_CALENDAR.FISCAL_PERIOD as FISCAL_PERIOD comment='Fiscal period (1-12, 4-4-5 pattern within each quarter)',
		FISCAL_CALENDAR.FISCAL_WEEK as FISCAL_WEEK comment='Fiscal week number within the year',
		ITEM_MASTER.SKU as SKU comment='Product SKU identifier',
		ITEM_MASTER.DESCRIPTION as DESCRIPTION comment='Product description',
		ITEM_MASTER.CATEGORY as CATEGORY comment='Product category',
		ITEM_MASTER.SUBCATEGORY as SUBCATEGORY comment='Product subcategory',
		MOVEMENTS.MOVEMENT_ID as MOVEMENT_ID comment='Movement identifier',
		MOVEMENTS.SKU as SKU comment='Product moved',
		MOVEMENTS.MOVEMENT_TYPE as MOVEMENT_TYPE comment='RECEIPT, PICK, ADJUSTMENT, TRANSFER',
		MOVEMENTS.MOVEMENT_DATE as MOVEMENT_DATE comment='Date of the inventory movement',
		SHIPMENTS.SHIPMENT_ID as SHIPMENT_ID comment='Shipment identifier',
		SHIPMENTS.ORDER_ID as ORDER_ID comment='Order being shipped',
		SHIPMENTS.CARRIER as CARRIER comment='Carrier code: USPS, UPS, FEDX, DHL',
		SHIPMENTS.ZONE as ZONE comment='Shipping zone (1-8). Higher = farther = more expensive.',
		SHIPMENTS.WEIGHT_BREAK as WEIGHT_BREAK comment='Weight tier: LT1LB, 1-5LB, 5-20LB, 20-50LB, GT50LB',
		SHIPMENTS.SHIP_DATE as SHIP_DATE comment='Date shipment was manifested',
    SHIPMENTS.CARRIER_FIRST_SCAN_TS as CARRIER_FIRST_SCAN_TS comment='When carrier physically scanned the package. On-time = this <= SHIP_BY_DATE.',
    -- REQUIRED, not optional. Three verified queries reference s.SHIP_BY_DATE
    -- where s is aliased to SHIPMENTS. Verified query SQL resolves against the
    -- LOGICAL model, not the physical table, so omitting this declaration makes
    -- the entire model invalid even though the physical column exists:
    --   "Invalid semantic model yaml. SQL compilation error:
    --    invalid identifier 'S.SHIP_BY_DATE'"
    -- Note the ON_TIME_RATE metric below also references SHIP_BY_DATE and does
    -- NOT need this -- metric expressions resolve against physical columns. Only
    -- the verified queries go through the logical model. That asymmetry is why
    -- the bug survived: the metric worked, so the view looked fine.
    SHIPMENTS.SHIP_BY_DATE as SHIP_BY_DATE comment='Customer SLA deadline: package must be tendered to the carrier by this date. This is the on-time shipping definition. Do NOT use PROMISED_DELIVERY_DATE.',
    SHIPMENTS.TRACKING_NUMBER as TRACKING_NUMBER comment='Carrier tracking number',
    -- Also REQUIRED: the "on-time shipping rate BY WAREHOUSE" verified query
    -- selects s.WAREHOUSE_ID. Same logical-model resolution rule as SHIP_BY_DATE
    -- above -- undeclared here, the whole v2 model is invalid. v1 declared this
    -- one but not SHIP_BY_DATE; v2 declared neither, so both views were broken
    -- for different reasons and the fix is not symmetric between them.
    SHIPMENTS.WAREHOUSE_ID as WAREHOUSE_ID comment='Warehouse that shipped the order. Use for per-DC on-time comparisons.',
		ZONE_RATE_CARDS.CARRIER as CARRIER comment='Rate card carrier code',
		ZONE_RATE_CARDS.ZONE as ZONE comment='Rate card zone',
		ZONE_RATE_CARDS.WEIGHT_BREAK as WEIGHT_BREAK comment='Rate card weight tier',
		ZONE_RATE_CARDS.EFFECTIVE_DATE as EFFECTIVE_DATE comment='Rate card start date',
		ZONE_RATE_CARDS.EXPIRY_DATE as EXPIRY_DATE comment='Rate card end date',
		TENANT_MAP.TENANT_ID as TENANT_ID comment='Tenant code (joins to ORDERS.TENANT_ID)',
		TENANT_MAP.TENANT_NAME as TENANT_NAME with synonyms=('client','customer name','3PL client') comment='Business name of the tenant. Alderwood Logistics=T001, Bellweather Freight=T002, Cobalt Apparel=T003, Dunmore Distribution=T004, Everline Medical=T005, Foxglove Foods=T006.'
	)
	metrics (
		ORDERS.ORDER_FILL_RATE as COUNT(CASE WHEN LINES_FILLED = TOTAL_LINES THEN 1 END) / NULLIF(COUNT(ORDER_ID), 0) with synonyms=('fill rate','order completion rate') comment='Fraction of orders where ALL lines were fully shipped. Order fill = orders with LINES_FILLED = TOTAL_LINES divided by total orders. This is the DEFAULT fill rate unless "line fill" or "unit fill" is specified.',
		ORDERS.LINE_FILL_RATE as SUM(LINES_FILLED) / NULLIF(SUM(TOTAL_LINES), 0) with synonyms=('line fill rate','line completion rate') comment='Fraction of order LINES that were fully shipped. Line fill = total lines filled / total lines ordered. Use when question says "line fill rate" explicitly.',
		ORDER_LINES.UNIT_FILL_RATE as SUM(QTY_SHIPPED_EACHES) / NULLIF(SUM(QTY_ORDERED_EACHES), 0) with synonyms=('unit fill rate','eaches fill rate') comment='Fraction of UNITS (eaches) shipped vs ordered. Unit fill = total eaches shipped / total eaches ordered. Use when question says "unit fill" or asks about eaches-level fulfillment.',
		SHIPMENTS.ON_TIME_RATE as COUNT(CASE WHEN CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE THEN 1 END) / NULLIF(COUNT(SHIPMENT_ID), 0) with synonyms=('on-time shipping','OTD','on-time delivery') comment='Fraction of shipments tendered to carrier on or before SHIP_BY_DATE. On-time = CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE. Do NOT use PROMISED_DELIVERY_DATE for on-time calculations.'
	)
	comment='Fulfillment intelligence - v2 optimized. CUSTOM INSTRUCTIONS: (1) On-time shipping is defined as CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE. Do NOT use PROMISED_DELIVERY_DATE. (2) Fill rate has THREE definitions: ORDER fill (all lines complete), LINE fill (lines filled / total lines), UNIT fill (eaches shipped / eaches ordered). Default to ORDER fill unless specified. (3) Units: "units" means eaches unless "cartons" is explicitly stated. One carton = EACHES_PER_CARTON eaches (varies by SKU). (4) Active SKU = at least one MOVEMENT in the trailing 30 days. (5) Fiscal calendar: this company uses 4-4-5 retail fiscal calendar. "Last month" means the prior fiscal period, NOT a calendar month. Always join FISCAL_CALENDAR on ORDER_DATE::DATE = CALENDAR_DATE. (6) Shipping cost: JOIN SHIPMENTS to ZONE_RATE_CARDS on (CARRIER, ZONE, WEIGHT_BREAK) with SHIP_DATE between EFFECTIVE_DATE and EXPIRY_DATE. Cost = RATE_PER_PACKAGE * (1 + FUEL_SURCHARGE_PCT/100) * PACKAGE_COUNT.'
	ai_verified_queries (
		VQ_ON_TIME_ALL AS ( 
QUESTION 'What is the on-time shipping rate for all tenants between 2025-06-01 and 2025-12-31?' 
VERIFIED_AT 1786561492
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION true
SQL 'SELECT COUNT(CASE WHEN s.CARRIER_FIRST_SCAN_TS <= s.SHIP_BY_DATE THEN 1 END)::FLOAT / NULLIF(COUNT(*), 0) AS on_time_rate FROM AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.SHIPMENTS s WHERE s.SHIP_DATE BETWEEN ''2025-06-01'' AND ''2025-12-31'''),
		VQ_ON_TIME_BY_WAREHOUSE AS ( 
QUESTION 'Show the on-time shipping rate by warehouse for shipments between 2025-03-01 and 2025-09-30.' 
VERIFIED_AT 1786561492
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT s.WAREHOUSE_ID, COUNT(CASE WHEN s.CARRIER_FIRST_SCAN_TS <= s.SHIP_BY_DATE THEN 1 END)::FLOAT / NULLIF(COUNT(*), 0) AS on_time_rate FROM AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.SHIPMENTS s WHERE s.SHIP_DATE BETWEEN ''2025-03-01'' AND ''2025-09-30'' GROUP BY s.WAREHOUSE_ID ORDER BY on_time_rate'),
		VQ_LATE_SHIPMENTS_COUNT AS ( 
QUESTION 'How many shipments missed their ship-by date between 2025-06-01 and 2025-12-31?' 
VERIFIED_AT 1786561492
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT COUNT(*) AS late_shipments FROM AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.SHIPMENTS s WHERE s.CARRIER_FIRST_SCAN_TS > s.SHIP_BY_DATE AND s.SHIP_DATE BETWEEN ''2025-06-01'' AND ''2025-12-31'''),
		VQ_ORDER_FILL_RATE AS ( 
QUESTION 'What is the order fill rate for tenant Alderwood between 2025-04-01 and 2025-10-31?' 
VERIFIED_AT 1786561492
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION true
SQL 'SELECT COUNT(CASE WHEN o.LINES_FILLED = o.TOTAL_LINES THEN 1 END)::FLOAT / NULLIF(COUNT(*), 0) AS order_fill_rate FROM AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDERS o WHERE o.TENANT_ID = ''T001'' AND o.ORDER_DATE BETWEEN ''2025-04-01'' AND ''2025-10-31'''),
		VQ_LINE_FILL_RATE AS ( 
QUESTION 'What is the line fill rate across all tenants for orders placed between 2025-01-26 and 2025-12-31?' 
VERIFIED_AT 1786561492
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT SUM(o.LINES_FILLED)::FLOAT / NULLIF(SUM(o.TOTAL_LINES), 0) AS line_fill_rate FROM AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDERS o WHERE o.ORDER_DATE BETWEEN ''2025-01-26'' AND ''2025-12-31'''),
		VQ_UNIT_FILL_RATE AS ( 
QUESTION 'What is the unit fill rate (eaches shipped divided by eaches ordered) for orders between 2025-01-26 and 2025-12-31?' 
VERIFIED_AT 1786561492
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT SUM(ol.QTY_SHIPPED_EACHES)::FLOAT / NULLIF(SUM(ol.QTY_ORDERED_EACHES), 0) AS unit_fill_rate FROM AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDER_LINES ol JOIN AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDERS o ON ol.ORDER_ID = o.ORDER_ID WHERE o.ORDER_DATE BETWEEN ''2025-01-26'' AND ''2025-12-31'''),
		VQ_TOTAL_UNITS_SHIPPED AS ( 
QUESTION 'How many total units were shipped between 2025-03-01 and 2025-09-30?' 
VERIFIED_AT 1786561492
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT SUM(ol.QTY_SHIPPED_EACHES) AS total_units_shipped FROM AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDER_LINES ol JOIN AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDERS o ON ol.ORDER_ID = o.ORDER_ID WHERE o.ORDER_DATE BETWEEN ''2025-03-01'' AND ''2025-09-30'''),
		VQ_CARTONS_BY_WAREHOUSE AS ( 
QUESTION 'How many cartons were shipped from warehouse ATL-DC1 between 2025-06-01 and 2025-12-31?' 
VERIFIED_AT 1786561492
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT SUM(ol.QTY_CARTONS) AS total_cartons FROM AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDER_LINES ol JOIN AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDERS o ON ol.ORDER_ID = o.ORDER_ID WHERE o.WAREHOUSE_ID = ''ATL-DC1'' AND o.ORDER_DATE BETWEEN ''2025-06-01'' AND ''2025-12-31'''),
		VQ_ORDER_LINES_COUNT AS ( 
QUESTION 'How many distinct order lines were there between 2025-02-01 and 2025-08-31?' 
VERIFIED_AT 1786561492
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT COUNT(*) AS total_order_lines FROM AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDER_LINES ol JOIN AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDERS o ON ol.ORDER_ID = o.ORDER_ID WHERE o.ORDER_DATE BETWEEN ''2025-02-01'' AND ''2025-08-31'''),
		VQ_AVG_COST_BY_CARRIER AS ( 
QUESTION 'What is the average shipping cost per shipment by carrier between 2025-06-01 and 2025-12-31?' 
VERIFIED_AT 1786561492
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT s.CARRIER, AVG(zrc.RATE_PER_PACKAGE * (1 + zrc.FUEL_SURCHARGE_PCT / 100.0) * s.PACKAGE_COUNT) AS avg_cost FROM AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.SHIPMENTS s JOIN AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.ZONE_RATE_CARDS zrc ON s.CARRIER = zrc.CARRIER AND s.ZONE = zrc.ZONE AND s.WEIGHT_BREAK = zrc.WEIGHT_BREAK AND s.SHIP_DATE::DATE BETWEEN zrc.EFFECTIVE_DATE AND zrc.EXPIRY_DATE WHERE s.SHIP_DATE BETWEEN ''2025-06-01'' AND ''2025-12-31'' GROUP BY s.CARRIER ORDER BY avg_cost DESC'),
		VQ_TOTAL_COST_BY_ZONE AS ( 
QUESTION 'What is the total shipping cost by zone between 2025-06-01 and 2025-12-31?' 
VERIFIED_AT 1786561492
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT s.ZONE, SUM(zrc.RATE_PER_PACKAGE * (1 + zrc.FUEL_SURCHARGE_PCT / 100.0) * s.PACKAGE_COUNT) AS total_cost FROM AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.SHIPMENTS s JOIN AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.ZONE_RATE_CARDS zrc ON s.CARRIER = zrc.CARRIER AND s.ZONE = zrc.ZONE AND s.WEIGHT_BREAK = zrc.WEIGHT_BREAK AND s.SHIP_DATE::DATE BETWEEN zrc.EFFECTIVE_DATE AND zrc.EXPIRY_DATE WHERE s.SHIP_DATE BETWEEN ''2025-06-01'' AND ''2025-12-31'' GROUP BY s.ZONE ORDER BY s.ZONE'),
		VQ_HIGHEST_COST_CARRIER AS ( 
QUESTION 'Which carrier has the highest average cost per shipment between 2025-06-01 and 2025-12-31?' 
VERIFIED_AT 1786561492
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT s.CARRIER, AVG(zrc.RATE_PER_PACKAGE * (1 + zrc.FUEL_SURCHARGE_PCT / 100.0) * s.PACKAGE_COUNT) AS avg_cost FROM AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.SHIPMENTS s JOIN AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.ZONE_RATE_CARDS zrc ON s.CARRIER = zrc.CARRIER AND s.ZONE = zrc.ZONE AND s.WEIGHT_BREAK = zrc.WEIGHT_BREAK AND s.SHIP_DATE::DATE BETWEEN zrc.EFFECTIVE_DATE AND zrc.EXPIRY_DATE WHERE s.SHIP_DATE BETWEEN ''2025-06-01'' AND ''2025-12-31'' GROUP BY s.CARRIER ORDER BY avg_cost DESC LIMIT 1'),
		VQ_FISCAL_LOOKUP AS ( 
QUESTION 'What fiscal period and fiscal year does the date 2025-08-15 fall in?' 
VERIFIED_AT 1786561492
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT FISCAL_YEAR, FISCAL_PERIOD, FISCAL_QUARTER, FISCAL_WEEK FROM AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.FISCAL_CALENDAR_445 WHERE CALENDAR_DATE = ''2025-08-15'''),
		VQ_ORDERS_IN_FISCAL_PERIOD AS ( 
QUESTION 'How many orders were placed in fiscal period 7 of fiscal year 2025?' 
VERIFIED_AT 1786561492
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT COUNT(*) AS order_count FROM AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDERS o JOIN AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.FISCAL_CALENDAR_445 fc ON o.ORDER_DATE::DATE = fc.CALENDAR_DATE WHERE fc.FISCAL_YEAR = 2025 AND fc.FISCAL_PERIOD = 7'),
		VQ_FISCAL_PERIOD_REVENUE AS ( 
QUESTION 'What is the total revenue in fiscal period 8 of fiscal year 2025?' 
VERIFIED_AT 1786561492
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT SUM(ol.QTY_SHIPPED_EACHES * ol.UNIT_PRICE) AS revenue FROM AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDER_LINES ol JOIN AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDERS o ON ol.ORDER_ID = o.ORDER_ID JOIN AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.FISCAL_CALENDAR_445 fc ON o.ORDER_DATE::DATE = fc.CALENDAR_DATE WHERE fc.FISCAL_YEAR = 2025 AND fc.FISCAL_PERIOD = 8'),
		VQ_ACTIVE_SKU_COUNT AS ( 
QUESTION 'How many active SKUs are there as of December 2025?' 
VERIFIED_AT 1786561492
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT COUNT(DISTINCT m.SKU) AS active_sku_count FROM AGENT_EVAL_DEMO.INVENTORY_INTELLIGENCE.MOVEMENTS m WHERE m.MOVEMENT_DATE BETWEEN ''2025-12-01'' AND ''2025-12-31'''),
		VQ_ACTIVE_SKU_LIST AS ( 
QUESTION 'Which SKUs had inventory movement in the 30 days ending 2025-12-31?' 
VERIFIED_AT 1786561492
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT DISTINCT m.SKU FROM AGENT_EVAL_DEMO.INVENTORY_INTELLIGENCE.MOVEMENTS m WHERE m.MOVEMENT_DATE BETWEEN ''2025-12-01'' AND ''2025-12-31'' ORDER BY m.SKU'),
		VQ_INACTIVE_SKU_COUNT AS ( 
QUESTION 'How many SKUs in the item master had zero inventory movement in the 30 days ending 2025-12-31?' 
VERIFIED_AT 1786561492
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT COUNT(*) AS inactive_skus FROM AGENT_EVAL_DEMO.INVENTORY_INTELLIGENCE.ITEM_MASTER im WHERE NOT EXISTS (SELECT 1 FROM AGENT_EVAL_DEMO.INVENTORY_INTELLIGENCE.MOVEMENTS m WHERE m.SKU = im.SKU AND m.MOVEMENT_DATE BETWEEN ''2025-12-01'' AND ''2025-12-31'')'),
		VQ_ORDERS_BY_WH AS ( 
QUESTION 'How many orders per warehouse were placed between 2025-06-01 and 2025-09-30?' 
VERIFIED_AT 1786561492
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT WAREHOUSE_ID, COUNT(*) AS order_count FROM AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDERS WHERE ORDER_DATE BETWEEN ''2025-06-01'' AND ''2025-09-30'' GROUP BY WAREHOUSE_ID ORDER BY order_count DESC'),
		VQ_EXCEPTIONS_BY_TYPE AS ( 
QUESTION 'What is the total number of exceptions by type for tenant Bellweather between 2025-05-01 and 2025-11-30?' 
VERIFIED_AT 1786561492
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT EXCEPTION_TYPE, COUNT(*) AS exception_count FROM AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.EXCEPTIONS WHERE TENANT_ID = ''T002'' AND EXCEPTION_DATE BETWEEN ''2025-05-01'' AND ''2025-11-30'' GROUP BY EXCEPTION_TYPE ORDER BY exception_count DESC')
	);
