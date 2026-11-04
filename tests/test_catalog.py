def test_catalog_loads_30_products_with_unique_ids(catalog):
    items = catalog.all()
    assert len(items) == 30
    assert len({p.id for p in items}) == 30
    assert all(p.price_cents > 0 for p in items)


def test_categories_and_merchants(catalog):
    assert set(catalog.categories()) == {"electronics", "stationery", "groceries", "home", "gaming"}
    assert "VoltMart" in catalog.merchants()


def test_search_finds_relevant_product_first(catalog):
    assert catalog.search("usb-c cable")[0].id == "usb-c-cable"
    assert catalog.search("notebook")[0].id == "a5-notebook"
    assert catalog.search("headphones")[0].id == "anc-headphones"


def test_search_respects_price_cap_and_empty_query(catalog):
    assert all(p.price_cents <= 1000 for p in catalog.search("cable charger", max_price_cents=1000))
    assert catalog.search("") == []
    assert catalog.search("zzzz-nothing") == []
