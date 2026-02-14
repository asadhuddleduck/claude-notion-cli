"""Custom exceptions for Notion API operations."""

from __future__ import annotations


class NotionError(Exception):
    """Base exception for Notion operations."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class NotionAPIError(NotionError):
    """Raised when the Notion API returns an error."""

    def __init__(self, code: str, message: str, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(code, message)


class NotionAuthError(NotionError):
    """Raised when authentication fails."""
    pass


class NotionValidationError(NotionError):
    """Raised when input validation fails."""
    pass
