#!/usr/bin/env python3
"""notion-cli.py - Notion API CLI for Claude Code.

Replaces all 14 Notion MCP tools with direct API access.
Zero dependencies beyond Python 3 standard library.

Usage:
    python3 notion-cli.py <subcommand> [flags]
    python3 notion-cli.py --help
"""

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ============================================================
# Constants
# ============================================================

BASE_URL = "https://api.notion.com/v1"
API_VERSION = "2022-06-28"
KEYCHAIN_SERVICE = "notion-cli"
KEYCHAIN_ACCOUNT = "notion-api"
MIN_REQUEST_INTERVAL = 0.34  # ~3 req/sec
MAX_RETRIES = 3
DEFAULT_PAGE_SIZE = 100

# ============================================================
# Keychain Functions
# ============================================================


def get_api_token():
    """Retrieve Notion API token from macOS Keychain."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password",
             "-a", KEYCHAIN_ACCOUNT,
             "-s", KEYCHAIN_SERVICE,
             "-w"],
            capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        error_exit("auth_missing",
                   "No Notion API token found in Keychain. "
                   "Run: python3 notion-cli.py setup --token YOUR_TOKEN",
                   exit_code=2)


def store_api_token(token):
    """Store Notion API token in macOS Keychain."""
    subprocess.run(
        ["security", "add-generic-password",
         "-a", KEYCHAIN_ACCOUNT,
         "-s", KEYCHAIN_SERVICE,
         "-w", token,
         "-U"],
        check=True
    )


# ============================================================
# Output Helpers
# ============================================================


def output(data):
    """Print JSON to stdout."""
    print(json.dumps(data, indent=2, ensure_ascii=False))


def error_exit(code, message, exit_code=1):
    """Print error JSON to stderr and exit."""
    print(json.dumps({"error": True, "code": code, "message": message},
                     indent=2), file=sys.stderr)
    sys.exit(exit_code)


# ============================================================
# ID Normalization
# ============================================================


def format_uuid(hex32):
    """Format 32 hex chars as standard UUID with dashes."""
    return (f"{hex32[:8]}-{hex32[8:12]}-{hex32[12:16]}-"
            f"{hex32[16:20]}-{hex32[20:]}")


def normalize_id(id_or_url):
    """Extract a UUID from a Notion URL or raw ID string."""
    if not id_or_url:
        return id_or_url

    # Handle Notion URLs
    if "notion.so" in id_or_url or "notion.site" in id_or_url:
        # Remove query params and fragments for ID extraction
        clean = id_or_url.split("?")[0].split("#")[0]
        # Try to find UUID pattern (with or without dashes)
        match = re.search(
            r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})',
            clean)
        if match:
            return match.group(1)
        # Try 32-char hex at end of URL path
        match = re.search(r'([a-f0-9]{32})(?:\?|#|$)', id_or_url)
        if match:
            return format_uuid(match.group(1))
        # Try last segment with possible title prefix
        parts = clean.rstrip("/").split("/")
        last = parts[-1] if parts else ""
        # Notion URLs often have "Title-<32hex>" at the end
        match = re.search(r'([a-f0-9]{32})$', last)
        if match:
            return format_uuid(match.group(1))

    # Already a UUID with dashes
    uuid_match = re.match(
        r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$',
        id_or_url)
    if uuid_match:
        return id_or_url

    # Raw 32-char hex
    raw = id_or_url.replace("-", "")
    if len(raw) == 32 and re.match(r'^[a-f0-9]{32}$', raw):
        return format_uuid(raw)

    return id_or_url


# ============================================================
# Rich Text Helpers
# ============================================================


def make_rich_text(text, bold=False, italic=False, code=False,
                   strikethrough=False, underline=False,
                   color="default", link=None):
    """Create a single rich text object."""
    rt = {
        "type": "text",
        "text": {"content": text},
        "annotations": {
            "bold": bold,
            "italic": italic,
            "strikethrough": strikethrough,
            "underline": underline,
            "code": code,
            "color": color,
        },
    }
    if link:
        rt["text"]["link"] = {"url": link}
    return rt


def simple_rich_text(text):
    """Plain string to rich text array."""
    return [make_rich_text(text)]


def parse_rich_text_input(input_val):
    """Accept either a plain string or a JSON rich text array."""
    if isinstance(input_val, str):
        try:
            parsed = json.loads(input_val)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        return simple_rich_text(input_val)
    return input_val


def extract_plain_text(rich_text_array):
    """Extract plain text from a rich text array."""
    if not rich_text_array:
        return ""
    return "".join(rt.get("plain_text", rt.get("text", {}).get("content", ""))
                   for rt in rich_text_array)


# ============================================================
# Block Helpers
# ============================================================


def make_paragraph(text):
    """Create a paragraph block from plain text."""
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": simple_rich_text(text)},
    }


def make_heading(text, level=1):
    """Create a heading block (level 1-3)."""
    key = f"heading_{min(max(level, 1), 3)}"
    return {
        "object": "block",
        "type": key,
        key: {"rich_text": simple_rich_text(text)},
    }


def make_todo(text, checked=False):
    """Create a to-do block."""
    return {
        "object": "block",
        "type": "to_do",
        "to_do": {
            "rich_text": simple_rich_text(text),
            "checked": checked,
        },
    }


def make_bullet(text):
    """Create a bulleted list item block."""
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": simple_rich_text(text)},
    }


def make_numbered(text):
    """Create a numbered list item block."""
    return {
        "object": "block",
        "type": "numbered_list_item",
        "numbered_list_item": {"rich_text": simple_rich_text(text)},
    }


# ============================================================
# Notion API Client
# ============================================================


class NotionClient:
    """HTTP client for the Notion API with rate limiting and pagination."""

    def __init__(self, token):
        self.token = token
        self.last_request_time = 0

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": API_VERSION,
            "Content-Type": "application/json",
        }

    def _rate_limit(self):
        elapsed = time.time() - self.last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        self.last_request_time = time.time()

    def request(self, method, path, body=None, params=None):
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
                        # Rebuild the request since the stream is consumed
                        req = urllib.request.Request(
                            url, data=data, method=method,
                            headers=self._headers())
                        continue
                    error_exit("rate_limited",
                               f"Rate limited after {MAX_RETRIES} retries. "
                               f"Retry after {retry_after}s.",
                               exit_code=3)

                error_exit(
                    error_body.get("code", f"http_{e.code}"),
                    error_body.get("message", str(e)))

            except urllib.error.URLError as e:
                error_exit("connection_error", str(e.reason))

        error_exit("max_retries", "Maximum retries exceeded")

    def paginate(self, method, path, body=None, params=None,
                 max_results=None):
        """Auto-paginate and collect all results."""
        all_results = []
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


# ============================================================
# Subcommand: setup
# ============================================================


def cmd_setup(client, args):
    """Store or verify the API token."""
    if args.token:
        store_api_token(args.token)
        output({"success": True, "message": "Token stored in Keychain."})
        if not args.verify:
            return

    if args.verify:
        # Token is already loaded in client
        resp = client.request("GET", "/users/me")
        output({
            "success": True,
            "message": "Token is valid.",
            "bot": resp,
        })
        return

    if not args.token and not args.verify:
        error_exit("missing_args",
                   "Provide --token TOKEN to store, or --verify to test.")


# ============================================================
# Subcommand: fetch
# ============================================================


def cmd_fetch(client, args):
    """Retrieve a page, database, or block by ID/URL."""
    obj_id = normalize_id(args.id)
    target_type = args.type

    if target_type == "page" or not target_type:
        try:
            resp = client.request("GET", f"/pages/{obj_id}")
            if args.include_children:
                children = fetch_children_recursive(client, obj_id,
                                                    max_depth=10)
                resp["children"] = children
            output(resp)
            return
        except SystemExit:
            if target_type == "page":
                raise
            # Fall through to try database

    if target_type == "database" or not target_type:
        try:
            resp = client.request("GET", f"/databases/{obj_id}")
            output(resp)
            return
        except SystemExit:
            if target_type == "database":
                raise
            # Fall through to try block

    if target_type == "block" or not target_type:
        resp = client.request("GET", f"/blocks/{obj_id}")
        if args.include_children and resp.get("has_children"):
            children = fetch_children_recursive(client, obj_id, max_depth=10)
            resp["children"] = children
        output(resp)
        return


def fetch_children_recursive(client, block_id, max_depth=10, depth=0):
    """Recursively fetch block children."""
    if depth >= max_depth:
        return []

    result = client.paginate("GET", f"/blocks/{block_id}/children")
    blocks = result.get("results", [])

    for block in blocks:
        if block.get("has_children"):
            block["children"] = fetch_children_recursive(
                client, block["id"], max_depth, depth + 1)

    return blocks


# ============================================================
# Subcommand: search
# ============================================================


def cmd_search(client, args):
    """Search the workspace."""
    body = {"query": args.query}

    if args.filter:
        body["filter"] = {
            "value": args.filter,
            "property": "object",
        }

    if args.sort:
        body["sort"] = {
            "direction": "ascending" if args.sort == "asc" else "descending",
            "timestamp": "last_edited_time",
        }

    result = client.paginate("POST", "/search", body,
                             max_results=args.max_results)
    output(result)


# ============================================================
# Subcommand: create-page
# ============================================================


def cmd_create_page(client, args):
    """Create a new page."""
    parent_id = normalize_id(args.parent_id)
    parent_type = args.parent_type or "page_id"

    body = {
        "parent": {parent_type: parent_id},
    }

    # Build properties
    if args.properties_json:
        props = parse_json_arg(args.properties_json, "--properties-json")
        body["properties"] = props
    else:
        body["properties"] = {}

    # Add title
    if args.title:
        if parent_type == "database_id":
            # For database parents, find the title property name
            # Default to "Name" if not specified
            title_prop = args.title_property or "Name"
            body["properties"][title_prop] = {
                "title": simple_rich_text(args.title)
            }
        else:
            body["properties"]["title"] = {
                "title": simple_rich_text(args.title)
            }

    # Build children (content)
    children = []
    if args.content_json:
        children = parse_json_arg(args.content_json, "--content-json")
    elif args.content_text:
        children = [make_paragraph(args.content_text)]

    if children:
        body["children"] = children

    # Optional icon/cover
    if args.icon_emoji:
        body["icon"] = {"type": "emoji", "emoji": args.icon_emoji}
    if args.cover_url:
        body["cover"] = {"type": "external", "external": {"url": args.cover_url}}

    resp = client.request("POST", "/pages", body)
    output(resp)


# ============================================================
# Subcommand: update-page
# ============================================================


def cmd_update_page(client, args):
    """Update a page's properties, content, or metadata."""
    page_id = normalize_id(args.page_id)

    # Property updates
    if (args.properties_json or args.title or args.archive or args.unarchive
            or args.icon_emoji or args.cover_url):
        body = {}

        if args.properties_json:
            body["properties"] = parse_json_arg(args.properties_json,
                                                "--properties-json")
        elif args.title:
            # Need to know the title property name; fetch page first
            page = client.request("GET", f"/pages/{page_id}")
            title_prop = None
            for prop_name, prop_val in page.get("properties", {}).items():
                if prop_val.get("type") == "title":
                    title_prop = prop_name
                    break
            if not title_prop:
                title_prop = "title"
            body["properties"] = {
                title_prop: {"title": simple_rich_text(args.title)}
            }

        if args.archive:
            body["archived"] = True
        if args.unarchive:
            body["archived"] = False
        if args.icon_emoji:
            body["icon"] = {"type": "emoji", "emoji": args.icon_emoji}
        if args.cover_url:
            body["cover"] = {
                "type": "external",
                "external": {"url": args.cover_url},
            }

        resp = client.request("PATCH", f"/pages/{page_id}", body)

        # If no content to append, output now
        if not args.append_blocks_json and not args.append_text:
            output(resp)
            return

    # Content append
    if args.append_blocks_json or args.append_text:
        if args.append_blocks_json:
            children = parse_json_arg(args.append_blocks_json,
                                      "--append-blocks-json")
        else:
            children = [make_paragraph(args.append_text)]

        # Append in chunks of 100 (API limit)
        for i in range(0, len(children), 100):
            chunk = children[i:i + 100]
            resp = client.request(
                "PATCH", f"/blocks/{page_id}/children",
                {"children": chunk})

        output(resp)
        return

    # If nothing was specified
    if not any([args.properties_json, args.title, args.archive,
                args.unarchive, args.icon_emoji, args.cover_url,
                args.append_blocks_json, args.append_text]):
        error_exit("missing_args", "No update flags provided.")


# ============================================================
# Subcommand: create-database
# ============================================================


def cmd_create_database(client, args):
    """Create a new database."""
    parent_id = normalize_id(args.parent_id)

    body = {
        "parent": {"page_id": parent_id},
        "title": simple_rich_text(args.title),
        "properties": parse_json_arg(args.properties_json,
                                     "--properties-json"),
    }

    # Ensure there's a title property
    has_title_prop = any(
        "title" in v for v in body["properties"].values()
        if isinstance(v, dict)
    )
    if not has_title_prop:
        body["properties"]["Name"] = {"title": {}}

    if args.description:
        body["description"] = simple_rich_text(args.description)

    if args.inline:
        body["is_inline"] = True

    if args.icon_emoji:
        body["icon"] = {"type": "emoji", "emoji": args.icon_emoji}

    resp = client.request("POST", "/databases", body)
    output(resp)


# ============================================================
# Subcommand: update-database
# ============================================================


def cmd_update_database(client, args):
    """Update a database's schema or metadata."""
    db_id = normalize_id(args.database_id)
    body = {}

    if args.title:
        body["title"] = simple_rich_text(args.title)

    if args.description:
        body["description"] = simple_rich_text(args.description)

    props = {}
    if args.properties_json:
        props = parse_json_arg(args.properties_json, "--properties-json")

    if args.remove_properties:
        for prop_name in args.remove_properties.split(","):
            props[prop_name.strip()] = None

    if props:
        body["properties"] = props

    if args.archive:
        body["archived"] = True

    if not body:
        error_exit("missing_args", "No update flags provided.")

    resp = client.request("PATCH", f"/databases/{db_id}", body)
    output(resp)


# ============================================================
# Subcommand: query-database
# ============================================================


def cmd_query_database(client, args):
    """Query a database with filters and sorts."""
    db_id = normalize_id(args.database_id)
    body = {}

    if args.filter_json:
        body["filter"] = parse_json_arg(args.filter_json, "--filter-json")

    if args.sorts_json:
        body["sorts"] = parse_json_arg(args.sorts_json, "--sorts-json")

    if args.no_auto_paginate:
        # Single page query
        if args.page_size:
            body["page_size"] = min(int(args.page_size), 100)
        if args.cursor:
            body["start_cursor"] = args.cursor
        resp = client.request("POST", f"/databases/{db_id}/query", body)
        output(resp)
    else:
        # Auto-paginate
        result = client.paginate(
            "POST", f"/databases/{db_id}/query", body,
            max_results=args.max_results)
        output(result)


# ============================================================
# Subcommand: query-meeting-notes
# ============================================================


def cmd_query_meeting_notes(client, args):
    """Query meeting notes (composite search + filter)."""
    # Build a search query
    query = args.title_contains or "meeting"

    body = {"query": query}

    # Search for pages
    result = client.paginate("POST", "/search", body,
                             max_results=args.max_results or 50)
    pages = result.get("results", [])

    # Client-side filtering
    filtered = []
    for page in pages:
        if page.get("object") != "page":
            continue

        # Date filtering
        created = page.get("created_time", "")
        if args.date_from and created < args.date_from:
            continue
        if args.date_to and created > args.date_to:
            continue

        # Relative date filtering
        if args.date_relative:
            now = time.time()
            created_ts = _parse_iso_timestamp(created)
            if created_ts:
                if args.date_relative == "past_week":
                    if now - created_ts > 7 * 86400:
                        continue
                elif args.date_relative == "past_month":
                    if now - created_ts > 30 * 86400:
                        continue
                elif args.date_relative == "this_week":
                    # Approximate: past 7 days
                    if now - created_ts > 7 * 86400:
                        continue

        filtered.append(page)

    output({"results": filtered, "total": len(filtered)})


def _parse_iso_timestamp(iso_str):
    """Parse ISO 8601 timestamp to epoch seconds (basic parser)."""
    try:
        # Handle formats like 2026-02-14T10:30:00.000Z
        clean = iso_str.replace("Z", "+00:00")
        # Python 3.7+ fromisoformat
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(clean)
        return dt.timestamp()
    except Exception:
        return None


# ============================================================
# Subcommand: create-comment
# ============================================================


def cmd_create_comment(client, args):
    """Add a comment to a page."""
    body = {}

    if args.parent_id:
        body["parent"] = {"page_id": normalize_id(args.parent_id)}

    if args.discussion_id:
        body["discussion_id"] = args.discussion_id

    if args.rich_text_json:
        body["rich_text"] = parse_json_arg(args.rich_text_json,
                                           "--rich-text-json")
    elif args.text:
        body["rich_text"] = simple_rich_text(args.text)
    else:
        error_exit("missing_args", "Provide --text or --rich-text-json.")

    resp = client.request("POST", "/comments", body)
    output(resp)


# ============================================================
# Subcommand: get-comments
# ============================================================


def cmd_get_comments(client, args):
    """Get all comments on a page or block."""
    block_id = normalize_id(args.page_id)
    result = client.paginate(
        "GET", "/comments", params={"block_id": block_id},
        max_results=args.max_results)
    output(result)


# ============================================================
# Subcommand: get-users
# ============================================================


def cmd_get_users(client, args):
    """List or search workspace users."""
    if args.user_id:
        user_id = "me" if args.user_id == "me" else normalize_id(args.user_id)
        resp = client.request("GET", f"/users/{user_id}")
        output(resp)
        return

    result = client.paginate("GET", "/users",
                             max_results=args.max_results)
    users = result.get("results", [])

    # Client-side filtering
    if args.query:
        q = args.query.lower()
        users = [u for u in users
                 if q in u.get("name", "").lower()
                 or q in (u.get("person", {}).get("email", "")
                          if u.get("type") == "person" else "").lower()]

    output({"results": users, "total": len(users)})


# ============================================================
# Subcommand: get-teams
# ============================================================


def cmd_get_teams(client, args):
    """List teamspaces (limited by public API)."""
    # The public Notion API does not have a teams endpoint.
    # We can try to extract team info from user data or use search.
    # For now, we note the limitation.
    output({
        "warning": "The public Notion API does not have a dedicated teams "
                   "endpoint. Team information may be limited.",
        "suggestion": "Use the Notion UI to manage teams, or query users "
                      "to see workspace membership.",
    })

    # Attempt to list users as a proxy
    result = client.paginate("GET", "/users")
    users = result.get("results", [])

    if args.query:
        q = args.query.lower()
        users = [u for u in users
                 if q in u.get("name", "").lower()]

    output({"users": users, "total": len(users)})


# ============================================================
# Subcommand: move-page
# ============================================================


def cmd_move_page(client, args):
    """Move pages to a new parent."""
    page_ids = [normalize_id(pid.strip())
                for pid in args.page_ids.split(",")]
    new_parent_id = normalize_id(args.new_parent_id)
    parent_type = args.new_parent_type or "page_id"

    results = []
    for pid in page_ids:
        body = {"parent": {parent_type: new_parent_id}}
        resp = client.request("PATCH", f"/pages/{pid}", body)
        results.append(resp)

    if len(results) == 1:
        output(results[0])
    else:
        output({"results": results, "total": len(results)})


# ============================================================
# Subcommand: duplicate-page
# ============================================================


def cmd_duplicate_page(client, args):
    """Duplicate a page (composite operation)."""
    page_id = normalize_id(args.page_id)

    # 1. Fetch source page
    source = client.request("GET", f"/pages/{page_id}")

    # 2. Fetch source content
    children = fetch_children_recursive(client, page_id, max_depth=10)

    # 3. Determine parent
    parent = source.get("parent", {})
    if args.new_parent_id:
        parent = {"page_id": normalize_id(args.new_parent_id)}

    # 4. Build new page properties (copy from source)
    properties = {}
    for prop_name, prop_val in source.get("properties", {}).items():
        prop_type = prop_val.get("type")
        if prop_type == "title":
            new_title = args.new_title or (
                "Copy of " + extract_plain_text(prop_val.get("title", [])))
            properties[prop_name] = {"title": simple_rich_text(new_title)}
        elif prop_type in ("rich_text", "number", "select", "multi_select",
                           "date", "checkbox", "url", "email",
                           "phone_number"):
            properties[prop_name] = {prop_type: prop_val.get(prop_type)}
        # Skip computed/readonly properties (formula, rollup, created_by, etc.)

    # 5. Create new page
    create_body = {
        "parent": parent,
        "properties": properties,
    }

    if source.get("icon"):
        create_body["icon"] = source["icon"]
    if source.get("cover"):
        create_body["cover"] = source["cover"]

    # Add first batch of children (flatten to remove nested children first)
    top_level_blocks = _prepare_blocks_for_copy(children[:100])
    if top_level_blocks:
        create_body["children"] = top_level_blocks

    new_page = client.request("POST", "/pages", create_body)

    # 6. Append remaining blocks if >100
    if len(children) > 100:
        remaining = _prepare_blocks_for_copy(children[100:])
        for i in range(0, len(remaining), 100):
            chunk = remaining[i:i + 100]
            client.request(
                "PATCH", f"/blocks/{new_page['id']}/children",
                {"children": chunk})

    output({
        "success": True,
        "message": "Page duplicated.",
        "source_id": page_id,
        "new_page": new_page,
    })


def _prepare_blocks_for_copy(blocks):
    """Prepare blocks for copying by removing IDs and read-only fields."""
    prepared = []
    for block in blocks:
        block_type = block.get("type")
        if not block_type:
            continue

        new_block = {
            "object": "block",
            "type": block_type,
        }

        # Copy the type-specific content
        if block_type in block:
            content = dict(block[block_type])
            # Remove read-only fields
            content.pop("id", None)
            content.pop("created_time", None)
            content.pop("last_edited_time", None)
            new_block[block_type] = content

        # Handle nested children
        if block.get("children"):
            child_blocks = _prepare_blocks_for_copy(block["children"])
            if child_blocks and block_type in block:
                new_block[block_type]["children"] = child_blocks

        prepared.append(new_block)

    return prepared


# ============================================================
# Subcommand: blocks
# ============================================================


def cmd_blocks(client, args):
    """Block-level operations."""
    action = args.action
    block_id = normalize_id(args.block_id) if args.block_id else None

    if action == "get":
        if not block_id:
            error_exit("missing_args", "Block ID required.")
        resp = client.request("GET", f"/blocks/{block_id}")
        output(resp)

    elif action == "children":
        if not block_id:
            error_exit("missing_args", "Block ID required.")
        result = client.paginate(
            "GET", f"/blocks/{block_id}/children",
            max_results=args.max_results)
        output(result)

    elif action == "append":
        if not block_id:
            error_exit("missing_args", "Parent block ID required.")
        if args.blocks_json:
            children = parse_json_arg(args.blocks_json, "--blocks-json")
        elif args.text:
            children = [make_paragraph(args.text)]
        else:
            error_exit("missing_args",
                       "Provide --blocks-json or --text.")
        # Append in chunks
        for i in range(0, len(children), 100):
            chunk = children[i:i + 100]
            resp = client.request(
                "PATCH", f"/blocks/{block_id}/children",
                {"children": chunk})
        output(resp)

    elif action == "update":
        if not block_id:
            error_exit("missing_args", "Block ID required.")
        if not args.block_json:
            error_exit("missing_args", "Provide --block-json.")
        block_data = parse_json_arg(args.block_json, "--block-json")
        resp = client.request("PATCH", f"/blocks/{block_id}", block_data)
        output(resp)

    elif action == "delete":
        if not block_id:
            error_exit("missing_args", "Block ID required.")
        resp = client.request("DELETE", f"/blocks/{block_id}")
        output(resp)

    else:
        error_exit("invalid_action",
                   f"Unknown block action: {action}. "
                   "Use: get, children, append, update, delete")


# ============================================================
# JSON Argument Parser
# ============================================================


def parse_json_arg(value, flag_name):
    """Parse a JSON string argument, exiting on error."""
    try:
        return json.loads(value)
    except json.JSONDecodeError as e:
        error_exit("invalid_json",
                   f"Invalid JSON for {flag_name}: {e}")


# ============================================================
# Argument Parser
# ============================================================


def build_parser():
    """Build the argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="notion-cli",
        description="Notion API CLI for Claude Code")

    sub = parser.add_subparsers(dest="command", help="Subcommands")

    # --- setup ---
    p = sub.add_parser("setup", help="Store or verify API token")
    p.add_argument("--token", help="API token to store in Keychain")
    p.add_argument("--verify", action="store_true",
                   help="Verify token by calling /users/me")

    # --- fetch ---
    p = sub.add_parser("fetch", help="Retrieve page/database/block")
    p.add_argument("id", help="Page, database, or block ID/URL")
    p.add_argument("--type", choices=["page", "database", "block"],
                   help="Force type (auto-detected by default)")
    p.add_argument("--include-children", action="store_true",
                   help="Fetch block children recursively")

    # --- search ---
    p = sub.add_parser("search", help="Search the workspace")
    p.add_argument("query", help="Search query")
    p.add_argument("--filter", choices=["page", "database"],
                   help="Filter by object type")
    p.add_argument("--sort", choices=["asc", "desc"],
                   help="Sort by last_edited_time")
    p.add_argument("--max-results", type=int,
                   help="Maximum results to return")

    # --- create-page ---
    p = sub.add_parser("create-page", help="Create a new page")
    p.add_argument("--parent-id", required=True,
                   help="Parent page or database ID")
    p.add_argument("--title", help="Page title")
    p.add_argument("--parent-type",
                   choices=["page_id", "database_id"],
                   help="Parent type (default: page_id)")
    p.add_argument("--title-property", default="Name",
                   help="Title property name for database parents")
    p.add_argument("--properties-json",
                   help="Properties as JSON object")
    p.add_argument("--content-json",
                   help="Content blocks as JSON array")
    p.add_argument("--content-text",
                   help="Simple paragraph text")
    p.add_argument("--icon-emoji", help="Page icon emoji")
    p.add_argument("--cover-url", help="Cover image URL")

    # --- update-page ---
    p = sub.add_parser("update-page", help="Update a page")
    p.add_argument("page_id", help="Page ID or URL")
    p.add_argument("--properties-json",
                   help="Properties to update as JSON")
    p.add_argument("--title", help="Update page title")
    p.add_argument("--archive", action="store_true",
                   help="Archive the page")
    p.add_argument("--unarchive", action="store_true",
                   help="Unarchive the page")
    p.add_argument("--icon-emoji", help="Update icon emoji")
    p.add_argument("--cover-url", help="Update cover URL")
    p.add_argument("--append-blocks-json",
                   help="Blocks to append as JSON array")
    p.add_argument("--append-text",
                   help="Simple paragraph text to append")

    # --- create-database ---
    p = sub.add_parser("create-database", help="Create a database")
    p.add_argument("--parent-id", required=True,
                   help="Parent page ID")
    p.add_argument("--title", required=True,
                   help="Database title")
    p.add_argument("--properties-json", required=True,
                   help="Property schema as JSON")
    p.add_argument("--description", help="Database description")
    p.add_argument("--inline", action="store_true",
                   help="Create as inline database")
    p.add_argument("--icon-emoji", help="Database icon emoji")

    # --- update-database ---
    p = sub.add_parser("update-database", help="Update database schema")
    p.add_argument("database_id", help="Database ID or URL")
    p.add_argument("--title", help="New title")
    p.add_argument("--description", help="New description")
    p.add_argument("--properties-json",
                   help="Properties to add/update as JSON")
    p.add_argument("--remove-properties",
                   help="Comma-separated property names to remove")
    p.add_argument("--archive", action="store_true",
                   help="Archive the database")

    # --- query-database ---
    p = sub.add_parser("query-database", help="Query a database")
    p.add_argument("database_id", help="Database ID or URL")
    p.add_argument("--filter-json", help="Filter object as JSON")
    p.add_argument("--sorts-json", help="Sorts array as JSON")
    p.add_argument("--max-results", type=int,
                   help="Maximum total results")
    p.add_argument("--page-size", type=int,
                   help="Results per page (max 100)")
    p.add_argument("--cursor", help="Start cursor for manual pagination")
    p.add_argument("--no-auto-paginate", action="store_true",
                   help="Return single page with cursor")

    # --- query-meeting-notes ---
    p = sub.add_parser("query-meeting-notes",
                       help="Query meeting notes")
    p.add_argument("--title-contains", help="Filter by title keyword")
    p.add_argument("--date-from", help="Start date (YYYY-MM-DD)")
    p.add_argument("--date-to", help="End date (YYYY-MM-DD)")
    p.add_argument("--date-relative",
                   choices=["past_week", "past_month", "this_week"],
                   help="Relative date filter")
    p.add_argument("--attendee-id", help="Filter by attendee user ID")
    p.add_argument("--max-results", type=int, default=50,
                   help="Maximum results (default 50)")

    # --- create-comment ---
    p = sub.add_parser("create-comment", help="Add a comment to a page")
    p.add_argument("--parent-id", help="Page ID")
    p.add_argument("--discussion-id", help="Reply to discussion")
    p.add_argument("--text", help="Comment text")
    p.add_argument("--rich-text-json",
                   help="Rich text array as JSON")

    # --- get-comments ---
    p = sub.add_parser("get-comments", help="Get comments on a page")
    p.add_argument("page_id", help="Page or block ID")
    p.add_argument("--max-results", type=int,
                   help="Maximum comments to return")

    # --- get-users ---
    p = sub.add_parser("get-users", help="List workspace users")
    p.add_argument("--query", help="Filter by name or email")
    p.add_argument("--user-id",
                   help="Specific user ID (or 'me')")
    p.add_argument("--max-results", type=int,
                   help="Maximum users to return")

    # --- get-teams ---
    p = sub.add_parser("get-teams", help="List teamspaces (limited)")
    p.add_argument("--query", help="Filter by name")

    # --- move-page ---
    p = sub.add_parser("move-page", help="Move pages to new parent")
    p.add_argument("page_ids",
                   help="Comma-separated page IDs")
    p.add_argument("--new-parent-id", required=True,
                   help="New parent ID")
    p.add_argument("--new-parent-type",
                   choices=["page_id", "database_id"],
                   help="Parent type (default: page_id)")

    # --- duplicate-page ---
    p = sub.add_parser("duplicate-page", help="Duplicate a page")
    p.add_argument("page_id", help="Source page ID or URL")
    p.add_argument("--new-title", help="Title for the copy")
    p.add_argument("--new-parent-id",
                   help="Parent for the copy (default: same)")

    # --- blocks ---
    p = sub.add_parser("blocks", help="Block-level operations")
    p.add_argument("action",
                   choices=["get", "children", "append", "update", "delete"],
                   help="Block action")
    p.add_argument("block_id", nargs="?", help="Block ID")
    p.add_argument("--blocks-json",
                   help="Blocks to append as JSON array")
    p.add_argument("--block-json",
                   help="Block data for update as JSON")
    p.add_argument("--text",
                   help="Simple paragraph text (for append)")
    p.add_argument("--max-results", type=int,
                   help="Max children to return")

    return parser


# ============================================================
# Main
# ============================================================


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # For setup with --token, we may not have a stored token yet
    if args.command == "setup" and args.token and not args.verify:
        store_api_token(args.token)
        output({"success": True, "message": "Token stored in Keychain."})
        return

    # Get token from Keychain
    token = get_api_token()
    client = NotionClient(token)

    # Dispatch to subcommand
    commands = {
        "setup": cmd_setup,
        "fetch": cmd_fetch,
        "search": cmd_search,
        "create-page": cmd_create_page,
        "update-page": cmd_update_page,
        "create-database": cmd_create_database,
        "update-database": cmd_update_database,
        "query-database": cmd_query_database,
        "query-meeting-notes": cmd_query_meeting_notes,
        "create-comment": cmd_create_comment,
        "get-comments": cmd_get_comments,
        "get-users": cmd_get_users,
        "get-teams": cmd_get_teams,
        "move-page": cmd_move_page,
        "duplicate-page": cmd_duplicate_page,
        "blocks": cmd_blocks,
    }

    handler = commands.get(args.command)
    if handler:
        handler(client, args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
