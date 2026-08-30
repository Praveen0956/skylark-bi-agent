"""
monday_client.py
Thin wrapper around the monday.com GraphQL API v2.

Why a dedicated client instead of calling requests.post() inline everywhere:
- One place to handle auth headers, retries, and rate-limit backoff
- One place to change the API version or endpoint
- Keeps agent.py free of HTTP/GraphQL plumbing
"""

import os
import time
import httpx

MONDAY_API_URL = "https://api.monday.com/v2"


class MondayClient:
    def __init__(self, api_token: str | None = None):
        self.api_token = api_token or os.environ.get("MONDAY_API_TOKEN")
        if not self.api_token:
            raise ValueError("MONDAY_API_TOKEN is not set")
        self.headers = {
            "Authorization": self.api_token,
            "Content-Type": "application/json",
            "API-Version": "2024-10",
        }

    def _post(self, query: str, variables: dict | None = None, retries: int = 3) -> dict:
        payload = {"query": query, "variables": variables or {}}
        last_error = None
        for attempt in range(retries):
            try:
                resp = httpx.post(
                    MONDAY_API_URL, json=payload, headers=self.headers, timeout=30
                )
                if resp.status_code == 429:
                    # rate limited - back off and retry
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                data = resp.json()
                if "errors" in data:
                    raise RuntimeError(f"monday.com API error: {data['errors']}")
                return data["data"]
            except Exception as e:  # noqa: BLE001
                last_error = e
                time.sleep(1 * (attempt + 1))
        raise RuntimeError(f"monday.com request failed after {retries} attempts: {last_error}")

    def get_board_items(self, board_id: str, limit: int = 500) -> list[dict]:
        """
        Fetch all items (rows) on a board, including column values.
        Handles pagination via monday's cursor-based `next_items_page`.
        """
        query = """
        query ($boardId: [ID!], $limit: Int!, $cursor: String) {
          boards(ids: $boardId) {
            name
            items_page(limit: $limit, cursor: $cursor) {
              cursor
              items {
                id
                name
                column_values {
                  id
                  text
                  type
                  value
                }
              }
            }
          }
        }
        """
        items: list[dict] = []
        cursor = None
        while True:
            data = self._post(
                query, {"boardId": [board_id], "limit": limit, "cursor": cursor}
            )
            board = data["boards"][0]
            page = board["items_page"]
            items.extend(page["items"])
            cursor = page["cursor"]
            if not cursor:
                break
        return items

    def get_board_columns(self, board_id: str) -> list[dict]:
        query = """
        query ($boardId: [ID!]) {
          boards(ids: $boardId) {
            columns {
              id
              title
              type
            }
          }
        }
        """
        data = self._post(query, {"boardId": [board_id]})
        return data["boards"][0]["columns"]
