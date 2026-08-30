"""
agent.py
Claude tool-calling loop: connects natural-language questions to live
monday.com data via the tools defined below.

Why a *generic* tool surface (filter/group/aggregate) instead of one tool
per business question ("get_energy_sector_deals"): the assignment data and
questions aren't known in advance, and hardcoding per-question tools
doesn't generalize. A small set of generic primitives composes to answer
a wide range of BI questions. See DECISION_LOG.docx.

Why a 60s cache on board data: monday.com API calls are relatively slow
and rate-limited; BI questions in a single conversation often hit the same
board repeatedly. 60s balances freshness against latency/rate-limit risk.
"""

import os
import time
import anthropic

from monday_client import MondayClient
from data_normalizer import normalize_items
from analysis import filter_records, group_and_aggregate, top_n, list_distinct_values

MODEL = "claude-sonnet-4-6"
CACHE_TTL_SECONDS = 60

_cache: dict[str, tuple[float, list[dict]]] = {}


def _get_board_records(board_key: str, board_id: str) -> list[dict]:
    now = time.time()
    cached = _cache.get(board_key)
    if cached and (now - cached[0]) < CACHE_TTL_SECONDS:
        return cached[1]

    client = MondayClient()
    columns = client.get_board_columns(board_id)
    raw_items = client.get_board_items(board_id)
    records = normalize_items(raw_items, columns)
    _cache[board_key] = (now, records)
    return records


def _board_id_for(board_key: str) -> str:
    mapping = {
        "deals": os.environ.get("DEALS_BOARD_ID"),
        "work_orders": os.environ.get("WORK_ORDERS_BOARD_ID"),
    }
    board_id = mapping.get(board_key)
    if not board_id:
        raise ValueError(f"No board ID configured for '{board_key}'")
    return board_id


TOOLS = [
    {
        "name": "list_columns",
        "description": "List the available column names for a board, so you know what fields you can filter/group/aggregate on before calling other tools.",
        "input_schema": {
            "type": "object",
            "properties": {
                "board": {"type": "string", "enum": ["deals", "work_orders"]},
            },
            "required": ["board"],
        },
    },
    {
        "name": "query_aggregate",
        "description": "Filter records on a board, then group and aggregate a numeric column. Use this for questions like 'total deal value by sector' or 'average work order cost by status'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "board": {"type": "string", "enum": ["deals", "work_orders"]},
                "filters": {
                    "type": "object",
                    "description": "Optional exact-match filters, e.g. {\"Sector\": \"Energy\"}",
                },
                "group_by": {"type": "string", "description": "Column to group by"},
                "value_column": {"type": "string", "description": "Numeric column to aggregate"},
                "agg": {"type": "string", "enum": ["sum", "avg", "count", "min", "max"]},
            },
            "required": ["board", "group_by", "value_column", "agg"],
        },
    },
    {
        "name": "top_records",
        "description": "Return the top N records on a board sorted by a numeric column, optionally after filtering. Use for 'biggest deals', 'most expensive work orders', etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "board": {"type": "string", "enum": ["deals", "work_orders"]},
                "filters": {"type": "object"},
                "sort_column": {"type": "string"},
                "n": {"type": "integer", "default": 5},
                "descending": {"type": "boolean", "default": True},
            },
            "required": ["board", "sort_column"],
        },
    },
    {
        "name": "list_distinct",
        "description": "List distinct values present in a column, e.g. all sectors or all statuses. Useful to discover valid filter values before calling query_aggregate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "board": {"type": "string", "enum": ["deals", "work_orders"]},
                "column": {"type": "string"},
            },
            "required": ["board", "column"],
        },
    },
]


def _execute_tool(name: str, tool_input: dict) -> dict:
    board_key = tool_input["board"]
    board_id = _board_id_for(board_key)
    records = _get_board_records(board_key, board_id)

    if name == "list_columns":
        cols = sorted({k for r in records for k in r.keys() if k not in ("id",)})
        return {"columns": cols}

    if name == "query_aggregate":
        filtered = filter_records(records, tool_input.get("filters", {}))
        return group_and_aggregate(
            filtered,
            tool_input["group_by"],
            tool_input["value_column"],
            tool_input.get("agg", "sum"),
        )

    if name == "top_records":
        filtered = filter_records(records, tool_input.get("filters", {}))
        results = top_n(
            filtered,
            tool_input["sort_column"],
            tool_input.get("n", 5),
            tool_input.get("descending", True),
        )
        return {"records": results}

    if name == "list_distinct":
        return {"values": list_distinct_values(records, tool_input["column"])}

    raise ValueError(f"Unknown tool: {name}")


SYSTEM_PROMPT = """You are a business intelligence assistant for Skylark Drones, \
answering questions about deals and work orders sourced live from monday.com. \
Always use the tools to get real data -- never guess numbers. If a filter/column \
name isn't obvious, call list_columns or list_distinct first. Cite the specific \
numbers you found. If data was excluded due to quality issues (nulls/unparseable \
values), mention that transparently rather than hiding it. Keep answers concise \
and business-focused."""


def ask_agent(user_message: str, conversation_history: list[dict] | None = None) -> dict:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    messages = (conversation_history or []) + [{"role": "user", "content": user_message}]

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            text = "".join(b.text for b in response.content if b.type == "text")
            messages.append({"role": "assistant", "content": response.content})
            return {"reply": text, "messages": messages}

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            try:
                result = _execute_tool(block.name, block.input)
                content = str(result)
            except Exception as e:  # noqa: BLE001
                content = f"Error: {e}"
            tool_results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": content}
            )
        messages.append({"role": "user", "content": tool_results})
