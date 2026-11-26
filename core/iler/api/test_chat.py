"""Test chat (spec section 6.7): talk to the bot without WhatsApp. Runs a dry-run turn on the supplied history;
nothing is written to conversations/messages and nothing is sent to WhatsApp.
"""

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from iler.auth.deps import User, csrf_protect, current_user
from iler.config import brand_company, brand_name, gateway_mode
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
    from iler.engine.prompt import BotNotConfigured
    from iler.llm.base import ChatMessage, LlmError, public_error, rejected_providers
    from iler.usage import AccessSuspended, CoinsExhausted

    history = [ChatMessage(role=m.role, content=m.content) for m in body.messages]
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
    return {"reply": "\n\n".join(outcome.reply_parts), "tool_calls": list(outcome.tool_calls)}
