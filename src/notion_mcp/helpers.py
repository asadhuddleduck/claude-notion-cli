"""Pure helper functions for Notion API operations.

Includes ID normalization, rich text builders, block builders,
and JSON argument parsing.
"""

from __future__ import annotations

import json
import re

from .exceptions import NotionValidationError


# ============================================================
# ID Normalization
# ============================================================


def format_uuid(hex32: str) -> str:
    """Format 32 hex chars as standard UUID with dashes."""
    return (f"{hex32[:8]}-{hex32[8:12]}-{hex32[12:16]}-"
            f"{hex32[16:20]}-{hex32[20:]}")


def normalize_id(id_or_url: str | None) -> str | None:
    """Extract a UUID from a Notion URL or raw ID string."""
    if not id_or_url:
        return id_or_url

    # Handle Notion URLs
    if "notion.so" in id_or_url or "notion.site" in id_or_url:
        clean = id_or_url.split("?")[0].split("#")[0]
        match = re.search(
            r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})',
            clean)
        if match:
            return match.group(1)
        match = re.search(r'([a-f0-9]{32})(?:\?|#|$)', id_or_url)
        if match:
            return format_uuid(match.group(1))
        parts = clean.rstrip("/").split("/")
        last = parts[-1] if parts else ""
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


def make_rich_text(text: str, bold: bool = False, italic: bool = False,
                   code: bool = False, strikethrough: bool = False,
                   underline: bool = False, color: str = "default",
                   link: str | None = None) -> dict:
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


def simple_rich_text(text: str) -> list[dict]:
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


def extract_plain_text(rich_text_array: list | None) -> str:
    """Extract plain text from a rich text array."""
    if not rich_text_array:
        return ""
    return "".join(
        rt.get("plain_text", rt.get("text", {}).get("content", ""))
        for rt in rich_text_array
    )


# ============================================================
# Block Helpers
# ============================================================


def make_paragraph(text: str) -> dict:
    """Create a paragraph block from plain text."""
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": simple_rich_text(text)},
    }


def make_heading(text: str, level: int = 1) -> dict:
    """Create a heading block (level 1-3)."""
    key = f"heading_{min(max(level, 1), 3)}"
    return {
        "object": "block",
        "type": key,
        key: {"rich_text": simple_rich_text(text)},
    }


def make_todo(text: str, checked: bool = False) -> dict:
    """Create a to-do block."""
    return {
        "object": "block",
        "type": "to_do",
        "to_do": {
            "rich_text": simple_rich_text(text),
            "checked": checked,
        },
    }


def make_bullet(text: str) -> dict:
    """Create a bulleted list item block."""
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": simple_rich_text(text)},
    }


def make_numbered(text: str) -> dict:
    """Create a numbered list item block."""
    return {
        "object": "block",
        "type": "numbered_list_item",
        "numbered_list_item": {"rich_text": simple_rich_text(text)},
    }


# ============================================================
# JSON Argument Parsing
# ============================================================


def parse_json_arg(value: str, flag_name: str) -> dict | list:
    """Parse a JSON string argument, raising on error."""
    try:
        return json.loads(value)
    except json.JSONDecodeError as e:
        raise NotionValidationError(
            "invalid_json",
            f"Invalid JSON for {flag_name}: {e}"
        )
