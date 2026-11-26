"""Test chat (spec section 6.7): talk to the bot without WhatsApp. Runs a dry-run turn on the supplied history;
nothing is written to conversations/messages and nothing is sent to WhatsApp.

A client message may simulate a voice message or a photo (``media``): the bot then answers exactly as it would
in WhatsApp (engine.media), without calling the LLM. ``usage`` is the LLM spend of this reply in coins, from
the provider's token usage (the LLM gateway passes it through); it counts towards the install's spend.
"""

import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator, model_validator

from iler.auth.deps import User, csrf_protect, current_user
from iler.config import brand_company, brand_name, gateway_mode
from iler.errors import ApiError

log = logging.getLogger("iler.api.test_chat")

router = APIRouter(tags=["test-chat"])


class TestChatToolCall(BaseModel):
    """A tool call an earlier bot reply of the test dialogue made (echoed back by the admin panel), so the
    client card of the test dialogue knows about a booking or a fact from it."""

    name: str = Field(max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

    @field_validator("args")
    @classmethod
    def _small(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(v, ensure_ascii=False, default=str)) > 2000:
            raise ValueError("tool call args too large")
        return v


class TestChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field("", max_length=4000)
    media: Literal["audio", "image", "video"] | None = None  # simulated attachment of a client message
    tool_calls: list[TestChatToolCall] | None = Field(None, max_length=8)  # assistant messages only

    @model_validator(mode="after")
    def _not_empty(self) -> "TestChatMessage":
        if not self.content.strip() and self.media is None:
            raise ValueError("message must have text or media")
        if self.tool_calls and self.role != "assistant":
            raise ValueError("only assistant messages carry tool calls")
        return self


class TestChatRequest(BaseModel):
    messages: list[TestChatMessage] = Field(min_length=1, max_length=50)

    @field_validator("messages")
    @classmethod
    def _last_is_user(cls, v: list[TestChatMessage]) -> list[TestChatMessage]:
        if v and v[-1].role != "user":
            raise ValueError("last message must be from the user")
        return v


class TestChatUsage(BaseModel):
    coins: float
    input_tokens: int
    output_tokens: int


class TestChatResponse(BaseModel):
    reply: str
    tool_calls: list[dict[str, Any]]
    handed_over: bool = False
    usage: TestChatUsage | None = None  # None: no LLM call (the fixed voice / photo reply)


@router.post("/api/test-chat", response_model=TestChatResponse)
async def post_test_chat(body: TestChatRequest, _user: User = Depends(current_user), _csrf=Depends(csrf_protect)):
    from iler import settings_store
    from iler.engine import media, prompt
    from iler.engine.prompt import BotNotConfigured
    from iler.engine.turn import run_turn  # lazy: engine is optional at import time
    from iler.llm.base import ChatMessage, LlmError, ToolCall, public_error, rejected_providers
    from iler.settings_models import BotSettings
    from iler.usage import AccessSuspended, CoinsExhausted

    bot = await settings_store.get("bot", BotSettings)
    idx = len(body.messages)
    while idx > 0 and body.messages[idx - 1].role == "user":
        idx -= 1
    pending = [(m.content, m.media) for m in body.messages[idx:]]
    earlier = [m.content for m in body.messages[:idx] if m.role == "user" and m.content]
    decision = media.decide(pending, bot, earlier) if bot.prompt.strip() else None
    if decision is not None:
        return {"reply": decision.reply, "tool_calls": [], "handed_over": decision.handover, "usage": None}

    history = [
        ChatMessage(
            role=m.role, content=prompt.message_text(m.content, {"type": m.media} if m.media else None),
            # successful calls only: the card shows what the dialogue actually did (the prompt drops them)
            tool_calls=[ToolCall(id=f"t{i}-{j}", name=c.name, arguments=c.args)
                        for j, c in enumerate(m.tool_calls or []) if not c.error] or None,
        )
        for i, m in enumerate(body.messages)
    ]
    try:
        outcome = await run_turn(None, dry_run=True, history=history)
    except BotNotConfigured as e:
        raise ApiError(409, "bot_not_configured", "Промпт бота пуст: настройте бота по шаблону") from e
    except AccessSuspended as e:
        raise ApiError(409, "llm_access_suspended",
                       f"Доступ к моделям приостановлен — обратитесь в {brand_company()}") from e
    except CoinsExhausted as e:
        text = (f"Коины {brand_name()} закончились" if gateway_mode()
                else f"Лимит коинов {brand_name()} на этот месяц исчерпан")
        raise ApiError(409, "coins_exhausted", text) from e
    except LlmError as e:
        # Never 502/504: Cloudflare (cloud edition) replaces those bodies with its own page and the admin
        # loses the reason. 424 = the provider refused ILER's key, 503 = provider unreachable or failing.
        status = 424 if rejected_providers(str(e)) else 503
        raise ApiError(status, "llm_error", public_error(str(e))) from e
    return {
        "reply": "\n\n".join(outcome.reply_parts),
        "tool_calls": list(outcome.tool_calls),
        "handed_over": bool(getattr(outcome, "handed_over", False)),
        "usage": {"coins": getattr(outcome, "coins", 0.0), "input_tokens": getattr(outcome, "input_tokens", 0),
                  "output_tokens": getattr(outcome, "output_tokens", 0)},
    }
