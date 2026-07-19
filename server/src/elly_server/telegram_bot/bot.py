"""Elly Telegram bot: remote access to the same chat/LLM tool-calling
loop as the in-app chat panel, with offline message queueing.

Run with `uv run elly-telegram` -- a separate process from
elly-api/elly-mcp, sharing the same domain layer (see PLAN.md section
0.1's "MCP server + API server, same domain layer, separate processes"
pattern). Requires ELLY_TELEGRAM_BOT_TOKEN (get one from @BotFather on
Telegram); the Telegram channel is entirely optional -- everything
else in Elly works fine without it.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from elly_server.config import get_telegram_bot_token
from elly_server.db.base import get_session, init_db
from elly_server.domain import chat, telegram as telegram_domain
from elly_server.telegram_bot.rate_limiter import SlidingWindowRateLimiter
from elly_server.timeutil import now

logger = logging.getLogger("elly_server.telegram_bot")

# Same threshold/purpose as the REST chat endpoints (api/routers/chat.py) --
# not the same Limiter instance (that's FastAPI-specific middleware this
# standalone process doesn't go through), but the same protection.
_rate_limiter = SlidingWindowRateLimiter(max_calls=20, window_seconds=60)

# Module-level, reset each process run (intentionally -- we want the
# "catching up" notice once per reconnect, not once ever).
_bot_start_time = None
_catchup_notice_sent = False


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    chat_id = update.message.chat_id
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "This bot needs a pairing code from your Elly dashboard's Settings tab. "
            "Generate one there, then send: /start <code>"
        )
        return

    code = args[0]
    with get_session() as session:
        ok = telegram_domain.verify_and_pair(session, code, chat_id)

    if ok:
        await update.message.reply_text(
            "Paired! You can chat with me here just like the in-app chat panel -- "
            "log a habit, brain-dump some tasks, ask what's on today."
        )
    else:
        await update.message.reply_text(
            "That code is invalid or expired. Generate a new one in Elly's Settings tab."
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _catchup_notice_sent
    message = update.message
    if message is None or not message.text:
        return
    chat_id = message.chat_id

    with get_session() as session:
        if not telegram_domain.is_authorized_chat(session, chat_id):
            # Deliberately generic -- never confirms/denies pairing state
            # or what this bot even is to an unrecognized sender.
            await context.bot.send_message(chat_id, "This bot isn't set up for this chat.")
            return

    # Telegram's message timestamps are UTC-aware; convert to naive
    # local time for an apples-to-apples comparison against
    # elly_server.timeutil's naive-local convention used everywhere else.
    message_local_time = message.date.astimezone().replace(tzinfo=None)
    is_backlog = _bot_start_time is not None and message_local_time < _bot_start_time

    if is_backlog and not _catchup_notice_sent:
        _catchup_notice_sent = True
        await context.bot.send_message(
            chat_id,
            "Catching up on messages from while I was offline -- if it's been more "
            "than a day or two, Telegram may not have kept everything.",
        )

    with get_session() as session:
        inbound = telegram_domain.record_inbound_message(
            session, chat_id, update.update_id, message.text
        )
        conv_id = telegram_domain.get_conversation_id(session)

    if inbound["status"] == "processed":
        # Telegram redelivered an update we already fully handled (e.g.
        # the process was killed after replying but before its internal
        # offset advanced) -- record_inbound_message is idempotent on
        # telegram_update_id, so skip re-processing/re-replying entirely.
        return

    if conv_id is None:
        # Shouldn't happen -- pairing always creates a conversation --
        # but fail safely rather than crash the handler.
        await context.bot.send_message(chat_id, "Something isn't set up right -- try re-pairing.")
        return

    if not _rate_limiter.allow():
        await context.bot.send_message(
            chat_id,
            "You're sending messages a bit fast -- give me a moment and try again shortly.",
        )
        return

    try:
        with get_session() as session:
            result = chat.send_message(session, conv_id, message.text)
            telegram_domain.mark_processed(session, inbound["id"])
        reply = result.get("content") or "..."
    except Exception as e:
        logger.exception("Failed to process Telegram message %s", inbound["id"])
        with get_session() as session:
            telegram_domain.mark_error(session, inbound["id"], str(e))
        reply = "Sorry, something went wrong processing that."

    await context.bot.send_message(chat_id, reply)


def _drain_startup_backlog() -> None:
    """Resume any message left mid-processing from a previous crash.

    This is separate from (and much rarer than) Telegram's own offline
    message queueing, which `run_polling()` handles automatically by
    delivering everything since the last acknowledged update. This only
    matters if the process died between recording a message and
    finishing its reply -- runs once, synchronously, before polling
    starts.
    """
    with get_session() as session:
        pending = telegram_domain.get_unprocessed_messages(session)
        conv_id = telegram_domain.get_conversation_id(session)

    if not pending or conv_id is None:
        return

    logger.info("Resuming %d message(s) left unprocessed from a previous run", len(pending))
    for item in pending:
        try:
            with get_session() as session:
                chat.send_message(session, conv_id, item["text"])
                telegram_domain.mark_processed(session, item["id"])
        except Exception:
            logger.exception("Failed to resume message %s", item["id"])


def main() -> None:
    global _bot_start_time

    token = get_telegram_bot_token()
    if not token:
        print(
            "ELLY_TELEGRAM_BOT_TOKEN is not set -- get a token from @BotFather on "
            "Telegram and add it to server/.env. The Telegram channel is optional; "
            "the dashboard and MCP server work fine without it."
        )
        return

    init_db()
    _drain_startup_backlog()
    _bot_start_time = now()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Elly Telegram bot starting (long polling)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
