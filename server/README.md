# elly-server

Python backend for Elly: the domain layer + the MCP server + the REST
API. See the repo-root `README.md` for the overall picture -- this repo
is backend-only (no bundled web UI).

## Dev setup

```sh
uv sync             # install deps into .venv
uv run elly-mcp     # run the MCP server on stdio (for OpenCode/Claude Desktop)
uv run elly-api     # run the REST API on http://127.0.0.1:8765
```

Both share the same SQLite database and the same domain logic -- run either
or both at once. With `elly-api` running, interactive docs are at
`http://127.0.0.1:8765/docs`.

The optional Telegram remote-access bot no longer needs a separate process/
terminal: `elly-api` spawns and manages it automatically (as a child process,
see `telegram_bot/process_manager.py`) whenever a bot token is configured --
either via the REST API's settings endpoint (paste a token from
@BotFather, then restart) or the `ELLY_TELEGRAM_BOT_TOKEN` env var.
`uv run elly-telegram` still works standalone too, for anyone who wants
to run it as its own separate process instead.

Override the data location for testing with `ELLY_DATA_DIR=/tmp/whatever`
(default is `~/Library/Application Support/Elly/`). Override the API port
with `ELLY_API_PORT` (default `8765`) and allowed dev-server CORS origins
with a comma-separated `ELLY_CORS_ORIGINS` (default covers Vite's usual
`localhost:5173`). See `.env.example` for the full list.

## Layout

```
src/elly_server/
├── config.py       # paths / env config
├── timeutil.py      # naive local-time parsing helpers
├── db/
│   ├── base.py       # engine/session, init_db()
│   ├── models.py      # SQLAlchemy models
│   ├── encrypted_types.py # field-level encryption for sensitive columns
│   └── serialize.py    # model -> dict for MCP/REST responses
├── domain/            # business logic (notes, calendar, tasks, habits,
│                       # insights, memory, dashboard, chat, telegram,
│                       # ollama_admin) -- shared by MCP + REST, the ONLY
│                       # place logic lives
├── mcp_server/
│   └── server.py        # FastMCP tools/resources/prompts (stdio transport)
├── telegram_bot/
│   ├── bot.py             # optional Telegram remote-access process
│   └── process_manager.py  # spawns/monitors bot.py as elly-api's child process
└── api/
    ├── app.py            # FastAPI app: CORS, error mapping, route mounting
    ├── deps.py            # DB session dependency
    ├── schemas.py          # Pydantic request models (responses are plain dicts)
    └── routers/             # one thin router per domain area (incl.
                              # system.py's self-restart endpoint)
```

## Tests

```sh
uv run pytest        # full suite
uv run ruff check .  # lint
```

Run `uv run pytest` after any change to the habit-streak logic in
`domain/habits.py` (covered by `tests/test_habits_streak.py`), and generally
before committing anything.

## Scripts

- `scripts/smoke_test.py` -- exercises every MCP tool once against a
  throwaway DB (`ELLY_DATA_DIR=/tmp/elly-smoke uv run python scripts/smoke_test.py`)
