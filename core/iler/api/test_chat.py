"""Test chat (spec section 6.7): talk to the bot without WhatsApp. Runs a dry-run turn on the supplied history;
nothing is written to conversations/messages and nothing is sent to WhatsApp.
"""

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from iler.auth.deps import User, csrf_protect, current_user
from iler.errors import ApiError

log = logging.getLogger("iler.api.test_chat")

router = APIRouter(tags=["test-chat"])


class TestChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class TestChatRequest(BaseModel):
    messages: list[TestChatMessage] = Field(min_length=1, max_length=50)

    @field_validator("messages")
    @classmethod
    def _last_is_user(cls, v: list[TestChatMessage]) -> list[TestChatMessage]:
        if v and v[-1].role != "user":
            raise ValueError("last message must be from the user")
        return v


class TestChatResponse(BaseModel):
    reply: str
    tool_calls: list[dict[str, Any]]


@router.post("/api/test-chat", response_model=TestChatResponse)
async def post_test_chat(body: TestChatRequest, _user: User = Depends(current_user), _csrf=Depends(csrf_protect)):
    from iler.engine.turn import run_turn  # lazy: engine is optional at import time
    from iler.llm.base import ChatMessage, LlmError

    history = [ChatMessage(role=m.role, content=m.content) for m in body.messages]
    try:
        outcome = await run_turn(None, dry_run=True, history=history)
    except LlmError as e:
        raise ApiError(502, "llm_error", str(e)) from e
    return {"reply": "\n\n".join(outcome.reply_parts), "tool_calls": list(outcome.tool_calls)}
