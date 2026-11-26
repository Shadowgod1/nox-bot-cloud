"""Usage parsing and the price table (no database)."""

import json
import logging
from decimal import Decimal

from nox_portal import prices
from nox_portal.config import get_settings
from nox_portal.mailer import Mail, _build
from nox_portal.metering import Meter, SseSplitter, is_usage_only_chunk
from nox_portal.prices import Tokens
from tests.gw import ANTHROPIC_MSG, ANTHROPIC_SSE, GEMINI_JSON, GEMINI_SSE, OPENAI_CHAT, OPENAI_RESPONSES_SSE, openai_chunks


def _sse(provider: str, raw: bytes, cut: int = 5) -> Meter:
    m, sp = Meter(provider), SseSplitter()
    out = b""
    for i in range(0, len(raw), cut):
        for ev in sp.push(raw[i:i + cut]):
            m.feed(ev.data)
            out += ev.raw
    for ev in sp.flush():
        m.feed(ev.data)
        out += ev.raw
    assert out == raw, "splitter must not change a byte"
    return m


def test_anthropic_json_with_cache():
    m = Meter("anthropic")
    m.feed_body(json.dumps(ANTHROPIC_MSG).encode())
    assert m.found and m.model == "claude-sonnet-5-5" and m.upstream_id == "msg_01"
    assert m.tokens == Tokens(input=1000, output=500, cache_write=2000, cache_read=4000)


def test_anthropic_stream_message_start_plus_delta():
    m = _sse("anthropic", ANTHROPIC_SSE)
    assert m.tokens == Tokens(input=300, output=200, cache_write=0, cache_read=100)
    assert m.model == "claude-haiku-5-5"


def test_anthropic_delta_with_cumulative_input_overrides():
    m = Meter("anthropic")
    m.feed({"type": "message_start", "message": {"usage": {"input_tokens": 10, "output_tokens": 1}}})
    m.feed({"type": "message_delta", "usage": {"output_tokens": 50, "input_tokens": 12, "cache_read_input_tokens": None}})
    assert m.tokens == Tokens(input=12, output=50)


def test_openai_chat_stream_and_responses():
    m = Meter("openai")
    m.feed_body(json.dumps(OPENAI_CHAT).encode())
    assert m.tokens == Tokens(input=1000, output=100) and m.model == "gpt-5.4-mini"
    m = _sse("openai", b"".join(openai_chunks(include_usage=True)), cut=17)
    assert m.tokens == Tokens(input=40, output=7)
    m = _sse("openai", OPENAI_RESPONSES_SSE)
    assert m.tokens == Tokens(input=120, output=30) and m.upstream_id == "resp_1"
    m = Meter("openai")
    m.feed({"object": "list", "model": "text-embedding-3-small", "usage": {"prompt_tokens": 8, "total_tokens": 8}})
    assert m.tokens == Tokens(input=8)


def test_openai_without_usage_is_not_found():
    m = _sse("openai", b"".join(openai_chunks(include_usage=False)))
    assert not m.found


def test_gemini_json_array_and_sse():
    m = Meter("gemini")
    m.feed_body(json.dumps(GEMINI_JSON).encode())
    assert m.tokens == Tokens(input=100, output=75) and m.upstream_id == "gem-1"
    m = Meter("gemini")  # streamGenerateContent without alt=sse: a JSON array, cumulative usage
    m.feed_body(json.dumps([GEMINI_JSON, {**GEMINI_JSON, "usageMetadata": {"promptTokenCount": 100,
                                                                           "candidatesTokenCount": 90}}]).encode())
    assert m.tokens == Tokens(input=100, output=90)
    m = _sse("gemini", GEMINI_SSE, cut=11)
    assert m.tokens == Tokens(input=10, output=25)


def test_usage_only_chunk():
    assert is_usage_only_chunk({"choices": [], "usage": {"prompt_tokens": 1}})
    assert not is_usage_only_chunk({"choices": [{}], "usage": None})


def test_prices_mirror_core_and_unknown_models(monkeypatch, caplog):
    monkeypatch.setattr(get_settings(), "model_prices", "")
    price, priced = prices.price_for("anthropic", "claude-haiku-5-5-20261001")
    assert priced and (price.input, price.output) == (0.10, 0.50)
    assert (price.cache_write, price.cache_read) == (0.125, 0.01)
    with caplog.at_level(logging.WARNING, logger="nox_portal.prices"):
        price, priced = prices.price_for("anthropic", "claude-opus-9")
    assert not priced and (price.input, price.output) == (2.0, 10.0)  # top Claude rate
    assert "claude-opus-9" in caplog.text
    for provider, model in (("openai", "gpt-5.4-mini"), ("gemini", "gemini-3.8-flash")):
        price, priced = prices.price_for(provider, model)
        assert not priced and (price.input, price.output) == prices.FALLBACK_PRICE
        assert prices.coins(price, Tokens(input=1, output=1)) > 0  # never 0


def test_price_override_same_format_as_core(monkeypatch):
    monkeypatch.setattr(get_settings(), "model_prices", json.dumps(
        {"gpt-5.4-mini": [0.25, 2.0], "gpt-5.4": [1.25, 10.0], "gemini-3.8-flash": [0.3, 2.5, 0.3, 0.03],
         "claude-sonnet-5-5": [3, 15]}))
    assert prices.price_for("openai", "gpt-5.4-mini-2026-03-01") == (prices.Price(0.25, 2.0, 0.3125, 0.025), True)
    assert prices.price_for("openai", "gpt-5.4")[0].output == 10.0
    # unknown OpenAI model: the most expensive OpenAI entry, not the global fallback
    assert prices.price_for("openai", "gpt-9-turbo") == (prices.Price(1.25, 10.0, 1.5625, 0.125), False)
    assert prices.price_for("gemini", "models/gemini-3.8-flash")[0] == prices.Price(0.3, 2.5, 0.3, 0.03)
    assert prices.price_for("anthropic", "claude-sonnet-5-5")[0].input == 3
    monkeypatch.setattr(get_settings(), "model_prices", "{not json")
    assert prices.price_for("anthropic", "claude-sonnet-5-5")[0].input == 2.0


def test_coins_round_up():
    price = prices.Price.of((2.0, 10.0))
    assert prices.coins(price, Tokens(input=1)) == Decimal("0.002")
    assert prices.coins(price, Tokens(input=1000, output=500)) == Decimal("7.000")


def test_reply_to_header(monkeypatch):
    monkeypatch.setattr(get_settings(), "smtp_from", "Nox service <bot@noxsw.kz>")
    msg = _build(Mail(to="client@example.kz", subject="s", text="t"))
    assert msg["Reply-To"] is None
    monkeypatch.setattr(get_settings(), "smtp_reply_to", "info@noxsw.kz")
    msg = _build(Mail(to="client@example.kz", subject="s", text="t"))
    assert msg["Reply-To"] == "info@noxsw.kz"
    assert msg["From"] == "Nox service <bot@noxsw.kz>"
