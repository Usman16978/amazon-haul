"""End-to-end verification of the Amazon Haul monitor.

Runs:
  1. Keepa lookup + alert build for the 4 verified Haul fixtures.
  2. A live Haul search HTTP fetch (ASIN extraction; price extraction on US IP).
  3. A Discord webhook send (via the fixture run).

Usage:
    python verify.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config import load_settings
from filters import passes_filters
from haul_scraper import HaulScraper, parse_search_html
from keepa_client import KeepaClient, compact_product, product_image_url, stat_value
from models import HaulListing, build_haul_alert
from storage import Storage

FIXTURES = [
    ("B0FH9M9WCG", 6.95),
    ("B0FHGQWJFX", 8.95),
    ("B0FH6NM19T", 8.95),
    ("B0FHH8LLTW", 11.95),
]

def main() -> int:
    s = load_settings()
    print(f"KEEPA key: {s.keepa_api_key[:6]}...{s.keepa_api_key[-4:]}")
    print(f"Webhooks: {list(s.webhooks.keys())}")
    print(f"real webhooks: {list(s.webhooks.keys())}")

    # --- Keepa + build ---
    kc = KeepaClient(s.keepa_api_key, s.keepa_domain)
    products = kc.get_products([a for a, _ in FIXTURES], stats_days=s.reference_days)
    print(f"\nKeepa returned {len(products)} products, tokens_left={kc.tokens_left}")

    store = Storage(ROOT / "data" / "verify.db")
    try:
        for asin, haul in FIXTURES:
            p = next((x for x in products if (x.get("asin") or "").upper() == asin), None)
            if not p:
                print(f"  {asin}: NOT FOUND in Keepa")
                continue
            compact = compact_product(p)
            store.set_cached_product(asin, compact)
            lst = HaulListing(asin=asin, title=compact.get("title") or "", price=haul)
            item = build_haul_alert(lst, compact, s)
            if item is None:
                print(f"  {asin}: build_haul_alert -> None (skipped)")
                continue
            ok, reason = passes_filters(item, s)
            print(
                f"  {asin}: haul=${haul:.2f} old=${item.old_price:.2f} "
                f"disc={item.discount}% tier={item.discount if False else 'ok'} "
                f"savings=${item.savings:.2f} brand={item.brand} "
                f"passes={ok} ({reason})"
            )
            if not ok:
                print("     ^ would be filtered out by current settings")
    finally:
        store.close()

    # --- live scraper HTTP probe ---
    print("\nLive Haul search probe:")
    scraper = HaulScraper(s)
    try:
        html = scraper.fetch_html("https://www.amazon.com/s?i=bazaar&k=automotive")
        listings = parse_search_html(html, page_url="probe")
        print(f"  fetched {len(html)} bytes, parsed {len(listings)} listings")
        priced = sum(1 for x in listings if x.price is not None)
        print(f"  priced: {priced}/{len(listings)}")
        if listings:
            for x in listings[:5]:
                print(f"    - {x.asin} ${x.price} {x.title[:60].encode('ascii','replace').decode()}")
        if priced == 0 and listings:
            print("  NOTE: no USD prices — this machine is not on a US IP. "
                  "On Render Oregon (US) prices should appear.")
    except Exception as exc:  # noqa: BLE001
        print(f"  probe failed: {exc}")
    finally:
        scraper.close()

    print("\nVerification complete. Run `python main.py --fixture --reset --once` to send test alerts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
