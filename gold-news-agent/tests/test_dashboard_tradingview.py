"""TradingView page: the CSP admits TradingView's embed only while the page is configured, the
front end learns the symbol from /api/state, and config rejects anything but EXCHANGE:TICKER."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from pydantic import ValidationError

from gold_agent.config import Settings
from gold_agent.core.app import AppContext
from gold_agent.dashboard.app import create_app
from gold_agent.dashboard.live import LiveHub


def _with_symbol(settings: Settings, symbol: str | None) -> Settings:
    dashboard = settings.dashboard.model_copy(update={"tradingview_symbol": symbol})
    return settings.model_copy(update={"dashboard": dashboard})


async def _page(ctx: AppContext) -> tuple[str, dict[str, object]]:
    await ctx.db.create_all()
    app = create_app(ctx, LiveHub(), password=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8787") as http:
        page = await http.get("/")
        state = (await http.get("/api/state")).json()
    await ctx.close()
    return page.headers["content-security-policy"], state


async def test_the_default_install_shows_spot_gold_from_tradingview(
    ctx_factory: Callable[..., AppContext],
) -> None:
    csp, state = await _page(ctx_factory())
    assert state["tradingview_symbol"] == "OANDA:XAUUSD"
    directives = dict(part.split(" ", 1) for part in csp.split("; "))
    assert directives["script-src"] == "'self' https://s3.tradingview.com"
    assert "https://www.tradingview-widget.com" in directives["frame-src"]
    assert "unsafe" not in csp and directives["connect-src"].startswith("'self'")


async def test_switching_tradingview_off_keeps_the_page_first_party_only(
    ctx_factory: Callable[..., AppContext], settings: Settings
) -> None:
    csp, state = await _page(ctx_factory(settings_override=_with_symbol(settings, None)))
    assert state["tradingview_symbol"] is None
    assert "tradingview" not in csp
    assert "script-src 'self';" in csp and "frame-src 'none'" in csp


@pytest.mark.parametrize("symbol", ["XAUUSD", "oanda:xauusd", "OANDA:XAU USD", "A:B;script-src *"])
def test_a_symbol_that_is_not_exchange_colon_ticker_is_rejected(
    settings: Settings, symbol: str
) -> None:
    raw = settings.model_dump(mode="json")
    raw["dashboard"]["tradingview_symbol"] = symbol
    with pytest.raises(ValidationError, match="tradingview_symbol"):
        Settings.model_validate(raw)
