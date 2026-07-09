"""Collect raw FIFA schedule/results and men's ranking pages.

This script intentionally stores a raw, auditable snapshot before attempting to
normalize football data. Run it locally; it does not submit data anywhere.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, Page, async_playwright


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_RAW_DATA_DIR = BASE_DIR / "data" / "raw" / "fifa"

SOURCES = {
    "world_cup_schedule_results": (
        "https://www.fifa.com/en/tournaments/mens/worldcup/"
        "canadamexicousa2026/articles/"
        "match-schedule-fixtures-results-teams-stadiums"
    ),
    "mens_world_ranking": (
        "https://inside.fifa.com/en/fifa-world-ranking/men"
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Save raw snapshots of official FIFA data pages."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_RAW_DATA_DIR,
        help=f"Snapshot root directory (default: {DEFAULT_RAW_DATA_DIR}).",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser window for troubleshooting.",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=3.0,
        help="Extra time for dynamic content after DOM load (default: 3).",
    )
    return parser.parse_args()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def extract_dom_records(page: Page) -> list[dict[str, Any]]:
    """Extract bounded, useful DOM records without relying on FIFA CSS classes."""
    return await page.locator("h1, h2, h3, h4, p, li, tr").evaluate_all(
        """
        elements => elements
          .map(element => ({
            tag: element.tagName.toLowerCase(),
            text: (element.innerText || '').replace(/\\s+/g, ' ').trim(),
            cells: element.tagName.toLowerCase() === 'tr'
              ? Array.from(element.querySelectorAll('th, td'))
                  .map(cell => (cell.innerText || '').replace(/\\s+/g, ' ').trim())
                  .filter(Boolean)
              : []
          }))
          .filter(record => record.text)
        """
    )


async def prepare_dynamic_content(
    page: Page,
    source_name: str,
    settle_seconds: float,
) -> dict[str, Any]:
    """Expand source-specific controls before saving the snapshot."""
    interaction: dict[str, Any] = {}
    if source_name != "mens_world_ranking":
        return interaction

    reject_cookies = page.get_by_role("button", name="Reject All", exact=True)
    if await reject_cookies.count() == 1 and await reject_cookies.is_visible():
        await reject_cookies.click()
        interaction["cookie_dialog_dismissed"] = True

    rows = page.locator("table tr")
    interaction["ranking_table_rows_before_expand"] = await rows.count()

    show_full = page.get_by_text("Show full rankings", exact=True)
    show_full_count = await show_full.count()
    interaction["show_full_rankings_control_count"] = show_full_count
    interaction["show_full_rankings_clicked"] = False

    if show_full_count == 1 and await show_full.is_visible():
        await show_full.scroll_into_view_if_needed()
        await show_full.click()
        interaction["show_full_rankings_clicked"] = True
        await page.wait_for_timeout(round(settle_seconds * 1000))

    # Some FIFA ranking views append rows while the page is scrolled.
    previous_count = -1
    stable_rounds = 0
    for _ in range(12):
        current_count = await rows.count()
        if current_count == previous_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
        if stable_rounds >= 2:
            break
        previous_count = current_count
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(500)

    interaction["ranking_table_rows_after_expand"] = await rows.count()
    return interaction


async def collect_source(
    browser: Browser,
    source_name: str,
    source_url: str,
    snapshot_dir: Path,
    settle_seconds: float,
) -> dict[str, Any]:
    page = await browser.new_page(
        user_agent=(
            "Mozilla/5.0 (compatible; WorldCupResearchCollector/1.0; "
            "+local-research)"
        ),
        locale="en-US",
    )
    started_at = datetime.now(timezone.utc)

    try:
        response = await page.goto(
            source_url,
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        await page.locator("body").wait_for(state="attached", timeout=30_000)
        await page.wait_for_timeout(round(settle_seconds * 1000))
        interaction = await prepare_dynamic_content(
            page=page,
            source_name=source_name,
            settle_seconds=settle_seconds,
        )

        html = await page.content()
        text = await page.locator("body").inner_text()
        dom_records = await extract_dom_records(page)
        title = await page.title()
        final_url = page.url

        source_dir = snapshot_dir / source_name
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "page.html").write_text(html, encoding="utf-8")
        (source_dir / "page.txt").write_text(text, encoding="utf-8")
        (source_dir / "dom_records.json").write_text(
            json.dumps(dom_records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        metadata = {
            "source_name": source_name,
            "requested_url": source_url,
            "final_url": final_url,
            "title": title,
            "http_status": response.status if response else None,
            "collected_at_utc": datetime.now(timezone.utc).isoformat(),
            "html_sha256": sha256_text(html),
            "text_sha256": sha256_text(text),
            "text_character_count": len(text),
            "dom_record_count": len(dom_records),
            "interaction": interaction,
        }
        (source_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {"ok": True, **metadata}
    except Exception as exc:
        return {
            "ok": False,
            "source_name": source_name,
            "requested_url": source_url,
            "started_at_utc": started_at.isoformat(),
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    finally:
        await page.close()


async def main() -> int:
    args = parse_args()
    if args.settle_seconds < 0:
        raise ValueError("--settle-seconds cannot be negative")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_dir = args.output_dir / timestamp
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=not args.headed)
        try:
            results = []
            for source_name, source_url in SOURCES.items():
                result = await collect_source(
                    browser=browser,
                    source_name=source_name,
                    source_url=source_url,
                    snapshot_dir=snapshot_dir,
                    settle_seconds=args.settle_seconds,
                )
                results.append(result)
                status = "OK" if result["ok"] else "FAILED"
                print(f"[{status}] {source_name}")

                # Be polite to the public website; these pages do not need concurrency.
                await asyncio.sleep(1)
        finally:
            await browser.close()

    manifest = {
        "snapshot_id": timestamp,
        "snapshot_directory": str(snapshot_dir.resolve()),
        "results": results,
    }
    (snapshot_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    succeeded = sum(result["ok"] for result in results)
    print(f"Collected {succeeded}/{len(results)} sources into {snapshot_dir}")
    return 0 if succeeded == len(results) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("Collection interrupted.", file=sys.stderr)
        raise SystemExit(130)
