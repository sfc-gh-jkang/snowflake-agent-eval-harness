"""
generate_ops_corpus.py — Synthetic ops knowledge documents for OPS_KNOWLEDGE_SEARCH.

Produces ~60 documents across four categories:
  - Carrier tariff sheets (6)
  - Pick/pack SOPs (15)
  - Exception playbooks (20) — includes Tuesday-wave-missed-cutoff answer
  - Cutoff & scheduling policies (12)
  - General operating procedures (10)

Output: data/ops_knowledge_corpus.parquet
"""

import pandas as pd
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent / "data"
OUTPUT_DIR.mkdir(exist_ok=True)

WAREHOUSES = ["ATL-DC1", "ATL-DC2", "CHI-DC1", "DAL-DC1", "LAX-DC1"]
CARRIERS = ["FEDEX", "UPS", "USPS", "DHL", "XPO", "ONTRAC"]


def generate_carrier_tariffs():
    docs = []
    for carrier in CARRIERS:
        surcharge_info = {
            "FEDEX": ("12.5%", "15.0%", "$4.50", "$18.00"),
            "UPS": ("11.8%", "14.5%", "$4.25", "$17.50"),
            "USPS": ("0%", "0%", "$3.00", "$12.00"),
            "DHL": ("14.0%", "17.5%", "$6.00", "$25.00"),
            "XPO": ("10.0%", "13.0%", "$8.00", "$35.00"),
            "ONTRAC": ("8.5%", "11.0%", "$3.50", "$10.00"),
        }[carrier]
        docs.append({
            "DOC_ID": f"TARIFF-{carrier}-2025",
            "TITLE": f"{carrier} Rate Card & Surcharges — FY2025",
            "DOC_TYPE": "CARRIER_TARIFF",
            "CARRIER": carrier,
            "WAREHOUSE_ID": None,
            "CONTENT": f"""## {carrier} Commercial Rate Card — Effective 2025-01-01

### Fuel Surcharge
- Ground: {surcharge_info[0]} (reviewed quarterly)
- Express/Air: {surcharge_info[1]}

### Residential Surcharge
- Ground residential: {surcharge_info[2]} per package
- Oversize (>130" L+G): {surcharge_info[3]} per package

### Zone-Based Pricing
Rates are zone-based (1–8) with weight breaks: 0-1LB, 1-5LB, 5-20LB, 20-50LB, 50-150LB.
Rate lookup: SHIPPING_INTELLIGENCE.ZONE_RATE_CARDS filtered by CARRIER='{carrier}'.

### Dimensional Weight
DIM factor: 139 (domestic), 166 (international).
Billable weight = MAX(actual weight, L×W×H / DIM factor).

### Accessorial Charges
- Delivery area surcharge (DAS): $3.75 (extended: $5.50)
- Signature required: $5.25
- Hazmat handling: $45.00 per package (requires UN number)
- Saturday delivery: $16.00

### Contract Terms
- Payment NET-30 from invoice date
- Volume discount tiers: 500/wk (5%), 2000/wk (12%), 5000/wk (18%)
- Rate escalator: CPI + 2.0% annually
""",
        })
    return docs


def generate_pick_pack_sops():
    docs = []

    docs.append({
        "DOC_ID": "SOP-PICK-001",
        "TITLE": "Standard Pick Process — Discrete Order Picking",
        "DOC_TYPE": "PICK_PACK_SOP",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Discrete Order Picking SOP

### Scope
Applies to all standard (non-expedited) orders with ≤10 lines.

### Process
1. Scanner assigns pick task from wave queue (FIFO within priority).
2. Walk to location displayed on RF gun. Confirm location barcode.
3. Pick quantity displayed. Scan item UPC to confirm SKU match.
4. Place in tote. Scan tote barcode to associate.
5. Repeat until all lines complete.
6. Deliver tote to pack station.

### Short Pick Procedure
If location is empty or insufficient quantity:
1. Confirm zero/short on RF gun.
2. System flags line as SHORT status.
3. Exception auto-generated (EXCEPTION_TYPE = 'SHORT_PICK').
4. Replenishment task created for inventory team.
5. Order may ship partial if SHIP_PARTIAL_FLAG = 'Y' on the order.

### Performance Standards
- Target: 120 picks/hour (discrete), 200 picks/hour (batch).
- Accuracy target: 99.7% pick accuracy.
- Reference: LABOR_INTELLIGENCE.LABOR_STANDARDS for zone-specific targets.

### Definition: Short Pick
A short pick occurs when the picker cannot fulfill the requested quantity from the assigned location. This includes: (a) location is empty, (b) location has insufficient quantity, (c) item is damaged and cannot be shipped. A short pick does NOT mean the order is cancelled — it means the LINE_STATUS is set to 'SHORT' and the line ships with reduced quantity or zero quantity depending on available stock.
""",
    })

    docs.append({
        "DOC_ID": "SOP-PICK-002",
        "TITLE": "Batch Picking — Multi-Order Wave Process",
        "DOC_TYPE": "PICK_PACK_SOP",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Batch Picking SOP (Wave-Based)

### Scope
Applies to waves with >20 orders of similar SKU profiles (e.g., subscription boxes).

### Wave Formation
1. WMS groups orders by zone affinity, carrier, and cutoff time.
2. Maximum batch size: 30 orders per picker per wave.
3. Wave released to floor at scheduled CUTOFF_TIME minus 4 hours.

### Process
1. Receive batch assignment on RF gun showing aggregated quantities per location.
2. Pick total quantity for all orders in batch to cart.
3. Sort picked items into individual order totes at sort station.
4. System validates each tote against expected SKU/qty.
5. Flag discrepancies as exceptions.

### Metrics
- Target: 200 units/hour for batch pick.
- Sort accuracy: 99.9%.
""",
    })

    docs.append({
        "DOC_ID": "SOP-PICK-003",
        "TITLE": "Pack Station Standard Operating Procedure",
        "DOC_TYPE": "PICK_PACK_SOP",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Pack Station SOP

### Cartonization
1. System recommends box size based on item dims and quantity.
2. Packer scans tote → system displays packing list.
3. Scan each item UPC as placed in box. System validates count.
4. Add dunnage per standard (bubble wrap for fragile, cold packs for temp-sensitive).
5. Print and apply shipping label.
6. Weigh package on scale — compare to expected weight (tolerance ±5%).

### Multi-Carton Shipments
If order exceeds single box capacity:
- System assigns PACKAGE_COUNT > 1 on the shipment.
- Each carton gets its own tracking number.
- Master tracking number links child packages.

### Quality Checks
- Random audit: 5% of packages opened and verified before sealing.
- Hazmat orders require secondary verification and MSDS sheet in package.
""",
    })

    docs.append({
        "DOC_ID": "SOP-PICK-004",
        "TITLE": "Expedited Order Handling — Next Day & Same Day",
        "DOC_TYPE": "PICK_PACK_SOP",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Expedited Order Processing

### Priority Levels
- NEXT_DAY: Must be picked, packed, and tendered to carrier by daily cutoff.
- SAME_DAY: Must be picked, packed, and tendered within 2 hours of receipt.

### Handling Rules
1. Expedited orders bypass wave queue — assigned immediately on receipt.
2. Dedicated expedited pick zone with high-velocity SKUs pre-staged.
3. Packer prints label before pick completes (pre-manifested).
4. If any line is SHORT, do NOT hold the order — ship what is available.
5. Short lines auto-reorder and ship separately.

### Carrier Selection
- NEXT_DAY: FEDEX Priority Overnight or UPS Next Day Air.
- SAME_DAY: Local courier (ONTRAC) where available, else FEDEX SameDay.
- System selects cheapest qualifying carrier from ZONE_RATE_CARDS.
""",
    })

    docs.append({
        "DOC_ID": "SOP-PICK-005",
        "TITLE": "Cold Chain Handling — Temperature-Sensitive SKUs",
        "DOC_TYPE": "PICK_PACK_SOP",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Cold Chain / Temperature-Sensitive SOP

### Identification
Items with TEMPERATURE_SENSITIVE = 'Y' in ITEM_MASTER require cold chain handling.

### Storage
- Refrigerated zone: 34–38°F (pick zone COLD-A through COLD-D).
- Frozen zone: -10 to 0°F (pick zone FREEZE-A, FREEZE-B).

### Picking Rules
1. Cold items picked LAST in pick sequence to minimize ambient exposure.
2. Maximum dwell time at ambient: 15 minutes.
3. Place in insulated tote immediately after pick.

### Packing
- Gel packs (refrigerated) or dry ice (frozen) per qty table.
- Insulated liner in every box containing temp-sensitive items.
- Ship label must include "PERISHABLE — KEEP REFRIGERATED" sticker.
- Carrier must be overnight-capable (no ground for frozen).
""",
    })

    docs.append({
        "DOC_ID": "SOP-PICK-006",
        "TITLE": "Hazmat Picking and Packing Requirements",
        "DOC_TYPE": "PICK_PACK_SOP",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Hazmat Handling SOP

### Identification
Items with HAZMAT_FLAG = 'Y' in ITEM_MASTER are regulated materials.

### Picking
1. Only certified hazmat pickers may handle these items.
2. Must wear appropriate PPE (gloves minimum; respirator for Class 6).
3. Separate tote from non-hazmat items — never co-mingle.

### Packing
1. Inner packaging per DOT/IATA requirements.
2. Outer box must display hazmat diamond label + UN number.
3. Include Safety Data Sheet (SDS) inside package.
4. Maximum qty per package per carrier DOT limits.

### Shipping Restrictions
- Ground only (no air) for most Class 3, 8, 9 materials.
- DHL and XPO accept ORM-D; USPS does not accept any Class 3+.
- Carrier surcharge: $45/package for hazmat handling.
""",
    })

    docs.append({
        "DOC_ID": "SOP-PICK-007",
        "TITLE": "Returns Processing — Inbound Reverse Logistics",
        "DOC_TYPE": "PICK_PACK_SOP",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Returns Processing SOP

### Receiving
1. Scan RMA barcode on return label.
2. Open package, inspect item condition.
3. Grade: A (resellable), B (refurbish), C (damaged/destroy).

### Inventory Update
- Grade A: Return to pick location. MOVEMENTS record with MOVEMENT_TYPE = 'RETURN_RESTOCK'.
- Grade B: Move to refurbishment area. MOVEMENT_TYPE = 'RETURN_REFURB'.
- Grade C: Move to disposal. MOVEMENT_TYPE = 'RETURN_DISPOSE'.

### Timing
- Returns must be processed within 24 hours of receipt.
- Refunds trigger on Grade A restock completion.
""",
    })

    docs.append({
        "DOC_ID": "SOP-PICK-008",
        "TITLE": "Cycle Count Procedure",
        "DOC_TYPE": "PICK_PACK_SOP",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Cycle Count SOP

### Frequency
- A items (top 20% by movement velocity): weekly
- B items: monthly
- C items (bottom 20%): quarterly

### Process
1. System generates count tasks for scheduled locations.
2. Counter scans location barcode, counts physical qty.
3. If variance > 2 units or > 5%, recount required.
4. Supervisor approves adjustments > $500 value.
5. ON_HAND.QTY_ON_HAND_EACHES updated; MOVEMENT_TYPE = 'ADJUSTMENT'.

### Active SKU Definition
A SKU is considered "active" if it has at least one MOVEMENT record in the trailing 30 days in INVENTORY_INTELLIGENCE.MOVEMENTS. SKUs with no movement in 30+ days are classified as "slow" or "dead" stock regardless of ON_HAND quantity.
""",
    })

    docs.append({
        "DOC_ID": "SOP-PICK-009",
        "TITLE": "Wave Planning and Release Standards",
        "DOC_TYPE": "PICK_PACK_SOP",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Wave Planning Standards

### Wave Formation Criteria
- Group by: carrier cutoff time → warehouse zone → order priority.
- Maximum wave size: 500 orders or 5,000 lines (whichever first).
- Minimum wave size: 20 orders (below this, hold for next wave unless expedited).

### Release Schedule
- Wave 1 (early AM): Released at 06:00, cutoff 10:00.
- Wave 2 (midday): Released at 10:00, cutoff 14:00.
- Wave 3 (afternoon): Released at 14:00, cutoff 17:00 (ground) / 18:00 (express).
- Wave 4 (evening, ATL-DC1 only): Released at 17:00, cutoff 20:00 (next-day express only).

### Priority Override
- NEXT_DAY orders released immediately on receipt (no wave queue).
- SAME_DAY orders bypass waves entirely — direct-to-floor assignment.

### Wave Status Definitions
- PLANNED: Wave formed, not yet released to floor.
- RELEASED: Picks assigned to pickers.
- IN_PROGRESS: At least one pick started.
- COMPLETE: All picks done, totes at pack.
- CANCELLED: Wave voided (e.g., carrier pickup cancelled).
""",
    })

    docs.append({
        "DOC_ID": "SOP-PICK-010",
        "TITLE": "Replenishment Process — Forward Pick Locations",
        "DOC_TYPE": "PICK_PACK_SOP",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Replenishment SOP

### Trigger
Replenishment is triggered when forward pick location qty falls below MIN_QTY threshold.
System calculates replenishment qty = MAX_QTY - CURRENT_QTY.

### Process
1. System creates replenishment task (MOVEMENT_TYPE = 'REPLENISHMENT').
2. Forklift operator retrieves from bulk/reserve location.
3. Deliver to forward pick location, scan to confirm.
4. ON_HAND updated for both locations.

### Priority
- Replenishment for active wave orders: HIGH (within 30 min).
- Proactive replenishment (forecast-based): MEDIUM (within 2 hours).
- Dead stock cleanup: LOW (next shift).
""",
    })

    docs.append({
        "DOC_ID": "SOP-PICK-011",
        "TITLE": "Quality Assurance — Outbound Audit Process",
        "DOC_TYPE": "PICK_PACK_SOP",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Outbound Quality Audit SOP

### Audit Rate
- Standard orders: 5% random audit.
- New picker (first 30 days): 25% audit.
- High-value orders (>$500): 100% audit.
- Hazmat: 100% audit.

### Audit Process
1. QA scanner intercepts package before seal.
2. Open, verify contents against packing slip.
3. Check item condition, quantity, correct SKU.
4. If pass: seal and release. If fail: return to pack station, log exception.

### Metrics
- Audit failure rate target: <0.3%.
- Root cause logged in EXCEPTIONS table (EXCEPTION_TYPE = 'QA_FAIL').
""",
    })

    docs.append({
        "DOC_ID": "SOP-PICK-012",
        "TITLE": "Receiving — Inbound ASN Processing",
        "DOC_TYPE": "PICK_PACK_SOP",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Inbound Receiving SOP

### ASN-Based Receiving
1. Verify trailer appointment against ASN (Advance Ship Notice).
2. Unload pallets; scan case barcodes against ASN manifest.
3. Variance tolerance: ±2% by unit count.
4. Over-receipt: stage separately, notify client.
5. Short-receipt: log exception, update PO.

### Putaway
1. System assigns putaway location based on item velocity and zone.
2. Scan location barcode to confirm placement.
3. MOVEMENTS record: MOVEMENT_TYPE = 'RECEIVE'.
4. ON_HAND updated with received quantity.

### Timing
- Unload within 2 hours of dock assignment.
- Putaway complete within 4 hours of unload.
""",
    })

    docs.append({
        "DOC_ID": "SOP-PICK-013",
        "TITLE": "Label and Document Printing Standards",
        "DOC_TYPE": "PICK_PACK_SOP",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Label and Document Standards

### Shipping Labels
- Format: 4x6 thermal direct (ZPL format).
- Must include: tracking barcode, service type, zone, weight.
- Carrier-specific format: FEDEX (FX2D), UPS (MaxiCode), USPS (IMpb).

### Packing Slips
- Auto-printed at pack station with order contents.
- Includes: order ID, SKU, description, qty, customer reference.
- No pricing shown on packing slip (B2B exception: include PO pricing if flagged).

### Compliance Labels
- Retail orders: GS1-128 case label per retailer spec.
- ASN required for all B2B shipments within 30 min of ship confirm.
""",
    })

    docs.append({
        "DOC_ID": "SOP-PICK-014",
        "TITLE": "Shift Handover Protocol",
        "DOC_TYPE": "PICK_PACK_SOP",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Shift Handover SOP

### End-of-Shift Responsibilities
1. Complete all in-progress picks (no partial totes left on floor).
2. Return incomplete waves to queue with status note.
3. Log any equipment issues in maintenance system.
4. Update whiteboard with: orders shipped, exceptions pending, carrier delays.

### Start-of-Shift Responsibilities
1. Review exceptions from previous shift.
2. Check carrier cutoff schedule for today.
3. Verify expedited order queue — any past-due?
4. Review replenishment backlog.
""",
    })

    docs.append({
        "DOC_ID": "SOP-PICK-015",
        "TITLE": "Inventory Adjustment Authorization",
        "DOC_TYPE": "PICK_PACK_SOP",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Inventory Adjustment Authorization

### Threshold Levels
- ≤ 5 units AND ≤ $100 value: picker self-approve, log reason.
- 6–50 units OR $100–$1000: supervisor approval required.
- >50 units OR >$1000: operations manager + client notification.
- Complete location zero-out: director approval + root cause investigation.

### Reason Codes
- DAMAGE: Item found damaged in location.
- EXPIRY: Past use-by date (food/pharma only).
- MISCOUNT: Cycle count variance.
- THEFT: Suspected shrinkage (triggers security review).

### Recording
All adjustments recorded as MOVEMENTS with MOVEMENT_TYPE = 'ADJUSTMENT'.
""",
    })

    return docs


def generate_exception_playbooks():
    docs = []

    # THE CRITICAL ONE: Tuesday wave missed cutoff
    docs.append({
        "DOC_ID": "PLAYBOOK-EXC-001",
        "TITLE": "Exception Playbook: Wave Missed Carrier Cutoff",
        "DOC_TYPE": "EXCEPTION_PLAYBOOK",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Exception Playbook: Wave Missed Carrier Cutoff

### Description
A wave's pick/pack completion time exceeds the carrier pickup cutoff, causing shipments to miss the scheduled truck and delay delivery by one business day.

### Root Causes (in order of frequency)
1. **Labor shortage on shift** — insufficient pickers assigned to wave volume.
2. **Late wave release** — wave not released until after planned start (e.g., waiting on inventory replenishment).
3. **High exception rate in wave** — short picks or QA failures causing rework.
4. **Carrier early departure** — truck arrived/departed before scheduled cutoff.
5. **System delay** — WMS batch job lag in releasing picks to floor.

### Specific Case: Alderwood Logistics Tuesday Waves
Alderwood Logistics (TENANT_ID = 'T001') historically experiences missed cutoffs on **Tuesday afternoons** at ATL-DC1. Root cause analysis determined:
- Tuesday is Alderwood's highest-volume day (30% above daily average) due to their retail replenishment cycle with big-box partners.
- Wave 3 (14:00 release, 17:00 ground cutoff) consistently overflows because Alderwood Tuesday volume fills Wave 2 capacity, pushing overflow into Wave 3.
- The 17:00 FedEx Ground cutoff at ATL-DC1 is the earliest of all DCs (others are 18:00+).
- **Resolution applied**: ATL-DC1 now splits Alderwood Tuesday volume across Waves 2 and 3 starting at 09:00 release (pre-wave), giving 8 hours of pick time instead of 3. Orders received after 13:00 Tuesday route to Wave 4 (express only) or defer to Wednesday Wave 1.
- **Monitoring**: Alert triggers if Alderwood Tuesday pick completion rate < 85% by 15:00.

### General Resolution Steps
1. **Immediate**: Identify unshipped orders in the missed wave. Check next available carrier pickup.
2. **If express carrier still available**: Re-manifest affected shipments to express service (cost escalation — log for client billing review).
3. **If no same-day option**: Mark shipments for first pickup next business day. Update customer delivery promise by +1 day.
4. **Notify client**: Auto-email to tenant account manager with affected order IDs, new ETAs.
5. **Log**: Create exception record (EXCEPTION_TYPE = 'MISSED_CUTOFF') with wave ID and root cause code.

### Prevention
- Set wave capacity alerts at 80% of labor-hour budget.
- Stagger high-volume tenant waves to avoid concentration.
- Pre-manifest express backup labels for orders within 2 hours of cutoff.

### Metrics to Monitor
- % of waves completing before cutoff (target: 98%).
- Average minutes-to-cutoff at wave completion.
- Exception rate by day-of-week and tenant (surface anomalies like the Tuesday pattern).
""",
    })

    docs.append({
        "DOC_ID": "PLAYBOOK-EXC-002",
        "TITLE": "Exception Playbook: Short Pick — Location Empty",
        "DOC_TYPE": "EXCEPTION_PLAYBOOK",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Exception Playbook: Short Pick — Location Empty

### Description
Picker arrives at assigned location and finds zero quantity despite system showing availability.

### Root Causes
1. Inventory not putaway after receiving (misplaced pallet).
2. Previous picker took extra units without scanning.
3. Cycle count not performed — phantom inventory.
4. Return not processed — system shows stock from expected return.

### Resolution
1. Picker marks short on RF gun → LINE_STATUS = 'SHORT'.
2. System checks alternate locations for same SKU.
3. If alternate found: redirect pick task to new location.
4. If no alternate: order ships partial (if allowed) or holds for replenishment.
5. Trigger cycle count of original location within 4 hours.
6. Exception logged: EXCEPTION_TYPE = 'SHORT_PICK'.

### Impact
- LINE_STATUS = 'SHORT' means QTY_SHIPPED_EACHES may be less than QTY_ORDERED_EACHES.
- A SHORT line can ship with partial quantity (not always zero).
""",
    })

    docs.append({
        "DOC_ID": "PLAYBOOK-EXC-003",
        "TITLE": "Exception Playbook: Carrier Damage Claim",
        "DOC_TYPE": "EXCEPTION_PLAYBOOK",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Exception Playbook: Carrier Damage in Transit

### Description
Customer reports package arrived damaged. Carrier scan shows delivery but item is broken/unusable.

### Evidence Required
1. Photo of damaged package exterior.
2. Photo of damaged item.
3. Tracking number and carrier scan history (from CARRIER_SCANS).
4. Original package weight vs delivered weight (if available).

### Resolution
1. Initiate carrier claim within 48 hours of delivery.
2. Reship replacement order (new ORDER_ID with reference to original).
3. Log exception: EXCEPTION_TYPE = 'CARRIER_DAMAGE'.
4. Track claim resolution (typically 5–15 business days for carrier response).

### Carrier-Specific Filing
- FEDEX: File via fedex.com/claims. Reference: tracking + service type.
- UPS: File via ups.com/claims. 60-day window from delivery.
- USPS: PS Form 1000 for insured packages. 30-day window.
""",
    })

    docs.append({
        "DOC_ID": "PLAYBOOK-EXC-004",
        "TITLE": "Exception Playbook: Address Correction Required",
        "DOC_TYPE": "EXCEPTION_PLAYBOOK",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Exception Playbook: Invalid/Undeliverable Address

### Triggers
- Address validation service returns "undeliverable" or "PO Box for ground."
- Carrier returns package as "No Such Address" or "Insufficient Address."

### Resolution
1. Hold shipment before manifest (if caught at validation).
2. Contact customer for correction (auto-email with 24-hour response window).
3. If no response in 24h: escalate to tenant account manager.
4. If package already shipped and returned: re-route or reship with corrected address.
5. Exception logged: EXCEPTION_TYPE = 'ADDRESS_INVALID'.

### Cost Impact
- Address correction surcharge: $18–$22 per package (carrier-dependent).
- Return-to-sender cost: full outbound rate + return rate.
""",
    })

    docs.append({
        "DOC_ID": "PLAYBOOK-EXC-005",
        "TITLE": "Exception Playbook: Order Cancelled After Wave Release",
        "DOC_TYPE": "EXCEPTION_PLAYBOOK",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Exception Playbook: Cancellation After Wave Release

### Description
Customer cancels order after it has been released to a pick wave (already assigned to pickers).

### Decision Points
1. **Before pick start**: Cancel pick tasks, remove from wave, return to order pool.
2. **During pick**: If tote not sealed — return items to locations, void tote.
3. **After pack, before manifest**: Void shipping label, return to stock.
4. **After manifest/carrier tender**: Package recall (carrier intercept fee ~$15).

### Resolution
1. System checks pick task status for each line.
2. Apply cheapest reversal path based on current state.
3. Exception logged: EXCEPTION_TYPE = 'CANCELLED_POST_WAVE'.
4. Inventory updated with returns to location.
""",
    })

    docs.append({
        "DOC_ID": "PLAYBOOK-EXC-006",
        "TITLE": "Exception Playbook: Duplicate Order Detection",
        "DOC_TYPE": "EXCEPTION_PLAYBOOK",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Exception Playbook: Duplicate Order

### Detection Rules
- Same customer + same SKUs + same quantities within 5 minutes = likely duplicate.
- Same PO number from B2B customer = definite duplicate.
- System flags for review; does NOT auto-cancel.

### Resolution
1. Hold second order in EXCEPTION status.
2. Notify tenant account manager.
3. If confirmed duplicate: cancel and log EXCEPTION_TYPE = 'DUPLICATE_ORDER'.
4. If confirmed intentional (reorder): release to normal flow.
""",
    })

    docs.append({
        "DOC_ID": "PLAYBOOK-EXC-007",
        "TITLE": "Exception Playbook: Overweight Package — Carrier Rejection",
        "DOC_TYPE": "EXCEPTION_PLAYBOOK",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Exception Playbook: Overweight/Oversize Rejection

### Trigger
Package exceeds carrier weight/size limit for selected service.
- FEDEX Ground: 150 lbs max, 108" max length, 165" L+G.
- UPS Ground: 150 lbs max, 108" max length, 165" L+G.
- USPS: 70 lbs max, 108" combined L+G.
- XPO/LTL: No standard max (freight class applies).

### Resolution
1. Re-rate package with correct carrier/service (usually upgrade to LTL/freight).
2. If multi-carton is possible: split into smaller packages under limit.
3. Re-manifest with new carrier. Void original label.
4. Exception logged: EXCEPTION_TYPE = 'OVERWEIGHT'.
5. Cost delta logged for client billing review.
""",
    })

    docs.append({
        "DOC_ID": "PLAYBOOK-EXC-008",
        "TITLE": "Exception Playbook: Inventory Hold — Quarantine",
        "DOC_TYPE": "EXCEPTION_PLAYBOOK",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Exception Playbook: Inventory Hold / Quarantine

### Triggers
- Client-initiated hold (recall, quality issue).
- Regulatory hold (FDA, CPSC recall notice).
- Internal QA failure during receiving or cycle count.

### Resolution
1. Immediately change affected inventory to HOLD status.
2. Move physical stock to quarantine zone.
3. Block all pick tasks for affected SKU+lot.
4. Orders with affected items: hold in exception queue.
5. Wait for disposition instruction (rework, destroy, return to vendor).
6. Exception logged: EXCEPTION_TYPE = 'QUARANTINE'.
""",
    })

    docs.append({
        "DOC_ID": "PLAYBOOK-EXC-009",
        "TITLE": "Exception Playbook: Mis-ship — Wrong Item Sent",
        "DOC_TYPE": "EXCEPTION_PLAYBOOK",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Exception Playbook: Mis-ship (Wrong Item Delivered)

### Detection
- Customer contacts support reporting wrong item.
- Inventory discrepancy found at cycle count (swapped locations).

### Root Causes
1. Items stored in wrong location (putaway error).
2. Picker grabbed adjacent item (location proximity issue).
3. UPC barcode damaged/unreadable — picker overrode scan.
4. Look-alike SKUs in adjacent slots.

### Resolution
1. Ship correct item immediately (expedited, no charge).
2. Provide prepaid return label for wrong item.
3. On return: inspect, restock, update inventory.
4. Root cause: adjust slotting if proximity issue.
5. Exception logged: EXCEPTION_TYPE = 'MIS_SHIP'.
""",
    })

    docs.append({
        "DOC_ID": "PLAYBOOK-EXC-010",
        "TITLE": "Exception Playbook: Carrier Pickup No-Show",
        "DOC_TYPE": "EXCEPTION_PLAYBOOK",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Exception Playbook: Carrier Pickup No-Show

### Description
Scheduled carrier truck does not arrive for pickup by cutoff + 30 minutes.

### Resolution
1. Contact carrier dispatch for ETA (phone numbers in carrier contact sheet).
2. If ETA > 1 hour past cutoff: manifest affected shipments to alternate carrier.
3. If no alternate available: hold for next business day first pickup.
4. Exception logged: EXCEPTION_TYPE = 'CARRIER_NO_SHOW'.
5. File service failure claim with carrier (credit for delayed pickup).

### Impact
All orders on the missed pickup delay +1 business day minimum.
Update customer delivery promises accordingly.
""",
    })

    docs.append({
        "DOC_ID": "PLAYBOOK-EXC-011",
        "TITLE": "Exception Playbook: System Downtime — WMS Outage",
        "DOC_TYPE": "EXCEPTION_PLAYBOOK",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Exception Playbook: WMS System Downtime

### Immediate Actions
1. Switch to paper-based picking (pre-printed wave sheets kept as backup).
2. No new waves released during outage.
3. Packing continues for already-picked totes (labels printed offline).
4. Receiving: paper log receipts, enter post-recovery.

### Recovery
1. On system restore: reconcile paper picks against system state.
2. Void and re-print any labels generated offline that don't match.
3. Log all orders affected: EXCEPTION_TYPE = 'SYSTEM_OUTAGE'.
4. Report actual downtime and order impact to all tenants.
""",
    })

    docs.append({
        "DOC_ID": "PLAYBOOK-EXC-012",
        "TITLE": "Exception Playbook: Hazmat Spill in Warehouse",
        "DOC_TYPE": "EXCEPTION_PLAYBOOK",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Exception Playbook: Hazmat Spill

### Immediate Actions
1. Evacuate affected zone. Activate spill response team.
2. Contain spill (absorbent materials, berms).
3. Stop all pick activity in affected zone.

### Resolution
1. Spill response per MSDS for the specific material.
2. Document: photos, affected area, estimated qty spilled.
3. Dispose of contaminated materials per EPA guidelines.
4. Adjust inventory for lost/destroyed product.
5. Zone inspection before reopening to operations.
6. Exception logged: EXCEPTION_TYPE = 'HAZMAT_INCIDENT'.
""",
    })

    docs.append({
        "DOC_ID": "PLAYBOOK-EXC-013",
        "TITLE": "Exception Playbook: SLA Breach — Late Shipment",
        "DOC_TYPE": "EXCEPTION_PLAYBOOK",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Exception Playbook: SLA Breach — Shipment Past SHIP_BY_DATE

### Definition
A shipment is "late" when CARRIER_FIRST_SCAN_TS > SHIP_BY_DATE on the order.
This is the contractual SLA metric — NOT based on PROMISED_DELIVERY_DATE (which is the carrier's transit commitment to the end customer).

### Important Distinction
- **On-time to ship** (our SLA): CARRIER_FIRST_SCAN_TS ≤ SHIP_BY_DATE
- **On-time to deliver** (carrier's SLA): actual delivery ≤ PROMISED_DELIVERY_DATE
- These are different metrics. We control the first; the carrier controls the second.

### Resolution
1. Identify root cause: warehouse delay vs order-entry error vs carrier late pickup.
2. If warehouse fault: credit client per SLA penalty schedule in contract.
3. If carrier fault: file claim with carrier; no warehouse penalty.
4. Exception logged: EXCEPTION_TYPE = 'LATE_SHIPMENT'.

### Prevention
- Flagging: orders within 4 hours of SHIP_BY_DATE with no pick task = auto-escalate.
- Dashboard alert: orders passing SHIP_BY_DATE without CARRIER_FIRST_SCAN_TS.
""",
    })

    docs.append({
        "DOC_ID": "PLAYBOOK-EXC-014",
        "TITLE": "Exception Playbook: Allocation Conflict — Oversold SKU",
        "DOC_TYPE": "EXCEPTION_PLAYBOOK",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Exception Playbook: Oversold / Allocation Conflict

### Description
Multiple orders compete for the same limited inventory. Total ordered > available.

### Resolution Priority
1. First-in-first-out by ORDER_DATE.
2. Within same date: PRIORITY field (NEXT_DAY > EXPEDITED > STANDARD).
3. Within same priority: B2B customers with contractual SLAs first.
4. Remaining orders: hold in allocation queue, await replenishment.
5. If replenishment ETA > 5 days: offer substitute SKU or cancel line.
6. Exception logged: EXCEPTION_TYPE = 'OVERSOLD'.
""",
    })

    docs.append({
        "DOC_ID": "PLAYBOOK-EXC-015",
        "TITLE": "Exception Playbook: Label Print Failure",
        "DOC_TYPE": "EXCEPTION_PLAYBOOK",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Exception Playbook: Label Print Failure

### Common Causes
1. Thermal printer out of labels/ribbon.
2. Network connectivity loss to label server.
3. Carrier API rate limit hit (manifesting too fast).
4. Invalid address data causing label generation error.

### Resolution
1. Identify scope: single label vs bulk failure.
2. Single label: reprint from WMS. Check address validity.
3. Bulk failure (printer/network): redirect to backup printer.
4. API rate limit: queue and retry with exponential backoff.
5. If all printers down: hold packages, do NOT ship unlabeled.
""",
    })

    docs.append({
        "DOC_ID": "PLAYBOOK-EXC-016",
        "TITLE": "Exception Playbook: Customer Refused Delivery",
        "DOC_TYPE": "EXCEPTION_PLAYBOOK",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Exception Playbook: Delivery Refused

### Detection
Carrier scan shows 'REFUSED' or 'RETURN TO SENDER' scan event.
Visible in CARRIER_SCANS with SCAN_TYPE = 'REFUSED'.

### Resolution
1. Package returns to warehouse (3–7 business days for ground).
2. On receipt: process as return (Grade A/B/C per returns SOP).
3. Notify tenant of refused delivery + reason if known.
4. Restock if Grade A; client decides on re-ship.
5. Exception logged: EXCEPTION_TYPE = 'REFUSED'.
""",
    })

    docs.append({
        "DOC_ID": "PLAYBOOK-EXC-017",
        "TITLE": "Exception Playbook: Incorrect Cartonization",
        "DOC_TYPE": "EXCEPTION_PLAYBOOK",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Exception Playbook: Incorrect Cartonization

### Description
Items packed in wrong-size box, causing DIM weight overage or damage risk.

### Detection
- Scale check at pack station: actual weight vs expected differs by >15%.
- Box too large: excess dunnage needed, DIM weight exceeds actual by 2x+.
- Box too small: items compressed, damage risk.

### Resolution
1. Repack in correct carton size per cartonization table.
2. Void original label (DIM weight may change rate).
3. Re-manifest with correct dimensions.
4. Root cause: update cartonization rules if systematic.
""",
    })

    docs.append({
        "DOC_ID": "PLAYBOOK-EXC-018",
        "TITLE": "Exception Playbook: Multi-Tenant Data Leak Prevention",
        "DOC_TYPE": "EXCEPTION_PLAYBOOK",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Exception Playbook: Tenant Data Isolation Breach

### Description
A query, report, or API response exposes data belonging to a different tenant.
This is a CRITICAL severity incident.

### Detection
- Cross-tenant order ID referenced in wrong tenant's report.
- Aggregate metrics include data from other tenants.
- API response includes TENANT_ID different from authenticated user's tenant.

### Immediate Actions
1. Halt affected report/API endpoint.
2. Identify scope: which tenants' data was exposed, to whom, for how long.
3. Engage security team within 15 minutes.
4. Preserve all logs and query history.

### Root Causes
- Missing TENANT_ID filter in query/view.
- Row access policy not applied to new table.
- Report hardcoded wrong tenant filter.
- API session token mismatch.

### Prevention
- ALL queries MUST filter by TENANT_ID from session context.
- Row access policies on every tenant-scoped table.
- Quarterly audit: run cross-tenant test queries and verify zero rows returned.
""",
    })

    docs.append({
        "DOC_ID": "PLAYBOOK-EXC-019",
        "TITLE": "Exception Playbook: Dock Door Scheduling Conflict",
        "DOC_TYPE": "EXCEPTION_PLAYBOOK",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Exception Playbook: Dock Door Conflict

### Description
Multiple carriers scheduled for same dock door at same time, or inbound receiving blocks outbound pickup.

### Resolution
1. Priority: outbound pickups always take priority over inbound receiving (revenue impact).
2. Redirect inbound to alternate door or hold in yard.
3. If no alternate door: carrier waits in queue (detention charges may apply after 2 hours).
4. Log conflict for dock scheduling system improvement.
""",
    })

    docs.append({
        "DOC_ID": "PLAYBOOK-EXC-020",
        "TITLE": "Exception Playbook: Partial Shipment Authorization",
        "DOC_TYPE": "EXCEPTION_PLAYBOOK",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Exception Playbook: Partial Shipment Decision

### When to Ship Partial
- Order has SHIP_PARTIAL_FLAG = 'Y' AND at least one line is complete.
- Or: PRIORITY = 'NEXT_DAY' / 'SAME_DAY' and delay for full order would breach SLA.
- Or: Replenishment ETA > 3 business days and client pre-authorized partial.

### When to Hold
- SHIP_PARTIAL_FLAG = 'N' (client requires complete order).
- All lines are SHORT (nothing to ship).
- Regulatory hold on any line item blocks entire order.

### Cost Impact
- Partial shipments incur double shipping cost (original + backorder).
- Client billing: per contract — some absorb split-ship cost, others pass through.
- Track in SHIPMENTS: PACKAGE_COUNT and link to original ORDER_ID.
""",
    })

    return docs


def generate_cutoff_policies():
    docs = []

    docs.append({
        "DOC_ID": "POLICY-CUT-001",
        "TITLE": "Carrier Cutoff Schedule — ATL-DC1",
        "DOC_TYPE": "CUTOFF_POLICY",
        "CARRIER": None,
        "WAREHOUSE_ID": "ATL-DC1",
        "CONTENT": """## Carrier Cutoff Schedule — ATL-DC1 (Atlanta)

### Daily Cutoff Times (Eastern Time)
| Carrier | Ground | Express | Next Day Air |
|---------|--------|---------|--------------|
| FEDEX   | 17:00  | 18:30   | 20:00        |
| UPS     | 17:30  | 18:00   | 19:30        |
| USPS    | 16:00  | N/A     | N/A          |
| DHL     | 16:30  | 17:30   | 19:00        |
| ONTRAC  | 18:00  | N/A     | N/A          |
| XPO     | 15:00  | N/A     | N/A          |

### Notes
- FEDEX Ground cutoff at ATL-DC1 is 17:00 — this is the EARLIEST ground cutoff across all DCs.
- Saturday pickup available: FEDEX only, 12:00 noon cutoff, express services only.
- Holiday schedule: No pickups on federal holidays. Day-before-holiday cutoffs move 2 hours earlier.
- XPO (LTL): appointment-based, typically scheduled day-prior. 15:00 is the latest same-day booking time.
""",
    })

    docs.append({
        "DOC_ID": "POLICY-CUT-002",
        "TITLE": "Carrier Cutoff Schedule — ATL-DC2",
        "DOC_TYPE": "CUTOFF_POLICY",
        "CARRIER": None,
        "WAREHOUSE_ID": "ATL-DC2",
        "CONTENT": """## Carrier Cutoff Schedule — ATL-DC2 (Atlanta South)

### Daily Cutoff Times (Eastern Time)
| Carrier | Ground | Express | Next Day Air |
|---------|--------|---------|--------------|
| FEDEX   | 18:00  | 19:00   | 20:30        |
| UPS     | 18:00  | 18:30   | 20:00        |
| USPS    | 16:30  | N/A     | N/A          |
| DHL     | 17:00  | 18:00   | 19:30        |
| ONTRAC  | 18:30  | N/A     | N/A          |
| XPO     | 16:00  | N/A     | N/A          |

### Notes
- Later cutoffs than ATL-DC1 due to later carrier route scheduling.
- Overflow from ATL-DC1 routes here when ATL-DC1 hits capacity.
""",
    })

    docs.append({
        "DOC_ID": "POLICY-CUT-003",
        "TITLE": "Carrier Cutoff Schedule — CHI-DC1",
        "DOC_TYPE": "CUTOFF_POLICY",
        "CARRIER": None,
        "WAREHOUSE_ID": "CHI-DC1",
        "CONTENT": """## Carrier Cutoff Schedule — CHI-DC1 (Chicago)

### Daily Cutoff Times (Central Time)
| Carrier | Ground | Express | Next Day Air |
|---------|--------|---------|--------------|
| FEDEX   | 18:00  | 19:00   | 20:30        |
| UPS     | 18:30  | 19:00   | 20:00        |
| USPS    | 16:00  | N/A     | N/A          |
| DHL     | 17:00  | 18:00   | 19:00        |
| ONTRAC  | N/A    | N/A     | N/A          |
| XPO     | 16:00  | N/A     | N/A          |

### Notes
- ONTRAC does not service Chicago metro. Use FEDEX/UPS for regional delivery.
- Winter weather: cutoffs may move 1 hour earlier Nov–Feb on severe weather days.
""",
    })

    docs.append({
        "DOC_ID": "POLICY-CUT-004",
        "TITLE": "Carrier Cutoff Schedule — DAL-DC1",
        "DOC_TYPE": "CUTOFF_POLICY",
        "CARRIER": None,
        "WAREHOUSE_ID": "DAL-DC1",
        "CONTENT": """## Carrier Cutoff Schedule — DAL-DC1 (Dallas)

### Daily Cutoff Times (Central Time)
| Carrier | Ground | Express | Next Day Air |
|---------|--------|---------|--------------|
| FEDEX   | 18:30  | 19:30   | 21:00        |
| UPS     | 18:00  | 19:00   | 20:30        |
| USPS    | 16:30  | N/A     | N/A          |
| DHL     | 17:30  | 18:30   | 19:30        |
| ONTRAC  | N/A    | N/A     | N/A          |
| XPO     | 16:30  | N/A     | N/A          |

### Notes
- Latest FEDEX Ground cutoff in the network (18:30 CT).
- Hub proximity advantage: FEDEX hub in Fort Worth enables late departures.
""",
    })

    docs.append({
        "DOC_ID": "POLICY-CUT-005",
        "TITLE": "Carrier Cutoff Schedule — LAX-DC1",
        "DOC_TYPE": "CUTOFF_POLICY",
        "CARRIER": None,
        "WAREHOUSE_ID": "LAX-DC1",
        "CONTENT": """## Carrier Cutoff Schedule — LAX-DC1 (Los Angeles)

### Daily Cutoff Times (Pacific Time)
| Carrier | Ground | Express | Next Day Air |
|---------|--------|---------|--------------|
| FEDEX   | 18:00  | 19:00   | 20:00        |
| UPS     | 18:00  | 19:00   | 20:00        |
| USPS    | 16:00  | N/A     | N/A          |
| DHL     | 17:00  | 18:30   | 19:30        |
| ONTRAC  | 19:00  | N/A     | N/A          |
| XPO     | 16:00  | N/A     | N/A          |

### Notes
- ONTRAC has strongest coverage in CA/West Coast — preferred for regional ground.
- ONTRAC cutoff is the latest of any carrier at any DC (19:00 PT).
""",
    })

    docs.append({
        "DOC_ID": "POLICY-CUT-006",
        "TITLE": "Wave Release Schedule — All DCs",
        "DOC_TYPE": "CUTOFF_POLICY",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Standard Wave Release Schedule

### All DCs (Local Time)
| Wave | Release Time | Target Complete | Services |
|------|-------------|-----------------|----------|
| 1    | 06:00       | 10:00           | All services |
| 2    | 10:00       | 14:00           | All services |
| 3    | 14:00       | 17:00           | Ground + Express |
| 4    | 17:00       | 20:00           | Express/Next Day only |

### Rules
- Orders received before wave release time are included in that wave.
- Orders received after Wave 3 release: queue for Wave 4 (express) or next-day Wave 1 (ground).
- Expedited/Next_Day orders bypass wave queue entirely — direct-to-floor.
- Wave capacity: 500 orders max. If exceeded, overflow to next wave.

### Holiday Adjustments
- Day before holiday: only Waves 1–3. Wave 4 cancelled.
- Day after holiday: extra Wave 0 at 04:00 to clear backlog.
""",
    })

    docs.append({
        "DOC_ID": "POLICY-CUT-007",
        "TITLE": "Order Receipt Cutoff — Same-Day Eligibility",
        "DOC_TYPE": "CUTOFF_POLICY",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Same-Day Shipping Eligibility Cutoff

### Criteria for Same-Day Ship
1. Order received before **14:00 local DC time** (ground services).
2. Order received before **16:00 local DC time** (express services).
3. All lines in stock (no allocation holds).
4. No hazmat items (requires next-day processing minimum).
5. Address validated (no exceptions).

### Next-Day Air Eligibility
- Order received before **18:00 local** qualifies for next-business-day delivery.
- After 18:00: delivery guarantee is business day + 2.

### Weekend Orders
- Received Saturday/Sunday: queued for Monday Wave 1.
- Exception: tenants with Saturday processing agreement (currently none active).
""",
    })

    docs.append({
        "DOC_ID": "POLICY-CUT-008",
        "TITLE": "Peak Season Cutoff Adjustments",
        "DOC_TYPE": "CUTOFF_POLICY",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Peak Season Cutoff Policy (Nov 15 – Dec 31)

### Adjusted Carrier Cutoffs
- All carrier cutoffs move 1 hour EARLIER during peak season.
- Additional Saturday pickups added (FEDEX + UPS at all DCs).
- USPS holiday deadline: Dec 19 (ground), Dec 21 (Priority), Dec 23 (Express).

### Wave Adjustments
- Wave 0 added at 04:00 (overnight received orders).
- Wave 3 split into 3A (14:00, standard) and 3B (15:30, express only).
- Maximum wave size increased to 750 orders during peak.

### Staffing
- Peak staffing level: 150% of standard across all shifts.
- Mandatory overtime available (up to 12-hour shifts).
""",
    })

    docs.append({
        "DOC_ID": "POLICY-CUT-009",
        "TITLE": "Tenant-Specific SLA Cutoffs",
        "DOC_TYPE": "CUTOFF_POLICY",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Tenant-Specific SLA Commitments

### Alderwood Logistics (T001)
- Ship-by SLA: 1 business day from order receipt.
- Tuesday volume surge: pre-wave at 09:00 to spread load.
- Penalty: $5/order for breach of ship-by date.

### Bellweather Freight (T002)
- Ship-by SLA: 2 business days (heavy freight, longer pick times).
- XPO LTL must be booked by 14:00 day prior.
- No Saturday processing.

### Cobalt Apparel (T003)
- Ship-by SLA: 1 business day (standard), same-day (expedited).
- Seasonal peaks: August (back-to-school), November (holiday).
- Requires branded packing slip with logo.

### Dunmore Distribution (T004)
- Ship-by SLA: same-day for orders received before 14:00.
- DTC ecommerce focus: high carrier mix (FEDEX 40%, USPS 35%, UPS 25%).

### Everline Medical (T005)
- Ship-by SLA: same-day for all orders (healthcare urgency).
- Cold chain required for 30% of SKUs.
- Regulatory: lot tracking mandatory.

### Foxglove Foods (T006)
- Ship-by SLA: same-day (perishable items cannot hold).
- Overnight carrier only (no ground for temp-sensitive).
- Weekend processing: Saturday shipping active.
""",
    })

    docs.append({
        "DOC_ID": "POLICY-CUT-010",
        "TITLE": "Carrier Service Level Selection Rules",
        "DOC_TYPE": "CUTOFF_POLICY",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Carrier & Service Selection Rules (Rate Shopping)

### Selection Priority
1. Meet delivery promise (PROMISED_DELIVERY_DATE must be achievable).
2. Cheapest qualifying carrier from ZONE_RATE_CARDS.
3. If tied on cost: prefer carrier with best on-time record for that zone.

### Override Rules
- HAZMAT_FLAG = 'Y': exclude USPS (no hazmat), prefer FEDEX/UPS.
- TEMPERATURE_SENSITIVE = 'Y': overnight only. Exclude ground services.
- WEIGHT > 150 LBS: must use XPO (LTL/freight).
- ONTRAC: only for zones 1–3 (West Coast regional).

### Cost Calculation
Total cost per package = RATE_PER_PACKAGE × (1 + FUEL_SURCHARGE_PCT/100) + accessorials.
Rate lookup: SHIPPING_INTELLIGENCE.ZONE_RATE_CARDS ON (CARRIER, ZONE, WEIGHT_BREAK).
""",
    })

    docs.append({
        "DOC_ID": "POLICY-CUT-011",
        "TITLE": "Manifest Close and End-of-Day Process",
        "DOC_TYPE": "CUTOFF_POLICY",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## End-of-Day Manifest Close Process

### Timing
- Manifest closes 30 minutes before last carrier pickup.
- No new shipments manifested after close (hold for next day).

### Process
1. System auto-closes manifest at scheduled time.
2. Generate carrier pickup scan sheet (physical count verification).
3. Driver scans all packages at dock door.
4. Discrepancy (manifest vs physical): investigate before driver departs.
5. CARRIER_FIRST_SCAN_TS recorded when carrier scans package.

### Important
- CARRIER_FIRST_SCAN_TS is the definitive "shipped" timestamp for SLA purposes.
- This is NOT the same as when WMS marks the order shipped internally.
- SLA measurement: CARRIER_FIRST_SCAN_TS ≤ SHIP_BY_DATE = on-time.
""",
    })

    docs.append({
        "DOC_ID": "POLICY-CUT-012",
        "TITLE": "International Shipment Cutoffs and Documentation",
        "DOC_TYPE": "CUTOFF_POLICY",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## International Shipment Policy

### Cutoffs
- International express (DHL/FEDEX): 2 hours before domestic express cutoff.
- Reason: customs documentation preparation time.

### Required Documentation
- Commercial invoice (auto-generated from order data).
- Customs declaration (HS code required per SKU — in ITEM_MASTER extended attributes).
- Certificate of origin (if required by destination country).

### Restrictions
- Hazmat: no international shipment of Class 1–6 materials.
- Perishable: international cold chain only via DHL ThermoNet.
- Value > $2500: requires formal customs entry (Importer of Record needed).
""",
    })

    return docs


def generate_general_ops_docs():
    docs = []

    docs.append({
        "DOC_ID": "OPS-GEN-001",
        "TITLE": "KPI Definitions — Fulfillment Operations",
        "DOC_TYPE": "GENERAL_OPS",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## KPI Definitions for Fulfillment Operations

### On-Time Shipment Rate
**Definition**: Percentage of orders where CARRIER_FIRST_SCAN_TS ≤ SHIP_BY_DATE.
**Correct formula**: COUNT(orders WHERE carrier_first_scan_ts <= ship_by_date) / COUNT(all shipped orders)
**Important**: Do NOT use PROMISED_DELIVERY_DATE for this metric. That date is the carrier's delivery commitment, not our shipping SLA.
**Target**: 95% across all tenants.

### Fill Rate (THREE distinct definitions)
1. **Order Fill Rate**: % of orders where ALL lines are complete (LINES_FILLED = TOTAL_LINES).
   Formula: COUNT(orders WHERE lines_filled = total_lines) / COUNT(all orders)
2. **Line Fill Rate**: % of order lines shipped complete.
   Formula: SUM(lines_filled) / SUM(total_lines) across all orders.
3. **Unit Fill Rate**: % of units (eaches) shipped vs ordered.
   Formula: SUM(qty_shipped_eaches) / SUM(qty_ordered_eaches) across all order lines.
**CRITICAL**: Always specify WHICH fill rate when reporting. They produce very different numbers.

### Units Shipped
**Definition**: Total eaches shipped. NOT cartons, NOT lines.
- Eaches: individual units (QTY_SHIPPED_EACHES on ORDER_LINES)
- Cartons: derived from eaches / EACHES_PER_CARTON (from ITEM_MASTER join)
- Lines: count of ORDER_LINES rows
**These are three completely different numbers.**

### Active SKU Count
**Definition**: SKUs with at least one MOVEMENT record in the trailing 30 days.
Source: INVENTORY_INTELLIGENCE.MOVEMENTS WHERE MOVEMENT_DATE >= CURRENT_DATE - 30.
**NOT**: total SKUs in ITEM_MASTER (which includes dead stock).

### Cost Per Shipment
**Definition**: Total shipping cost divided by number of shipments.
Requires joining SHIPMENTS to ZONE_RATE_CARDS on (CARRIER, ZONE, WEIGHT_BREAK).
Cost = RATE_PER_PACKAGE * (1 + FUEL_SURCHARGE_PCT/100).
""",
    })

    docs.append({
        "DOC_ID": "OPS-GEN-002",
        "TITLE": "Fiscal Calendar — 4-4-5 Retail Calendar Explained",
        "DOC_TYPE": "GENERAL_OPS",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## 4-4-5 Retail Fiscal Calendar

### Overview
Our fiscal calendar follows the 4-4-5 retail pattern, NOT the standard Gregorian calendar.
"Last month" in business context means the LAST FISCAL PERIOD, not the last calendar month.

### Structure
- Fiscal year starts on the Sunday closest to February 1.
- 12 periods per year, grouped into 4 quarters.
- Each quarter has 3 periods: 4 weeks + 4 weeks + 5 weeks = 13 weeks/quarter.
- Total: 52 weeks (364 days). 53rd week added every 5–6 years.

### Lookup Table
Use FULFILLMENT_INTELLIGENCE.FISCAL_CALENDAR_445 to map any CALENDAR_DATE to its:
- FISCAL_YEAR, FISCAL_QUARTER, FISCAL_PERIOD, FISCAL_WEEK

### Why This Matters
- A fiscal period is exactly 4 or 5 weeks. Calendar months are 28–31 days.
- Comparing "last period" to "this period" gives apples-to-apples (same number of weeks).
- Comparing calendar months has built-in noise (different days per month, weekend distribution).
- When someone asks for "monthly" metrics, ask: fiscal period or calendar month?

### Important Note
The fiscal calendar table starts on 2025-01-26 (first fiscal week of FY2025).
Orders before 2025-01-26 do not have a fiscal calendar mapping — use calendar dates for that range.
""",
    })

    docs.append({
        "DOC_ID": "OPS-GEN-003",
        "TITLE": "Warehouse Layout — Zone Definitions",
        "DOC_TYPE": "GENERAL_OPS",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Warehouse Zone Layout

### Standard Zone Structure (all DCs)
- **ZONE-A**: Fast-moving / high-velocity SKUs. Floor-level pick, conveyor-fed.
- **ZONE-B**: Medium movers. Standard shelving, 4 levels.
- **ZONE-C**: Slow movers. Deep racking, forklift-access upper levels.
- **ZONE-D**: Bulk/oversize items. Wide-aisle racking.
- **COLD-A to COLD-D**: Refrigerated zone (34–38°F).
- **FREEZE-A, FREEZE-B**: Frozen zone (-10 to 0°F).
- **HAZMAT-A**: Isolated hazmat storage with spill containment.
- **RETURNS**: Dedicated returns processing area.
- **STAGING**: Outbound staging (sorted by carrier/route).

### Zone Assignment in Data
PICK_TASKS.ZONE reflects where the picker physically went.
LABOR_STANDARDS are defined per zone + pick method combination.
""",
    })

    docs.append({
        "DOC_ID": "OPS-GEN-004",
        "TITLE": "Tenant Onboarding Checklist",
        "DOC_TYPE": "GENERAL_OPS",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## New Tenant Onboarding Checklist

### Data Setup
1. Assign TENANT_ID (format: T###).
2. Create tenant role in WMS.
3. Load item master (SKU, description, dims, weight, category).
4. Configure zone rate cards (carrier contracts specific to tenant).
5. Set SLA parameters (ship-by rules, partial ship policy).
6. Configure row access policy for tenant data isolation.

### Operational Setup
1. Assign warehouse locations (zone allocation based on forecast volume).
2. Set up packing slip templates (branded vs generic).
3. Configure carrier accounts and label formats.
4. Define wave priority rules and cutoff preferences.
5. Establish returns address and RMA process.

### Go-Live
1. Pilot batch: 50 orders through full cycle.
2. Verify: labels, tracking, scan events, SLA measurement.
3. Production release after pilot validation.
""",
    })

    docs.append({
        "DOC_ID": "OPS-GEN-005",
        "TITLE": "Data Model Reference — Table Relationships",
        "DOC_TYPE": "GENERAL_OPS",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Data Model — Key Relationships

### Order Lifecycle
ORDERS (1) → ORDER_LINES (many) → PICK_TASKS (one per line) → SHIPMENTS (aggregated)

### Key Joins
- ORDERS.ORDER_ID = ORDER_LINES.ORDER_ID
- ORDER_LINES.ORDER_ID = PICK_TASKS.ORDER_ID AND ORDER_LINES.ORDER_LINE_ID = PICK_TASKS.ORDER_LINE_ID
- ORDERS.ORDER_ID = SHIPMENTS.ORDER_ID
- SHIPMENTS.SHIPMENT_ID = CARRIER_SCANS.SHIPMENT_ID
- SHIPMENTS.(CARRIER, ZONE, WEIGHT_BREAK) = ZONE_RATE_CARDS.(CARRIER, ZONE, WEIGHT_BREAK) [for cost]
- ORDER_LINES.SKU = ITEM_MASTER.SKU [for item attributes]
- ORDER_LINES.WAVE_ID = WAVES.WAVE_ID

### Tenant Scope
TENANT_ID is present on: ORDERS, ORDER_LINES, EXCEPTIONS, ON_HAND, MOVEMENTS, PICK_TASKS, SHIPMENTS, CARRIER_SCANS.
All tenant-scoped queries MUST filter by TENANT_ID.

### Fiscal Calendar Join
FISCAL_CALENDAR_445.CALENDAR_DATE = DATE(any_timestamp_column)
""",
    })

    docs.append({
        "DOC_ID": "OPS-GEN-006",
        "TITLE": "Reporting Cadence and Audience",
        "DOC_TYPE": "GENERAL_OPS",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Reporting Cadence

### Daily Operations Report (7:00 AM)
- Yesterday's orders shipped, exceptions, on-time rate.
- Today's order backlog and wave plan.
- Audience: DC operations managers.

### Weekly Tenant Scorecard (Monday AM)
- Prior week: fill rate, on-time rate, exception count, units shipped.
- Audience: tenant account managers + client contacts.
- Uses fiscal week boundaries (Mon–Sun).

### Monthly Executive Summary
- Full fiscal period metrics with trend lines.
- Cost per shipment by tenant and carrier.
- SLA compliance with penalty/credit accounting.
- Audience: VP Operations, CFO.

### Quarterly Business Review (per tenant)
- Period-over-period comparison (4-4-5 calendar).
- Volume forecast vs actual.
- Service level vs contractual targets.
- Rate optimization recommendations.
""",
    })

    docs.append({
        "DOC_ID": "OPS-GEN-007",
        "TITLE": "Escalation Matrix — Operations Issues",
        "DOC_TYPE": "GENERAL_OPS",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Escalation Matrix

### Severity Levels
| Level | Definition | Response Time | Escalation To |
|-------|-----------|---------------|---------------|
| P1 - Critical | System down, all operations halted | 15 min | VP Ops + IT Director |
| P2 - High | Carrier missed, >100 orders delayed | 30 min | Ops Manager + Tenant AM |
| P3 - Medium | Single tenant SLA at risk | 2 hours | Shift Supervisor |
| P4 - Low | Individual order exception | 4 hours | Team Lead |

### After-Hours Escalation
- P1: On-call ops manager (rotating schedule) + IT support.
- P2: On-call ops manager.
- P3/P4: Queue for next business day unless client flags urgent.
""",
    })

    docs.append({
        "DOC_ID": "OPS-GEN-008",
        "TITLE": "Inventory Accuracy Standards",
        "DOC_TYPE": "GENERAL_OPS",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Inventory Accuracy Standards

### Targets
- Location accuracy: 99.5% (unit count matches system at location level).
- SKU accuracy: 99.9% (no wrong SKU in location).
- System-wide inventory variance: < 0.1% of total value.

### Measurement
- Cycle count program: covers all A/B/C velocity tiers per frequency schedule.
- Annual wall-to-wall count: not required if cycle count maintains 99.5%+ accuracy.
- Variance = ABS(system qty - physical qty) / system qty.

### Corrective Actions
- Location < 95% accuracy: root cause investigation within 24 hours.
- Zone < 98% accuracy: process audit + retraining.
- Repeat offenders: slotting review (wrong items too close together).
""",
    })

    docs.append({
        "DOC_ID": "OPS-GEN-009",
        "TITLE": "Carrier Performance Monitoring",
        "DOC_TYPE": "GENERAL_OPS",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Carrier Performance Monitoring

### Metrics Tracked
1. **On-time pickup**: Did carrier arrive before cutoff? (from dock camera + scan logs)
2. **Transit time compliance**: Actual transit days vs service-level commitment.
3. **Damage rate**: Claims filed / total packages shipped per carrier.
4. **Lost package rate**: Packages with no delivery scan after 7 business days.

### Data Sources
- CARRIER_SCANS: tracks PICKUP, IN_TRANSIT, OUT_FOR_DELIVERY, DELIVERED, EXCEPTION scan events.
- CARRIER_FIRST_SCAN_TS on SHIPMENTS: when carrier takes custody.

### Review Cadence
- Weekly: flag carriers with >5% late pickup rate.
- Monthly: rate negotiation leverage report (volume vs performance).
- Quarterly: carrier scorecard shared with all tenants.
""",
    })

    docs.append({
        "DOC_ID": "OPS-GEN-010",
        "TITLE": "Data Retention and Archival Policy",
        "DOC_TYPE": "GENERAL_OPS",
        "CARRIER": None,
        "WAREHOUSE_ID": None,
        "CONTENT": """## Data Retention Policy

### Active Data (online, queryable)
- Orders + lines: 24 months rolling.
- Shipments + scans: 18 months rolling.
- Inventory snapshots: current + 90 days history.
- Exceptions: 12 months rolling.

### Archive (cold storage, retrievable on request)
- All transactional data: 7 years (regulatory compliance).
- Carrier rate cards: indefinite (contract reference).
- Labor standards: keep all versions (audit trail).

### Deletion
- PII (customer names/addresses): purged from archive after 3 years unless legal hold.
- Operational metrics: aggregated and retained indefinitely; detail rows follow retention schedule.
""",
    })

    return docs


def main():
    all_docs = []
    all_docs.extend(generate_carrier_tariffs())
    all_docs.extend(generate_pick_pack_sops())
    all_docs.extend(generate_exception_playbooks())
    all_docs.extend(generate_cutoff_policies())
    all_docs.extend(generate_general_ops_docs())

    df = pd.DataFrame(all_docs)
    # Ensure consistent column order
    df = df[["DOC_ID", "TITLE", "DOC_TYPE", "CARRIER", "WAREHOUSE_ID", "CONTENT"]]

    output_path = OUTPUT_DIR / "ops_knowledge_corpus.parquet"
    df.to_parquet(output_path, index=False)

    print(f"Generated {len(df)} ops knowledge documents")
    print(f"Output: {output_path}")
    print(f"\nBreakdown by DOC_TYPE:")
    print(df["DOC_TYPE"].value_counts().to_string())


if __name__ == "__main__":
    main()
