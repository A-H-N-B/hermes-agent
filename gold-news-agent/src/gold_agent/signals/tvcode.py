"""One-line signal code for the "Gold News Agent" TradingView indicator.

Pine Script cannot fetch data from outside TradingView, so a signal reaches the chart as text the
user pastes into the indicator's settings. The field order below is the contract with
`dashboard/static/gold_news_agent.pine` (it parses exactly these 13 fields): bump CODE_VERSION
when it changes so an old indicator rejects a new code instead of misreading it.

GNA1|action|created_unix|reference_mid|entry_low|entry_high|stop|tp1|tp2|tp3|valid_unix|confidence|P/B

reference_mid is the mid price the levels were computed from; the indicator adds
(chart price at created_unix - reference_mid) to every level, so a PAXG-proxy signal lands on
the broker's XAUUSD chart. P = proxy price source, B = broker price.
"""

from __future__ import annotations

from gold_agent.models.signals import Signal

CODE_VERSION = "GNA1"


def tradingview_code(signal: Signal) -> str:
    def level(value: float) -> str:
        return f"{value:.2f}"

    fields = [
        CODE_VERSION,
        signal.action,
        str(int(signal.created_at_utc.timestamp())),
        level((signal.bid + signal.ask) / 2),
        level(signal.entry_low),
        level(signal.entry_high),
        level(signal.stop_loss),
        level(signal.tp1),
        level(signal.tp2),
        level(signal.tp3),
        str(int(signal.valid_until_utc.timestamp())),
        str(signal.confidence),
        "P" if signal.proxy_price else "B",
    ]
    return "|".join(fields)
