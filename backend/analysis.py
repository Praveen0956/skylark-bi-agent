"""
analysis.py
A small, generic aggregation engine over normalized records.

Why the LLM doesn't do the arithmetic itself (see DECISION_LOG.docx):
LLMs are unreliable at exact arithmetic over many rows. Instead, the agent
calls these functions as *tools* and only reasons over the returned numbers
-- deterministic math, LLM does the interpretation/explanation.
"""

from collections import defaultdict
from statistics import mean


def filter_records(records: list[dict], filters: dict) -> list[dict]:
    """filters: {"column_name": "value_to_match"} - exact match, case-insensitive."""
    if not filters:
        return records
    out = []
    for r in records:
        match = True
        for col, val in filters.items():
            rec_val = r.get(col)
            if rec_val is None or str(rec_val).lower() != str(val).lower():
                match = False
                break
        if match:
            out.append(r)
    return out


def group_and_aggregate(
    records: list[dict],
    group_by: str,
    value_column: str,
    agg: str = "sum",
) -> dict:
    """
    agg: one of "sum", "avg", "count", "min", "max"
    Nulls in value_column are excluded from numeric aggregation but counted
    separately as data_quality_excluded so the agent can mention them.
    """
    groups: dict[str, list[float]] = defaultdict(list)
    excluded = 0

    for r in records:
        key = r.get(group_by) or "Unspecified"
        val = r.get(value_column)
        if agg == "count":
            groups[key].append(1)
            continue
        if val is None:
            excluded += 1
            continue
        try:
            groups[key].append(float(val))
        except (TypeError, ValueError):
            excluded += 1

    result = {}
    for key, values in groups.items():
        if not values:
            result[key] = None
            continue
        if agg == "sum":
            result[key] = round(sum(values), 2)
        elif agg == "avg":
            result[key] = round(mean(values), 2)
        elif agg == "count":
            result[key] = len(values)
        elif agg == "min":
            result[key] = round(min(values), 2)
        elif agg == "max":
            result[key] = round(max(values), 2)

    return {
        "aggregation": agg,
        "group_by": group_by,
        "value_column": value_column,
        "results": result,
        "data_quality_excluded_rows": excluded,
    }


def top_n(records: list[dict], sort_column: str, n: int = 5, descending: bool = True) -> list[dict]:
    def sort_key(r):
        val = r.get(sort_column)
        try:
            return float(val)
        except (TypeError, ValueError):
            return float("-inf") if descending else float("inf")

    sorted_records = sorted(records, key=sort_key, reverse=descending)
    return sorted_records[:n]


def list_distinct_values(records: list[dict], column: str) -> list[str]:
    vals = {r.get(column) for r in records if r.get(column) is not None}
    return sorted(vals)
