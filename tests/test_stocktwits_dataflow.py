from __future__ import annotations

import json
from email.message import Message
from urllib.error import HTTPError

import pytest

from tradingagents.dataflows import stocktwits


@pytest.mark.unit
def test_stocktwits_symbol_candidates_normalize_crypto_yahoo_tickers():
    assert stocktwits.stocktwits_symbol_candidates("BTC-USD") == ["BTC.X", "BTC-USD", "BTC"]
    assert stocktwits.stocktwits_symbol_candidates(" eth-usd ") == ["ETH.X", "ETH-USD", "ETH"]
    assert stocktwits.stocktwits_symbol_candidates("AAPL") == ["AAPL"]


@pytest.mark.unit
def test_stocktwits_403_404_are_quiet_data_unavailable(monkeypatch, caplog):
    calls: list[str] = []

    def fake_urlopen(req, timeout=0):
        calls.append(req.full_url)
        raise HTTPError(req.full_url, 404, "Not Found", hdrs=Message(), fp=None)

    monkeypatch.setattr(stocktwits, "urlopen", fake_urlopen)
    caplog.set_level("WARNING")

    result = stocktwits.fetch_stocktwits_messages("BTC-USD", timeout=0.01)

    assert "stocktwits unavailable" in result
    assert "BTC.X" in result
    assert "BTC-USD" in result
    assert "BTC" in result
    assert len(calls) == 3
    assert "StockTwits fetch failed" not in caplog.text


@pytest.mark.unit
def test_stocktwits_uses_first_successful_candidate(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({
                "messages": [
                    {
                        "created_at": "2026-05-20T00:00:00Z",
                        "user": {"username": "tester"},
                        "entities": {"sentiment": {"basic": "Bullish"}},
                        "body": "BTC sentiment smoke",
                    }
                ]
            }).encode()

    def fake_urlopen(req, timeout=0):
        if "BTC.X" in req.full_url:
            return FakeResponse()
        raise AssertionError(f"unexpected fallback request: {req.full_url}")

    monkeypatch.setattr(stocktwits, "urlopen", fake_urlopen)

    result = stocktwits.fetch_stocktwits_messages("BTC-USD", timeout=0.01)

    assert "Bullish: 1" in result
    assert "@tester" in result
    assert "BTC sentiment smoke" in result


@pytest.mark.unit
def test_stocktwits_can_be_disabled(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_STOCKTWITS_ENABLED", "false")
    assert "disabled" in stocktwits.fetch_stocktwits_messages("BTC-USD")
