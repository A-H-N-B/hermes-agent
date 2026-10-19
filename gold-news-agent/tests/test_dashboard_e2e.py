"""Dashboard in a real browser (Playwright + Chromium): login, the six pages (TradingView via a
stubbed embed), live push over WebSocket, hostile news text rendered inert, strict CSP without
violations, phone width."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from gold_agent.config import Settings
from gold_agent.core.app import AppContext
from gold_agent.core.clock import ManualClock
from gold_agent.services.dashboard import DashboardService
from tests.builders import analysis
from tests.dashboard_seed import HOSTILE, NOW, seed_dashboard

playwright_api = pytest.importorskip("playwright.async_api")

PASSWORD = "uji-e2e"  # noqa: S105 - test-only password
FALLBACK_CHROMIUM = Path("/opt/pw-browsers/chromium")


@pytest.fixture
async def dashboard(
    ctx: AppContext, clock: ManualClock, settings: Settings
) -> AsyncIterator[DashboardService]:
    clock.set(NOW)
    await seed_dashboard(ctx, settings)
    ctx.env["DASHBOARD_PASSWORD"] = PASSWORD
    ctx.settings = settings.model_copy(
        update={"dashboard": settings.dashboard.model_copy(update={"port": 0})}
    )
    service = DashboardService(ctx)
    stop = asyncio.Event()
    task = asyncio.create_task(service.run(stop))
    for _ in range(100):
        if service.port:
            break
        await asyncio.sleep(0.05)
    yield service
    stop.set()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(task, timeout=15)


async def launch(p: Any) -> Any:
    """Playwright's own browser build if present, else the image's preinstalled Chromium."""
    try:
        return await p.chromium.launch()
    except Exception:
        if not await asyncio.to_thread(FALLBACK_CHROMIUM.exists):
            pytest.skip("no Chromium available for Playwright")
        return await p.chromium.launch(executable_path=str(FALLBACK_CHROMIUM))


# Stand-in for TradingView's embed script (the real one is a network fetch): it reads its config
# the way the real one does and opens the widget iframe, so a CSP that blocked either shows up.
TRADINGVIEW_STUB = """(() => {
  const script = document.currentScript;
  const config = JSON.parse(script.textContent);
  const frame = document.createElement("iframe");
  frame.src = "https://www.tradingview-widget.com/embed-widget/advanced-chart/";
  frame.dataset.symbol = config.symbol;
  script.parentElement.querySelector(".tradingview-widget-container__widget").appendChild(frame);
})();"""


async def _fake_tradingview_script(route: Any) -> None:
    await route.fulfill(status=200, content_type="text/javascript", body=TRADINGVIEW_STUB)


async def _fake_tradingview_frame(route: Any) -> None:
    await route.fulfill(status=200, content_type="text/html", body="<p>chart</p>")


async def test_dashboard_pages_live_push_and_safety(dashboard: DashboardService) -> None:
    base = f"http://127.0.0.1:{dashboard.port}"
    problems: list[str] = []
    async with playwright_api.async_playwright() as p:
        browser = await launch(p)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: problems.append(str(e)))
        await page.route("https://s3.tradingview.com/**", _fake_tradingview_script)
        await page.route("https://www.tradingview-widget.com/**", _fake_tradingview_frame)

        await page.goto(base + "/")
        assert page.url.endswith("/login")
        await page.fill("#password", "salah")
        await page.click("button[type=submit]")
        await page.wait_for_selector("#login-error:not([hidden])")
        await page.fill("#password", PASSWORD)
        await page.click("button[type=submit]")

        await page.wait_for_selector("#signal-card .action.BUY")
        card = await page.text_content("#signal-card") or ""
        assert "2649.60–2650.40" in card and "2643.60" in card and "Batal jika" in card
        await page.wait_for_selector("#chart canvas")
        news = await page.text_content("#news") or ""
        assert HOSTILE in news  # shown literally ...
        assert await page.locator("#news img").count() == 0  # ... never parsed as markup
        assert await page.evaluate("document.body.dataset.pwned") is None
        await page.wait_for_selector("#conn:not(.off)")
        fresh = analysis("live1", first_seen=NOW, summary_id="Berita baru lewat WebSocket")
        await dashboard.hub.on_analysis(fresh.model_dump(mode="json"))
        await page.wait_for_selector("#news li[data-id=live1]")
        overflow = await page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1  # usable on a phone: no sideways scrolling

        await page.click("nav a[data-page=outlook]")
        await page.wait_for_selector("#outlook-table tbody tr")
        visible = [
            name
            for name in ("live", "outlook", "kalender", "performa", "kesehatan")
            if await page.is_visible(f"#page-{name}")
        ]
        assert visible == ["outlook"]  # one page at a time
        squeezed = await page.evaluate(
            "(() => { const w = document.querySelector('#page-outlook .table-wrap');"
            " return w.scrollWidth - w.clientWidth; })()"
        )
        assert squeezed <= 1  # on a phone the table stacks instead of hiding columns
        assert await page.locator("#outlook-table tbody tr").count() == 24
        first_row = await page.text_content("#outlook-table tbody tr") or ""
        assert first_row.startswith("19:00–20:00")
        assert "CPI m/m" in (await page.text_content("#countdown") or "")

        await page.click("nav a[data-page=kalender]")
        await page.wait_for_selector("#calendar-table details summary")
        assert "CPI m/m" in (await page.text_content("#calendar-table") or "")

        await page.click("nav a[data-page=performa]")
        await page.wait_for_selector("#perf-notes li")
        assert "sampel belum cukup" in (await page.text_content("#perf-notes") or "")

        await page.click("nav a[data-page=kesehatan]")
        await page.wait_for_selector("#sources-table tbody tr")
        sources = await page.text_content("#sources-table") or ""
        assert "bls_latest" in sources and "down" in sources and "HTTP 403" in sources

        await page.click("nav a[data-page=tradingview]")
        frame = await page.wait_for_selector("#tv-widget iframe")
        assert await frame.get_attribute("data-symbol") == "OANDA:XAUUSD"
        assert await page.get_attribute("#tv-open", "href") == (
            "https://www.tradingview.com/chart/?symbol=OANDA%3AXAUUSD"
        )

        await browser.close()
    assert problems == [], problems  # includes any Content-Security-Policy violation
