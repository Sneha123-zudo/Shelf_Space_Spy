from app.services.normalize import (
    CanonicalIndex,
    brand_candidates,
    detect_brand,
    detect_brands,
    extract_size,
    normalize_products,
    normalize_title,
    offer_key,
    source_key,
    standardize_units,
)


def test_unit_standardization_and_title_normalization():
    assert standardize_units("Bottle 30 ml") == "Bottle 30ml"
    assert standardize_units("1.7 fl. oz") == "1.7floz"
    assert standardize_units("500 Grams pack") == "500g pack"
    assert standardize_units("10 %") == "10pct"
    assert normalize_title("The NEW Trail Shoe, 2 Pack - 30 ML!") == "trail shoe pack2 30ml"
    assert normalize_title("Widget & Gadget for Kids") == "widget gadget kids"


def test_extract_size():
    assert extract_size("Hydration Pack 1.5 L Blue") == "1.5l"
    assert extract_size("Protein Powder 2 lbs") == "2lb"
    assert extract_size("Running shoe") is None


def test_source_key_strips_domains_and_sub_shops():
    assert source_key("Amazon.com") == "amazon"
    assert source_key("amazon.in") == "amazon"
    assert source_key("Purplle.com - Purplle Shop") == "purplle"
    assert source_key("Dick's Sporting Goods") == "dickssportinggoods"
    assert source_key(None) == ""


def test_known_brand_wins_and_matches_multiword_names():
    match = detect_brand("Dot & Key Strawberry Dew Gel 50ml", ["Dot & Key", "Key"])
    assert match.name == "Dot & Key" and match.method == "known" and not match.inferred
    # earliest occurrence wins when several known brands appear
    assert detect_brand("Hoka vs Nike comparison", ["Nike", "Hoka"]).name == "Hoka"


def test_pattern_and_first_token_detection():
    assert detect_brand("Daily Fluid Gel | All Types | Tested By WishCare").name == "WishCare"
    assert detect_brand("Daily Fluid Gel | All Types | Tested By WishCare").method == "pattern"
    # "by X" in the middle of a title is marketing copy, not a brand
    assert detect_brand("Acme Serum, Powered by Deep Penetration Formula | 20 ml").name == "Acme"
    assert detect_brand("Trailblazer | Waterproof Hiking Boot").name == "Trailblazer"
    guess = detect_brand("Brooks Ghost 16 Running Shoes")
    assert guess.name == "Brooks" and guess.inferred
    assert detect_brand("The Derma Co 1% Gel").name == "The Derma Co"
    assert detect_brand("Dot & Key Strawberry").name == "Dot & Key"
    assert detect_brand("10% Off Running Shoes") is None
    assert detect_brand("New Lightweight Sneakers") is None
    assert detect_brand("") is None


def test_multi_brand_retailer_is_not_a_brand_but_brand_store_is():
    products = [
        {"title": "Amazon Basics Yoga Mat", "source": "Amazon.com"},
        {"title": "Gaiam Yoga Mat 6mm", "source": "Amazon.com"},
        {"title": "Manduka PRO Yoga Mat", "source": "Manduka"},
        {"title": "Manduka eKO Lite Mat", "source": "REI"},
    ]
    matches = detect_brands(products)
    assert matches[0] is None  # "Amazon" is the retailer selling several brands
    assert matches[1].name == "Gaiam"
    assert matches[2].name == "Manduka"  # single-brand store keeps its brand


def test_brand_candidates_counts_and_inferred_flags():
    products = [
        {"title": "Brooks Ghost 16", "source": "REI"},
        {"title": "Brooks Glycerin 21", "source": "Zappos"},
        {"title": "Zorbo Trail Runner", "source": "Walmart"},
        {"title": "Hoka Clifton 9", "source": "REI"},
    ]
    candidates = brand_candidates(products, known_brands=["Hoka"])
    by_name = {c["name"]: c for c in candidates}
    assert by_name["Brooks"] == {"name": "Brooks", "count": 2, "inferred": False}  # seen at 2 retailers
    assert by_name["Zorbo"]["inferred"] is True  # single sighting, first-token guess
    assert by_name["Hoka"]["inferred"] is False  # user-confirmed
    assert candidates[-1]["name"] == "Zorbo"  # guesses rank after confirmed brands


def test_canonical_index_matching_order():
    index = CanonicalIndex()
    first = index.assign("111", "Brooks Ghost 16 Running Shoe 10.5", "Brooks")
    assert first == "pid:111"
    # 1. same product_id
    assert index.assign("111", "Completely different title", "Brooks") == first
    # 2. exact normalized title (punctuation/case/filler differences only)
    assert index.assign("222", "BROOKS Ghost 16 - Running Shoe, 10.5", "Brooks") == first
    # 3. brand + size + core words, different word order
    sig = index.assign("333", "Hydro Flask Bottle Wide Mouth 32 oz", "Hydro Flask")
    assert index.assign("444", "Hydro Flask Wide Mouth Bottle 32 oz Black Edition", "Hydro Flask") == sig
    # 4. fuzzy fallback within the same brand
    fuzzy = index.assign("555", "Garmin Forerunner 265 GPS Running Smartwatch Black", "Garmin")
    assert index.assign("556", "Garmin Forerunner 265 GPS Running Smart watch Black", "Garmin") == fuzzy
    # different numbers are different products even when titles are close
    assert index.assign("666", "Garmin Forerunner 965 GPS Running Smartwatch Black", "Garmin") != fuzzy
    # no product id: stable hash key
    assert index.assign(None, "Generic Water Bottle", None).startswith("t:")


def test_normalize_products_keeps_keys_stable_across_scans():
    scan1 = normalize_products(
        [{"title": "Hoka Clifton 9 Running Shoe", "source": "REI", "product_id": "9"}], ["Hoka"]
    )
    scan2 = normalize_products(
        [{"title": "HOKA Clifton 9 running shoe", "source": "Zappos", "product_id": "10"}], ["Hoka"], history=scan1
    )
    assert scan1[0]["brand"] == "Hoka" and scan1[0]["brand_inferred"] is False
    assert scan2[0]["canonical_key"] == scan1[0]["canonical_key"]


def test_offer_key_requires_same_retailer():
    a = {"product_id": "1", "source": "REI"}
    b = {"product_id": "1", "source": "Zappos"}
    assert offer_key(a) != offer_key(b)
    assert offer_key(a) == offer_key({"product_id": "1", "source": "rei"})


def test_pack_expressions_normalize_to_the_same_tokens():
    from app.services.normalize import normalize_title

    a = normalize_title("Acme Serum 2 x 30 ml")
    b = normalize_title("Acme Serum 30ml x 2")
    c = normalize_title("Acme Serum 30 ml pack of 2")
    assert a == b == c and "pack2" in a.split()
    assert normalize_title("Acme Serum 30 ml pack of 1") == normalize_title("Acme Serum 30 ml")


def test_different_pack_sizes_are_not_merged():
    from app.services.normalize import CanonicalIndex

    index = CanonicalIndex()
    keys = {
        index.assign(None, "Acme Hydrating Serum 30 ml", "Acme"),
        index.assign(None, "Acme Hydrating Serum 50 ml", "Acme"),
        index.assign(None, "Acme Hydrating Serum 2 x 30 ml", "Acme"),
        index.assign(None, "Acme Hydrating Serum 30 ml pack of 3", "Acme"),
    }
    assert len(keys) == 4
    assert index.assign(None, "Acme Hydrating Serum 30ml", "Acme") == index.assign(None, "ACME hydrating serum 30 ML", "Acme")


def test_metric_units_are_converted_before_comparison():
    from app.services.normalize import CanonicalIndex, normalize_title, parse_pack

    assert "1000ml" in normalize_title("Acme Cleanser 1 L").split()
    assert "500g" in normalize_title("Acme Oats 0.5 kg").split()
    index = CanonicalIndex()
    assert index.assign(None, "Acme Cleanser 1 L", "Acme") == index.assign(None, "Acme Cleanser 1000 ml", "Acme")
    assert parse_pack("Acme Cleanser 1 L").size == 1000.0


def test_parse_pack_reports_count_size_and_total():
    from app.services.normalize import parse_pack

    info = parse_pack("Acme Serum 2 x 15 ml")
    assert (info.count, info.size, info.unit, info.total) == (2, 15.0, "ml", 30.0)
    assert parse_pack("Acme Bar 3-pack 50 g").count == 3
    plain = parse_pack("Running shoe")
    assert (plain.count, plain.size, plain.unit, plain.total) == (1, None, None, None)


# --------------------------------------------------------------------------- brand rules (generic)


def _brands(titles, known=(), sources=None, preferred=None):
    from app.services.normalize import detect_brands

    products = [{"title": t, "source": (sources or {}).get(t, "Shop")} for t in titles]
    return [m.name if m else None for m in detect_brands(products, known, preferred)]


def test_condition_and_descriptor_words_are_not_brands():
    names = _brands(["Refurbished Gadget Pro 2", "Open Box Gadget Mini", "Bluetooth Gadget Lite", "Waterproof Gadget Max", "Acme Gadget Plus"])
    assert names[:4] == [None, None, None, None]
    assert names[4] == "Acme"


def test_one_spelling_per_brand_most_common_wins_and_ties_prefer_internal_capitals():
    titles = [f"JLab Gadget {i}" for i in range(3)] + ["Jlab Gadget 9"]
    assert set(_brands(titles)) == {"JLab"}
    assert set(_brands(["Jlab Thing One", "JLab Thing Two"])) == {"JLab"}  # tie: internal capital wins


def test_a_known_brand_spelling_and_earlier_scans_beat_the_batch():
    assert set(_brands(["JBL Thing One", "Jbl Thing Two"], known=["JBL"])) == {"JBL"}
    assert set(_brands(["Jlab Thing One", "Jlab Thing Two"], preferred={"jlab": "JLab"})) == {"JLab"}


def test_seller_store_becomes_the_brand_only_when_the_title_starts_with_its_name():
    sources = {
        "Veat00l V6 Gadget Wireless": "Walmart - VEATOOL",
        "Generic Gadget For Phones": "Walmart - Deals999",
        "Deals999 Wow Gadget": "Walmart - Deals999",
        "Tiny Thing": "Walmart - X",
    }
    from app.services.normalize import detect_brands

    products = [{"title": t, "source": src} for t, src in sources.items()]
    matches = detect_brands(products)
    veat, generic, reseller_named, tiny = matches
    assert veat and veat.name.lower() == "veatool" and veat.method == "seller" and veat.inferred
    assert generic is None  # a reseller whose name is not in the title stays unbranded
    assert reseller_named and reseller_named.name == "Deals999"
    assert tiny is None or tiny.method != "seller"  # a seller name under 4 letters is too weak to trust


def test_seller_rule_never_overrides_a_detected_brand_and_normalize_products_flags_it():
    from app.services.normalize import normalize_products

    out = normalize_products(
        [
            {"title": "Acme Widget 30ml", "source": "Walmart - OtherShop", "product_id": "1"},
            {"title": "Veat00l Gadget", "source": "Walmart - VEATOOL", "product_id": "2"},
        ],
        known_brands=["Acme"],
    )
    assert out[0]["brand"] == "Acme" and out[0]["brand_inferred"] is False
    assert out[1]["brand"].lower() == "veatool" and out[1]["brand_inferred"] is True
