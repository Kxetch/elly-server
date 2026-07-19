# Contributing to Elly

Thanks for considering contributing. Elly's backend is a solo-maintained
project built around a specific philosophy (see below) -- reading this
first will save you a round-trip on a PR that doesn't fit.

This repository contains **only the backend** (the domain logic + MCP
server + REST API) -- there's no bundled web UI here. It's the
open-source foundation the full product (including the dashboard PWA
and one-click installers) is built on.

## Before you start

- **For anything more than a small fix, open an issue first** to
  discuss the approach. This project has some deliberate, documented
  design constraints (single-user, no accounts, naive local time, no
  shame-toned copy anywhere) that aren't oversights -- see the "Design
  principles" section below before proposing a change that touches
  any of them.
- **Security issues:** do not open a public issue. See `SECURITY.md`
  for how to report privately.

## Design principles that shape every PR review

These apply to backend strings, tool docstrings, and any user-facing
copy equally:

- Never shame, guilt-trip, or nag. No stark "streak broken" states, no
  guilt-toned copy anywhere. A missed day is normal.
- Prefer tiny, concrete next steps over big plans (BJ Fogg Tiny
  Habits) -- if a feature encourages breaking work into smaller steps,
  make the first one absurdly easy to start.
- Support autonomy -- offer options, don't prescribe what the user
  "should" do.
- Externalize time -- concrete dates/times, not vague references.
- Insights are narrated by the LLM from raw structured data. Don't
  hard-code judgmental or performance-review-style copy in the backend.

A PR that's technically correct but violates one of these will get
asked to change before merge -- please raise questions about this in an
issue before investing a lot of time in an approach that conflicts
with it.

## Architecture rules (enforced, not just suggested)

- `src/elly_server/domain/*` is the **only** place business logic
  lives. The MCP server, REST API, and Telegram bot all call into these
  same functions -- never duplicate logic in any of those layers, and
  never let `domain/*` import from `mcp`, `fastapi`, or
  `python-telegram-bot`.
- All datetimes are naive local wall-clock time (`timeutil.py`) -- no
  UTC, no timezone awareness anywhere. This is a deliberate
  simplification for a single-user, single-machine app.
- Keep the MCP tool count lean -- prefer richer parameters on fewer
  tools over many narrow ones.
- New REST request fields need the same range/enum validation that
  exists in the chat tool-calling schema for the same field (see
  `api/schemas.py` and `domain/chat.py`'s `_build_tools()`) -- don't
  let the two calling conventions drift apart in strictness.

## Development setup

```sh
uv sync             # install deps into .venv
uv run elly-mcp     # stdio MCP server, for OpenCode/Claude Desktop
uv run elly-api     # REST API on http://127.0.0.1:8765
```

See `server/README.md` for the full layout and env var overrides.

## Testing (required before opening a PR)

```sh
uv run pytest        # full suite
uv run ruff check src/
```

Every new `domain/*` function needs a pytest test in `tests/`. This
runs in CI (`.github/workflows/ci.yml`) on every PR; it needs to be
green before merge.

## Commit messages / PR description

Write a clear, specific commit message describing *what* changed and
*why* -- not just "fix bug." If your change fixes something
non-obvious, explain the root cause briefly (future maintainers,
including the author months later, will thank you). Reference the
issue number if there is one.

## Code of Conduct

Participation in this project is governed by
[`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md).

## License

By contributing, you agree that your contributions will be licensed
under the project's [AGPL-3.0 license](./LICENSE).
