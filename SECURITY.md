# Security

Elly is self-hosted software: you run it on your own hardware, and you're
trusting it with genuinely sensitive content -- diary entries, mood/
energy check-ins, personal notes, and chat conversations with the LLM.
This document is an honest account of the current threat model: what's
protected today, what's explicitly not yet, and how to report a
problem. It will be updated as the threat model evolves.

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Use GitHub's private vulnerability reporting: go to this repository's
**Security** tab and click **"Report a vulnerability."** This opens a
private conversation visible only to you and the maintainer -- nothing
is public until (and unless) you and the maintainer agree it should be,
which is standard practice for coordinated disclosure.

Please include:
1. What you found and why it matters (impact)
2. Steps to reproduce
3. The version/commit you tested against

We'll treat all reports seriously regardless of format, but the above
helps us triage faster. Given this is a solo-maintained project, please
allow a reasonable amount of time for a response.

## Threat model

Elly is designed around one specific, deliberate setup: **a single user,
running the app on their own machine, with the dashboard reachable
only from that same machine** (`127.0.0.1` -- never a LAN or public
address). Remote interaction
happens through a separate, purpose-built channel (a Telegram bot,
landing in Sprint 2), not by exposing the dashboard itself to the
network. Everything below is designed against that specific model --
it does not (yet) cover multi-user, multi-device-on-a-LAN, or
public-internet deployments.

### What's protected today

- **Local API access token.** Every REST route except `/api/health`
  and `/api/setup/verify-token` requires a bearer token, generated
  once on first boot and printed to the server's own terminal/log
  output. The token is a 256-bit value stored in the OS keychain
  (macOS Keychain / Windows Credential Manager / Linux Secret Service)
  when one is available, falling back to a `0600`-permissioned file
  outside the repo when it isn't (the normal case inside a headless
  Docker container). This closes off the most realistic local attack:
  a malicious website open in another browser tab, or any other local
  process/script, silently reading or deleting your diary just by
  knowing the port number. The MCP server (stdio transport, spawned as
  a subprocess by OpenCode/Claude Desktop) doesn't need this --
  there's no network listener to protect there in the first place.
- **The dashboard binds to `127.0.0.1` by default**, and this default is
  what a native (non-Docker) install always uses. It's technically
  configurable via `ELLY_API_HOST` (necessary for Docker -- see the
  Docker section below for why), but the real, enforced exposure
  boundary for a Docker install is `docker-compose.yml`'s host-side
  port mapping, which is committed to the repo as
  `127.0.0.1:8765:8765` and would need a deliberate, visible edit to
  widen.
- **Rate limiting** on the chat endpoints (which trigger real LLM API
  calls and therefore real cost) and the token-verification endpoint,
  via `slowapi`.
- **Request validation.** REST request bodies are validated with
  Pydantic, including range/enum constraints (mood/energy 1-9, task
  priority, habit cadence, colour swatches, etc.) that mirror what the
  LLM's own tool-calling schema already enforced -- previously the
  REST layer was looser than the chat layer for the same fields.
- **Request body size limits** (2 MiB default, `ELLY_MAX_BODY_BYTES`) and
  basic security response headers (CSP, `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`) --
  defense-in-depth measures that matter even for a loopback-only
  service, in case of a future embedded webview or misconfiguration.
- **No cookies, ever.** Auth is a manually-attached `Authorization:
  Bearer` header, not a cookie, so CORS is configured with
  `allow_credentials=False` -- this closes off cookie-based CSRF as a
  category entirely rather than relying on the origin allow-list alone.
- **Secrets never committed.** `.env` (containing your LLM API key) is
  gitignored; `.env.example` documents every variable without real
  values. The access token lives in the OS keychain/a local file, never
  in the SQLite database or any config file that might get backed up
  or shared.
- **Field-level encryption at rest for the most sensitive content.**
  Diary/notebook entries, remembered facts/goals/preferences, chat
  conversation history, and habit-log reflection notes are encrypted
  before being written to disk -- see "Encryption at rest, explained in
  full" below for exactly what's covered, what isn't, and why this is
  field-level rather than whole-database encryption.

### What's explicitly not protected yet (tracked, not forgotten)

- **Event/Task titles and calendar metadata are not encrypted.** Only
  the free-text content fields listed above are -- see the encryption
  section below for the full list and the reasoning (mainly: these
  fields are searched via SQL pattern-matching today, which stops
  working against ciphertext, and they're lower-sensitivity than diary/
  chat content in the common case). If you put genuinely sensitive
  information in an event or task title, know that it's currently
  plaintext on disk.
- **The request-body-size middleware checks the client-reported
  `Content-Length` header, not actual bytes streamed.** A client that
  lies about its own Content-Length (or omits it and streams instead)
  isn't caught by this specific check. This is a defense-in-depth
  layer, not the primary defense -- the primary defense is that the app
  is loopback-only in the first place.
- **No account/password system, no multi-user support.** By design --
  a single access token is deliberately simpler than a full auth system,
  since there's only ever one legitimate user of a given instance.
- **The local access token is shared across all Elly instances on one
  OS user account**, since OS keychains key by service name, not by
  data directory. Running two separate Elly instances (e.g. a test
  instance and a production one) on the same machine under the same OS
  user will currently share one token. Not a concern for the intended
  single-instance-per-machine setup; worth knowing if you deliberately
  run more than one.
- **No LAN or public-internet exposure story yet.** If you want to view
  your dashboard from another device on your home network, this isn't
  supported today (Tailscale
  or a Caddy reverse-proxy with real TLS would be the recommended path
  once it's built and documented, not just opening the port).
- **Native signed installers, code-signing, and notarization** aren't
  built yet -- installation today is via `uv run elly-api`/Docker, not a
  signed `.dmg`/`.exe`.

### Encryption at rest, explained in full (Sprint 4, done)

**What's encrypted:** `Note.body`/`title` (diary/notebook content --
the single most sensitive thing Elly stores), `Memory.content`
(remembered facts/goals/preferences), `ChatMessage.content` and
`ChatMessage.tool_arguments` (conversation history -- `tool_arguments`
matters too, since a diary entry created via chat flows through as a
tool call argument and would otherwise sit in cleartext there even
with `content` itself encrypted), `HabitLog.note` (an optional
reflection attached to a habit completion), and
`InboundTelegramMessage.text` (the raw message a paired Telegram chat
sent, before any processing).

**What's not encrypted:** Event/Task titles, calendar/task metadata
(timestamps, IDs, priority, status), habit names, mood/energy numbers,
and app settings. These fields are either searched via SQL
pattern-matching today (encrypting them would break that; see below)
or are lower-sensitivity structural data (an integer mood rating on its
own reveals much less than the diary text explaining *why*).

**Why field-level, not whole-database (SQLCipher):** whole-database
encryption was seriously considered first. The standard Python package
for it, `sqlcipher3-binary`, only ships prebuilt wheels for **x86_64
Linux** -- no macOS wheels, no ARM64 Linux wheels. Using it would mean
native compilation (the SQLCipher C library + OpenSSL + a compiler
toolchain) on every Mac dev machine *and* on every Raspberry Pi Docker
build, breaking the "just `uv sync`" / "just `docker compose up`"
simplicity this project is built around -- exactly the kind of
fragility this project has otherwise been careful to avoid. Field-level
encryption via `cryptography`'s Fernet (authenticated AES-128-CBC +
HMAC) has genuinely universal wheel support and added zero new build
complexity.

**The real functional trade-off:** `search_notes()` and `recall()`
(memory search) used to run a SQL `ilike` pattern match directly
against `Note.body`/`title`/`Memory.content`. That's no longer possible
against ciphertext, so both now fetch candidate rows (using whatever
*unencrypted* filters still apply -- type, tags, date range) and then
filter by the already-decrypted content in Python. This is completely
correct and has no accuracy trade-off, just a performance one: it scans
rather than using a SQL index for the text-match step. At personal-use
data volumes (hundreds to low thousands of notes/memories) this is
imperceptible; it would not scale to a large multi-user corpus, which
isn't this app's use case.

**Key storage:** the encryption key lives in the same place as the API
access token (see above) -- OS keychain when available, a
`0600`-permissioned file (`.elly_dbkey`, separate file from the token's
`.elly_token`) otherwise. **If this key is lost, encrypted data is
permanently unrecoverable** -- there is no recovery mechanism, by
design (a recovery backdoor would itself be a vulnerability). Back up
your OS keychain / data directory accordingly.

**Migration:** existing plaintext data (from before this was added) is
encrypted in place by a one-time Alembic data migration
(`3e0cec013a14_encrypt_sensitive_fields.py`) that runs automatically on
next boot -- verified end-to-end against a scratch database seeded with
pre-migration plaintext rows, confirming both that the ciphertext on
disk never contains the original plaintext and that the ORM correctly
decrypts it back afterward.

### Docker networking, explained in full (Sprint 3, done)

This needed a real fix mid-development, so it's documented in detail
rather than glossed over: a process bound to `127.0.0.1` **inside** a
container is unreachable through Docker's port publishing no matter
what the host-side mapping says -- Docker's NAT delivers incoming
traffic to the container's own network interface, never its loopback.
The Docker image therefore sets `ELLY_API_HOST=0.0.0.0` so the app binds
to all interfaces *inside its own container's network namespace* --
this is not a security downgrade, because "all interfaces this
container has" is just the one internal interface connected to
Docker's bridge network, not your LAN. The actual, enforced boundary --
whether this is reachable from anywhere beyond the host machine -- is
entirely controlled by `docker-compose.yml`'s **host-side** port
mapping (`127.0.0.1:8765:8765`, never `0.0.0.0:8765:8765`). Native
(non-Docker) installs never set `ELLY_API_HOST` and keep the hardcoded-
safe `127.0.0.1` default throughout. See `config.py::get_api_host()`'s
docstring and the `Dockerfile`'s comments for the same explanation
in-line with the code.

### Telegram remote access (Sprint 2, done)

The Telegram bot (`elly-telegram`, a separate opt-in process -- the
dashboard/MCP server/REST API all work fine without it) is paired to
exactly one Telegram chat via an in-app, time-limited (10-minute)
6-digit pairing code -- never a hardcoded chat ID, and the code is
never accepted twice or after expiry. Any sender other than the paired
chat gets a generic, non-revealing reply ("This bot isn't set up for
this chat") that confirms nothing about what the bot is or whether
pairing exists at all.

Every incoming message is persisted to a local durability table
(`inbound_telegram_messages`, keyed uniquely on Telegram's own
`update_id`) the instant it's received, before being run through the
LLM tool-calling loop -- if the bot process crashes mid-reply, the
message is never silently lost, and a redelivered update is never
processed twice.

Messages sent while the machine is off queue on Telegram's own servers
and get processed in order once the bot reconnects; this is a
convenience for realistic day-to-day gaps (a lunch break, a commute, an
evening away from your desk), **not a guaranteed indefinite mailbox** --
Telegram's own server-side retention for undelivered updates is not
indefinite. The bot says so plainly in its first reply after
reconnecting (detected by comparing each message's own timestamp
against the bot's own start time) rather than implying otherwise.

The bot process runs its own lightweight in-memory rate limiter
(same threshold as the REST chat endpoints -- 20 messages/minute) since
it's a separate process that doesn't go through the FastAPI/slowapi
middleware at all.

### Telegram/Ollama setup from the Settings UI, and the self-restart endpoint

Two things worth being explicit about, since they're new surface area:

**The Telegram bot token can now be stored in the database** (an
`AppSettings.telegram_bot_token` column), set from the Settings tab
instead of only via `.env`. This is a deliberate, narrow exception to
`AppSettings` otherwise holding no secrets (see its docstring in
`db/models.py`) -- the column uses the same field-level encryption
(`EncryptedText`, Fernet) as `Note.body`/`Memory.content`/etc, so it's
never plaintext at rest, and `GET /api/settings` never includes it in
its response (`domain/settings.py::get_settings()` strips it
explicitly; use `/api/telegram/status`'s `bot_configured`/`bot_running`
booleans instead, which reveal nothing about the token's value). Same
"if the encryption key is lost, this is unrecoverable" caveat as every
other encrypted field.

**`elly-api` now spawns the Telegram bot as its own child process**
(`telegram_bot/process_manager.py`) whenever a token is configured,
instead of requiring you to run `uv run elly-telegram` yourself in a
second terminal. The child process only ever receives the token via
its own environment variable (`ELLY_TELEGRAM_BOT_TOKEN`, set by the
parent at spawn time) -- `bot.py` itself is unchanged and still has no
direct database access to secrets beyond what it's handed. The parent
terminates this child cleanly on shutdown and before every restart (see
below) specifically to avoid two bot processes answering the same chat
at once.

**`POST /api/system/restart`** (authenticated, same as every other
route) lets the Settings UI apply a new/changed/removed Telegram token
without you manually restarting anything. It works by having the
process replace its own image in place (`os.execv`) a fraction of a
second after responding -- same PID, fresh interpreter, re-reads every
env var and DB setting from scratch. This is deliberately a narrow,
single-purpose escape hatch (no arbitrary command execution, no
parameters, nothing configurable about *how* it restarts) rather than
a general-purpose "run a command" endpoint -- the blast radius of a
misused/compromised restart endpoint is "the app becomes briefly
unavailable then comes back," never code execution beyond exactly
re-running the same `elly-api` process. It requires the same bearer
token as every other authenticated route, so it's exactly as protected
as, say, deleting a note.

**Ollama's own REST API** (never a host-level installer) is used for
the "test connection" and "pull a model" features in Settings -- Elly
sends plain HTTP requests to whatever Ollama URL is configured (default
`http://localhost:11434`), the same one already used for chat. No new
network egress beyond what Ollama-as-LLM-provider already implied; see
`domain/ollama_admin.py`.
