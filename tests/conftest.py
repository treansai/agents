from pathlib import Path

import pytest

from agenomic_agents.common.config import Settings
from agenomic_agents.common.ledger import SignedLedger


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        env="test",
        api_key="test-api-key",
        ledger_hmac_key="test-ledger-key",
        database_path=tmp_path / "test.db",
    )


@pytest.fixture
def ledger(settings: Settings) -> SignedLedger:
    return SignedLedger(settings.database_path, settings.ledger_hmac_key.get_secret_value())
