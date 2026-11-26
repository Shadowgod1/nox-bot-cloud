"""LLM tool `handover_to_operator` (spec 5.3): status needs_operator, pause, admin notification."""

from iler.engine.pii import one_line
from iler.settings_models import AllSettings
from iler.tools import registry
from iler.tools.base import ToolContext, ToolResult

REASON_MAX = 200


class HandoverToOperator:
    name = "handover_to_operator"
    description = (
        "Передаёт диалог живому администратору: клиент просит человека, недоволен, или вопрос вне твоей "
        "компетенции. После вызова коротко скажи клиенту, что администратор скоро ответит."
    )
    parameters = {
        "type": "object",
        "properties": {"reason": {"type": "string", "description": "Кратко: почему нужен оператор"}},
        "required": ["reason"],
        "additionalProperties": False,
    }

    def enabled(self, settings: AllSettings) -> bool:
        return True

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        reason = one_line(args.get("reason"), REASON_MAX) or "без причины"
        result = {"status": "handed_over", "note": ("Вопрос передан администратору, он подключится. Скажи клиенту, что передала вопрос "
                                                       "администратору; не пиши клиенту, что бот перестаёт отвечать.")}
        if ctx.dry_run or ctx.conversation_id is None:
            return ToolResult(result)
        from iler.engine import sender, store

        before = await store.get_conversation(ctx.conversation_id)
        already = before is not None and before["status"] == "needs_operator"
        conv = await store.set_status(ctx.conversation_id, "needs_operator",
                                      pause_minutes=ctx.settings.handover_pause_minutes)
        if conv is not None and not already:  # admins were told already: no repeated notification
            name = one_line(conv["name"], 60)
            who = " ".join(x for x in (f"«{name}»" if name else "", f"+{conv['phone']}" if conv["phone"] else "")
                           if x)
            from iler.notify import conversation_link

            link = await conversation_link(ctx.conversation_id)
            await sender.notify_admins(
                f"Нужен оператор: {who or 'клиент'}. Причина: «{reason}». Ответьте клиенту из админки или с телефона."
                + (f"\nДиалог: {link}" if link else ""),
                key=f"handover:{ctx.conversation_id}:{ctx.extra.get('turn_id', '')}",
            )
        return ToolResult(result)


TOOL = registry.register(HandoverToOperator())
