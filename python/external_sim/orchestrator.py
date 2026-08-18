"""ADK-shaped orchestrator: planner -> tool_router -> responder.

Shaped like a typical Google-ADK-style agent pipeline.
Each stage is a distinct function so TruLens can instrument at the span level.

MODEL PARITY -- READ BEFORE CHANGING ORCHESTRATION_MODEL
--------------------------------------------------------
This orchestrator and the native Cortex Agent MUST name the same model, or any
comparison between them is not apples-to-apples and should not be presented as
one. Both are pinned to claude-opus-4-8:

  external (here)      ORCHESTRATION_MODEL below, used by _llm_call and passed
                       to SnowflakeTools for its text-to-SQL step
  native agent         sql/07_agent.sql -> models.orchestration: claude-opus-4-8

claude-opus-4-8 was chosen because it is what the Cortex Agent ALREADY selected
on its own: the agent shipped with `models.orchestration: ""` (auto-select), and
LLM_MODEL in GET_AI_OBSERVABILITY_EVENTS_NORMALIZED showed claude-opus-4-8 on 72
of its spans. Pinning both sides to that model aligns them without changing the
native agent's actual behaviour. It also stops the comparison silently breaking
if Snowflake changes what auto-select resolves to.

The previous state was NOT comparable: this file ran mistral-large2 while the
agent auto-selected an Opus model, and a dead dataclass default advertised
claude-3-5-sonnet, which nothing ever called and which is deprecated for the
Cortex Agent API.
"""

import json
from dataclasses import dataclass, field
from typing import Optional

import snowflake.connector
from trulens.core.otel.instrument import instrument
from trulens.otel.semconv.trace import SpanAttributes

from .tools import SnowflakeTools, ToolResult

# Single source of truth for the external side of the comparison.
# Keep in lockstep with models.orchestration in sql/07_agent.sql.
ORCHESTRATION_MODEL = "claude-opus-4-8"


@dataclass
class Plan:
    """Output of the planner stage."""
    reasoning: str
    tools_needed: list[str]
    sub_questions: list[str] = field(default_factory=list)


@dataclass
class OrchestratorResponse:
    """Final output of the full pipeline."""
    answer: str
    plan: Plan
    tool_results: list[ToolResult]
    # No default. This field previously defaulted to "claude-3-5-sonnet", a model
    # this code never called -- run() always overwrites it with self.model. A
    # stale default here is not harmless: it is the first thing a reader sees
    # when asking "what model did the external agent use?", and it gave the wrong
    # answer. claude-3-5-sonnet is also deprecated for the Cortex Agent API.
    # Make it required so it can only ever hold the model actually used.
    model_used: str


AVAILABLE_TOOLS = {
    "FULFILLMENT_SV": "Semantic view for fulfillment operations: orders, waves, pick tasks, inventory, labor standards. Use for quantitative questions about order volume, fill rates, on-time performance, wave efficiency.",
    "SHIPPING_SV": "Semantic view for shipping analytics: shipments, carrier performance, zone rate cards, cost analysis. Use for shipping cost, carrier SLA, zone-based pricing questions.",
    "ITEM_CATALOG_SEARCH": "Full-text search over product catalog (SKU, description, category, taxonomy). Use for fuzzy product lookups, finding items by partial name or description.",
    "OPS_KNOWLEDGE_SEARCH": "Full-text search over operations knowledge base: SOPs, exception playbooks, cutoff policies, carrier tariffs. Use for procedural questions, policy lookups, exception handling.",
}

PLANNER_SYSTEM_PROMPT = """You are a supply-chain intelligence planner for a 3PL fulfillment company.
Given a user question, determine which tools are needed to answer it.

Available tools:
{tools}

Rules:
- If the question is about metrics, KPIs, or quantitative data → use a semantic view (FULFILLMENT_SV or SHIPPING_SV)
- If the question asks about procedures, SOPs, policies, or "why did X happen" → use OPS_KNOWLEDGE_SEARCH
- If the question mentions a product by name/description → use ITEM_CATALOG_SEARCH
- Some questions need MULTIPLE tools (e.g., "why did Tuesday's wave miss cutoff" needs both SHIPPING_SV for data and OPS_KNOWLEDGE_SEARCH for the exception playbook)
- If the question is about shipping costs or carrier rates → use SHIPPING_SV (it has zone rate cards)

Return a JSON object with:
- "reasoning": why you chose these tools
- "tools_needed": list of tool names
- "sub_questions": optional list of refined questions for each tool
"""

RESPONDER_SYSTEM_PROMPT = """You are a supply-chain intelligence assistant for a 3PL fulfillment company.
Synthesize the tool results into a clear, actionable answer.

Be specific with numbers. If data shows dates, quantities, or rates, include them.
If a tool returned an error, acknowledge the limitation.
"""


class ExternalOrchestrator:
    """ADK-shaped orchestrator: plan → route → respond."""

    def __init__(self, connection: snowflake.connector.SnowflakeConnection):
        self.conn = connection
        self.model = ORCHESTRATION_MODEL
        # Pass the model down so the tool layer's text-to-SQL step uses the SAME
        # model as the planner and responder. Previously tools.py hardcoded
        # mistral-large2 independently, so even within this one agent two
        # different models were in play.
        self.tools = SnowflakeTools(connection, model=ORCHESTRATION_MODEL)

    def _llm_call(self, system: str, user: str) -> str:
        """Call Cortex with AI_COMPLETE and extract text from the response envelope.

        AI_COMPLETE, not SNOWFLAKE.CORTEX.COMPLETE -- the SNOWFLAKE.CORTEX.*
        namespace is deprecated in favour of the AI_* top-level functions.
        """
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT AI_COMPLETE("
                "  %s,"
                "  ARRAY_CONSTRUCT("
                "    OBJECT_CONSTRUCT('role', 'system', 'content', %s),"
                "    OBJECT_CONSTRUCT('role', 'user', 'content', %s)"
                "  ),"
                "  OBJECT_CONSTRUCT('temperature', 0)"
                ")",
                (self.model, system, user),
            )
            row = cur.fetchone()
            if not row or not row[0]:
                return ""
            raw = row[0]
            # AI_COMPLETE returns the completion TEXT directly -- unlike the
            # deprecated SNOWFLAKE.CORTEX.COMPLETE, which wrapped it in
            # {"choices":[{"messages":"..."}],...}. Verified live: the array-of-
            # messages form returns bare text.
            #
            # Keeping the old envelope unwrap here was an active bug, not dead
            # code. When the planner returned a JSON document, json.loads()
            # succeeded and the result was subscripted with ["choices"], raising
            # TypeError: string indices must be integers -- which was NOT in the
            # except tuple, so it escaped and killed the whole run with
            # "Error encountered during invoking app main method".
            # Unwrap defensively only if an envelope is actually present.
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    return raw
                if isinstance(parsed, dict) and "choices" in parsed:
                    try:
                        return parsed["choices"][0]["messages"]
                    except (KeyError, IndexError, TypeError):
                        return raw
                # AI_COMPLETE returns a JSON-ENCODED string: the raw value carries
                # literal backslash-n instead of newlines. Return the DECODED form.
                if isinstance(parsed, str):
                    return parsed
                return raw
            return str(raw)
        finally:
            cur.close()

    @instrument(span_type=SpanAttributes.SpanType.GRAPH_NODE)
    def plan(self, question: str) -> Plan:
        """Stage 1: Determine which tools to invoke."""
        tools_desc = "\n".join(
            f"- {name}: {desc}" for name, desc in AVAILABLE_TOOLS.items()
        )
        system = PLANNER_SYSTEM_PROMPT.format(tools=tools_desc)
        raw = self._llm_call(system, question)

        # Parse JSON from response
        try:
            # Strip markdown code fences if present
            clean = raw.strip()
            if clean.startswith("```"):
                clean = "\n".join(clean.split("\n")[1:])
            if clean.endswith("```"):
                clean = clean.rsplit("```", 1)[0]
            clean = clean.strip()
            data = json.loads(clean)
            # json.loads happily returns a str/list/int for valid-but-not-object
            # JSON, and .get() on those raises AttributeError -- which was not in
            # the except tuple below, so it escaped and aborted the whole run with
            # "Error encountered during invoking app main method: 'str' object has
            # no attribute 'get'". Different models wrap their JSON differently,
            # so validate the shape rather than trusting it.
            if not isinstance(data, dict):
                raise ValueError(f"planner returned {type(data).__name__}, not an object")
            tools = data.get("tools_needed") or []
            if not isinstance(tools, list):
                tools = [tools]
            subs = data.get("sub_questions") or []
            if not isinstance(subs, list):
                subs = [subs]
            return Plan(
                reasoning=str(data.get("reasoning", "")),
                tools_needed=[str(t) for t in tools],
                sub_questions=subs,
            )
        except (json.JSONDecodeError, KeyError, AttributeError, TypeError, ValueError):
            # Fallback: use FULFILLMENT_SV
            return Plan(
                reasoning=f"Could not parse plan, defaulting. Raw: {raw[:200]}",
                tools_needed=["FULFILLMENT_SV"],
                sub_questions=[question],
            )

    @instrument(span_type=SpanAttributes.SpanType.TOOL)
    def route(self, question: str, plan: Plan) -> list[ToolResult]:
        """Stage 2: Dispatch to the appropriate tools."""
        results = []
        sub_q = plan.sub_questions if plan.sub_questions else [question] * len(plan.tools_needed)

        for i, tool_name in enumerate(plan.tools_needed):
            # sub_questions may be strings or dicts with 'question'/'tool' keys
            raw_q = sub_q[i] if i < len(sub_q) else question
            if isinstance(raw_q, dict):
                q = raw_q.get("question", raw_q.get("sub_question", question))
            else:
                q = str(raw_q)
            if tool_name in ("FULFILLMENT_SV", "SHIPPING_SV"):
                results.append(self.tools.query_analyst(q, tool_name))
            elif tool_name == "ITEM_CATALOG_SEARCH":
                results.append(self.tools.search_items(q))
            elif tool_name == "OPS_KNOWLEDGE_SEARCH":
                results.append(self.tools.search_ops_knowledge(q))
            else:
                results.append(ToolResult(
                    tool_name=tool_name,
                    query=q,
                    result=f"Unknown tool: {tool_name}",
                    error="Tool not found",
                ))
        return results

    @instrument(span_type=SpanAttributes.SpanType.GENERATION)
    def respond(self, question: str, tool_results: list[ToolResult]) -> str:
        """Stage 3: Synthesize tool outputs into a final answer."""
        context_parts = []
        for tr in tool_results:
            context_parts.append(
                f"[{tr.tool_name}] Query: {tr.query}\n"
                f"Result: {tr.result[:2000]}"
                + (f"\nSQL: {tr.sql_generated}" if tr.sql_generated else "")
                + (f"\nError: {tr.error}" if tr.error else "")
            )
        context = "\n\n".join(context_parts)
        user_msg = f"Question: {question}\n\nTool Results:\n{context}"
        return self._llm_call(RESPONDER_SYSTEM_PROMPT, user_msg)

    @instrument(span_type=SpanAttributes.SpanType.AGENT)
    def run(self, question: str) -> OrchestratorResponse:
        """Full pipeline: plan → route → respond."""
        plan = self.plan(question)
        tool_results = self.route(question, plan)
        answer = self.respond(question, tool_results)
        return OrchestratorResponse(
            answer=answer,
            plan=plan,
            tool_results=tool_results,
            model_used=self.model,
        )

    # Callable interface for TruLens wrapping
    @instrument(span_type=SpanAttributes.SpanType.RECORD_ROOT)
    def __call__(self, question: str) -> str:
        """TruLens-compatible callable: input str → output str."""
        response = self.run(question)
        return response.answer
