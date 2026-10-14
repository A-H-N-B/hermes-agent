from __future__ import annotations

from gold_agent.collectors.parsers import FeedItem
from gold_agent.processing.normalizer import (
    Normalizer,
    canonical_url,
    clean_title,
    detect_lang,
    normalize_title,
    stable_id,
    title_hash,
    title_numbers,
)
from tests.conftest import T0, make_source


def test_canonical_url_drops_tracking_params_and_fragments() -> None:
    url = "HTTPS://News.Example.com/a/b/?utm_source=x&id=7&fbclid=abc&b=2#top"
    assert canonical_url(url) == "https://news.example.com/a/b?b=2&id=7"
    assert canonical_url(None) is None


def test_clean_title_strips_noise_but_keeps_meaning() -> None:
    assert (
        clean_title("🚨 BREAKING: Fed hikes rates by 25bp (Reuters)") == "Fed hikes rates by 25bp"
    )
    assert clean_title("*US CPI MoM 0.4% vs 0.3% exp") == "US CPI MoM 0.4% vs 0.3% exp"


def test_normalized_titles_ignore_case_punctuation_and_clock_times() -> None:
    a = normalize_title("Gold jumps to record at 14:05 GMT!")
    b = normalize_title("GOLD JUMPS TO RECORD at 14:06 gmt")
    assert a == b
    assert title_hash("Gold jumps to record at 14:05 GMT!") == title_hash("gold jumps to record at")


def test_title_numbers_keep_release_values_distinct() -> None:
    assert title_numbers("US CPI MoM 0.4% vs 0.3% exp at 08:30") == ("0.4%", "0.3%")
    assert title_numbers("US CPI MoM 0.4% vs 0.3% exp") != title_numbers("US CPI MoM 0.3% vs 0.3%")


def test_language_guess() -> None:
    assert detect_lang("Harga emas naik tajam setelah data inflasi AS yang lebih rendah") == "id"
    assert detect_lang("Gold rises after softer US inflation data") == "en"


def test_ids_are_stable_per_source_and_external_id() -> None:
    assert stable_id("a", "x") == stable_id("a", "x")
    assert stable_id("a", "x") != stable_id("b", "x")


def test_feed_items_become_news_items_with_source_metadata() -> None:
    source = make_source(tier=2, reliability=0.9, official=True)
    item = Normalizer().from_feed_item(
        source,
        FeedItem(
            id="guid-1",
            title="BREAKING: Fed holds rates",
            summary="Fed holds rates",
            link="https://x.io/a?utm_medium=rss",
            published=T0,
        ),
        first_seen_at=T0,
        backfill=False,
    )
    assert item is not None
    assert item.title == "Fed holds rates"
    assert item.body is None  # summary identical to title
    assert item.url_canonical == "https://x.io/a"
    assert (item.tier, item.reliability, item.official) == (2, 0.9, True)


def test_chat_text_first_line_is_headline() -> None:
    item = Normalizer().from_text(
        make_source(type="telegram", url=None),
        external_id="1:2",
        text="\n🔴 US CPI MoM 0.4% vs 0.3% exp\nCore 0.3% vs 0.3% exp\n",
        url=None,
        published_at=T0,
        first_seen_at=T0,
    )
    assert item is not None
    assert item.title == "US CPI MoM 0.4% vs 0.3% exp"
    assert item.body is not None and "Core 0.3%" in item.body
    assert (
        Normalizer().from_text(
            make_source(type="telegram", url=None),
            external_id="1:3",
            text="   ",
            url=None,
            published_at=T0,
            first_seen_at=T0,
        )
        is None
    )
