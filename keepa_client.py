"""Keepa API wrapper for the Haul monitor.

The Haul monitor does NOT use Keepa deals. It scrapes Haul prices, then uses
Keepa /product only for the regular Amazon price history (avg90) + graph.
"""
from __future__ import annotations

import json
import logging
import time

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.keepa.com"
IMAGE_BASE = "https://m.media-amazon.com/images/I/"

# Dark Keepa chart, same style as the normal Amazon monitor.
_GRAPH_DARK = (
    "cBackground=1E1F22"
    "&cFont=DBDEE1"
    "&cAmazon=FF9900"
    "&cNew=9B7EDE"
    "&cUsed=888888"
    "&cSales=2ECC71"
    "&cBB=E91E8C"
    "&cFBA=3498DB"
    "&cFBM=95A5A6"
)

IDX_RATING = 16          # value is stars * 10  (e.g. 45 -> 4.5)
IDX_REVIEW_COUNT = 17


def decode_keepa_image_name(image) -> str:
    if image is None:
        return ""
    if isinstance(image, str):
        return image.strip()
    if isinstance(image, (list, tuple)):
        try:
            return "".join(chr(int(c)) for c in image).strip()
        except (TypeError, ValueError):
            return ""
    return str(image).strip()


def amazon_image_url(image_id: str | list | None, *, size: int = 120) -> str:
    raw = decode_keepa_image_name(image_id)
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        if "/images/I/" in raw and "._SL" not in raw and size > 0:
            base, _, query = raw.partition("?")
            if base.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
                stem, dot, ext = base.rpartition(".")
                raw = f"{stem}._SL{int(size)}_{dot}{ext}"
                if query:
                    raw = raw + "?" + query
                return raw
        return raw
    name = raw.split("/")[-1].split("?")[0]
    if not name:
        return ""
    if "." not in name:
        name = name + ".jpg"
    if size > 0 and "._SL" not in name:
        stem, dot, ext = name.rpartition(".")
        name = f"{stem}._SL{int(size)}_{dot}{ext}"
    return IMAGE_BASE + name


def product_image_url(product: dict | None) -> str:
    if not product:
        return ""
    images = product.get("images")
    if isinstance(images, list) and images:
        first = images[0] or {}
        if isinstance(first, dict):
            name = first.get("l") or first.get("m") or ""
            built = amazon_image_url(name if isinstance(name, str) else None)
            if built:
                return built
    csv = product.get("imagesCSV")
    if csv:
        first = str(csv).split(",")[0].strip()
        built = amazon_image_url(first)
        if built:
            return built
    return ""


def graph_image_url(asin: str, domain_code: str, days: int = 90) -> str:
    """Public Keepa price-history PNG — dark theme (0 tokens)."""
    return (
        f"https://graph.keepa.com/pricehistory.png?"
        f"asin={asin}&domain={domain_code}"
        f"&amazon=1&new=1&bb=1&salesrank=1&fba=1"
        f"&range={days}&width=600&height=220"
        f"&{_GRAPH_DARK}"
    )


def graph_image_url_api(asin: str, api_key: str, domain: int = 1, days: int = 90) -> str:
    """Keepa API graphimage (uses tokens) — fallback when public graph is blocked."""
    return (
        f"https://api.keepa.com/graphimage?"
        f"key={api_key}&domain={domain}&asin={asin}"
        f"&amazon=1&new=1&bb=1&salesrank=1&fba=1"
        f"&range={days}&width=600&height=220"
        f"&{_GRAPH_DARK}"
    )


def stat_value(stats: dict, name: str, price_type: int) -> int | None:
    """Read a flat stats array (current/avg30/avg90/...) for a price type."""
    arr = stats.get(name) or []
    try:
        v = arr[price_type]
    except (IndexError, TypeError):
        return None
    return int(v) if v is not None and int(v) >= 0 else None


def product_price(product: dict | None, name: str, price_type: int) -> float | None:
    if not product:
        return None
    cents = stat_value(product.get("stats") or {}, name, price_type)
    return round(cents / 100.0, 2) if cents is not None else None


def compact_product(product: dict) -> dict:
    """Keep only the fields the monitor/cache needs (small SQLite rows)."""
    stats = product.get("stats") or {}
    return {
        "asin": product.get("asin"),
        "title": product.get("title"),
        "brand": product.get("brand"),
        "manufacturer": product.get("manufacturer"),
        "productType": product.get("productType"),
        "isHaul": product.get("isHaul"),
        "rootCategory": product.get("rootCategory"),
        "categoryTree": product.get("categoryTree") or [],
        "imagesCSV": product.get("imagesCSV"),
        "images": product.get("images"),
        "trackingSince": product.get("trackingSince"),
        "coupon": product.get("coupon"),
        "promotions": product.get("promotions"),
        "businessOnlyProduct": product.get("businessOnlyProduct"),
        "isB2B": product.get("isB2B"),
        "stats": {
            "current": stats.get("current") or [],
            "avg30": stats.get("avg30") or [],
            "avg90": stats.get("avg90") or [],
            "avg180": stats.get("avg180") or [],
            "buyBoxSellerId": stats.get("buyBoxSellerId"),
            "buyBoxIsFBA": stats.get("buyBoxIsFBA"),
            "totalOfferCount": stats.get("totalOfferCount"),
        },
    }


class KeepaError(RuntimeError):
    pass


class KeepaClient:
    def __init__(self, api_key: str, domain: int = 1, timeout: float = 60.0):
        if not api_key:
            raise KeepaError("KEEPA_API_KEY is not set")
        self.api_key = api_key
        self.domain = domain
        self._client = httpx.Client(timeout=timeout, headers={"User-Agent": "AHM/1.0"})
        self.tokens_left: int | None = None
        self.refill_in_ms: int = 0

    def close(self) -> None:
        self._client.close()

    def get_products(self, asins: list[str], stats_days: int = 90) -> list[dict]:
        """Batch /product lookup. Keepa charges 1 token per ASIN."""
        clean: list[str] = []
        seen: set[str] = set()
        for a in asins:
            a = (a or "").strip().upper()
            if a and a not in seen:
                seen.add(a)
                clean.append(a)
        if not clean:
            return []
        params = {
            "key": self.api_key,
            "domain": self.domain,
            "asin": ",".join(clean[:100]),
            "stats": stats_days,
        }
        data = self._request("/product", params)
        return (data or {}).get("products") or []

    def get_product(self, asin: str, stats_days: int = 90) -> dict | None:
        products = self.get_products([asin], stats_days=stats_days)
        return products[0] if products else None

    def _request(self, path: str, params: dict, retries: int = 3) -> dict:
        url = BASE_URL + path
        for attempt in range(retries + 1):
            try:
                resp = self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                if attempt < retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise KeepaError(f"HTTP error calling {path}: {exc}") from exc

            if resp.status_code == 429:
                try:
                    hint = resp.json()
                    refill_ms = int(hint.get("refillIn") or 0)
                    self.tokens_left = hint.get("tokensLeft", self.tokens_left)
                except Exception:
                    refill_ms = 0
                wait = max(5, min(60, (refill_ms // 1000) + 2)) if refill_ms else 15 * (attempt + 1)
                logger.warning("Keepa 429 (rate/tokens). Waiting %ss", wait)
                time.sleep(wait)
                continue

            if resp.status_code != 200:
                raise KeepaError(f"Keepa {path} returned {resp.status_code}: {resp.text[:200]}")

            data = resp.json()
            self.tokens_left = data.get("tokensLeft", self.tokens_left)
            self.refill_in_ms = data.get("refillIn", 0) or 0
            return data
        raise KeepaError(f"Keepa {path} failed after retries")
