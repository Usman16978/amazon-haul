"""Quality filters for Haul alerts.

The key junk filter is structural: a Haul-only ASIN has no Keepa avg90
(productType 3), so it never reaches this stage. These filters then cut
low-value branded/regular items that are still not worth an alert.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from config import Settings
from models import AlertItem

# Keepa time is minutes since 2011-01-01 00:00 UTC.
KEEPA_EPOCH = datetime(2011, 1, 1, tzinfo=timezone.utc)

# Obvious low-value / wrong-category titles.
DEFAULT_JUNK_KEYWORDS = (
    "phone case", "phone cover", "screen protector", "keychain", "key chain",
    "sticker", "decal", "jewelry", "earring", "necklace", "bracelet",
    "makeup", "lipstick", "eyeliner", "mascara", "wig", "hair extension",
    "false eyelash", "nail polish", "press on nail", "tattoo", "socks",
    "underwear", "panties", "lingerie", "swimsuit", "plush", "stuffed animal",
    "figurine", "ornament", "poster", "wall art", "paperback", "hardcover",
    "kindle edition", "audiobook", "dvd", "blu-ray", "video game",
    "party favor", "beads", "yarn", "sewing", "scrapbook", "lanyard",
    "wristband", "sunglasses", "wallet", "purse", "handbag", "backpack",
    "luggage", "umbrella", "water bottle", "tumbler", "detergent",
    "shampoo", "conditioner", "lotion", "serum", "supplement", "vitamin",
    "protein powder", "pet toy", "dog toy", "cat toy", "candy", "coffee",
    "tea", "seasoning", "sauce", "vinegar",
    # Amazon monitor spam bits
    "for parts", "not working", "does not work", "doesn't work",
    "wholesale lot", "no returns", "parts only", "as is",
)

# Root browse nodes that are almost always low-value on Haul.
DEFAULT_EXCLUDE_CATEGORIES = (
    7141123011,   # Clothing, Shoes & Jewelry
    11055981,     # Beauty & Personal Care
    283155,       # Books
    133140011,    # Kindle Store / eBooks
    165793011,    # Toys & Games
    2617941011,   # Arts, Crafts & Sewing
    2335752011,   # Cell Phones & Accessories
    468642,       # Video Games
    2625373011,   # Movies & TV
    229534,       # Software
    16310101,     # Grocery & Gourmet Food
    165796011,    # Baby
    3367581,      # Jewelry
    3290252011,   # Luggage & Travel Gear
    5174,         # CDs & Vinyl
)

DEFAULT_EXCLUDE_CATEGORY_NAMES = (
    "clothing", "shoes", "jewelry", "beauty", "books", "kindle", "ebooks",
    "toys & games", "arts, crafts", "cell phones", "video games", "movies & tv",
    "software", "grocery", "baby", "luggage", "cds & vinyl",
)

GENERIC_BRANDS = (
    "generic", "unbranded", "unknown", "no brand", "none", "n/a",
)


def _brand_l(item: AlertItem) -> str:
    return (item.brand or "").strip().lower()


def _tracking_days(item: AlertItem) -> int | None:
    if item.tracking_since is None:
        return None
    try:
        tracked = KEEPA_EPOCH + timedelta(minutes=int(item.tracking_since))
    except (TypeError, ValueError, OverflowError):
        return None
    return max(0, (datetime.now(timezone.utc) - tracked).days)


def passes_filters(item: AlertItem, s: Settings) -> tuple[bool, str]:
    # --- price / discount sanity -------------------------------------
    if item.old_price < s.min_original_price:
        return False, f"regular avg90 ${item.old_price:.2f} < ${s.min_original_price:.2f}"
    if item.new_price < s.min_haul_price:
        return False, f"Haul price ${item.new_price:.2f} < ${s.min_haul_price:.2f}"
    if item.new_price > s.max_haul_price:
        return False, f"Haul price ${item.new_price:.2f} > ${s.max_haul_price:.2f}"
    if item.savings < s.min_savings:
        return False, f"savings ${item.savings:.2f} < ${s.min_savings:.2f}"
    if item.discount < s.min_discount:
        return False, f"discount {item.discount}% < {s.min_discount}%"
    if item.discount > s.max_discount:
        return False, f"discount {item.discount}% > {s.max_discount}% (unreliable baseline)"

    # --- title -------------------------------------------------------
    title_l = (item.title or "").lower()
    junk = tuple(s.exclude_keywords) or DEFAULT_JUNK_KEYWORDS
    for bit in junk:
        if bit and bit in title_l:
            return False, f"junk title: {bit}"
    if s.title_keywords and not any(k in title_l for k in s.title_keywords):
        return False, "title missing required keyword"

    # --- category ----------------------------------------------------
    cats = set(item.categories) | ({item.root_cat} if item.root_cat else set())
    exclude_cats = set(s.exclude_categories) or set(DEFAULT_EXCLUDE_CATEGORIES)
    if s.include_categories and not (cats & set(s.include_categories)):
        return False, "category not in include list"
    if cats & exclude_cats:
        return False, "excluded category"
    cat_names = " ".join(item.category_names or [])
    for bit in DEFAULT_EXCLUDE_CATEGORY_NAMES:
        if bit in cat_names:
            return False, f"excluded category name: {bit}"

    # --- brand -------------------------------------------------------
    brand_l = _brand_l(item)
    if s.require_brand and not brand_l:
        return False, "missing brand"
    if s.exclude_generic_brand and brand_l in GENERIC_BRANDS:
        return False, f"generic brand: {item.brand}"
    if s.include_brands and brand_l not in s.include_brands:
        return False, "brand not in include list"
    if s.exclude_brands and brand_l in s.exclude_brands:
        return False, "brand in exclude list"

    # --- reviews / rating -------------------------------------------
    if s.min_review_count > 0:
        if item.review_count is None or item.review_count < s.min_review_count:
            return False, f"reviews {item.review_count} < {s.min_review_count}"
    if s.min_rating > 0:
        if item.rating is None or item.rating < s.min_rating:
            return False, f"rating {item.rating} < {s.min_rating}"

    # --- age of Keepa history ---------------------------------------
    if s.min_tracking_days > 0:
        days = _tracking_days(item)
        if days is not None and days < s.min_tracking_days:
            return False, f"tracked {days}d < {s.min_tracking_days}d"

    return True, "ok"


def tier_for_discount(discount: int, s: Settings) -> int | None:
    """Highest configured tier reached. 90%+ routes to the 80 channel."""
    eligible = [t for t in (80, 70, 60, 50) if discount >= t and s.webhooks.get(t)]
    return max(eligible) if eligible else None


def score(item: AlertItem) -> tuple:
    """Higher is better: discount, dollar savings, lower Haul price."""
    return (item.discount, item.savings, -item.new_price)


def passes_price_error_quality(
    item: AlertItem,
    product: dict | None,
    s: Settings,
) -> tuple[bool, str]:
    """Stricter gates so we post real price errors, not Keepa deal junk."""
    min_recent = int(getattr(s, "min_recent_discount", 0) or 0)
    if min_recent > 0:
        recent = item.recent_discount
        if recent is not None and recent < min_recent:
            return False, "not a sudden drop (7d disc %d%% < %d%%)" % (recent, min_recent)

    if bool(getattr(s, "reject_business", False)) and item.business_required:
        return False, "business-only product"

    if bool(getattr(s, "reject_promotions", False)) and item.promotion:
        return False, "promotion/coupon"

    if bool(getattr(s, "exclude_books", True)):
        title_l = (item.title or "").lower()
        if any(b in title_l for b in ("paperback", "hardcover", "kindle", "audiobook", "audio cd")):
            return False, "book / kindle / audiobook excluded"
        if 283155 in (item.categories or []) or 133140011 in (item.categories or []):
            return False, "book category excluded"

    return True, "ok"
