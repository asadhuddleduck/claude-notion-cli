#!/usr/bin/env python3
"""notion-cli.py - Notion API CLI for Claude Code.

Standalone CLI entry point. Uses the shared notion_mcp package
for all business logic. Also supports running without the package
installed (falls back to inline imports for Keychain-only operations).

Usage:
    python3 notion-cli.py <subcommand> [flags]
    python3 notion-cli.py --help
"""

import argparse
import json
import os
import subprocess
import sys

# ============================================================
# Try to import from the package; fall back to standalone mode
# ============================================================

try:
    # Add src/ to path so we can import without installing
    _src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
    if _src not in sys.path:
        sys.path.insert(0, _src)

    from notion_mcp.client import NotionClient
    from notion_mcp.exceptions import NotionError
    from notion_mcp import operations

    PACKAGE_AVAILABLE = True
except ImportError:
    PACKAGE_AVAILABLE = False

# ============================================================
# Constants
# ============================================================

KEYCHAIN_SERVICE = "notion-cli"
KEYCHAIN_ACCOUNT = "notion-api"

# ============================================================
# Keychain Functions
# ============================================================


def get_api_token():
    """Retrieve Notion API token from macOS Keychain, then env var."""
    # Try Keychain first (macOS only)
    try:
        result = subprocess.run(
            ["security", "find-generic-password",
             "-a", KEYCHAIN_ACCOUNT,
             "-s", KEYCHAIN_SERVICE,
             "-w"],
            capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Fall back to environment variable
    token = os.environ.get("NOTION_API_TOKEN")
    if token:
        return token

    error_exit("auth_missing",
               "No Notion API token found. Either:\n"
               "  - Run: python3 notion-cli.py setup --token YOUR_TOKEN\n"
               "  - Set NOTION_API_TOKEN environment variable",
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
# CLI Command Handlers (thin wrappers around operations.*)
# ============================================================


def cmd_setup(client, args):
    if args.token:
        store_api_token(args.token)
        output({"success": True, "message": "Token stored in Keychain."})
        if not args.verify:
            return
    if args.verify:
        result = operations.setup(client)
        output(result)
        return
    if not args.token and not args.verify:
        error_exit("missing_args",
                   "Provide --token TOKEN to store, or --verify to test.")


def cmd_fetch(client, args):
    result = operations.fetch(
        client, args.id, type=args.type,
        include_children=args.include_children)
    output(result)


def cmd_search(client, args):
    result = operations.search(
        client, args.query, filter=args.filter,
        sort=args.sort, max_results=args.max_results)
    output(result)


def cmd_create_page(client, args):
    result = operations.create_page(
        client, args.parent_id, title=args.title,
        parent_type=args.parent_type or "page_id",
        title_property=args.title_property or "Name",
        properties_json=args.properties_json,
        content_json=args.content_json,
        content_text=args.content_text,
        icon_emoji=args.icon_emoji,
        cover_url=args.cover_url)
    output(result)


def cmd_update_page(client, args):
    result = operations.update_page(
        client, args.page_id,
        properties_json=args.properties_json,
        title=args.title, archive=args.archive,
        unarchive=args.unarchive,
        icon_emoji=args.icon_emoji,
        cover_url=args.cover_url,
        append_blocks_json=args.append_blocks_json,
        append_text=args.append_text)
    output(result)


def cmd_create_database(client, args):
    result = operations.create_database(
        client, args.parent_id, args.title,
        args.properties_json,
        description=args.description,
        inline=args.inline,
        icon_emoji=args.icon_emoji)
    output(result)


def cmd_update_database(client, args):
    result = operations.update_database(
        client, args.database_id,
        title=args.title, description=args.description,
        properties_json=args.properties_json,
        remove_properties=args.remove_properties,
        archive=args.archive)
    output(result)


def cmd_query_database(client, args):
    result = operations.query_database(
        client, args.database_id,
        filter_json=args.filter_json,
        sorts_json=args.sorts_json,
        max_results=args.max_results,
        page_size=args.page_size,
        cursor=args.cursor,
        no_auto_paginate=args.no_auto_paginate)
    output(result)


def cmd_query_meeting_notes(client, args):
    result = operations.query_meeting_notes(
        client, title_contains=args.title_contains,
        date_from=args.date_from, date_to=args.date_to,
        date_relative=args.date_relative,
        attendee_id=args.attendee_id,
        max_results=args.max_results)
    output(result)


def cmd_create_comment(client, args):
    result = operations.create_comment(
        client, parent_id=args.parent_id,
        discussion_id=args.discussion_id,
        text=args.text,
        rich_text_json=args.rich_text_json)
    output(result)


def cmd_get_comments(client, args):
    result = operations.get_comments(
        client, args.page_id,
        max_results=args.max_results)
    output(result)


def cmd_get_users(client, args):
    result = operations.get_users(
        client, query=args.query,
        user_id=args.user_id,
        max_results=args.max_results)
    output(result)


def cmd_get_teams(client, args):
    result = operations.get_teams(client, query=args.query)
    output(result)


def cmd_move_page(client, args):
    result = operations.move_page(
        client, args.page_ids, args.new_parent_id,
        new_parent_type=args.new_parent_type or "page_id")
    output(result)


def cmd_duplicate_page(client, args):
    result = operations.duplicate_page(
        client, args.page_id,
        new_title=args.new_title,
        new_parent_id=args.new_parent_id)
    output(result)


def cmd_blocks(client, args):
    result = operations.blocks(
        client, args.action, block_id=args.block_id,
        blocks_json=args.blocks_json,
        block_json=args.block_json,
        text=args.text,
        max_results=args.max_results)
    output(result)


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
    p.add_argument("--properties-json", help="Properties as JSON object")
    p.add_argument("--content-json", help="Content blocks as JSON array")
    p.add_argument("--content-text", help="Simple paragraph text")
    p.add_argument("--icon-emoji", help="Page icon emoji")
    p.add_argument("--cover-url", help="Cover image URL")

    # --- update-page ---
    p = sub.add_parser("update-page", help="Update a page")
    p.add_argument("page_id", help="Page ID or URL")
    p.add_argument("--properties-json", help="Properties to update as JSON")
    p.add_argument("--title", help="Update page title")
    p.add_argument("--archive", action="store_true", help="Archive the page")
    p.add_argument("--unarchive", action="store_true", help="Unarchive the page")
    p.add_argument("--icon-emoji", help="Update icon emoji")
    p.add_argument("--cover-url", help="Update cover URL")
    p.add_argument("--append-blocks-json", help="Blocks to append as JSON array")
    p.add_argument("--append-text", help="Simple paragraph text to append")

    # --- create-database ---
    p = sub.add_parser("create-database", help="Create a database")
    p.add_argument("--parent-id", required=True, help="Parent page ID")
    p.add_argument("--title", required=True, help="Database title")
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
    p.add_argument("--properties-json", help="Properties to add/update as JSON")
    p.add_argument("--remove-properties",
                   help="Comma-separated property names to remove")
    p.add_argument("--archive", action="store_true",
                   help="Archive the database")

    # --- query-database ---
    p = sub.add_parser("query-database", help="Query a database")
    p.add_argument("database_id", help="Database ID or URL")
    p.add_argument("--filter-json", help="Filter object as JSON")
    p.add_argument("--sorts-json", help="Sorts array as JSON")
    p.add_argument("--max-results", type=int, help="Maximum total results")
    p.add_argument("--page-size", type=int, help="Results per page (max 100)")
    p.add_argument("--cursor", help="Start cursor for manual pagination")
    p.add_argument("--no-auto-paginate", action="store_true",
                   help="Return single page with cursor")

    # --- query-meeting-notes ---
    p = sub.add_parser("query-meeting-notes", help="Query meeting notes")
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
    p.add_argument("--rich-text-json", help="Rich text array as JSON")

    # --- get-comments ---
    p = sub.add_parser("get-comments", help="Get comments on a page")
    p.add_argument("page_id", help="Page or block ID")
    p.add_argument("--max-results", type=int, help="Maximum comments to return")

    # --- get-users ---
    p = sub.add_parser("get-users", help="List workspace users")
    p.add_argument("--query", help="Filter by name or email")
    p.add_argument("--user-id", help="Specific user ID (or 'me')")
    p.add_argument("--max-results", type=int, help="Maximum users to return")

    # --- get-teams ---
    p = sub.add_parser("get-teams", help="List teamspaces (limited)")
    p.add_argument("--query", help="Filter by name")

    # --- move-page ---
    p = sub.add_parser("move-page", help="Move pages to new parent")
    p.add_argument("page_ids", help="Comma-separated page IDs")
    p.add_argument("--new-parent-id", required=True, help="New parent ID")
    p.add_argument("--new-parent-type",
                   choices=["page_id", "database_id"],
                   help="Parent type (default: page_id)")

    # --- duplicate-page ---
    p = sub.add_parser("duplicate-page", help="Duplicate a page")
    p.add_argument("page_id", help="Source page ID or URL")
    p.add_argument("--new-title", help="Title for the copy")
    p.add_argument("--new-parent-id", help="Parent for the copy (default: same)")

    # --- blocks ---
    p = sub.add_parser("blocks", help="Block-level operations")
    p.add_argument("action",
                   choices=["get", "children", "append", "update", "delete"],
                   help="Block action")
    p.add_argument("block_id", nargs="?", help="Block ID")
    p.add_argument("--blocks-json", help="Blocks to append as JSON array")
    p.add_argument("--block-json", help="Block data for update as JSON")
    p.add_argument("--text", help="Simple paragraph text (for append)")
    p.add_argument("--max-results", type=int, help="Max children to return")

    return parser


# ============================================================
# Main
# ============================================================


def main():
    if not PACKAGE_AVAILABLE:
        error_exit("import_error",
                   "notion_mcp package not found. Run from the project "
                   "directory or install with: pip install -e .")

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

    # Get token and create client
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
        try:
            handler(client, args)
        except NotionError as e:
            error_exit(e.code, e.message)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
