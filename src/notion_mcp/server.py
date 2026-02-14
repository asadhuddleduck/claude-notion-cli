#!/usr/bin/env python3
"""Notion MCP Server — exposes Notion API as MCP tools for Claude.

Run with:
    NOTION_API_TOKEN=ntn_... notion-mcp
    NOTION_API_TOKEN=ntn_... python -m notion_mcp.server
"""

from __future__ import annotations

import json
import logging
import os
import sys

from mcp.server.fastmcp import FastMCP

from .client import NotionClient
from . import operations

# Logging to stderr only (stdout is reserved for JSON-RPC)
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger("notion-mcp")

# Create the MCP server
mcp = FastMCP("notion")

# Singleton client (shared rate limiter across concurrent tool calls)
_client: NotionClient | None = None


def get_client() -> NotionClient:
    """Get or create a NotionClient using NOTION_API_TOKEN env var."""
    global _client
    if _client is None:
        token = os.environ.get("NOTION_API_TOKEN")
        if not token:
            raise RuntimeError(
                "NOTION_API_TOKEN environment variable is required. "
                "Set it in your MCP server configuration."
            )
        _client = NotionClient(token)
    return _client


def _json(data: dict) -> str:
    """Serialize a dict to formatted JSON string."""
    return json.dumps(data, indent=2, ensure_ascii=False)


# ============================================================
# Tool Definitions (16 tools)
# ============================================================


@mcp.tool()
def notion_setup() -> str:
    """Verify the Notion API token is valid by calling /users/me.

    Returns bot user info if the token is valid.
    """
    return _json(operations.setup(get_client()))


@mcp.tool()
def notion_fetch(
    id: str,
    type: str | None = None,
    include_children: bool = False,
) -> str:
    """Retrieve a Notion page, database, or block by ID or URL.

    Accepts Notion URLs or raw UUIDs. Auto-detects whether the ID
    refers to a page, database, or block (or specify type to force).
    Set include_children=True to recursively fetch all child blocks.

    Args:
        id: Page, database, or block ID or Notion URL.
        type: Force type: "page", "database", or "block". Auto-detects if omitted.
        include_children: If True, recursively fetch child blocks.
    """
    return _json(operations.fetch(
        get_client(), id, type=type, include_children=include_children))


@mcp.tool()
def notion_search(
    query: str,
    filter: str | None = None,
    sort: str | None = None,
    max_results: int | None = None,
) -> str:
    """Search the Notion workspace for pages and databases.

    Args:
        query: Search query string.
        filter: Filter by object type: "page" or "database".
        sort: Sort by last_edited_time: "asc" or "desc".
        max_results: Maximum number of results to return.
    """
    return _json(operations.search(
        get_client(), query, filter=filter, sort=sort,
        max_results=max_results))


@mcp.tool()
def notion_create_page(
    parent_id: str,
    title: str | None = None,
    parent_type: str = "page_id",
    title_property: str = "Name",
    properties_json: str | None = None,
    content_json: str | None = None,
    content_text: str | None = None,
    icon_emoji: str | None = None,
    cover_url: str | None = None,
) -> str:
    """Create a new Notion page under a parent page or database.

    For database parents, use parent_type="database_id" and set
    title_property to the database's title property name.

    Args:
        parent_id: Parent page or database ID/URL.
        title: Page title text.
        parent_type: "page_id" or "database_id".
        title_property: Title property name for database parents (default "Name").
        properties_json: Full Notion properties object as JSON string.
        content_json: Content blocks as JSON array string.
        content_text: Simple paragraph text content.
        icon_emoji: Page icon as an emoji character.
        cover_url: Cover image URL.
    """
    return _json(operations.create_page(
        get_client(), parent_id, title=title, parent_type=parent_type,
        title_property=title_property, properties_json=properties_json,
        content_json=content_json, content_text=content_text,
        icon_emoji=icon_emoji, cover_url=cover_url))


@mcp.tool()
def notion_update_page(
    page_id: str,
    properties_json: str | None = None,
    title: str | None = None,
    archive: bool = False,
    unarchive: bool = False,
    icon_emoji: str | None = None,
    cover_url: str | None = None,
    append_blocks_json: str | None = None,
    append_text: str | None = None,
) -> str:
    """Update a Notion page's properties, metadata, or append content.

    Args:
        page_id: Page ID or URL.
        properties_json: Properties to update as JSON string.
        title: New page title (auto-finds the title property).
        archive: Set True to archive the page.
        unarchive: Set True to unarchive the page.
        icon_emoji: New icon emoji.
        cover_url: New cover image URL.
        append_blocks_json: Blocks to append as JSON array string.
        append_text: Simple paragraph text to append.
    """
    return _json(operations.update_page(
        get_client(), page_id, properties_json=properties_json,
        title=title, archive=archive, unarchive=unarchive,
        icon_emoji=icon_emoji, cover_url=cover_url,
        append_blocks_json=append_blocks_json, append_text=append_text))


@mcp.tool()
def notion_create_database(
    parent_id: str,
    title: str,
    properties_json: str,
    description: str | None = None,
    inline: bool = False,
    icon_emoji: str | None = None,
) -> str:
    """Create a new Notion database under a parent page.

    A "Name" title property is added automatically if none specified.

    Args:
        parent_id: Parent page ID/URL.
        title: Database title.
        properties_json: Property schema as JSON string.
        description: Database description text.
        inline: If True, create as inline database.
        icon_emoji: Database icon emoji.
    """
    return _json(operations.create_database(
        get_client(), parent_id, title, properties_json,
        description=description, inline=inline, icon_emoji=icon_emoji))


@mcp.tool()
def notion_update_database(
    database_id: str,
    title: str | None = None,
    description: str | None = None,
    properties_json: str | None = None,
    remove_properties: str | None = None,
    archive: bool = False,
) -> str:
    """Update a Notion database's schema, title, or description.

    Args:
        database_id: Database ID or URL.
        title: New database title.
        description: New database description.
        properties_json: Properties to add or update as JSON string.
        remove_properties: Comma-separated property names to remove.
        archive: Set True to archive the database.
    """
    return _json(operations.update_database(
        get_client(), database_id, title=title, description=description,
        properties_json=properties_json, remove_properties=remove_properties,
        archive=archive))


@mcp.tool()
def notion_query_database(
    database_id: str,
    filter_json: str | None = None,
    sorts_json: str | None = None,
    max_results: int | None = None,
    page_size: int | None = None,
    cursor: str | None = None,
    no_auto_paginate: bool = False,
) -> str:
    """Query a Notion database with optional filters and sorting.

    Auto-paginates by default. Use no_auto_paginate=True with cursor
    for manual pagination of large datasets.

    Args:
        database_id: Database ID or URL.
        filter_json: Notion filter object as JSON string.
        sorts_json: Notion sorts array as JSON string.
        max_results: Maximum total results.
        page_size: Results per page (max 100, for manual pagination).
        cursor: Start cursor for manual pagination.
        no_auto_paginate: If True, return single page with cursor.
    """
    return _json(operations.query_database(
        get_client(), database_id, filter_json=filter_json,
        sorts_json=sorts_json, max_results=max_results,
        page_size=page_size, cursor=cursor,
        no_auto_paginate=no_auto_paginate))


@mcp.tool()
def notion_query_meeting_notes(
    title_contains: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    date_relative: str | None = None,
    attendee_id: str | None = None,
    max_results: int = 50,
) -> str:
    """Search for meeting notes with optional date and title filters.

    Args:
        title_contains: Filter by title keyword.
        date_from: Start date filter (YYYY-MM-DD).
        date_to: End date filter (YYYY-MM-DD).
        date_relative: Relative date: "past_week", "past_month", "this_week".
        attendee_id: Filter by attendee user ID.
        max_results: Maximum results (default 50).
    """
    return _json(operations.query_meeting_notes(
        get_client(), title_contains=title_contains, date_from=date_from,
        date_to=date_to, date_relative=date_relative,
        attendee_id=attendee_id, max_results=max_results))


@mcp.tool()
def notion_create_comment(
    parent_id: str | None = None,
    discussion_id: str | None = None,
    text: str | None = None,
    rich_text_json: str | None = None,
) -> str:
    """Add a comment to a Notion page or reply to a discussion.

    Provide parent_id for a new comment, or discussion_id for a reply.
    Provide text (plain string) or rich_text_json (Notion rich text array).

    Args:
        parent_id: Page ID to comment on.
        discussion_id: Discussion thread ID to reply to.
        text: Plain text comment.
        rich_text_json: Rich text array as JSON string.
    """
    return _json(operations.create_comment(
        get_client(), parent_id=parent_id, discussion_id=discussion_id,
        text=text, rich_text_json=rich_text_json))


@mcp.tool()
def notion_get_comments(
    page_id: str,
    max_results: int | None = None,
) -> str:
    """Get all comments on a Notion page or block.

    Args:
        page_id: Page or block ID/URL.
        max_results: Maximum comments to return.
    """
    return _json(operations.get_comments(
        get_client(), page_id, max_results=max_results))


@mcp.tool()
def notion_get_users(
    query: str | None = None,
    user_id: str | None = None,
    max_results: int | None = None,
) -> str:
    """List workspace users or fetch a specific user.

    Args:
        query: Filter users by name or email (client-side).
        user_id: Specific user ID, or "me" for the bot user.
        max_results: Maximum users to return.
    """
    return _json(operations.get_users(
        get_client(), query=query, user_id=user_id,
        max_results=max_results))


@mcp.tool()
def notion_get_teams(
    query: str | None = None,
) -> str:
    """List teamspaces (limited — Notion public API has no teams endpoint).

    Returns workspace users as a proxy for team information.

    Args:
        query: Filter by name.
    """
    return _json(operations.get_teams(get_client(), query=query))


@mcp.tool()
def notion_move_page(
    page_ids: str,
    new_parent_id: str,
    new_parent_type: str = "page_id",
) -> str:
    """Move one or more pages to a new parent.

    Args:
        page_ids: Comma-separated page IDs or URLs.
        new_parent_id: New parent page or database ID/URL.
        new_parent_type: "page_id" or "database_id".
    """
    return _json(operations.move_page(
        get_client(), page_ids, new_parent_id,
        new_parent_type=new_parent_type))


@mcp.tool()
def notion_duplicate_page(
    page_id: str,
    new_title: str | None = None,
    new_parent_id: str | None = None,
) -> str:
    """Duplicate a Notion page with all its content.

    Copies properties, content blocks, icon, and cover.

    Args:
        page_id: Source page ID or URL.
        new_title: Title for the duplicated page.
        new_parent_id: Parent for the copy (defaults to same parent).
    """
    return _json(operations.duplicate_page(
        get_client(), page_id, new_title=new_title,
        new_parent_id=new_parent_id))


@mcp.tool()
def notion_blocks(
    action: str,
    block_id: str | None = None,
    blocks_json: str | None = None,
    block_json: str | None = None,
    text: str | None = None,
    max_results: int | None = None,
) -> str:
    """Block-level operations: get, children, append, update, delete.

    Args:
        action: One of: "get", "children", "append", "update", "delete".
        block_id: Block ID or URL (required for all actions).
        blocks_json: JSON array of blocks (for "append").
        block_json: JSON object for block update (for "update").
        text: Simple paragraph text (for "append").
        max_results: Max children to return (for "children").
    """
    return _json(operations.blocks(
        get_client(), action, block_id=block_id,
        blocks_json=blocks_json, block_json=block_json,
        text=text, max_results=max_results))


# ============================================================
# Entry Point
# ============================================================


def main():
    """Run the MCP server with stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
