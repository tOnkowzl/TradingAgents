"""StockTwits public symbol-stream fetcher.

StockTwits exposes a per-symbol message stream at
``api.stocktwits.com/api/2/streams/symbol/{ticker}.json`` that requires no
API key, no OAuth, and no registration. Each message includes a
user-labeled sentiment field (``Bullish``/``Bearish``/null), the message
body, timestamp, and posting user.

The function is deliberately self-contained: short timeout, graceful
degradation on any HTTP or parse failure, and a string return type so
the calling agent gets a uniform interface regardless of whether the
network call succeeded.
"""

from __future__ import annotations

import json
import logging
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_API = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
_UA = "tradingagents/0.2 (+https://github.com/TauricResearch/TradingAgents)"
_CRYPTO_USD_RE = re.compile(r"^([A-Z0-9]+)-USD$")


def stocktwits_symbol_candidates(ticker: str) -> list[str]:
    """Return StockTwits symbol candidates for a market ticker.

    Yahoo-style crypto tickers use ``BTC-USD``/``ETH-USD`` while
    StockTwits cashtags commonly use ``BTC.X``/``ETH.X``. Try the
    StockTwits crypto form first, then fall back to the original ticker so
    equities and exchange-suffixed symbols keep their exact identity.
    """
    raw = (ticker or "").strip().upper().lstrip("$")
    if not raw:
        return []
    candidates: list[str] = []
    match = _CRYPTO_USD_RE.match(raw)
    if match:
        base = match.group(1)
        candidates.extend([f"{base}.X", raw, base])
    else:
        candidates.append(raw)
    out: list[str] = []
    seen: set[str] = set()
    for sym in candidates:
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out


def _stocktwits_enabled() -> bool:
    return os.environ.get("TRADINGAGENTS_STOCKTWITS_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def fetch_stocktwits_messages(ticker: str, limit: int = 30, timeout: float = 6.0) -> str:
    """Fetch recent StockTwits messages for ``ticker`` and return them as a
    formatted plaintext block ready for prompt injection.

    Returns a placeholder string when the endpoint is unreachable, the
    symbol has no messages, or the response shape is unexpected — the
    caller never has to special-case None or exceptions. StockTwits often
    blocks server-side/API traffic with Cloudflare or returns 404 for
    Yahoo-style crypto tickers; those expected cases are treated as data
    unavailability, not noisy runtime warnings.
    """
    candidates = stocktwits_symbol_candidates(ticker)
    if not _stocktwits_enabled():
        return f"<stocktwits disabled by TRADINGAGENTS_STOCKTWITS_ENABLED=0 for ${ticker.upper()}>"
    if not candidates:
        return "<stocktwits unavailable: empty ticker>"

    failures: list[str] = []
    data: dict | None = None
    used_symbol = candidates[0]
    for symbol in candidates:
        used_symbol = symbol
        url = _API.format(ticker=symbol)
        req = Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
        try:
            with urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
            break
        except HTTPError as exc:
            failures.append(f"{symbol}: HTTP {exc.code}")
            # 403/404 are common for StockTwits' public endpoint in hosted or
            # containerized environments. Keep the LLM honest via a placeholder
            # but do not spam worker logs with warnings every TA cycle.
            if exc.code in {403, 404}:
                logger.debug("StockTwits unavailable for %s via %s: HTTP %s", ticker, symbol, exc.code)
            else:
                logger.warning("StockTwits fetch failed for %s via %s: %s", ticker, symbol, exc)
        except (URLError, json.JSONDecodeError, TimeoutError) as exc:
            failures.append(f"{symbol}: {type(exc).__name__}")
            logger.warning("StockTwits fetch failed for %s via %s: %s", ticker, symbol, exc)

    if data is None:
        attempted = ", ".join(candidates)
        details = "; ".join(failures) if failures else "no attempts"
        return f"<stocktwits unavailable for ${ticker.upper()}; attempted {attempted}; {details}>"

    messages = data.get("messages", []) if isinstance(data, dict) else []
    if not messages:
        return f"<no StockTwits messages found for ${used_symbol}>"

    lines = []
    bullish = bearish = unlabeled = 0
    for m in messages[:limit]:
        created = m.get("created_at", "")
        user = (m.get("user") or {}).get("username", "?")
        entities = m.get("entities") or {}
        sentiment_obj = entities.get("sentiment") or {}
        sentiment = sentiment_obj.get("basic") if isinstance(sentiment_obj, dict) else None
        body = (m.get("body") or "").replace("\n", " ").strip()
        if len(body) > 280:
            body = body[:280] + "…"

        if sentiment == "Bullish":
            bullish += 1
            tag = "Bullish"
        elif sentiment == "Bearish":
            bearish += 1
            tag = "Bearish"
        else:
            unlabeled += 1
            tag = "no-label"
        lines.append(f"[{created} · @{user} · {tag}] {body}")

    total = bullish + bearish + unlabeled
    bull_pct = round(100 * bullish / total) if total else 0
    bear_pct = round(100 * bearish / total) if total else 0
    summary = (
        f"Bullish: {bullish} ({bull_pct}%) · "
        f"Bearish: {bearish} ({bear_pct}%) · "
        f"Unlabeled: {unlabeled} · "
        f"Total: {total} most-recent messages"
    )
    return summary + "\n\n" + "\n".join(lines)
