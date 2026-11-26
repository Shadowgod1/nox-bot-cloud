"""Client language guess for the bot's fixed replies (voice / photo): Kazakh or not.

The LLM replies in the client's language by itself (base prompt rule); this is only for the texts the bot
sends without the LLM. Kazakh is recognised by its own letters (ә ғ қ ң ө ұ ү һ і) or, for people who type
Kazakh with Russian letters, by a few very common words.
"""

import re
from typing import Literal

_KK_LETTERS = set("әғқңөұүһіӘҒҚҢӨҰҮҺІ")
_KK_WORDS = {
    "сәлем", "салем", "салеметсизбе", "сәлеметсіз", "рахмет", "рахмет!", "канша", "қанша", "турады", "тұрады",
    "бар", "ма", "ме", "жок", "жоқ", "иа", "иә", "кешке", "ертен", "ертең", "бугин", "бүгін", "жазылу",
    "жазылайын", "жазылуга", "кайда", "қайда", "уакыт", "уақыт", "керек", "болады", "бола", "ма?", "ме?",
}
_KK_STRONG = {"сәлем", "салем", "рахмет", "канша", "қанша", "жазылу", "жазылайын", "кайда", "қайда", "керек",
              "болады", "ертен", "ертең", "бугин", "бүгін", "уакыт", "уақыт", "тұрады", "турады"}
_WORD = re.compile(r"[\wәғқңөұүһі]+", re.IGNORECASE)

Lang = Literal["kk", "ru"]


def detect(text: str) -> Lang | None:
    """'kk' for Kazakh, 'ru' for other Cyrillic text, None when there is nothing to judge by."""
    text = text or ""
    if any(ch in _KK_LETTERS for ch in text):
        return "kk"
    words = [w.lower() for w in _WORD.findall(text)]
    if not words:
        return None
    if any(w in _KK_STRONG for w in words) or sum(w in _KK_WORDS for w in words) >= 2:
        return "kk"
    if any("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in text):
        return "ru"
    return None


def detect_recent(texts: list[str]) -> Lang | None:
    """Language of the latest client text that tells it (newest first wins)."""
    for t in reversed(texts):
        lang = detect(t)
        if lang:
            return lang
    return None
