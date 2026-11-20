# Amazon Haul Price Monitor

Separate service from the Amazon Price Error Monitor. Amazon Haul prices are
NOT available in Keepa, so this monitor scrapes Amazon Haul directly for the
current Haul price and uses Keepa only for the regular Amazon price history
(90-day average, brand, category and the Keepa price graph).

Alerts use the SAME layout as the Amazon price-error monitor, with
`Seller: Haul` and `Haul-50 / Haul-60 / Haul-70 / Haul-80` tier labels.

## How it works
1. Scrape `amazon.com/s?i=bazaar&k=...` (Amazon Haul) for ASIN + Haul price.
2. For each ASIN, read Keepa `/product` for the regular Amazon avg90, brand, category, graph.
3. Discount = (avg90 - Haul price) / avg90.
4. Haul-only junk ASINs have no Keepa price history (`productType 3`) and are skipped automatically.
5. Extra filters: min regular price, min savings, category/brand/title blocklists, tracking age.
6. Send to Discord in the configured tier.

## Run locally
```
cd amazon-haul-monitor
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe run_ui.bat   (or: venv\Scripts\python.exe web_ui.py)
```
Open http://127.0.0.1:8787, paste the Keepa key + webhooks, then Start.

## Deploy on Render
Use render.yaml (Starter plan, Oregon, disk at /var/data). Set env vars:
KEEPA_API_KEY, DISCORD_WEBHOOK_50/60/70/80, UI_PASSWORD.

## Filters (choose categories, brands, etc.)
All filters are env vars (and most are editable in the UI). Empty = no restriction.

- `MIN_ORIGINAL_PRICE` — regular Amazon 90d avg floor (default 15)
- `MIN_HAUL_PRICE` / `MAX_HAUL_PRICE` — Haul price range (default 3 / 25)
- `MIN_SAVINGS` — minimum dollar savings (default 8)
- `MIN_DISCOUNT` / `MAX_DISCOUNT` — discount band (default 50 / 95)
- `MIN_TRACKING_DAYS` — product must be tracked this long (default 60)
- `REQUIRE_BRAND` / `EXCLUDE_GENERIC_BRAND` — brand gates
- `MIN_REVIEW_COUNT` / `MIN_RATING` — review gates
- `INCLUDE_CATEGORIES` / `EXCLUDE_CATEGORIES` — comma-separated Keepa category IDs (choose categories, same as the Amazon monitor)
- `INCLUDE_BRANDS` / `EXCLUDE_BRANDS` — comma-separated brand names (lowercase)
- `TITLE_KEYWORDS` / `EXCLUDE_KEYWORDS` — title allow/block lists
- `REJECT_PROMOTIONS` / `REJECT_BUSINESS` — drop coupon / business-only items
- `MIN_RECENT_DISCOUNT` — require a sudden 7-day drop (0 = off)
- `EXCLUDE_BOOKS` — drop books/kindle/audiobooks (default true)
- `VERIFY_HAUL_STOCK` — fetch Haul product page to confirm stock (default false; search tile already detects OOS)
- Built-in title blocklist: phone case, sticker, keychain, jewelry, makeup, toys, etc.
- Built-in category blocklist: clothing, beauty, books, toys, jewelry, cell phones, video games, movies, grocery, baby, luggage, etc.
- Haul-only items (no Keepa price history) are skipped automatically.
- Stock check: search tile pe "Only X left" / "Currently unavailable" detect hota hai. Optional `VERIFY_HAUL_STOCK=true` product page fetch karke stock confirm karta hai (default off, kyunki extra HTTP requests). Add-to-Cart link Haul product page pe jaata hai jo stock status show karta hai.

## Files
- config.py        settings loader
- keepa_client.py  Keepa /product wrapper + cache compaction
- haul_scraper.py  Amazon Haul HTML scraper
- models.py        HaulListing + AlertItem + build_haul_alert
- filters.py       junk / quality filters + tier routing
- storage.py       SQLite dedupe + price history + product cache
- monitor.py       scan loop
- discord_notifier.py  Discord send (logo + graph)
- alert_uis.py     embed layout
- web_ui.py        dashboard
- verify.py        end-to-end verification helper
- main.py          CLI (--once, --force, --reset, --fixture)
