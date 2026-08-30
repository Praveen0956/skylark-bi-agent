"""
data_normalizer.py
Turns raw, messy monday.com column values into clean, typed records.

Why this exists as a separate module (see DECISION_LOG.docx for the full
rationale): the assignment explicitly calls out "inconsistent data" as the
core pain point. Rather than letting the LLM guess at parsing "₹1,20,000"
vs "120000.00" vs "1.2L", we normalize deterministically here, once, so
every downstream consumer (analysis.py, the agent) works with clean types.
"""

import re
from datetime import datetime

CURRENCY_PATTERN = re.compile(r"[^\d.\-]")

DATE_FORMATS = [
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b %Y",
]


def clean_currency(raw: str | None) -> float | None:
    """Strip currency symbols, commas, whitespace -> float. Handles Indian
    lakh/crore shorthand (e.g. '1.2L', '3Cr')."""
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    multiplier = 1
    if raw[-1].upper() == "L":
        multiplier = 100_000
        raw = raw[:-1]
    elif raw[-2:].upper() == "CR":
        multiplier = 10_000_000
        raw = raw[:-2]
    cleaned = CURRENCY_PATTERN.sub("", raw)
    if not cleaned or cleaned in {"-", "."}:
        return None
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return None


def clean_date(raw: str | None) -> str | None:
    """Normalize any of the DATE_FORMATS into ISO 8601 (YYYY-MM-DD)."""
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None  # unparseable -> null, not a guess


def clean_status(raw: str | None) -> str | None:
    """Normalize casing/whitespace so 'In Progress', 'in progress',
    'IN PROGRESS ' all collapse to one canonical value."""
    if not raw or not raw.strip():
        return None
    return raw.strip().title()


def clean_text(raw: str | None) -> str | None:
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped if stripped else None


# Maps a monday.com column "type" to the cleaning function to apply.
# Falls back to clean_text for anything unrecognized.
COLUMN_TYPE_CLEANERS = {
    "numbers": clean_currency,
    "date": clean_date,
    "status": clean_status,
    "text": clean_text,
    "long_text": clean_text,
}


def normalize_items(raw_items: list[dict], columns: list[dict]) -> list[dict]:
    """
    raw_items: output of MondayClient.get_board_items()
    columns:   output of MondayClient.get_board_columns()  (for type lookup)

    Returns a list of flat dicts: {"name": ..., "<column_title>": cleaned_value, ...}
    Unparseable values become None rather than being silently dropped or
    guessed, so downstream aggregation can decide how to handle nulls
    (e.g. exclude from averages, flag in a data-quality note).
    """
    col_type_by_id = {c["id"]: c["type"] for c in columns}
    col_title_by_id = {c["id"]: c["title"] for c in columns}

    normalized = []
    for item in raw_items:
        record = {"id": item["id"], "name": item["name"]}
        for cv in item["column_values"]:
            col_type = col_type_by_id.get(cv["id"], "text")
            cleaner = COLUMN_TYPE_CLEANERS.get(col_type, clean_text)
            title = col_title_by_id.get(cv["id"], cv["id"])
            record[title] = cleaner(cv["text"])
        normalized.append(record)
    return normalized
