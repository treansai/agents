import hashlib
import hmac
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    run_id: str
    agent: str
    action: str
    payload: dict[str, Any]
    previous_hash: str = ""
    signature: str = ""


class SignedLedger:
    """SQLite append-only hash chain with HMAC signatures for tamper evidence."""

    def __init__(self, database_path: Path, secret: str) -> None:
        self.database_path = database_path
        self.secret = secret.encode()
        self._lock = threading.Lock()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    timestamp TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    action TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    signature TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_run_id ON audit_events(run_id, sequence)"
            )

    def append(
        self, *, run_id: str, agent: str, action: str, payload: dict[str, Any]
    ) -> AuditEvent:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT signature FROM audit_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous_hash = previous["signature"] if previous else "GENESIS"
            unsigned = AuditEvent(
                run_id=run_id,
                agent=agent,
                action=action,
                payload=payload,
                previous_hash=previous_hash,
            )
            canonical = self._canonical(unsigned)
            signature = hmac.new(self.secret, canonical, hashlib.sha256).hexdigest()
            event = unsigned.model_copy(update={"signature": signature})
            connection.execute(
                """
                INSERT INTO audit_events
                (event_id, timestamp, run_id, agent, action, payload, previous_hash, signature)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.timestamp.isoformat(),
                    event.run_id,
                    event.agent,
                    event.action,
                    json.dumps(event.payload, sort_keys=True, separators=(",", ":")),
                    event.previous_hash,
                    event.signature,
                ),
            )
            connection.commit()
            return event

    def list_run(self, run_id: str) -> list[AuditEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE run_id = ? ORDER BY sequence", (run_id,)
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def verify(self) -> bool:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM audit_events ORDER BY sequence").fetchall()
        previous_hash = "GENESIS"
        for row in rows:
            event = self._from_row(row)
            if event.previous_hash != previous_hash:
                return False
            unsigned = event.model_copy(update={"signature": ""})
            expected = hmac.new(self.secret, self._canonical(unsigned), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(event.signature, expected):
                return False
            previous_hash = event.signature
        return True

    @staticmethod
    def _canonical(event: AuditEvent) -> bytes:
        data = event.model_dump(mode="json", exclude={"signature"})
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> AuditEvent:
        return AuditEvent(
            event_id=row["event_id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            run_id=row["run_id"],
            agent=row["agent"],
            action=row["action"],
            payload=json.loads(row["payload"]),
            previous_hash=row["previous_hash"],
            signature=row["signature"],
        )
