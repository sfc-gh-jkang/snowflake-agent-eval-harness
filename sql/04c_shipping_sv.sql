/*=============================================================================
  04c_shipping_sv.sql - AGENT_EVAL_DEMO.AI.SHIPPING_SV, captured via GET_DDL.
  NOTE: CARRIER_SCANS.SCAN_ID is declared as a logical column ON PURPOSE.
  It is the primary key, and Cortex Analyst rejects the ENTIRE view with
  error 392700 if a key column is not also a logical column - even though
  CREATE SEMANTIC VIEW itself accepts it. This was a latent defect that
  would have fired the first time the agent routed a carrier question here.
=============================================================================*/

USE ROLE ACCOUNTADMIN;
USE DATABASE AGENT_EVAL_DEMO;
USE SCHEMA AI;
USE WAREHOUSE AGENT_EVAL_DEMO_WH;

create or replace semantic view SHIPPING_SV
	tables (
		AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.SHIPMENTS primary key (SHIPMENT_ID) comment='Outbound shipments with carrier details and delivery timing. Each shipment fulfills one order. CARRIER_FIRST_SCAN_TS is when the carrier physically accepted the package.',
		AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.CARRIER_SCANS primary key (SCAN_ID) comment='Carrier scan events tracking package movement through the delivery network (pickup, transit, delivery, exception).',
		AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.ZONE_RATE_CARDS comment='Carrier rate cards defining cost per package by carrier, zone, and weight tier. Join to SHIPMENTS on (CARRIER, ZONE, WEIGHT_BREAK) with effective date range. Total cost = RATE_PER_PACKAGE * (1 + FUEL_SURCHARGE_PCT/100) * PACKAGE_COUNT.'
	)
	relationships (
		SCANS_TO_SHIPMENTS as CARRIER_SCANS(SHIPMENT_ID) references SHIPMENTS(SHIPMENT_ID)
	)
	facts (
		SHIPMENTS.TOTAL_WEIGHT_LB as TOTAL_WEIGHT_LB comment='Actual total shipment weight in pounds',
		SHIPMENTS.PACKAGE_COUNT as PACKAGE_COUNT comment='Number of packages/parcels in this shipment',
		SHIPMENTS.SHIPMENT_RECORD as 1 comment='Record counter for shipment aggregations',
		ZONE_RATE_CARDS.RATE_PER_PACKAGE as RATE_PER_PACKAGE comment='Base rate per package in USD for this carrier/zone/weight combination',
		ZONE_RATE_CARDS.FUEL_SURCHARGE_PCT as FUEL_SURCHARGE_PCT comment='Fuel surcharge as a percentage added to the base rate'
	)
	dimensions (
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
		CARRIER_SCANS.SHIPMENT_ID as SHIPMENT_ID comment='FK to SHIPMENTS. Declared as a logical column because it is used in a relationship. Harmless, and removes any risk of the 392700 class of rejection.',
		CARRIER_SCANS.SCAN_ID as SCAN_ID comment='Unique scan event id. Declared as a logical column because it is the primary key: Cortex Analyst rejects the whole view with error 392700 if a key column is not also a logical column, even though CREATE SEMANTIC VIEW itself accepts it.',
		CARRIER_SCANS.SCAN_TYPE as SCAN_TYPE comment='Scan event type: PICKUP, IN_TRANSIT, OUT_FOR_DELIVERY, DELIVERED, EXCEPTION, RETURN_TO_SENDER',
		CARRIER_SCANS.SCAN_TIMESTAMP as SCAN_TIMESTAMP comment='When the scan occurred',
		CARRIER_SCANS.LOCATION as LOCATION comment='Facility or city where the scan occurred',
		ZONE_RATE_CARDS.EFFECTIVE_DATE as EFFECTIVE_DATE comment='Rate card effective start date',
		ZONE_RATE_CARDS.EXPIRY_DATE as EXPIRY_DATE comment='Rate card expiration date'
	)
	metrics (
		SHIPMENTS.ON_TIME_SHIP_RATE as COUNT(CASE WHEN CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE THEN 1 END) / NULLIF(COUNT(SHIPMENT_RECORD), 0) comment='Percentage of shipments tendered to carrier on or before the SLA (SHIP_BY_DATE). On-time = CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE.',
		SHIPMENTS.TOTAL_SHIPMENTS as COUNT(SHIPMENT_RECORD) comment='Total number of shipments',
		SHIPMENTS.LATE_SHIPMENTS as COUNT(CASE WHEN CARRIER_FIRST_SCAN_TS > SHIP_BY_DATE THEN 1 END) comment='Number of shipments that missed the SLA deadline'
	)
	comment='Shipping intelligence with carrier performance, on-time delivery, and cost analytics. Use SHIP_BY_DATE for on-time SLA calculations, not PROMISED_DELIVERY_DATE.'
	ai_verified_queries (
		VQ_ON_TIME_UPS AS ( 
QUESTION 'What is the on-time rate for UPS shipments between 2025-07-01 and 2025-12-31?' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT COUNT(CASE WHEN CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE THEN 1 END)::FLOAT / NULLIF(COUNT(*), 0) AS ups_on_time_rate FROM SHIPMENTS WHERE CARRIER = ''UPS'' AND SHIP_DATE BETWEEN ''2025-07-01'' AND ''2025-12-31'''),
		VQ_LATE_BY_CARRIER AS ( 
QUESTION 'How many shipments were late (missed the SLA) by carrier between 2025-06-01 and 2025-12-31?' 
VERIFIED_AT 1723488000
VERIFIED_BY '( STEWARD = data_engineering )'
ONBOARDING_QUESTION false
SQL 'SELECT CARRIER, COUNT(*) AS late_shipments FROM SHIPMENTS WHERE CARRIER_FIRST_SCAN_TS > SHIP_BY_DATE AND SHIP_DATE BETWEEN ''2025-06-01'' AND ''2025-12-31'' GROUP BY CARRIER ORDER BY late_shipments DESC')
	);
