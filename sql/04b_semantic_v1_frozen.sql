/*=============================================================================
  04b_semantic_v1_frozen.sql — FROZEN v1 snapshot: AGENT_EVAL_DEMO.AI.FULFILLMENT_SV_V1

  STATUS: ARCHIVAL / DO NOT RE-RUN unless the live FULFILLMENT_SV_V1 object
  is accidentally dropped. The canonical build script is 04_semantic_v1.sql
  (which targets FULFILLMENT_SV, the object that later becomes v2). This file
  targets a DIFFERENT object (FULFILLMENT_SV_V1) used only for live re-runs.

  WHY THIS FILE EXISTS
  Step G ran CREATE OR REPLACE on FULFILLMENT_SV to produce v2, which DESTROYED
  v1 in place. That made a live baseline re-run impossible, and the demo promises
  exactly that ("pre-baked runs + live re-run"). FULFILLMENT_SV_V1 is a separate,
  frozen object holding the weak v1 definition with the SAME 20 verified queries
  as v2, so the baseline can be re-run on demand and reproduce the 0.40-0.45 band.

  DO NOT "improve" this view. Its weaknesses are the demo:
    - vague column descriptions
    - no metrics, no filters, no custom instructions
    - no TENANT_NAME dimension (so business names cannot resolve)
    - ZONE_RATE_CARDS declared but with a vague comment, no relationship, no cost
      metric and no custom instruction, so cost questions have no guidance. The
      table itself CANNOT be omitted: 3 of the shared 20 verified queries join it,
      and verified-query SQL resolves against the logical model, so omitting it
      breaks the ENTIRE model, not just cost. That is exactly the bug this file
      shipped with -- "Object 'ZONE_RATE_CARDS' does not exist or not authorized".

  Captured with GET_DDL from the live object.
=============================================================================*/

USE ROLE ACCOUNTADMIN;
USE DATABASE AGENT_EVAL_DEMO;
USE SCHEMA AI;
USE WAREHOUSE AGENT_EVAL_DEMO_WH;

create or replace semantic view FULFILLMENT_SV_V1
	tables (
		AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDERS primary key (ORDER_ID) comment='Orders table',
		AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDER_LINES primary key (ORDER_LINE_ID) comment='Order line items',
		AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.WAVES primary key (WAVE_ID) comment='Picking waves',
		AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.EXCEPTIONS primary key (EXCEPTION_ID) comment='Exceptions',
		FISCAL_CALENDAR as AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.FISCAL_CALENDAR_445 primary key (CALENDAR_DATE) comment='Calendar',
		AGENT_EVAL_DEMO.INVENTORY_INTELLIGENCE.ITEM_MASTER primary key (SKU) comment='Products',
		AGENT_EVAL_DEMO.INVENTORY_INTELLIGENCE.MOVEMENTS primary key (MOVEMENT_ID) comment='Inventory movements',
    AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.SHIPMENTS primary key (SHIPMENT_ID) comment='Shipments',
    -- ZONE_RATE_CARDS MUST be declared here even though v1 is the "weak" view.
    -- v1 carries the SAME 20 verified queries as v2, and 3 of them join
    -- ZONE_RATE_CARDS. Verified-query SQL resolves table names against the LOGICAL
    -- model, so omitting the table makes the whole model fail to load with
    -- "Object 'ZONE_RATE_CARDS' does not exist or not authorized" -- not just the
    -- cost questions. v1 stays weak the way every other v1 column is weak: a vague
    -- comment, no relationship, no cost metric, no custom instruction.
    AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.ZONE_RATE_CARDS comment='Rate cards'
  )
	relationships (
		LINES_TO_ITEMS as ORDER_LINES(SKU) references ITEM_MASTER(SKU),
		LINES_TO_ORDERS as ORDER_LINES(ORDER_ID) references ORDERS(ORDER_ID),
		LINES_TO_WAVES as ORDER_LINES(WAVE_ID) references WAVES(WAVE_ID),
		EXCEPTIONS_TO_ORDERS as EXCEPTIONS(ORDER_ID) references ORDERS(ORDER_ID),
		MOVEMENTS_TO_ITEMS as MOVEMENTS(SKU) references ITEM_MASTER(SKU),
		SHIPMENTS_TO_ORDERS as SHIPMENTS(ORDER_ID) references ORDERS(ORDER_ID)
	)
	facts (
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
	dimensions (
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
    -- REQUIRED, not optional. Three verified queries below reference
    -- s.SHIP_BY_DATE where s is aliased to SHIPMENTS. Verified query SQL is
    -- resolved against the LOGICAL model, not the physical table, so even though
    -- SHIPPING_INTELLIGENCE.SHIPMENTS really has this column, omitting the
    -- declaration makes the whole model invalid:
    --   "Invalid semantic model yaml. SQL compilation error:
    --    invalid identifier 'S.SHIP_BY_DATE'"
    -- Snowsight refuses to open the view in that state. Kept deliberately vague
    -- ('Date') like its siblings -- this is the WEAK v1 baseline and a good
    -- description here would inflate the baseline score.
    SHIPMENTS.SHIP_BY_DATE as SHIP_BY_DATE comment='Date',
    SHIPMENTS.WAREHOUSE_ID as WAREHOUSE_ID comment='Warehouse',
    SHIPMENTS.TRACKING_NUMBER as TRACKING_NUMBER comment='Tracking',
    -- Same rule as SHIP_BY_DATE above, one level up: the 3 cost verified queries
    -- reference these ZONE_RATE_CARDS columns, so they must be declared or the
    -- model fails to load. Comments stay vague on purpose.
    ZONE_RATE_CARDS.CARRIER as CARRIER comment='Carrier',
    ZONE_RATE_CARDS.ZONE as ZONE comment='Zone',
    ZONE_RATE_CARDS.WEIGHT_BREAK as WEIGHT_BREAK comment='Weight category',
    ZONE_RATE_CARDS.EFFECTIVE_DATE as EFFECTIVE_DATE comment='Date',
    ZONE_RATE_CARDS.EXPIRY_DATE as EXPIRY_DATE comment='Date'
  )
	comment='Fulfillment intelligence - v1 baseline (pre-optimization)'
	ai_verified_queries (
		VQ_ON_TIME_ALL AS ( 
QUESTION 'What is the on-time shipping rate for all tenants between 2025-06-01 and 2025-12-31?' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION true
SQL 'SELECT COUNT(CASE WHEN s.CARRIER_FIRST_SCAN_TS <= s.SHIP_BY_DATE THEN 1 END)::FLOAT / NULLIF(COUNT(*), 0) AS on_time_rate FROM SHIPMENTS s WHERE s.SHIP_DATE BETWEEN ''2025-06-01'' AND ''2025-12-31'''),
		VQ_ON_TIME_BY_WAREHOUSE AS ( 
QUESTION 'Show the on-time shipping rate by warehouse for shipments between 2025-03-01 and 2025-09-30.' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT s.WAREHOUSE_ID, COUNT(CASE WHEN s.CARRIER_FIRST_SCAN_TS <= s.SHIP_BY_DATE THEN 1 END)::FLOAT / NULLIF(COUNT(*), 0) AS on_time_rate FROM SHIPMENTS s WHERE s.SHIP_DATE BETWEEN ''2025-03-01'' AND ''2025-09-30'' GROUP BY s.WAREHOUSE_ID ORDER BY on_time_rate'),
		VQ_LATE_SHIPMENTS_COUNT AS ( 
QUESTION 'How many shipments missed their ship-by date between 2025-06-01 and 2025-12-31?' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT COUNT(*) AS late_shipments FROM SHIPMENTS s WHERE s.CARRIER_FIRST_SCAN_TS > s.SHIP_BY_DATE AND s.SHIP_DATE BETWEEN ''2025-06-01'' AND ''2025-12-31'''),
		VQ_ORDER_FILL_RATE AS ( 
QUESTION 'What is the order fill rate for tenant Alderwood between 2025-04-01 and 2025-10-31?' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION true
SQL 'SELECT COUNT(CASE WHEN o.LINES_FILLED = o.TOTAL_LINES THEN 1 END)::FLOAT / NULLIF(COUNT(*), 0) AS order_fill_rate FROM ORDERS o WHERE o.TENANT_ID = ''T001'' AND o.ORDER_DATE BETWEEN ''2025-04-01'' AND ''2025-10-31'''),
		VQ_LINE_FILL_RATE AS ( 
QUESTION 'What is the line fill rate across all tenants for orders placed between 2025-01-26 and 2025-12-31?' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT SUM(o.LINES_FILLED)::FLOAT / NULLIF(SUM(o.TOTAL_LINES), 0) AS line_fill_rate FROM ORDERS o WHERE o.ORDER_DATE BETWEEN ''2025-01-26'' AND ''2025-12-31'''),
		VQ_UNIT_FILL_RATE AS ( 
QUESTION 'What is the unit fill rate (eaches shipped divided by eaches ordered) for orders between 2025-01-26 and 2025-12-31?' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT SUM(ol.QTY_SHIPPED_EACHES)::FLOAT / NULLIF(SUM(ol.QTY_ORDERED_EACHES), 0) AS unit_fill_rate FROM ORDER_LINES ol JOIN ORDERS o ON ol.ORDER_ID = o.ORDER_ID WHERE o.ORDER_DATE BETWEEN ''2025-01-26'' AND ''2025-12-31'''),
		VQ_TOTAL_UNITS_SHIPPED AS ( 
QUESTION 'How many total units were shipped between 2025-03-01 and 2025-09-30?' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT SUM(ol.QTY_SHIPPED_EACHES) AS total_units_shipped FROM ORDER_LINES ol JOIN ORDERS o ON ol.ORDER_ID = o.ORDER_ID WHERE o.ORDER_DATE BETWEEN ''2025-03-01'' AND ''2025-09-30'''),
		VQ_CARTONS_BY_WAREHOUSE AS ( 
QUESTION 'How many cartons were shipped from warehouse ATL-DC1 between 2025-06-01 and 2025-12-31?' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT SUM(ol.QTY_CARTONS) AS total_cartons FROM ORDER_LINES ol JOIN ORDERS o ON ol.ORDER_ID = o.ORDER_ID WHERE o.WAREHOUSE_ID = ''ATL-DC1'' AND o.ORDER_DATE BETWEEN ''2025-06-01'' AND ''2025-12-31'''),
		VQ_ORDER_LINES_COUNT AS ( 
QUESTION 'How many distinct order lines were there between 2025-02-01 and 2025-08-31?' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT COUNT(*) AS total_order_lines FROM ORDER_LINES ol JOIN ORDERS o ON ol.ORDER_ID = o.ORDER_ID WHERE o.ORDER_DATE BETWEEN ''2025-02-01'' AND ''2025-08-31'''),
		VQ_AVG_COST_BY_CARRIER AS ( 
QUESTION 'What is the average shipping cost per shipment by carrier between 2025-06-01 and 2025-12-31?' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT s.CARRIER, AVG(zrc.RATE_PER_PACKAGE * (1 + zrc.FUEL_SURCHARGE_PCT / 100.0) * s.PACKAGE_COUNT) AS avg_cost FROM SHIPMENTS s JOIN ZONE_RATE_CARDS zrc ON s.CARRIER = zrc.CARRIER AND s.ZONE = zrc.ZONE AND s.WEIGHT_BREAK = zrc.WEIGHT_BREAK AND s.SHIP_DATE::DATE BETWEEN zrc.EFFECTIVE_DATE AND zrc.EXPIRY_DATE WHERE s.SHIP_DATE BETWEEN ''2025-06-01'' AND ''2025-12-31'' GROUP BY s.CARRIER ORDER BY avg_cost DESC'),
		VQ_TOTAL_COST_BY_ZONE AS ( 
QUESTION 'What is the total shipping cost by zone between 2025-06-01 and 2025-12-31?' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT s.ZONE, SUM(zrc.RATE_PER_PACKAGE * (1 + zrc.FUEL_SURCHARGE_PCT / 100.0) * s.PACKAGE_COUNT) AS total_cost FROM SHIPMENTS s JOIN ZONE_RATE_CARDS zrc ON s.CARRIER = zrc.CARRIER AND s.ZONE = zrc.ZONE AND s.WEIGHT_BREAK = zrc.WEIGHT_BREAK AND s.SHIP_DATE::DATE BETWEEN zrc.EFFECTIVE_DATE AND zrc.EXPIRY_DATE WHERE s.SHIP_DATE BETWEEN ''2025-06-01'' AND ''2025-12-31'' GROUP BY s.ZONE ORDER BY s.ZONE'),
		VQ_HIGHEST_COST_CARRIER AS ( 
QUESTION 'Which carrier has the highest average cost per shipment between 2025-06-01 and 2025-12-31?' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT s.CARRIER, AVG(zrc.RATE_PER_PACKAGE * (1 + zrc.FUEL_SURCHARGE_PCT / 100.0) * s.PACKAGE_COUNT) AS avg_cost FROM SHIPMENTS s JOIN ZONE_RATE_CARDS zrc ON s.CARRIER = zrc.CARRIER AND s.ZONE = zrc.ZONE AND s.WEIGHT_BREAK = zrc.WEIGHT_BREAK AND s.SHIP_DATE::DATE BETWEEN zrc.EFFECTIVE_DATE AND zrc.EXPIRY_DATE WHERE s.SHIP_DATE BETWEEN ''2025-06-01'' AND ''2025-12-31'' GROUP BY s.CARRIER ORDER BY avg_cost DESC LIMIT 1'),
		VQ_FISCAL_LOOKUP AS ( 
QUESTION 'What fiscal period and fiscal year does the date 2025-08-15 fall in?' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT FISCAL_YEAR, FISCAL_PERIOD, FISCAL_QUARTER, FISCAL_WEEK FROM FISCAL_CALENDAR WHERE CALENDAR_DATE = ''2025-08-15'''),
		VQ_ORDERS_IN_FISCAL_PERIOD AS ( 
QUESTION 'How many orders were placed in fiscal period 7 of fiscal year 2025?' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT COUNT(*) AS order_count FROM ORDERS o JOIN FISCAL_CALENDAR fc ON o.ORDER_DATE::DATE = fc.CALENDAR_DATE WHERE fc.FISCAL_YEAR = 2025 AND fc.FISCAL_PERIOD = 7'),
		VQ_FISCAL_PERIOD_REVENUE AS ( 
QUESTION 'What is the total revenue in fiscal period 8 of fiscal year 2025?' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT SUM(ol.QTY_SHIPPED_EACHES * ol.UNIT_PRICE) AS revenue FROM ORDER_LINES ol JOIN ORDERS o ON ol.ORDER_ID = o.ORDER_ID JOIN FISCAL_CALENDAR fc ON o.ORDER_DATE::DATE = fc.CALENDAR_DATE WHERE fc.FISCAL_YEAR = 2025 AND fc.FISCAL_PERIOD = 8'),
		VQ_ACTIVE_SKU_COUNT AS ( 
QUESTION 'How many active SKUs are there as of December 2025?' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT COUNT(DISTINCT m.SKU) AS active_sku_count FROM MOVEMENTS m WHERE m.MOVEMENT_DATE BETWEEN ''2025-12-01'' AND ''2025-12-31'''),
		VQ_ACTIVE_SKU_LIST AS ( 
QUESTION 'Which SKUs had inventory movement in the 30 days ending 2025-12-31?' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT DISTINCT m.SKU FROM MOVEMENTS m WHERE m.MOVEMENT_DATE BETWEEN ''2025-12-01'' AND ''2025-12-31'' ORDER BY m.SKU'),
		VQ_INACTIVE_SKU_COUNT AS ( 
QUESTION 'How many SKUs in the item master had zero inventory movement in the 30 days ending 2025-12-31?' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT COUNT(*) AS inactive_skus FROM ITEM_MASTER im WHERE NOT EXISTS (SELECT 1 FROM MOVEMENTS m WHERE m.SKU = im.SKU AND m.MOVEMENT_DATE BETWEEN ''2025-12-01'' AND ''2025-12-31'')'),
		VQ_ORDERS_BY_WH AS ( 
QUESTION 'How many orders per warehouse were placed between 2025-06-01 and 2025-09-30?' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT WAREHOUSE_ID, COUNT(*) AS order_count FROM ORDERS WHERE ORDER_DATE BETWEEN ''2025-06-01'' AND ''2025-09-30'' GROUP BY WAREHOUSE_ID ORDER BY order_count DESC'),
		VQ_EXCEPTIONS_BY_TYPE AS ( 
QUESTION 'What is the total number of exceptions by type for tenant Bellweather between 2025-05-01 and 2025-11-30?' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT EXCEPTION_TYPE, COUNT(*) AS exception_count FROM EXCEPTIONS WHERE TENANT_ID = ''T002'' AND EXCEPTION_DATE BETWEEN ''2025-05-01'' AND ''2025-11-30'' GROUP BY EXCEPTION_TYPE ORDER BY exception_count DESC')
	);
