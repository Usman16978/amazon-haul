"""Normalized Haul alert item assembled from a scraped Haul listing + Keepa."""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote_plus, urlencode

from config import Settings
from keepa_client import (
    IDX_RATING,
    IDX_REVIEW_COUNT,
    graph_image_url,
    product_image_url,
    stat_value,
)


@dataclass
class HaulListing:
    """One item scraped from an Amazon Haul page."""
    asin: str
    title: str = ""
    price: float | None = None
    image_url: str = ""
    rating: float | None = None
    review_count: int | None = None
    url: str = ""
    source: str = ""
    available: bool | None = None   # None = unknown, False = OOS/unavailable


@dataclass
class AlertItem:
    asin: str
    title: str
    image_url: str
    new_price: float          # Haul price
    old_price: float          # regular Amazon avg90
    discount: int
    seller: str
    promotion: bool
    business_required: bool
    brand: str | None
    review_count: int | None
    rating: float | None
    categories: list[int] = field(default_factory=list)
    category_names: list[str] = field(default_factory=list)
    root_cat: int = 0
    amazon_url: str = ""
    keepa_url: str = ""
    google_url: str = ""
    sas_url: str = ""
    ebay_url: str = ""
    atc_url: str = ""
    graph_url: str = ""
    recent_discount: int | None = None
    tracking_since: int | None = None
    channel_prefix: str = "Haul"

    @property
    def savings(self) -> float:
        return max(0.0, self.old_price - self.new_price)


def _current(stats: dict, idx: int) -> int | None:
    arr = stats.get("current") or []
    try:
        v = arr[idx]
    except (IndexError, TypeError):
        return None
    return int(v) if v is not None and int(v) >= 0 else None


def _category_ids(product: dict) -> tuple[list[int], list[str], int]:
    ids: list[int] = []
    names: list[str] = []
    for node in product.get("categoryTree") or []:
        if not isinstance(node, dict):
            continue
        try:
            cid = int(node.get("catId") or 0)
        except (TypeError, ValueError):
            cid = 0
        if cid:
            ids.append(cid)
        name = str(node.get("name") or "").strip().lower()
        if name:
            names.append(name)
    try:
        root = int(product.get("rootCategory") or 0)
    except (TypeError, ValueError):
        root = 0
    if root and root not in ids:
        ids.insert(0, root)
    return ids, names, root


def _recent_discount(stats: dict, price_type: int, new_price: float) -> int | None:
    """Discount vs the 7-day (avg30 index 1) average, mirroring the Amazon monitor."""
    week_cents = stat_value(stats, "avg30", price_type)
    if week_cents is None or week_cents <= 0:
        return None
    week_price = week_cents / 100.0
    if week_price <= new_price:
        return 0
    return int(round((week_price - new_price) / week_price * 100))


def build_haul_alert(
    listing: HaulListing,
    product: dict | None,
    settings: Settings,
) -> AlertItem | None:
    """Build a display-ready item, or None when Keepa has no usable baseline."""
    if not listing.asin or listing.price is None or listing.price <= 0:
        return None
    if not product:
        return None

    # Haul-only ASINs are productType 3 / isHaul true — Keepa has no price data.
    ptype = product.get("productType")
    if ptype is not None:
        try:
            if int(ptype) != 0:
                return None
        except (TypeError, ValueError):
            return None
    if bool(product.get("isHaul")):
        return None

    stats = product.get("stats") or {}
    pt = settings.price_type
    avg90_cents = stat_value(stats, "avg90", pt)
    if avg90_cents is None or avg90_cents <= 0:
        return None

    old_price = round(avg90_cents / 100.0, 2)
    new_price = round(float(listing.price), 2)
    if old_price <= 0 or new_price <= 0:
        return None
    if new_price >= old_price:
        return None

    discount = int(round((old_price - new_price) / old_price * 100))
    if discount <= 0:
        return None

    ids, names, root = _category_ids(product)
    brand = (product.get("brand") or product.get("manufacturer") or "").strip() or None
    title = (product.get("title") or listing.title or listing.asin).strip()
    image_url = product_image_url(product) or listing.image_url

    rating_raw = _current(stats, IDX_RATING)
    review_count = _current(stats, IDX_REVIEW_COUNT)

    coupon = product.get("coupon")
    promo = bool(coupon and any(bool(c) for c in coupon)) or bool(product.get("promotions"))

    host = settings.domain_host
    amazon_url = f"https://{host}/dp/{listing.asin}?s=bazaar&psc=1"
    q = title or listing.asin

    sas_url = "https://sas.selleramp.com/sas/lookup?" + urlencode(
        {"search_term": listing.asin, "sas_cost_price": f"{new_price:.2f}"}
    )

    return AlertItem(
        asin=listing.asin,
        title=title,
        image_url=image_url,
        new_price=new_price,
        old_price=old_price,
        discount=discount,
        seller=settings.seller_label,
        promotion=promo,
        business_required=bool(product.get("businessOnlyProduct") or product.get("isB2B")),
        brand=brand,
        review_count=review_count,
        rating=round(rating_raw / 10.0, 1) if rating_raw is not None else None,
        categories=ids,
        category_names=names,
        root_cat=root,
        amazon_url=amazon_url,
        keepa_url=f"https://keepa.com/#!product/{settings.keepa_domain}-{listing.asin}",
        google_url="https://www.google.com/search?q=" + quote_plus(q),
        sas_url=sas_url,
        ebay_url="https://www.ebay.com/sch/i.html?_nkw=" + quote_plus(q),
        atc_url=amazon_url,
        graph_url=graph_image_url(listing.asin, settings.domain_code, settings.reference_days)
        if settings.include_graph else "",
        recent_discount=_recent_discount(stats, pt, new_price),
        tracking_since=product.get("trackingSince"),
        channel_prefix=settings.channel_prefix,
    )
