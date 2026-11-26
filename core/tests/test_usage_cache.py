"""Coins of requests with Anthropic prompt caching (usage.coins_for)."""

import pytest

from iler import usage


def test_no_cache_is_billed_as_before():
    assert usage.coins_for("claude-sonnet-5-5", 4200, 100) == pytest.approx((4200 * 2 + 100 * 10) / 1000)


def test_cache_read_and_write_rates():
    # Sonnet 5.5: write 1.25x of $2, read $0.10 (0.05x); 200 plain input tokens, 4000 read, 100 output
    coins = usage.coins_for("claude-sonnet-5-5", 4200, 100, cache_read=4000)
    assert coins == pytest.approx((200 * 2 + 4000 * 0.10 + 100 * 10) / 1000)
    coins = usage.coins_for("claude-sonnet-5-5", 4200, 100, cache_write=4000)
    assert coins == pytest.approx((200 * 2 + 4000 * 2.5 + 100 * 10) / 1000)
    # Haiku 5.5: read 0.1x of $0.10
    coins = usage.coins_for("claude-haiku-5-5", 4200, 100, cache_read=4000)
    assert coins == pytest.approx((200 * 0.10 + 4000 * 0.01 + 100 * 0.50) / 1000)


def test_cache_parts_are_clamped_to_the_input():
    assert usage.coins_for("claude-haiku-5-5", 100, 0, cache_read=500) == pytest.approx(100 * 0.01 / 1000)
