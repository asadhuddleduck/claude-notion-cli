"""Notion API operations — shared business logic for CLI and MCP server.

All functions accept explicit keyword arguments, return dicts,
and raise exceptions from notion_mcp.exceptions on errors.
"""

from __future__ import annotations

import time
from datetime import datetime

from .client import NotionClient
from .exceptions import NotionValidationError
from .helpers import (
    extract_plain_text,
    make_paragraph,
    normalize_id,
    parse_json_arg,
    simple_rich_text,
)


# ============================================================
# Helpers (internal)
# ============================================================


def _fetch_children_recursive(client: NotionClient, block_id: str,
                               max_depth: int = 10, depth: int = 0) -> list:
    """Recursively fetch block children."""
    if depth >= max_depth:
        return []

    result = client.paginate("GET", f"/blocks/{block_id}/children")
    blocks = result.get("results", [])

    for block in blocks:
        if block.get("has_children"):
            block["children"] = _fetch_children_recursive(
                client, block["id"], max_depth, depth + 1)

    return blocks


def _prepare_blocks_for_copy(blocks: list) -> list:
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

        if block_type in block:
            content = dict(block[block_type])
            content.pop("id", None)
            content.pop("created_time", None)
            content.pop("last_edited_time", None)
            new_block[block_type] = content

        if block.get("children"):
            child_blocks = _prepare_blocks_for_copy(block["children"])
            if child_blocks and block_type in block:
                new_block[block_type]["children"] = child_blocks

        prepared.append(new_block)

    return prepared


def _parse_iso_timestamp(iso_str: str) -> float | None:
    """Parse ISO 8601 timestamp to epoch seconds."""
    try:
        clean = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        return dt.timestamp()
    except Exception:
        return None


# ============================================================
# Operations
# ============================================================


def setup(client: NotionClient) -> dict:
    """Verify the API token by calling /users/me."""
    resp = client.request("GET", "/users/me")
    return {"success": True, "message": "Token is valid.", "bot": resp}


def fetch(client: NotionClient, id: str,
          type: str | None = None,
          include_children: bool = False) -> dict:
    """Retrieve a page, database, or block by ID/URL."""
    obj_id = normalize_id(id)

    if type == "page" or not type:
        try:
            resp = client.request("GET", f"/pages/{obj_id}")
            if include_children:
                resp["children"] = _fetch_children_recursive(client, obj_id)
            return resp
        except Exception:
            if type == "page":
                raise

    if type == "database" or not type:
        try:
            resp = client.request("GET", f"/databases/{obj_id}")
            return resp
        except Exception:
            if type == "database":
                raise

    # block (or fallthrough)
    resp = client.request("GET", f"/blocks/{obj_id}")
    if include_children and resp.get("has_children"):
        resp["children"] = _fetch_children_recursive(client, obj_id)
    return resp


def search(client: NotionClient, query: str,
           filter: str | None = None,
           sort: str | None = None,
           max_results: int | None = None) -> dict:
    """Search the workspace."""
    body: dict = {"query": query}

    if filter:
        body["filter"] = {"value": filter, "property": "object"}

    if sort:
        body["sort"] = {
            "direction": "ascending" if sort == "asc" else "descending",
            "timestamp": "last_edited_time",
        }

    return client.paginate("POST", "/search", body, max_results=max_results)


def create_page(client: NotionClient, parent_id: str,
                title: str | None = None,
                parent_type: str = "page_id",
                title_property: str = "Name",
                properties_json: str | None = None,
                content_json: str | None = None,
                content_text: str | None = None,
                icon_emoji: str | None = None,
                cover_url: str | None = None) -> dict:
    """Create a new page."""
    pid = normalize_id(parent_id)
    body: dict = {"parent": {parent_type: pid}}

    if properties_json:
        body["properties"] = parse_json_arg(properties_json, "properties_json")
    else:
        body["properties"] = {}

    if title:
        if parent_type == "database_id":
            body["properties"][title_property] = {
                "title": simple_rich_text(title)
            }
        else:
            body["properties"]["title"] = {
                "title": simple_rich_text(title)
            }

    children = []
    if content_json:
        children = parse_json_arg(content_json, "content_json")
    elif content_text:
        children = [make_paragraph(content_text)]
    if children:
        body["children"] = children

    if icon_emoji:
        body["icon"] = {"type": "emoji", "emoji": icon_emoji}
    if cover_url:
        body["cover"] = {"type": "external", "external": {"url": cover_url}}

    return client.request("POST", "/pages", body)


def update_page(client: NotionClient, page_id: str,
                properties_json: str | None = None,
                title: str | None = None,
                archive: bool = False,
                unarchive: bool = False,
                icon_emoji: str | None = None,
                cover_url: str | None = None,
                append_blocks_json: str | None = None,
                append_text: str | None = None) -> dict:
    """Update a page's properties, content, or metadata."""
    pid = normalize_id(page_id)
    resp = None

    # Property / metadata updates
    if properties_json or title or archive or unarchive or icon_emoji or cover_url:
        body: dict = {}

        if properties_json:
            body["properties"] = parse_json_arg(properties_json, "properties_json")
        elif title:
            page = client.request("GET", f"/pages/{pid}")
            title_prop = None
            for prop_name, prop_val in page.get("properties", {}).items():
                if prop_val.get("type") == "title":
                    title_prop = prop_name
                    break
            body["properties"] = {
                (title_prop or "title"): {"title": simple_rich_text(title)}
            }

        if archive:
            body["archived"] = True
        if unarchive:
            body["archived"] = False
        if icon_emoji:
            body["icon"] = {"type": "emoji", "emoji": icon_emoji}
        if cover_url:
            body["cover"] = {"type": "external", "external": {"url": cover_url}}

        resp = client.request("PATCH", f"/pages/{pid}", body)

        if not append_blocks_json and not append_text:
            return resp

    # Content append
    if append_blocks_json or append_text:
        if append_blocks_json:
            children = parse_json_arg(append_blocks_json, "append_blocks_json")
        else:
            children = [make_paragraph(append_text)]

        for i in range(0, len(children), 100):
            chunk = children[i:i + 100]
            resp = client.request(
                "PATCH", f"/blocks/{pid}/children", {"children": chunk})
        return resp

    if resp is None:
        raise NotionValidationError("missing_args", "No update flags provided.")
    return resp


def create_database(client: NotionClient, parent_id: str, title: str,
                    properties_json: str,
                    description: str | None = None,
                    inline: bool = False,
                    icon_emoji: str | None = None) -> dict:
    """Create a new database."""
    pid = normalize_id(parent_id)
    props = parse_json_arg(properties_json, "properties_json")

    body: dict = {
        "parent": {"page_id": pid},
        "title": simple_rich_text(title),
        "properties": props,
    }

    has_title_prop = any(
        "title" in v for v in body["properties"].values()
        if isinstance(v, dict)
    )
    if not has_title_prop:
        body["properties"]["Name"] = {"title": {}}

    if description:
        body["description"] = simple_rich_text(description)
    if inline:
        body["is_inline"] = True
    if icon_emoji:
        body["icon"] = {"type": "emoji", "emoji": icon_emoji}

    return client.request("POST", "/databases", body)


def update_database(client: NotionClient, database_id: str,
                    title: str | None = None,
                    description: str | None = None,
                    properties_json: str | None = None,
                    remove_properties: str | None = None,
                    archive: bool = False) -> dict:
    """Update a database's schema or metadata."""
    db_id = normalize_id(database_id)
    body: dict = {}

    if title:
        body["title"] = simple_rich_text(title)
    if description:
        body["description"] = simple_rich_text(description)

    props: dict = {}
    if properties_json:
        props = parse_json_arg(properties_json, "properties_json")
    if remove_properties:
        for prop_name in remove_properties.split(","):
            props[prop_name.strip()] = None
    if props:
        body["properties"] = props

    if archive:
        body["archived"] = True

    if not body:
        raise NotionValidationError("missing_args", "No update flags provided.")

    return client.request("PATCH", f"/databases/{db_id}", body)


def query_database(client: NotionClient, database_id: str,
                   filter_json: str | None = None,
                   sorts_json: str | None = None,
                   max_results: int | None = None,
                   page_size: int | None = None,
                   cursor: str | None = None,
                   no_auto_paginate: bool = False) -> dict:
    """Query a database with filters and sorts."""
    db_id = normalize_id(database_id)
    body: dict = {}

    if filter_json:
        body["filter"] = parse_json_arg(filter_json, "filter_json")
    if sorts_json:
        body["sorts"] = parse_json_arg(sorts_json, "sorts_json")

    if no_auto_paginate:
        if page_size:
            body["page_size"] = min(int(page_size), 100)
        if cursor:
            body["start_cursor"] = cursor
        return client.request("POST", f"/databases/{db_id}/query", body)

    return client.paginate(
        "POST", f"/databases/{db_id}/query", body,
        max_results=max_results)


def query_meeting_notes(client: NotionClient,
                        title_contains: str | None = None,
                        date_from: str | None = None,
                        date_to: str | None = None,
                        date_relative: str | None = None,
                        attendee_id: str | None = None,
                        max_results: int = 50) -> dict:
    """Query meeting notes (composite search + filter)."""
    query = title_contains or "meeting"
    body: dict = {"query": query}

    result = client.paginate("POST", "/search", body, max_results=max_results)
    pages = result.get("results", [])

    filtered = []
    for page in pages:
        if page.get("object") != "page":
            continue

        created = page.get("created_time", "")
        if date_from and created < date_from:
            continue
        if date_to and created > date_to:
            continue

        if date_relative:
            now = time.time()
            created_ts = _parse_iso_timestamp(created)
            if created_ts:
                if date_relative == "past_week" and now - created_ts > 7 * 86400:
                    continue
                elif date_relative == "past_month" and now - created_ts > 30 * 86400:
                    continue
                elif date_relative == "this_week" and now - created_ts > 7 * 86400:
                    continue

        filtered.append(page)

    return {"results": filtered, "total": len(filtered)}


def create_comment(client: NotionClient,
                   parent_id: str | None = None,
                   discussion_id: str | None = None,
                   text: str | None = None,
                   rich_text_json: str | None = None) -> dict:
    """Add a comment to a page."""
    body: dict = {}

    if parent_id:
        body["parent"] = {"page_id": normalize_id(parent_id)}
    if discussion_id:
        body["discussion_id"] = discussion_id

    if rich_text_json:
        body["rich_text"] = parse_json_arg(rich_text_json, "rich_text_json")
    elif text:
        body["rich_text"] = simple_rich_text(text)
    else:
        raise NotionValidationError(
            "missing_args", "Provide text or rich_text_json.")

    return client.request("POST", "/comments", body)


def get_comments(client: NotionClient, page_id: str,
                 max_results: int | None = None) -> dict:
    """Get all comments on a page or block."""
    block_id = normalize_id(page_id)
    return client.paginate(
        "GET", "/comments", params={"block_id": block_id},
        max_results=max_results)


def get_users(client: NotionClient,
              query: str | None = None,
              user_id: str | None = None,
              max_results: int | None = None) -> dict:
    """List or search workspace users."""
    if user_id:
        uid = "me" if user_id == "me" else normalize_id(user_id)
        return client.request("GET", f"/users/{uid}")

    result = client.paginate("GET", "/users", max_results=max_results)
    users = result.get("results", [])

    if query:
        q = query.lower()
        users = [
            u for u in users
            if q in u.get("name", "").lower()
            or q in (u.get("person", {}).get("email", "")
                     if u.get("type") == "person" else "").lower()
        ]

    return {"results": users, "total": len(users)}


def get_teams(client: NotionClient,
              query: str | None = None) -> dict:
    """List teamspaces (limited by public API)."""
    result = client.paginate("GET", "/users")
    users = result.get("results", [])

    if query:
        q = query.lower()
        users = [u for u in users if q in u.get("name", "").lower()]

    return {
        "warning": "The public Notion API does not have a dedicated teams "
                   "endpoint. Returning workspace users as a proxy.",
        "users": users,
        "total": len(users),
    }


def move_page(client: NotionClient, page_ids: str,
              new_parent_id: str,
              new_parent_type: str = "page_id") -> dict:
    """Move pages to a new parent."""
    ids = [normalize_id(pid.strip()) for pid in page_ids.split(",")]
    parent_id = normalize_id(new_parent_id)

    results = []
    for pid in ids:
        body = {"parent": {new_parent_type: parent_id}}
        resp = client.request("PATCH", f"/pages/{pid}", body)
        results.append(resp)

    if len(results) == 1:
        return results[0]
    return {"results": results, "total": len(results)}


def duplicate_page(client: NotionClient, page_id: str,
                   new_title: str | None = None,
                   new_parent_id: str | None = None) -> dict:
    """Duplicate a page (composite operation)."""
    pid = normalize_id(page_id)

    source = client.request("GET", f"/pages/{pid}")
    children = _fetch_children_recursive(client, pid, max_depth=10)

    parent = source.get("parent", {})
    if new_parent_id:
        parent = {"page_id": normalize_id(new_parent_id)}

    properties: dict = {}
    for prop_name, prop_val in source.get("properties", {}).items():
        prop_type = prop_val.get("type")
        if prop_type == "title":
            t = new_title or ("Copy of " + extract_plain_text(
                prop_val.get("title", [])))
            properties[prop_name] = {"title": simple_rich_text(t)}
        elif prop_type in ("rich_text", "number", "select", "multi_select",
                           "date", "checkbox", "url", "email",
                           "phone_number"):
            properties[prop_name] = {prop_type: prop_val.get(prop_type)}

    create_body: dict = {"parent": parent, "properties": properties}

    if source.get("icon"):
        create_body["icon"] = source["icon"]
    if source.get("cover"):
        create_body["cover"] = source["cover"]

    top_level_blocks = _prepare_blocks_for_copy(children[:100])
    if top_level_blocks:
        create_body["children"] = top_level_blocks

    new_page = client.request("POST", "/pages", create_body)

    if len(children) > 100:
        remaining = _prepare_blocks_for_copy(children[100:])
        for i in range(0, len(remaining), 100):
            chunk = remaining[i:i + 100]
            client.request(
                "PATCH", f"/blocks/{new_page['id']}/children",
                {"children": chunk})

    return {
        "success": True,
        "message": "Page duplicated.",
        "source_id": pid,
        "new_page": new_page,
    }


def blocks(client: NotionClient, action: str,
           block_id: str | None = None,
           blocks_json: str | None = None,
           block_json: str | None = None,
           text: str | None = None,
           max_results: int | None = None) -> dict:
    """Block-level operations (get, children, append, update, delete)."""
    bid = normalize_id(block_id) if block_id else None

    if action == "get":
        if not bid:
            raise NotionValidationError("missing_args", "Block ID required.")
        return client.request("GET", f"/blocks/{bid}")

    elif action == "children":
        if not bid:
            raise NotionValidationError("missing_args", "Block ID required.")
        return client.paginate(
            "GET", f"/blocks/{bid}/children", max_results=max_results)

    elif action == "append":
        if not bid:
            raise NotionValidationError("missing_args", "Parent block ID required.")
        if blocks_json:
            children = parse_json_arg(blocks_json, "blocks_json")
        elif text:
            children = [make_paragraph(text)]
        else:
            raise NotionValidationError(
                "missing_args", "Provide blocks_json or text.")
        resp = None
        for i in range(0, len(children), 100):
            chunk = children[i:i + 100]
            resp = client.request(
                "PATCH", f"/blocks/{bid}/children", {"children": chunk})
        return resp

    elif action == "update":
        if not bid:
            raise NotionValidationError("missing_args", "Block ID required.")
        if not block_json:
            raise NotionValidationError("missing_args", "Provide block_json.")
        block_data = parse_json_arg(block_json, "block_json")
        return client.request("PATCH", f"/blocks/{bid}", block_data)

    elif action == "delete":
        if not bid:
            raise NotionValidationError("missing_args", "Block ID required.")
        return client.request("DELETE", f"/blocks/{bid}")

    else:
        raise NotionValidationError(
            "invalid_action",
            f"Unknown block action: {action}. "
            "Use: get, children, append, update, delete")
