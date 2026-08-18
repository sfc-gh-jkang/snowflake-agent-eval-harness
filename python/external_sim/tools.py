"""Tool implementations calling Snowflake-native services.

Each tool mirrors what the native Cortex Agent has access to:
- FULFILLMENT_SV (semantic view via Cortex Analyst REST)
- SHIPPING_SV (semantic view via Cortex Analyst REST)
- ITEM_CATALOG_SEARCH (Cortex Search)
- OPS_KNOWLEDGE_SEARCH (Cortex Search)

WHY THESE CARRY @instrument(RETRIEVAL) AND NOT JUST @instrument(TOOL)
---------------------------------------------------------------------
The orchestrator's route() already emits a TOOL span, so for pure tracing
these decorators are redundant. They exist for SCORING. Two of the five
documented server-side AI Observability metrics read RETRIEVAL attributes:

  context_relevance -> RETRIEVAL.QUERY_TEXT + RETRIEVAL.RETRIEVED_CONTEXTS
  groundedness      -> RETRIEVAL.RETRIEVED_CONTEXTS + RECORD_ROOT.OUTPUT

Before this, EXTERNAL_SIM emitted five span types (graph_node, tool,
generation, agent, record_root) and NO retrieval span, so those two metrics
could not be computed at all — 3 of 5 available. Measured, not assumed:
before the change, a span-type query over EXTERNAL_SIM returned five rows
and no 'retrieval' row.
  https://docs.snowflake.com/en/user-guide/snowflake-cortex/ai-observability/reference

query_analyst is instrumented as RETRIEVAL too, deliberately. It is
text-to-SQL rather than a vector search, but the responder grounds its
answer on the rows it returns just as much as on the Cortex Search hits.
Scoring groundedness against only the two search tools would measure the
answer against a partial context set and under-report it on every
data-driven question — which is most of them.
"""

import json
from dataclasses import dataclass
from typing import Optional

import snowflake.connector
from trulens.core.otel.instrument import instrument
from trulens.otel.semconv.trace import SpanAttributes


@dataclass
class ToolResult:
    tool_name: str
    query: str
    result: str
    sql_generated: Optional[str] = None
    error: Optional[str] = None


def _retrieval_attributes(ret, exception, *args, **kwargs):
    """Map a tool call's input/output onto RETRIEVAL span attributes.

    Deliberately defensive: the three instrumented methods have different
    signatures and the orchestrator calls them POSITIONALLY, so neither a
    fixed kwarg name nor a fixed arg index is safe across all three.

        query_analyst(question, semantic_view)
        search_items(query, limit=5)
        search_ops_knowledge(query, limit=3)

    Resolve the query by keyword first ('query' or 'question'), then fall
    back to the first string positional — which skips a leading `self` if
    TruLens forwards the unbound call. On an exception `ret` is None, so
    contexts becomes [] rather than raising inside the lambda; a throwing
    attributes callback would lose the span entirely and hide the failure
    we most want to see.
    """
    query = kwargs.get("query") or kwargs.get("question")
    if query is None:
        for arg in args:
            if isinstance(arg, str):
                query = arg
                break

    contexts = []
    if ret is not None and getattr(ret, "result", None):
        contexts = [str(ret.result)]

    return {
        SpanAttributes.RETRIEVAL.QUERY_TEXT: query or "",
        SpanAttributes.RETRIEVAL.RETRIEVED_CONTEXTS: contexts,
    }


# The external agent queries PHYSICAL TABLES. It deliberately has no semantic
# view, no verified queries and no metric definitions -- that is the whole point
# of the comparison: it can reach exactly the same data as the native agent, but
# has to GUESS every ambiguous definition (on-time, fill rate, units, active SKU)
# instead of reading it off a governed contract.
#
# This prompt previously said "use tables from the {semantic_view} semantic view",
# which is not something you can write SQL against that way. Every query failed to
# compile and the agent answered "technical error", so its scores measured a
# broken prompt rather than the absence of a semantic layer. Naming the real
# tables makes the test fair: the data is reachable, the DEFINITIONS are not.
_PHYSICAL_SCHEMA = (
    "Query these physical tables directly (fully qualify them): "
    "AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDERS(ORDER_ID,TENANT_ID,ORDER_DATE,WAREHOUSE_ID,"
    "LINES_FILLED,TOTAL_LINES,SHIP_BY_DATE), "
    "AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.ORDER_LINES(ORDER_ID,SKU,QTY_ORDERED_EACHES,"
    "QTY_SHIPPED_EACHES,QTY_CARTONS,UNIT_PRICE,WAVE_ID), "
    "AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.WAVES(WAVE_ID,WAREHOUSE_ID,WAVE_DATE), "
    "AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.EXCEPTIONS(ORDER_ID,EXCEPTION_TYPE), "
    "AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.FISCAL_CALENDAR_445(CALENDAR_DATE,FISCAL_YEAR,"
    "FISCAL_PERIOD,FISCAL_QUARTER,FISCAL_WEEK), "
    "AGENT_EVAL_DEMO.INVENTORY_INTELLIGENCE.ITEM_MASTER(SKU,DESCRIPTION,CATEGORY,SUBCATEGORY,HAZMAT_FLAG), "
    "AGENT_EVAL_DEMO.INVENTORY_INTELLIGENCE.ON_HAND(SKU,WAREHOUSE_ID,QTY_ON_HAND), "
    "AGENT_EVAL_DEMO.INVENTORY_INTELLIGENCE.MOVEMENTS(SKU,MOVEMENT_DATE,MOVEMENT_TYPE,QTY), "
    "AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.SHIPMENTS(SHIPMENT_ID,ORDER_ID,CARRIER,ZONE,WEIGHT_BREAK,"
    "SHIP_DATE,SHIP_BY_DATE,CARRIER_FIRST_SCAN_TS,PACKAGE_COUNT,WAREHOUSE_ID), "
    "AGENT_EVAL_DEMO.SHIPPING_INTELLIGENCE.ZONE_RATE_CARDS(CARRIER,ZONE,WEIGHT_BREAK,RATE_PER_PACKAGE,"
    "FUEL_SURCHARGE_PCT,EFFECTIVE_DATE,EXPIRY_DATE), "
    "AGENT_EVAL_DEMO.LABOR_INTELLIGENCE.PICK_TASKS(TASK_ID,WAVE_ID,PICKER_ID,SKU,QTY_PICKED)."
)

def _strip_code_fences(text: str) -> str:
    """Return the SQL inside a markdown code fence, if there is one.

    Handles ```sql ... ```, ``` ... ```, and un-fenced text. Also drops any
    leading prose the model emits before the first SELECT/WITH, which some
    models add despite being told to return only SQL.
    """
    t = (text or "").strip()
    if t.startswith("```"):
        lines = t.split("\n")
        lines = lines[1:]                                  # drop ```sql / ```
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    t = t.strip("`").strip()
    upper = t.upper()
    # EARLIEST keyword wins. Checking SELECT before WITH truncated CTE queries
    # at their inner SELECT: "WITH a AS (SELECT 5) SELECT * FROM a" became
    # "SELECT 5) SELECT * FROM a", which is a syntax error.
    starts = [i for i in (upper.find("SELECT"), upper.find("WITH")) if i >= 0]
    if starts:
        return t[min(starts):].strip()
    return t

class SnowflakeTools:
    """Wrapper for Snowflake-native AI services used by the external orchestrator."""

    def __init__(self, connection: snowflake.connector.SnowflakeConnection,
                 model: str = "claude-opus-4-8"):
        self.conn = connection
        self.database = "AGENT_EVAL_DEMO"
        self.schema = "AI"
        # Injected by ExternalOrchestrator so the text-to-SQL step below runs on
        # the SAME model as the planner and responder, and the same model as the
        # native Cortex Agent. This was hardcoded to mistral-large2, which meant
        # the external agent used two different models internally and neither
        # matched the native agent it was being compared against.
        self.model = model

    @instrument(
        span_type=SpanAttributes.SpanType.RETRIEVAL,
        attributes=_retrieval_attributes,
    )
    def query_analyst(self, question: str, semantic_view: str) -> ToolResult:
        """Generate SQL for the question, execute it, and return the rows.

        Note this is text-to-SQL over the semantic view's underlying tables, NOT
        a Cortex Analyst call -- the model is asked for SQL directly. That is
        deliberate: it is what makes the external agent a fair stand-in for an
        orchestrator that has not adopted the governed semantic layer, and it is
        why its correctness score is the argument for adopting one.

        Three unused SQL strings were removed from this method: they referenced
        claude-3-5-sonnet (deprecated) and a AGENT_EVAL_DEMO.AI.ANALYST_QUERY
        stored procedure that does not exist, and described a REST path this code
        never takes. They were assigned and never executed, so they changed
        nothing at runtime while misrepresenting what the method does.
        """
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT AI_COMPLETE("
                "  %s,"
                "  ARRAY_CONSTRUCT("
                "    OBJECT_CONSTRUCT('role', 'system', 'content', "
                "      'You are a supply chain data analyst. Write a Snowflake SQL query to answer the user question. "
                f"{_PHYSICAL_SCHEMA} "
                "Return ONLY executable SQL, no explanation, no markdown fences.'),"
                "    OBJECT_CONSTRUCT('role', 'user', 'content', %s)"
                "  ),"
                "  OBJECT_CONSTRUCT('temperature', 0)"
                ")",
                (self.model, question),
            )
            row = cur.fetchone()
            raw_response = row[0] if row else None

            # AI_COMPLETE returns the completion TEXT directly; only the
            # deprecated SNOWFLAKE.CORTEX.COMPLETE wrapped it in a
            # {"choices":[{"messages":...}]} envelope. Subscripting the parsed
            # value unconditionally raised TypeError: string indices must be
            # integers whenever the model returned valid-but-not-object JSON,
            # and TypeError was not caught, so it aborted the whole scoring run.
            # Same bug existed in orchestrator._llm_call; fix both or neither.
            generated_sql = raw_response
            if isinstance(raw_response, str):
                try:
                    parsed = json.loads(raw_response)
                except (json.JSONDecodeError, TypeError):
                    parsed = None
                if isinstance(parsed, dict) and "choices" in parsed:
                    try:
                        generated_sql = parsed["choices"][0]["messages"]
                    except (KeyError, IndexError, TypeError):
                        generated_sql = raw_response
                elif isinstance(parsed, str):
                    # AI_COMPLETE returns a JSON-ENCODED string, so the raw value
                    # contains literal backslash-n rather than real newlines (and
                    # wrapping quotes). Executing that verbatim produced
                    # "parse error ... near '110'" on every query. json.loads
                    # decodes the escapes; without this the SQL never compiles.
                    generated_sql = parsed

            # Try to execute the generated SQL.
            #
            # Strip MARKDOWN CODE FENCES before the SELECT check. The old code did
            # generated_sql.strip().strip('`').strip(), which removes bare
            # backticks but leaves the language tag: a fenced reply
            #     ```sql\nSELECT ...\n```
            # became "sql\nSELECT ...", so startswith("SELECT") was False, the
            # query was NEVER EXECUTED, and the method returned the SQL TEXT as
            # if it were the result. The agent then answered "I'm unable to
            # provide the actual value -- the tool returned only the SQL query
            # that would answer", and scored ~0 on correctness.
            #
            # This was invisible with mistral-large2, which replied with bare SQL.
            # claude-opus-4-8 fences its output, so pinning the model for parity
            # exposed a latent bug. The 0.037 correctness measured on
            # PARITY_EXTERNAL_V6 is therefore NOT a clean measurement of "no
            # semantic view" -- it is mostly this bug. Re-run after this fix
            # before quoting any external correctness number.
            if generated_sql:
                clean_sql = _strip_code_fences(generated_sql)
                if clean_sql.upper().startswith("SELECT"):
                    try:
                        cur.execute(clean_sql)
                        results = cur.fetchmany(20)
                        cols = [d[0] for d in cur.description]
                        formatted = json.dumps(
                            [dict(zip(cols, r)) for r in results],
                            default=str
                        )
                        return ToolResult(
                            tool_name=semantic_view,
                            query=question,
                            result=formatted,
                            sql_generated=clean_sql,
                        )
                    except Exception as e:
                        return ToolResult(
                            tool_name=semantic_view,
                            query=question,
                            result=f"SQL execution failed: {e}",
                            sql_generated=clean_sql,
                            error=str(e),
                        )

            return ToolResult(
                tool_name=semantic_view,
                query=question,
                result=generated_sql or "No response",
                sql_generated=generated_sql,
            )
        finally:
            cur.close()

    @instrument(
        span_type=SpanAttributes.SpanType.RETRIEVAL,
        attributes=_retrieval_attributes,
    )
    def search_items(self, query: str, limit: int = 5) -> ToolResult:
        """Search ITEM_CATALOG_SEARCH for SKU/product matches."""
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT PARSE_JSON(SNOWFLAKE.CORTEX.SEARCH_PREVIEW("
                "  'AGENT_EVAL_DEMO.AI.ITEM_CATALOG_SEARCH',"
                "  OBJECT_CONSTRUCT("
                "    'query', %s,"
                "    'columns', ARRAY_CONSTRUCT('SKU','SEARCH_TEXT','CATEGORY','SUBCATEGORY'),"
                "    'limit', %s"
                "  )"
                ")) AS results",
                (query, limit),
            )
            row = cur.fetchone()
            result = row[0] if row else "{}"
            return ToolResult(
                tool_name="ITEM_CATALOG_SEARCH",
                query=query,
                result=str(result),
            )
        except Exception as e:
            # Fallback: use SQL-based search
            cur.execute(
                "SELECT SKU, DESCRIPTION, CATEGORY, SUBCATEGORY "
                "FROM AGENT_EVAL_DEMO.INVENTORY_INTELLIGENCE.ITEM_MASTER "
                "WHERE LOWER(DESCRIPTION) LIKE LOWER(%s) "
                "LIMIT %s",
                (f"%{query}%", limit),
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            formatted = json.dumps(
                [dict(zip(cols, r)) for r in rows], default=str
            )
            return ToolResult(
                tool_name="ITEM_CATALOG_SEARCH",
                query=query,
                result=formatted,
                error=f"Search preview unavailable, used SQL fallback: {e}",
            )
        finally:
            cur.close()

    @instrument(
        span_type=SpanAttributes.SpanType.RETRIEVAL,
        attributes=_retrieval_attributes,
    )
    def search_ops_knowledge(self, query: str, limit: int = 3) -> ToolResult:
        """Search OPS_KNOWLEDGE_SEARCH for SOPs, playbooks, tariffs."""
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT PARSE_JSON(SNOWFLAKE.CORTEX.SEARCH_PREVIEW("
                "  'AGENT_EVAL_DEMO.AI.OPS_KNOWLEDGE_SEARCH',"
                "  OBJECT_CONSTRUCT("
                "    'query', %s,"
                "    'columns', ARRAY_CONSTRUCT('TITLE','CONTENT','DOC_TYPE'),"
                "    'limit', %s"
                "  )"
                ")) AS results",
                (query, limit),
            )
            row = cur.fetchone()
            result = row[0] if row else "{}"
            return ToolResult(
                tool_name="OPS_KNOWLEDGE_SEARCH",
                query=query,
                result=str(result),
            )
        except Exception as e:
            cur.execute(
                "SELECT TITLE, CONTENT, DOC_TYPE "
                "FROM AGENT_EVAL_DEMO.AI.OPS_KNOWLEDGE_CORPUS "
                "WHERE LOWER(CONTENT) LIKE LOWER(%s) "
                "LIMIT %s",
                (f"%{query}%", limit),
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            formatted = json.dumps(
                [dict(zip(cols, r)) for r in rows], default=str
            )
            return ToolResult(
                tool_name="OPS_KNOWLEDGE_SEARCH",
                query=query,
                result=formatted,
                error=f"Search preview unavailable, used SQL fallback: {e}",
            )
        finally:
            cur.close()
