"""Quick end-to-end smoke test for the Elly MCP server.

Not a real test suite (no pytest yet) -- just exercises every tool
once against a throwaway SQLite DB so I can see real request/response
shapes and confirm the forgiving-streak logic behaves as designed.
Run with:
    ELLY_DATA_DIR=/tmp/elly-smoke uv run python scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from elly_server.mcp_server.server import init, mcp


def show(label: str, result) -> None:
    print(f"\n--- {label} ---")
    print(json.dumps(result, indent=2, default=str))


async def main() -> None:
    init()

    diary = await mcp.call_tool(
        "create_note",
        {"body": "Slept badly but got through the morning ok.", "type": "diary", "mood": 5, "energy": 4},
    )
    show("create_note (diary)", diary)

    note = await mcp.call_tool(
        "create_note", {"body": "Idea: weekly review could suggest one tiny experiment.", "type": "note"}
    )
    show("create_note (notebook)", note)

    event = await mcp.call_tool(
        "create_event",
        {
            "title": "Dentist",
            "start_at": datetime.now().replace(hour=15, minute=0, second=0, microsecond=0).isoformat(),
            "end_at": datetime.now().replace(hour=15, minute=45, second=0, microsecond=0).isoformat(),
        },
    )
    show("create_event", event)

    today = await mcp.call_tool("list_today", {})
    show("list_today", today)

    task = await mcp.call_tool("create_task", {"title": "Sort out car insurance renewal", "priority": "medium"})
    show("create_task", task)

    habit = await mcp.call_tool(
        "create_habit", {"name": "Drink water", "cadence": "daily", "tiny_version": "one sip"}
    )
    show("create_habit", habit)

    log1 = await mcp.call_tool("log_habit", {"name": "water"})
    show("log_habit (today)", log1)

    status = await mcp.call_tool("get_habit_status", {"name": "water"})
    show("get_habit_status", status)

    mem = await mcp.call_tool("remember", {"content": "Prefers mornings for deep work.", "type": "preference"})
    show("remember", mem)

    recalled = await mcp.call_tool("recall", {"query": "mornings"})
    show("recall", recalled)

    weekly = await mcp.call_tool("weekly_review", {})
    show("weekly_review", weekly)

    resource = await mcp.read_resource("elly://today")
    show("resource elly://today", [r.content for r in resource])


if __name__ == "__main__":
    asyncio.run(main())
