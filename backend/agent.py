"""
agent.py
Gemini function-calling loop: connects natural-language questions to live
monday.com data via the tools defined below.

Provider note: Gemini (not Claude) is used here specifically because it has
a genuine no-cost free tier for the flash model, with no billing setup
required -- appropriate for a same-day student assignment submission. The
architecture (generic tool surface, deterministic aggregation, 60s cache)
is provider-agnostic; swapping to any other tool-calling LLM API is a
localized change to this file only. See DECISION_LOG.docx.

Why the LLM doesn't do the math itself: LLMs are unreliable at exact
arithmetic across many rows. All filtering/grouping/aggregation happens in
deterministic Python (analysis.py) -- the model only decides which tool to
call and interprets the returned numbers.

Why a *generic* tool surface instead of one tool per business question:
the exact questions a user will ask aren't known in advance. A small set
of generic primitives (list_columns, list_distinct, query_aggregate,
top_records) composes to answer a wide range of BI questions.

Why a 60s cache on board data: monday.com API calls are relatively slow
and rate-limited; a single conversation often hits the same board
repeatedly across tool calls.
"""

import json
import os
import time

import google.generativeai as genai

from monday_client import MondayClient
from data_normalizer import normalize_items
from analysis import filter_records, group_and_aggregate, top_n, list_distinct_values

MODEL_NAME = "gemini-2.5-flash"
CACHE_TTL_SECONDS = 60

_cache: dict[str, tuple[float, list[dict]]] = {}
_configured = False


def _ensure_configured():
    global _configured
    if not _configured:
        genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
        _configured = True


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


# --- Tool functions -------------------------------------------------------
# Plain Python functions with type hints + docstrings. Gemini's SDK
# introspects these to build the tool schema and supports automatic
# function calling (it invokes them itself and feeds results back).
# filters are passed as a JSON string (not dict) for reliable schema
# introspection across SDK versions.


def list_columns(board: str) -> dict:
    """List the available column names for a board ('deals' or
    'work_orders'), so you know what fields exist before filtering,
    grouping, or aggregating.

    Args:
        board: Either "deals" or "work_orders".
    """
    board_id = _board_id_for(board)
    records = _get_board_records(board, board_id)
    cols = sorted({k for r in records for k in r.keys() if k != "id"})
    return {"columns": cols}


def list_distinct(board: str, column: str) -> dict:
    """List distinct values present in a column, e.g. all sectors or all
    statuses. Useful to discover valid filter values before calling
    query_aggregate.

    Args:
        board: Either "deals" or "work_orders".
        column: The column name to list distinct values for.
    """
    board_id = _board_id_for(board)
    records = _get_board_records(board, board_id)
    return {"values": list_distinct_values(records, column)}


def query_aggregate(
    board: str,
    group_by: str,
    value_column: str,
    agg: str,
    filters_json: str = "{}",
) -> dict:
    """Filter records on a board, then group and aggregate a numeric
    column. Use for questions like 'total deal value by sector' or
    'average work order cost by status'.

    Args:
        board: Either "deals" or "work_orders".
        group_by: Column name to group results by.
        value_column: Numeric column to aggregate.
        agg: One of "sum", "avg", "count", "min", "max".
        filters_json: Optional JSON object string of exact-match filters,
            e.g. '{"Sector": "Energy"}'. Use "{}" for no filter.
    """
    board_id = _board_id_for(board)
    records = _get_board_records(board, board_id)
    try:
        filters = json.loads(filters_json) if filters_json else {}
    except json.JSONDecodeError:
        filters = {}
    filtered = filter_records(records, filters)
    return group_and_aggregate(filtered, group_by, value_column, agg)


def top_records(
    board: str,
    sort_column: str,
    n: int = 5,
    descending: bool = True,
    filters_json: str = "{}",
) -> dict:
    """Return the top N records on a board sorted by a numeric column,
    optionally after filtering. Use for 'biggest deals', 'most expensive
    work orders', etc.

    Args:
        board: Either "deals" or "work_orders".
        sort_column: Numeric column to sort by.
        n: Number of records to return.
        descending: True for highest-first, False for lowest-first.
        filters_json: Optional JSON object string of exact-match filters,
            e.g. '{"Sector": "Energy"}'. Use "{}" for no filter.
    """
    board_id = _board_id_for(board)
    records = _get_board_records(board, board_id)
    try:
        filters = json.loads(filters_json) if filters_json else {}
    except json.JSONDecodeError:
        filters = {}
    filtered = filter_records(records, filters)
    results = top_n(filtered, sort_column, n, descending)
    return {"records": results}


TOOL_FUNCTIONS = [list_columns, list_distinct, query_aggregate, top_records]

SYSTEM_PROMPT = """You are a business intelligence assistant for Skylark Drones, \
answering questions about deals and work orders sourced live from monday.com. \
Always use the tools to get real data -- never guess numbers. If a filter/column \
name isn't obvious, call list_columns or list_distinct first. Cite the specific \
numbers you found. If data was excluded due to quality issues (nulls/unparseable \
values), mention that transparently rather than hiding it. Keep answers concise \
and business-focused."""


def _history_to_gemini(history: list[dict]) -> list[dict]:
    """Convert our stored {role, text} history into Gemini's chat history
    format. We only persist plain text turns between requests (tool-call
    turns are handled within a single ask_agent call and not replayed)."""
    converted = []
    for turn in history:
        role = "model" if turn.get("role") == "assistant" else "user"
        converted.append({"role": role, "parts": [turn.get("text", "")]})
    return converted


def ask_agent(user_message: str, conversation_history: list[dict] | None = None) -> dict:
    _ensure_configured()

    model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        system_instruction=SYSTEM_PROMPT,
        tools=TOOL_FUNCTIONS,
    )

    chat = model.start_chat(
        history=_history_to_gemini(conversation_history or []),
        enable_automatic_function_calling=True,
    )

    response = chat.send_message(user_message)
    reply_text = response.text

    new_history = (conversation_history or []) + [
        {"role": "user", "text": user_message},
        {"role": "assistant", "text": reply_text},
    ]

    return {"reply": reply_text, "messages": new_history}