"""Mock product catalogue (fictional merchants). Single source of truth for prices."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .models import Product


class Catalog:
    def __init__(self, products: list[Product]):
        self._by_id = {p.id: p for p in products}

    @classmethod
    def load(cls, path: str | Path) -> "Catalog":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([Product(**r) for r in raw])

    def get(self, product_id: str) -> Product | None:
        return self._by_id.get(product_id)

    def all(self) -> list[Product]:
        return list(self._by_id.values())

    def categories(self) -> list[str]:
        return sorted({p.category for p in self._by_id.values()})

    def merchants(self) -> list[str]:
        return sorted({p.merchant for p in self._by_id.values()})

    def search(self, query: str, limit: int = 5, max_price_cents: int | None = None) -> list[Product]:
        """Simple keyword scoring over name, tags, category. Deterministic."""
        words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 1]
        if not words:
            return []
        scored: list[tuple[int, int, Product]] = []
        for p in self._by_id.values():
            if max_price_cents is not None and p.price_cents > max_price_cents:
                continue
            hay_name = p.name.lower()
            hay_tags = " ".join(p.tags + [p.category, p.id.replace("-", " ")])
            score = 0
            for w in words:
                stem = w.rstrip("s") if len(w) > 3 else w
                if stem in hay_name:
                    score += 3
                if stem in hay_tags:
                    score += 2
            if score:
                scored.append((score, -p.price_cents, p))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [p for _, _, p in scored[:limit]]
