"""Shared fixtures. Every test runs against a freshly seeded demo database."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import seed_demo_db  # noqa: E402
from nl2sql.config import Settings, get_settings  # noqa: E402
from nl2sql.db import dispose_engines  # noqa: E402

# Small enough that the whole suite seeds in well under a second, large enough
# that grouping and joins have something to work on.
TEST_CUSTOMERS = 40
TEST_ORDERS = 300


@pytest.fixture(scope="session")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Seed a throwaway analytics database and directory once per session."""
    directory = tmp_path_factory.mktemp("db")
    options = seed_demo_db.Options(
        db_path=directory / "demo.db",
        directory_path=directory / "directory.db",
        seed=seed_demo_db.DEFAULT_SEED,
        customers=TEST_CUSTOMERS,
        orders=TEST_ORDERS,
        months=seed_demo_db.DEFAULT_MONTHS,
        end_date=date.fromisoformat(seed_demo_db.DEFAULT_END_DATE),
        force=True,
    )
    counts = seed_demo_db.seed(options)
    return {
        "db_path": options.db_path,
        "directory_path": options.directory_path,
        "counts": counts,
    }


@pytest.fixture(autouse=True)
def settings(seeded: dict, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Point the application at the seeded test databases."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{seeded['db_path'].as_posix()}")
    monkeypatch.setenv(
        "DIRECTORY_URL", f"sqlite+aiosqlite:///{seeded['directory_path'].as_posix()}"
    )
    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setenv("ALLOWED_ORIGINS", "")
    get_settings.cache_clear()
    resolved = get_settings()
    yield resolved
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
async def _close_engines():
    """Dispose cached engines between tests so the URL override takes effect."""
    yield
    await dispose_engines()
