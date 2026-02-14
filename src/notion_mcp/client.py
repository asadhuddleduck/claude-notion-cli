"""Notion API HTTP client with rate limiting, retry, and pagination."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .exceptions import NotionAPIError

# ============================================================
# Constants
# ============================================================

BASE_URL = "https://api.notion.com/v1"
API_VERSION = "2022-06-28"
MIN_REQUEST_INTERVAL = 0.34  # ~3 req/sec
MAX_RETRIES = 3
DEFAULT_PAGE_SIZE = 100


# ============================================================
# Notion API Client
# ============================================================


class NotionClient:
    """HTTP client for the Notion API with rate limiting and pagination."""

    def __init__(self, token: str):
        self.token = token
        self.last_request_time = 0.0

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": API_VERSION,
            "Content-Type": "application/json",
        }

    def _rate_limit(self) -> None:
        elapsed = time.time() - self.last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        self.last_request_time = time.time()

    def request(self, method: str, path: str, body: dict | None = None,
                params: dict | None = None) -> dict:
        """Make an API request with rate limiting and retry on 429."""
        self._rate_limit()

        url = f"{BASE_URL}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(
            url, data=data, method=method, headers=self._headers())

        for attempt in range(MAX_RETRIES):
            try:
                with urllib.request.urlopen(req) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                try:
                    error_body = json.loads(e.read().decode("utf-8"))
                except Exception:
                    error_body = {"message": str(e)}

                if e.code == 429:
                    retry_after = float(e.headers.get("Retry-After", 1.0))
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(retry_after)
                        # Rebuild request since the stream is consumed
                        req = urllib.request.Request(
                            url, data=data, method=method,
                            headers=self._headers())
                        continue
                    raise NotionAPIError(
                        "rate_limited",
                        f"Rate limited after {MAX_RETRIES} retries. "
                        f"Retry after {retry_after}s.",
                        status_code=429,
                    )

                raise NotionAPIError(
                    error_body.get("code", f"http_{e.code}"),
                    error_body.get("message", str(e)),
                    status_code=e.code,
                )

            except urllib.error.URLError as e:
                raise NotionAPIError(
                    "connection_error", str(e.reason)
                )

        raise NotionAPIError("max_retries", "Maximum retries exceeded")

    def paginate(self, method: str, path: str, body: dict | None = None,
                 params: dict | None = None,
                 max_results: int | None = None) -> dict:
        """Auto-paginate and collect all results."""
        all_results: list = []
        cursor = None

        while True:
            if method == "POST":
                req_body = dict(body or {})
                req_body["page_size"] = DEFAULT_PAGE_SIZE
                if cursor:
                    req_body["start_cursor"] = cursor
                resp = self.request("POST", path, req_body)
            else:
                req_params = dict(params or {})
                req_params["page_size"] = DEFAULT_PAGE_SIZE
                if cursor:
                    req_params["start_cursor"] = cursor
                resp = self.request("GET", path, params=req_params)

            results = resp.get("results", [])
            all_results.extend(results)

            if max_results and len(all_results) >= max_results:
                all_results = all_results[:max_results]
                break

            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")

        return {"results": all_results, "total": len(all_results)}
