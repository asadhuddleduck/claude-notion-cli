# claude-notion-cli

Notion API CLI and MCP server for Claude Code, Claude Desktop, and Claude Cowork.

Zero-dependency Python CLI + MCP server replacing all 14 Notion MCP tools with direct API access.

## Quick Start

### CLI (Claude Code)

```bash
git clone https://github.com/asadhuddleduck/claude-notion-cli.git
cd claude-notion-cli
python3 notion-cli.py setup --token YOUR_NOTION_TOKEN
python3 notion-cli.py setup --verify
```

### MCP Server (Claude Cowork / Desktop)

Add to your Claude config:

```json
{
  "mcpServers": {
    "notion": {
      "command": "uv",
      "args": ["run", "--python", "3.12", "--directory", "/path/to/claude-notion-cli", "notion-mcp"],
      "env": {
        "NOTION_API_TOKEN": "ntn_YOUR_TOKEN_HERE"
      }
    }
  }
}
```

## 16 Tools

| Tool | Description |
|------|-------------|
| `setup` | Store/verify API token |
| `fetch` | Get page, database, or block by ID/URL |
| `search` | Search the workspace |
| `create-page` | Create pages with properties and content |
| `update-page` | Update properties, append content, archive |
| `create-database` | Create databases with schema |
| `update-database` | Update database schema |
| `query-database` | Query with filters and sorts |
| `query-meeting-notes` | Search meeting notes with date filters |
| `create-comment` | Add comments to pages |
| `get-comments` | List comments on a page |
| `get-users` | List/search workspace users |
| `get-teams` | List teamspaces |
| `move-page` | Move pages to new parent |
| `duplicate-page` | Copy a page with content |
| `blocks` | Get/append/update/delete blocks |

## Requirements

- Python 3.9+ for CLI
- Python 3.10+ for MCP server (handled automatically by `uv`)
- macOS Keychain for CLI token storage (or `NOTION_API_TOKEN` env var)
