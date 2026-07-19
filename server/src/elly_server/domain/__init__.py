"""Domain/service layer: the single source of truth for business logic.

Both the MCP server and (later) the REST API for the PWA call into
these same functions, so the LLM and the UI can never do different
things or drift out of sync.
"""
