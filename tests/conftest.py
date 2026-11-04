from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.catalog import Catalog
from app.ledger import Ledger

ROOT = Path(__file__).resolve().parents[1]

# Wednesday 4 Nov 2026, 12:00 IST (06:30 UTC): daytime, not quiet hours
NOON_IST = datetime(2026, 11, 4, 6, 30, tzinfo=timezone.utc)


@pytest.fixture
def catalog() -> Catalog:
    return Catalog.load(ROOT / "app" / "data" / "catalog.json")


@pytest.fixture
def ledger() -> Ledger:
    return Ledger(":memory:")


@pytest.fixture
def now() -> datetime:
    return NOON_IST
