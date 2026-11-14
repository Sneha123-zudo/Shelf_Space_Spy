"""Category-agnostic product normalization.

* ``normalize_title``: lowercase, unit standardization (``30 ml`` -> ``30ml``), punctuation
  and filler-word removal.
* Brand detection: user-confirmed brand list first, then ``by X`` / ``X |`` title patterns,
  then a first-token guess flagged as *inferred*. Multi-brand retailers seen in ``source``
  are never reported as brands (``source`` is the shop, not the maker).
* ``CanonicalIndex``: stable product keys across scans, matched in this order: Google
  ``product_id``, exact normalized title, brand + size + core words, RapidFuzz fallback.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.intelligence.common import ascii_fold, brand_key

try:  # RapidFuzz is a declared dependency; difflib keeps the module importable without it.
    from rapidfuzz import fuzz as _fuzz

    def _similarity(a: str, b: str) -> float:
        return float(_fuzz.token_sort_ratio(a, b))
except ImportError:  # pragma: no cover - exercised only when the dependency is missing
    from difflib import SequenceMatcher

    def _similarity(a: str, b: str) -> float:
        return 100.0 * SequenceMatcher(None, " ".join(sorted(a.split())), " ".join(sorted(b.split()))).ratio()


FUZZY_THRESHOLD = 90.0

_UNIT_CANONICAL = {
    "ml": "ml", "milliliter": "ml", "milliliters": "ml", "millilitre": "ml", "millilitres": "ml",
    "l": "l", "ltr": "l", "ltrs": "l", "liter": "l", "liters": "l", "litre": "l", "litres": "l",
    "g": "g", "gm": "g", "gms": "g", "gr": "g", "gram": "g", "grams": "g",
    "kg": "kg", "kgs": "kg", "kilogram": "kg", "kilograms": "kg",
    "mg": "mg",
    "oz": "oz", "ounce": "oz", "ounces": "oz",
    "floz": "floz", "fl oz": "floz", "fl. oz": "floz", "fl.oz": "floz",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "cm": "cm", "mm": "mm", "inch": "in", "inches": "in",
    "pc": "pc", "pcs": "pc", "piece": "pc", "pieces": "pc", "ct": "ct", "count": "ct",
    "gb": "gb", "tb": "tb", "mah": "mah",
}
_UNIT_PATTERN = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*("
    + "|".join(sorted((re.escape(unit) for unit in _UNIT_CANONICAL), key=len, reverse=True))
    + r")(?![a-z])",
    re.IGNORECASE,
)
_PERCENT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_SIZE_TOKEN = re.compile(r"^\d+(?:\.\d+)?(?:" + "|".join(sorted(set(_UNIT_CANONICAL.values()), key=len, reverse=True)) + r")$")

# Generic e-commerce filler. Nothing category-specific belongs here.
FILLER_WORDS = frozenset({
    "a", "an", "the", "and", "or", "for", "with", "of", "in", "on", "to", "from", "by", "at",
    "new", "best", "buy", "online", "sale", "free", "shipping", "delivery", "original", "genuine",
    "authentic", "official", "latest", "premium", "quality", "top", "rated", "item", "product",
})

# Leading words that are never a brand on their own.
NON_BRAND_WORDS = FILLER_WORDS | frozenset({
    "pack", "set", "combo", "kit", "bundle", "box", "case", "pair", "pairs", "lot", "refill",
    "men", "mens", "women", "womens", "ladies", "kids", "kid", "boys", "girls", "baby", "unisex", "adult",
    "organic", "natural", "pure", "professional", "classic", "daily", "ultra", "super", "mini",
    "large", "small", "medium", "extra", "multi", "value", "family", "travel", "sample", "trial",
    "deluxe", "limited", "edition", "hot", "cool", "smart", "portable", "wireless", "brand", "generic",
    "unbranded", "assorted", "various", "other", "this", "our", "my", "your", "all", "one",
    "get", "shop", "try", "use", "made", "make", "only", "just", "now", "special", "exclusive",
    # Condition and generic descriptors that open many titles ("Refurbished ...", "Open ear ...").
    "refurbished", "renewed", "used", "open", "bluetooth", "waterproof", "rechargeable", "electric",
    "digital", "gaming", "sport", "sports", "outdoor", "foldable", "adjustable", "lightweight",
    "upgraded", "newest", "latest", "heavy", "duty", "stainless", "steel", "led",
})

_DOMAIN_SUFFIX = re.compile(r"\.(com|in|co|net|org|shop|store|uk|us|au|ca|de|fr|io)(\.[a-z]{2})?$")


def standardize_units(text: str) -> str:
    """``30 ml`` -> ``30ml``, ``1.7 fl. oz`` -> ``1.7floz``, ``10 %`` -> ``10pct``."""
    text = _PERCENT_PATTERN.sub(lambda m: f"{m.group(1)}pct", text)
    return _UNIT_PATTERN.sub(lambda m: f"{m.group(1)}{_UNIT_CANONICAL[m.group(2).lower()]}", text)


_UNIT_ALT = "|".join(sorted((re.escape(unit) for unit in _UNIT_CANONICAL), key=len, reverse=True))
_NUM = r"\d+(?:\.\d+)?"
# "2 x 30 ml", "2x30ml"
_MULTI_BEFORE = re.compile(rf"(?<![\w.])(\d+)\s*[x\u00d7]\s*({_NUM})\s*({_UNIT_ALT})(?![a-z])", re.IGNORECASE)
# "30 ml x 2"
_MULTI_AFTER = re.compile(rf"(?<![\w.])({_NUM})\s*({_UNIT_ALT})\s*[x\u00d7]\s*(\d+)(?![\w.])", re.IGNORECASE)
# "pack of 3", "set of 2"
_PACK_OF = re.compile(r"\b(?:pack|pk|set|box|bundle|combo|lot)\s+of\s+(\d+)\b", re.IGNORECASE)
# "3 pack", "3-pack", "3 pk"
_N_PACK = re.compile(r"(?<![\w.])(\d+)\s*-?\s*(?:pack|pk)\b", re.IGNORECASE)
_METRIC_BASE = {"l": ("ml", 1000.0), "kg": ("g", 1000.0), "mg": ("g", 0.001)}
_METRIC_TOKEN = re.compile(rf"^({_NUM})(l|kg|mg)$")


def _pack_token(count: str) -> str:
    return "" if int(count) <= 1 else f" pack{int(count)} "


def _rewrite_packs(text: str) -> str:
    """Make pack expressions comparable: ``2 x 30 ml``, ``30ml x 2`` and ``30 ml pack of 2`` agree.

    The count becomes a ``packN`` token (a pack of one is just the product, so it adds none).
    """
    text = _MULTI_BEFORE.sub(lambda m: f" {m.group(2)} {m.group(3)}{_pack_token(m.group(1))}", text)
    text = _MULTI_AFTER.sub(lambda m: f" {m.group(1)} {m.group(2)}{_pack_token(m.group(3))}", text)
    text = _PACK_OF.sub(lambda m: _pack_token(m.group(1)), text)
    return _N_PACK.sub(lambda m: _pack_token(m.group(1)), text)


def _format_number(value: float) -> str:
    return format(round(value, 3), "f").rstrip("0").rstrip(".")


def to_base_unit(value: float, unit: str) -> tuple[float, str]:
    """``1.5 l`` -> ``(1500.0, "ml")``, ``0.5 kg`` -> ``(500.0, "g")``; other units are unchanged."""
    base, factor = _METRIC_BASE.get(unit, (unit, 1.0))
    return value * factor, base


def _canonical_measure(token: str) -> str:
    match = _METRIC_TOKEN.match(token)
    if not match:
        return token
    value, unit = to_base_unit(float(match.group(1)), match.group(2))
    return f"{_format_number(value)}{unit}"


def _tokens(text: str, *, canonical: bool = False) -> list[str]:
    text = ascii_fold(text).lower().replace("&", " and ")
    if canonical:
        text = _rewrite_packs(text)
    text = standardize_units(text)
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)  # keep decimal points only
    text = re.sub(r"[^a-z0-9.\s]", " ", text)
    tokens = text.split()
    return [_canonical_measure(token) for token in tokens] if canonical else tokens


def normalize_title(title: str) -> str:
    return " ".join(token for token in _tokens(title or "", canonical=True) if token not in FILLER_WORDS)


def extract_size(title: str) -> str | None:
    for token in _tokens(title or ""):
        if _SIZE_TOKEN.match(token):
            return token
    return None


@dataclass(frozen=True)
class PackInfo:
    """Size and multipack count parsed from a title. Sizes are in base units (ml, g, ...)."""

    count: int = 1
    size: float | None = None
    unit: str | None = None

    @property
    def total(self) -> float | None:
        return None if self.size is None else round(self.size * self.count, 3)


def parse_pack(title: str) -> PackInfo:
    """``2 x 30 ml`` -> count 2, 30 ml; ``Cleanser 1 L`` -> 1000 ml; ``pack of 3`` -> count 3."""
    folded = ascii_fold(title or "").lower().replace("&", " and ")
    count = 1
    for pattern, group in ((_MULTI_BEFORE, 1), (_MULTI_AFTER, 3), (_PACK_OF, 1), (_N_PACK, 1)):
        match = pattern.search(folded)
        if match:
            count = max(count, int(match.group(group)))
    size: float | None = None
    unit: str | None = None
    for token in _tokens(title or "", canonical=True):
        match = re.match(rf"^({_NUM})([a-z]+)$", token)
        if match and match.group(2) in set(_UNIT_CANONICAL.values()) - {"pc", "ct", "gb", "tb", "mah", "cm", "mm", "in"}:
            size, unit = float(match.group(1)), match.group(2)
            break
    return PackInfo(count=count, size=size, unit=unit)


def numeric_tokens(normalized_title: str) -> frozenset[str]:
    return frozenset(token for token in normalized_title.split() if any(ch.isdigit() for ch in token))


def source_key(source: str | None) -> str:
    """Retailer identity: ``Purplle.com - Purplle Shop`` -> ``purplle``, ``Amazon.in`` -> ``amazon``."""
    if not source:
        return ""
    text = ascii_fold(source).lower().strip()
    text = re.split(r"\s+[-|:]\s+", text)[0]
    text = re.sub(r"^www\.", "", text)
    text = _DOMAIN_SUFFIX.sub("", text)
    return re.sub(r"[^a-z0-9]", "", text)


def _display_name(raw: str) -> str:
    raw = raw.strip(" ,;:|-_/()[]{}\"'")
    if raw.isupper() and len(raw) > 3:
        return raw.title()
    if raw.islower():
        return raw[:1].upper() + raw[1:]
    return raw


def _looks_like_brand_word(word: str) -> bool:
    cleaned = brand_key(word)
    return bool(cleaned) and len(cleaned) >= 2 and not any(ch.isdigit() for ch in cleaned) and cleaned not in NON_BRAND_WORDS


@dataclass(frozen=True)
class BrandMatch:
    name: str
    method: str  # "known" | "pattern" | "first_token" | "seller"

    @property
    def key(self) -> str:
        return brand_key(self.name)

    @property
    def inferred(self) -> bool:
        return self.method in {"first_token", "seller"}


# "... Waterproof Hiking Boot By Trailco": only trusted at the very end of a title, so
# phrases such as "Powered by Advanced Grip Technology | ..." are not mistaken for brands.
_BY_PATTERN = re.compile(r"\b[Bb][Yy]\s+([A-Z][\w'&.]*(?:\s+[A-Z][\w'&.]*){0,2})\s*[)\]]?\s*$")
_PREFIX_TITLES = frozenset({"dr", "mr", "mrs", "ms", "st"})
_COMPANY_SUFFIXES = frozenset({"co", "company", "inc", "ltd", "llc", "labs", "lab", "gmbh"})


def _match_known(title: str, known: Sequence[str]) -> str | None:
    title_tokens = [brand_key(token) for token in ascii_fold(title).replace("&", " and ").split()]
    title_tokens = [token for token in title_tokens if token]
    best: tuple[int, int, str] | None = None  # (position, -length, name)
    for name in known:
        parts = [brand_key(token) for token in ascii_fold(name).replace("&", " and ").split()]
        parts = [part for part in parts if part]
        if not parts:
            continue
        for start in range(len(title_tokens) - len(parts) + 1):
            if title_tokens[start:start + len(parts)] == parts:
                candidate = (start, -len(parts), name)
                if best is None or candidate < best:
                    best = candidate
                break
    return best[2] if best else None


def detect_brand(title: str, known_brands: Sequence[str] = ()) -> BrandMatch | None:
    """Brand for one title, without retailer filtering (see ``detect_brands``)."""
    title = (title or "").strip()
    if not title:
        return None
    known = _match_known(title, known_brands)
    if known:
        return BrandMatch(known, "known")

    match = _BY_PATTERN.search(title)
    if match and _looks_like_brand_word(match.group(1).split()[0]):
        return BrandMatch(_display_name(match.group(1)), "pattern")

    if "|" in title:
        head = title.split("|", 1)[0].strip()
        words = head.split()
        if 1 <= len(words) <= 3 and all(_looks_like_brand_word(word) for word in words):
            return BrandMatch(_display_name(head), "pattern")

    guess = _first_token_guess(title)
    return BrandMatch(guess, "first_token") if guess else None


def _first_token_guess(title: str) -> str | None:
    words = [word for word in re.split(r"\s+", title) if word.strip(" ,;:|-_/()[]")]
    if not words:
        return None
    picked: list[str] = []
    index = 0
    # "The X" and "Dr. X" are one name; the leading word alone is never a brand.
    if brand_key(words[0]) in {"the"} | _PREFIX_TITLES and len(words) > 1:
        picked.append(words[0])
        index = 1
    if not _looks_like_brand_word(words[index]):
        return None
    picked.append(words[index])
    index += 1
    # "Black & Decker": an ampersand joins two capitalized words into one name.
    if index + 1 < len(words) and words[index] in {"&", "+"} and words[index + 1][:1].isupper():
        picked.extend(words[index:index + 2])
        index += 2
    # "The Acme Co", "Acme Labs".
    if index < len(words) and brand_key(words[index]) in _COMPANY_SUFFIXES:
        picked.append(words[index])
    name = " ".join(picked)
    return _display_name(name) if brand_key(name) else None


def retailer_keys(products: Sequence[dict[str, Any]], matches: Sequence[BrandMatch | None]) -> set[str]:
    """Sources that sell several brands (or look like a web shop) are retailers, not brands."""
    brands_per_source: dict[str, set[str]] = defaultdict(set)
    retailers: set[str] = set()
    for product, match in zip(products, matches, strict=True):
        key = source_key(product.get("source"))
        if not key:
            continue
        raw_source = (product.get("source") or "").lower()
        if _DOMAIN_SUFFIX.search(re.split(r"\s+[-|:]\s+", raw_source)[0].strip()):
            retailers.add(key)
        if match:
            brands_per_source[key].add(match.key)
    retailers.update(key for key, brands in brands_per_source.items() if len(brands) >= 2)
    return retailers


_SELLER_SPLIT = re.compile(r"\s+[-|:]\s+")


def _leet(text: str) -> str:
    """Obfuscated spellings: ``Veat00l`` and ``VEATOOL`` compare equal."""
    return brand_key(text).replace("0", "o")


def _seller_brand(product: dict[str, Any]) -> BrandMatch | None:
    """``Retailer - Seller`` sources: the seller store is the brand only when the title itself starts with
    that name (ignoring 0/o). A reseller whose name never appears in the title stays unbranded."""
    parts = _SELLER_SPLIT.split(product.get("source") or "", maxsplit=1)
    if len(parts) < 2:
        return None
    seller, seller_key = parts[1].strip(), _leet(parts[1])
    if len(seller_key) < 4:
        return None
    lead = _leet(" ".join((product.get("title") or "").split()[:3]))
    return BrandMatch(_display_name(seller), "seller") if seller_key in lead else None


def preferred_names(rows: Iterable[dict[str, Any]]) -> dict[str, str]:
    """Most frequent spelling of each brand in earlier observations, so a name keeps its look across scans."""
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        if row.get("brand"):
            counts[brand_key(row["brand"])][row["brand"]] += 1
    return {key: names.most_common(1)[0][0] for key, names in counts.items()}


def _canonical_names(
    matches: list[BrandMatch | None], known_brands: Sequence[str], preferred: dict[str, str]
) -> list[BrandMatch | None]:
    """One display spelling per brand: your own list wins, then what earlier scans used, then the most common
    spelling in this batch (so ``JLab`` x14 beats ``Jlab`` x6 everywhere)."""
    known = {brand_key(name): name for name in known_brands}
    seen: dict[str, Counter[str]] = defaultdict(Counter)
    for match in matches:
        if match:
            seen[match.key][match.name] += 1

    def choose(key: str) -> str:
        if key in known:
            return known[key]
        if key in preferred:
            return preferred[key]
        # Most common; on a tie prefer the spelling with internal capitals ("JLab" over "Jlab").
        return max(seen[key].items(), key=lambda item: (item[1], sum(ch.isupper() for ch in item[0][1:])))[0]

    chosen = {key: choose(key) for key in seen}
    return [BrandMatch(chosen[match.key], match.method) if match else None for match in matches]


def detect_brands(
    products: Sequence[dict[str, Any]], known_brands: Sequence[str] = (), preferred: dict[str, str] | None = None
) -> list[BrandMatch | None]:
    """Brand per product. Guesses that are really a multi-brand retailer become ``None``; titles with no brand
    can still get one from a ``Retailer - Seller`` source (flagged inferred)."""
    matches = [detect_brand(product.get("title", ""), known_brands) for product in products]
    retailers = retailer_keys(products, matches)
    kept = [
        None if match and match.method != "known" and match.key in retailers else match
        for match in matches
    ]
    filled = [match or _seller_brand(product) for match, product in zip(kept, products, strict=True)]
    return _canonical_names(filled, known_brands, preferred or {})


def brand_candidates(
    products: Sequence[dict[str, Any]],
    known_brands: Sequence[str] = (),
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Frequency-ranked brand suggestions: ``[{name, count, inferred}]``.

    A heuristic guess (title pattern or first token) is reported as not inferred only when
    it is corroborated: seen at two or more retailers, or in three or more listings.
    Everything else stays flagged for the user to confirm.
    """
    matches = detect_brands(products, known_brands)
    counts: Counter[str] = Counter()
    names: dict[str, Counter[str]] = defaultdict(Counter)
    methods: dict[str, set[str]] = defaultdict(set)
    sources: dict[str, set[str]] = defaultdict(set)
    for product, match in zip(products, matches, strict=True):
        if not match:
            continue
        counts[match.key] += 1
        names[match.key][match.name] += 1
        methods[match.key].add(match.method)
        sources[match.key].add(source_key(product.get("source")))
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    candidates: list[dict[str, Any]] = []
    for key, count in ranked:
        corroborated = len(sources[key] - {""}) >= 2 or count >= 3
        inferred = "known" not in methods[key] and not corroborated
        candidates.append({"name": names[key].most_common(1)[0][0], "count": count, "inferred": inferred})
    # Confirmed brands first, then guesses; both by frequency.
    candidates.sort(key=lambda c: (c["inferred"], -c["count"], c["name"].lower()))
    return candidates[:limit]


def _title_signature(normalized: str, brand: str | None) -> str | None:
    bkey = brand_key(brand)
    if not bkey:
        return None
    brand_tokens = set(normalized_brand_tokens(brand))
    words: list[str] = []
    for token in normalized.split():
        if token not in brand_tokens and not any(ch.isdigit() for ch in token) and token not in words:
            words.append(token)
    if len(words) < 2:
        return None
    # Sizes, percentages and model numbers must all match; the first three core words
    # identify the product line regardless of word order or trailing marketing text.
    numbers = sorted(numeric_tokens(normalized))
    return f"{bkey}|{','.join(numbers)}|{' '.join(sorted(words[:3]))}"


def normalized_brand_tokens(brand: str | None) -> list[str]:
    return normalize_title(brand or "").split()


@dataclass
class CanonicalIndex:
    """Assigns stable canonical keys to product listings across scans."""

    by_product_id: dict[str, str] = field(default_factory=dict)
    by_title: dict[str, str] = field(default_factory=dict)
    by_signature: dict[str, str] = field(default_factory=dict)
    by_brand: dict[str, list[tuple[str, frozenset[str], str]]] = field(default_factory=lambda: defaultdict(list))

    @classmethod
    def from_history(cls, rows: Iterable[dict[str, Any]]) -> CanonicalIndex:
        index = cls()
        for row in rows:
            if row.get("canonical_key"):
                index.add(row.get("product_id"), row.get("title", ""), row.get("brand"), row["canonical_key"])
        return index

    def add(self, product_id: str | None, title: str, brand: str | None, key: str) -> None:
        normalized = normalize_title(title)
        if product_id:
            self.by_product_id.setdefault(str(product_id), key)
        if normalized:
            self.by_title.setdefault(normalized, key)
        signature = _title_signature(normalized, brand)
        if signature:
            self.by_signature.setdefault(signature, key)
        bkey = brand_key(brand)
        if bkey and normalized:
            self.by_brand[bkey].append((normalized, numeric_tokens(normalized), key))

    def match(self, product_id: str | None, title: str, brand: str | None) -> tuple[str | None, str]:
        """Return (existing key or None, matching rule)."""
        if product_id and str(product_id) in self.by_product_id:
            return self.by_product_id[str(product_id)], "product_id"
        normalized = normalize_title(title)
        if normalized in self.by_title:
            return self.by_title[normalized], "title"
        signature = _title_signature(normalized, brand)
        if signature and signature in self.by_signature:
            return self.by_signature[signature], "signature"
        bkey = brand_key(brand)
        if bkey and normalized:
            numbers = numeric_tokens(normalized)
            best_score, best_key = 0.0, None
            for other, other_numbers, key in self.by_brand.get(bkey, []):
                if other_numbers != numbers:
                    continue  # "10%" vs "15%" or "30ml" vs "50ml" are different products
                score = _similarity(normalized, other)
                if score >= FUZZY_THRESHOLD and score > best_score:
                    best_score, best_key = score, key
            if best_key:
                return best_key, "fuzzy"
        return None, "new"

    def assign(self, product_id: str | None, title: str, brand: str | None) -> str:
        key, _ = self.match(product_id, title, brand)
        if key is None:
            if product_id:
                key = f"pid:{product_id}"
            else:
                digest = hashlib.sha1(normalize_title(title).encode("utf-8")).hexdigest()[:16]
                key = f"t:{digest}"
        self.add(product_id, title, brand, key)
        return key


def clean_product_id(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def normalize_products(
    products: Sequence[dict[str, Any]],
    known_brands: Sequence[str] = (),
    history: Iterable[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Return copies of ``products`` with ``brand``, ``brand_inferred`` and ``canonical_key`` set."""
    history = list(history)
    index = CanonicalIndex.from_history(history)
    matches = detect_brands(products, known_brands, preferred_names(history))
    normalized = []
    for product, match in zip(products, matches, strict=True):
        item = dict(product)
        item["product_id"] = clean_product_id(product.get("product_id"))
        item["brand"] = match.name if match else None
        item["brand_inferred"] = bool(match and match.inferred)
        item["canonical_key"] = index.assign(item["product_id"], product.get("title", ""), item["brand"])
        normalized.append(item)
    return normalized


def offer_key(observation: dict[str, Any]) -> str:
    """Same-offer identity for price comparisons: catalog product *and* retailer must match."""
    product = clean_product_id(observation.get("product_id")) or observation.get("canonical_key") or ""
    return f"{product}|{source_key(observation.get('source'))}"
