"""Elly REST API: a thin HTTP layer over `elly_server.domain.*`.

For the future PWA frontend. Mirrors the MCP tool surface as closely as
makes sense for REST -- same domain functions, same behavior, just a
different calling convention. Never add business logic here; if
something needs real logic, it belongs in `elly_server.domain.*` so both
this API and the MCP server can use it.
"""
