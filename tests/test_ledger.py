import sqlite3

from agenomic_agents.common.ledger import SignedLedger


def test_ledger_detects_tampering(ledger: SignedLedger) -> None:
    ledger.append(run_id="run-1", agent="test", action="started", payload={"value": 1})
    ledger.append(run_id="run-1", agent="test", action="finished", payload={"value": 2})
    assert ledger.verify()
    with sqlite3.connect(ledger.database_path) as connection:
        connection.execute(
            "UPDATE audit_events SET payload = ? WHERE action = ?", ("{}", "started")
        )
    assert not ledger.verify()
