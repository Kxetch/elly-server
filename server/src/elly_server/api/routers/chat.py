"""In-app chat with the LLM (OpenAI)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from elly_server.api.deps import get_db
from elly_server.api.rate_limit import limiter
from elly_server.domain import chat

router = APIRouter(prefix="/chat", tags=["chat"])


class SendMessageRequest(BaseModel):
    conversation_id: str | None = None
    content: str


@router.post("/messages", status_code=201)
@limiter.limit("20/minute")
def send_message(
    request: Request,
    payload: SendMessageRequest,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    if payload.conversation_id is None:
        conv_id = chat.create_conversation(session)
    else:
        conv_id = payload.conversation_id
    return {
        "conversation_id": conv_id,
        **chat.send_message(session, conv_id, payload.content),
    }


@router.post("/messages/stream")
@limiter.limit("20/minute")
async def send_message_stream(
    request: Request,
    payload: SendMessageRequest,
    session: Session = Depends(get_db),
) -> StreamingResponse:
    if payload.conversation_id is None:
        conv_id = chat.create_conversation(session)
    else:
        conv_id = payload.conversation_id

    async def event_generator():
        async for event in chat.send_message_stream(session, conv_id, payload.content):
            yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class ResolveToolRequest(BaseModel):
    conversation_id: str
    call_id: str
    decision: str  # "confirm" | "decline"


@router.post("/messages/resolve-tool")
@limiter.limit("20/minute")
async def resolve_tool(
    request: Request,
    payload: ResolveToolRequest,
    session: Session = Depends(get_db),
) -> StreamingResponse:
    """Resume a conversation after the user confirms or declines a
    destructive tool call (delete_note/delete_event/delete_task/
    delete_habit) that paused mid-stream awaiting confirmation."""
    async def event_generator():
        async for event in chat.resolve_pending_tool(
            session, payload.conversation_id, payload.call_id, payload.decision
        ):
            yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/conversations")
def list_conversations(
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return chat.list_conversations(session)


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str,
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return chat.get_history(session, conversation_id)


@router.post("/conversations", status_code=201)
def create_conversation(
    session: Session = Depends(get_db),
) -> dict[str, str]:
    conv_id = chat.create_conversation(session)
    return {"id": conv_id}


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: str,
    session: Session = Depends(get_db),
) -> dict[str, bool]:
    return {"deleted": chat.delete_conversation(session, conversation_id)}
