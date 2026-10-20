"""Dashboard in a real browser (Playwright + Chromium): login, the six pages (TradingView via a
stubbed embed), live push over WebSocket, hostile news text rendered inert, strict CSP without
violations, phone width."""

from __future__ import annotations

import asyncio
import contextlib
import re
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


async def _until(page: Any, expression: str) -> None:
    """Poll a page expression (wait_for_function evals strings, which the strict CSP refuses)."""
    for _ in range(100):
        if await page.evaluate(expression):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"never true: {expression}")


async def _fake_tradingview_script(route: Any) -> None:
    await route.fulfill(status=200, content_type="text/javascript", body=TRADINGVIEW_STUB)


async def _fake_tradingview_frame(route: Any) -> None:
    await route.fulfill(status=200, content_type="text/html", body="<p>chart</p>")


async def test_dashboard_pages_live_push_and_safety(dashboard: DashboardService) -> None:
    base = f"http://127.0.0.1:{dashboard.port}"
    problems: list[str] = []
    async with playwright_api.async_playwright() as p:
        browser = await launch(p)
        page = await browser.new_page(
            viewport={"width": 390, "height": 844},
            permissions=["clipboard-read", "clipboard-write"],
        )
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
        await _until(page, "charts.live && charts.live.lines.length === 6")  # entry x2, SL, TP1-3
        clock = await page.text_content("#clock") or ""
        assert re.search(r"\d{2}:\d{2}:\d{2} WIB$", clock), clock  # real-time WIB clock
        await page.wait_for_selector("#timing .board-head.BUY")
        timing = await page.text_content("#timing") or ""
        # The countdown runs on the browser clock; the seeded day is in the past, so it has expired.
        assert "BUY SEKARANG" in timing and "2649.60 – 2650.40" in timing and "2643.60" in timing
        assert "sampai 21:35 WIB" in timing
        assert "Semua jadwal peluang" in (await page.text_content("#timing-details") or "")
        assert await page.evaluate("charts.live.ind.groups.sar.sar.data().length") > 0
        await page.wait_for_selector("#tf-forecast .forecast-head")
        await page.click("#tf-button")
        await page.click(".tf-item[data-tf=M1]")
        await page.wait_for_selector(
            "#tf-forecast .forecast-head:has-text('M1 · 1 menit ke depan')"
        )
        assert await page.evaluate("charts.live.tf") == "M1"
        assert await page.is_hidden("#tf-ring")
        await page.click("#tf-button")
        await page.click(".tf-item[data-tf=M5]")
        await page.wait_for_selector(
            "#tf-forecast .forecast-head:has-text('M5 · 5 menit ke depan')"
        )
        await _until(page, "charts.live.lines.length === 6")
        assert await page.evaluate("charts.live.ind.groups.stoch.stoch_k.data().length") > 0
        await page.wait_for_selector("#ind-chips .chip")
        height = await page.evaluate("document.documentElement.scrollHeight")
        await page.wait_for_timeout(1500)
        assert await page.evaluate("document.documentElement.scrollHeight") == height  # no growth
        assert await page.evaluate("charts.live.ind.groups.ema.ema200.options().visible")
        await page.click("#ind-toggles input[data-ind=ema]")
        assert not await page.evaluate("charts.live.ind.groups.ema.ema200.options().visible")
        code = await page.text_content("#signal-card .tv-code code") or ""
        assert code.startswith("GNA1|BUY|") and "|2643.60|" in code
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
        await page.fill("#credit-input", "9.85")
        await page.click("#credit-form button[type=submit]")
        await page.wait_for_selector("#credit-status:has-text('Tersimpan')")
        assert (await page.text_content("#credit") or "").startswith("Claude $9.85")
        sources = await page.text_content("#sources-table") or ""
        assert "bls_latest" in sources and "down" in sources and "HTTP 403" in sources

        await page.click("nav a[data-page=tradingview]")
        frame = await page.wait_for_selector("#tv-widget iframe")
        await _until(page, "charts.gna && charts.gna.lines.length === 6")
        titles = await page.evaluate("charts.gna.lines.map((l) => l.options().title)")
        assert titles == [
            "BUY 2649.60–2650.40",
            "BUY zona bawah",
            "SL 2643.60",
            "TP1 2656.60",
            "TP2 2663.10",
            "TP3 2668.00",
        ]
        plan = await page.text_content("#gna-plan") or ""
        assert "BUY XAUUSD" in plan and "2649.60 – 2650.40" in plan and "2643.60" in plan
        assert "ambil 50%" in plan and "Batalkan rencana jika" in plan
        ahead = await page.evaluate(
            "(() => { const pts = charts.gna.projection.centre.data().filter((p) => 'value' in p);"
            " return { first: pts[0].time, last: charts.gna.last.time, n: pts.length,"
            " up: pts[12].value > charts.gna.last.close }; })()"
        )
        assert ahead["n"] > 0 and ahead["first"] > ahead["last"]  # forecast starts after the price
        assert ahead["up"]  # the active BUY's forecast heads for its TP1
        bands = await page.evaluate("charts.gna.sessions.data().map((b) => b.color)")
        assert bands and len(set(bands)) > 1  # session shading follows the bar times
        assert await frame.get_attribute("data-symbol") == "OANDA:XAUUSD"
        assert await page.get_attribute("#tv-open", "href") == (
            "https://www.tradingview.com/chart/?symbol=OANDA%3AXAUUSD"
        )
        await page.click("#tv-copy-script")
        await page.wait_for_selector("#tv-copy-status:has-text('Tersalin')")
        copied = await page.evaluate("navigator.clipboard.readText()")
        assert copied.startswith("// Gold News Agent") and "//@version=6" in copied

        await browser.close()
    assert problems == [], problems  # includes any Content-Security-Policy violation
