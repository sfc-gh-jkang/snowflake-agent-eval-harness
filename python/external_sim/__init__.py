"""EXTERNAL_SIM: ADK-shaped external orchestrator over the fulfilment dataset.

Shaped like a typical Google-ADK-style agent pipeline:
  planner → tool_router → responder

Calls the SAME Snowflake-native semantic views and Cortex Search services
as the native FULFILLMENT_ANALYST agent, but orchestrated externally.

Instrumented with TruLens >= 2.1.2 which auto-creates the EXTERNAL AGENT
object in Snowflake for unified observability.
"""
