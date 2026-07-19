# Elly (backend)

This repository is the **open-source backend** for Elly -- a self-hosted,
LLM-enhanced notebook + diary + calendar + habit companion, designed
around how ADHD brains actually work rather than generic
productivity-app assumptions.

**This repo contains only the domain logic, MCP server, and REST API --
there's no bundled web UI here.** It's fully usable on its own (via the
REST API or as an MCP server for OpenCode/Claude Desktop), and it's the
open-source foundation the full product (dashboard PWA + one-click
installers for macOS/Windows/Linux) is built on.

## Why this backend

- **Genuinely local-first.** SQLite on your own disk, no account, no
  cloud sync. The only network connections it ever makes are the LLM
  API call itself (optional -- point it at a fully local model via
  Ollama instead) and, if you enable it, Telegram's servers for remote
  access.
- **Security treated as a headline feature, not an afterthought.** A
  local access token gates every route, requests are rate-limited and
  validated, your diary/chat/memory content is encrypted at rest, and
  the full threat model is written down and kept honest in
  [`SECURITY.md`](./SECURITY.md) -- including what's *not* protected
  yet, not just what is.
- **An LLM that can actually act, not just chat.** Full tool-calling
  parity between conversational use and direct API/domain calls --
  create/reschedule events, break a vague task into tiny concrete
  steps, log a habit, remember a preference for later. Destructive
  actions (deleting a note, task, event, or habit) always require
  explicit confirmation.
- **Built around how ADHD actually works**, not generic productivity
  wisdom: forgiving streaks (a missed day never resets progress to
  zero), tiny-first-step task breakdowns, concrete externalized time
  instead of vague reminders, and zero shame-toned copy anywhere --
  see [`CONTRIBUTING.md`](./CONTRIBUTING.md)'s design principles.
- **Remote access via Telegram, not by exposing anything to a
  network.** Log a habit or brain-dump some tasks from your phone even
  while your machine is asleep -- messages queue and get processed the
  next time it's on.

**License:** [AGPL-3.0](./LICENSE) -- free to self-host, run, and
modify; if you run a modified version as a public network service, you
must offer users the modified source. See
[`CONTRIBUTING.md`](./CONTRIBUTING.md) if you'd like to help build it.

## How it's built

- **Data**: SQLite, stored at `~/Library/Application Support/Elly/elly.db`
  on macOS by default (configurable via `ELLY_DATA_DIR`) -- deliberately
  *not* inside a cloud-synced folder, since a live SQLite file being
  synced while open is a known corruption risk. Diary/notebook content,
  remembered facts, and chat history are encrypted at rest (field-level,
  via `cryptography`'s Fernet -- see `SECURITY.md` for exactly what's
  covered and why whole-database SQLCipher was deliberately not used).
- **Domain layer** (`server/src/elly_server/domain/`): the single source
  of truth for business logic (notes/diary, calendar, tasks, habits,
  insights, memory, budget). The MCP server, REST API, and Telegram bot
  all call into these same functions, so none of them can ever drift
  out of sync.
- **MCP server** (`server/src/elly_server/mcp_server/`): exposes the
  domain layer as MCP tools/resources/prompts, for OpenCode/Claude
  Desktop.
- **REST API** (`server/src/elly_server/api/`): a FastAPI layer over the
  same domain functions. Every route requires a local access token
  except the health check and one-time token verification -- see
  `SECURITY.md`.
- **Telegram bot** (`server/src/elly_server/telegram_bot/`): optional --
  configure a bot token via `ELLY_TELEGRAM_BOT_TOKEN` (or the REST API),
  spawned and managed automatically as `elly-api`'s own child process.
  Paired to exactly one chat via a one-time code -- never a hardcoded
  chat ID. Same domain layer, same chat/LLM tool-calling loop.
- **The only network connections are the LLM API call itself** (optional
  if you run a fully local model via Ollama) **and, only if you enable
  it, Telegram's servers** for the remote-access bot. Everything else
  runs on `127.0.0.1`.

## Running it

```sh
cd server
cp .env.example .env   # fill in OPENAI_API_KEY if you want cloud-based chat
uv sync
uv run elly-mcp    # stdio MCP server, for OpenCode/Claude Desktop
uv run elly-api    # REST API on http://127.0.0.1:8765
```

`elly-api` prints a local access token to the terminal the first time it
runs -- every route except the health check and that one-time token
check requires it (see `SECURITY.md` for why). From there you can pick
an LLM provider -- cloud (OpenAI, needs the API key above) or a fully
local model via [Ollama](https://ollama.com) (zero data leaves your
machine) -- via the REST API's settings endpoint.

`elly-mcp` works directly with OpenCode/Claude Desktop -- point your MCP
client config's `command`/`cwd` at this checkout and the `elly` tools
become available in conversation automatically.

Try prompts like:
- "log that I drank water" / "how's my water streak?"
- "log that I spent $12 on lunch" / "how much did I spend on groceries this month?"
- "start my morning planning" (uses the `morning_planning` MCP prompt)

See `server/README.md` for the full source layout and env var
overrides.

## Testing

```sh
cd server && uv run pytest
cd server && uv run ruff check src/
```

Runs in CI on every push (`.github/workflows/ci.yml`).

## Data model

`Note` (notebook + diary, unified) · `Event` (calendar) · `Task` (+ AI
breakdown into subtasks) · `Habit` / `HabitLog` (forgiving streaks -- a
missed day never resets progress to zero) · `BudgetEntry` (income/
expenses, one-off or recurring) · `Memory` (facts/goals/preferences
remembered across conversations) · `AppSettings` (LLM provider choice,
currency, encrypted Telegram bot token) · `TelegramLink` /
`InboundTelegramMessage` (pairing + offline message durability).

## Want the full app?

This repo is the backend only. The full product -- a dashboard PWA,
in-app chat, and one-click installers for macOS/Windows/Linux -- is
built on top of this same open-source foundation and distributed
separately.

## Contributing

Bug reports, feature ideas, and PRs are welcome -- see
[`CONTRIBUTING.md`](./CONTRIBUTING.md) for how the project is organized
and what to know before opening a PR. Please also read the
[`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md). Found a security issue?
See [`SECURITY.md`](./SECURITY.md) for how to report it privately.
