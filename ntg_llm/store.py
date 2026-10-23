"""Transactional persistent rotation, cooldowns and deployment access state."""

import json
import math
import sqlite3
import time
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from .defaults import ENFORCEMENT_REASONS, LEGACY_REPLACEMENTS
from .errors import RoutingError


class RotationStore:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = str(path), clock
        with self.connect() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS cooldown(bucket TEXT PRIMARY KEY, until REAL);
              CREATE TABLE IF NOT EXISTS unavailable(deployment TEXT PRIMARY KEY, reason TEXT, detected REAL);
              CREATE TABLE IF NOT EXISTS access_denials(deployment TEXT PRIMARY KEY, diagnostics TEXT, detected REAL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            yield db
        finally:
            db.close()

    def select(self, deployments, excluded=(), only=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Timestamp after acquiring the write lock, not before lock contention.
            now = self.clock()
            blocked = db.execute("SELECT v FROM state WHERE k='blocked'").fetchone()
            if blocked:
                diagnostics = json.loads(blocked[0])
                diagnostics.setdefault(
                    "access_error",
                    {
                        "reason": "PERMISSION_DENIED",
                        "explanation": "The earlier provider reason was not retained by the old code.",
                        "action": "Inspect this credential in the provider console or explicitly probe this same blocked deployment.",
                    },
                )
                raise RoutingError(
                    403, "Provider access denied; routing remains stopped", diagnostics
                )
            row = db.execute("SELECT v FROM state WHERE k='cursor'").fetchone()
            cursor = json.loads(row[0]) if row else None
            ids = [d.id for d in deployments]
            if cursor is not None and cursor not in ids:
                old_parts = json.loads(cursor)
                if len(old_parts) == 2:
                    scope, old_model = old_parts
                    migrated = json.dumps(
                        [scope, LEGACY_REPLACEMENTS.get(old_model, old_model)],
                        separators=(",", ":"),
                    )
                    if migrated in ids:
                        cursor = migrated
            start = (ids.index(cursor) + 1) % len(ids) if cursor in ids else 0
            waits = []
            unavailable = []
            for offset in range(len(ids)):
                d = deployments[(start + offset) % len(ids)]
                if d.id in excluded or (only is not None and d.id != only):
                    continue
                if db.execute("SELECT 1 FROM unavailable WHERE deployment=?", (d.id,)).fetchone():
                    unavailable.append(d.diagnostics)
                    continue
                cooldown = db.execute(
                    "SELECT until FROM cooldown WHERE bucket=?", (d.id,)
                ).fetchone()
                if cooldown and cooldown[0] > now:
                    waits.append(cooldown[0] - now)
                    continue
                if only is None:
                    db.execute(
                        "INSERT OR REPLACE INTO state VALUES('cursor',?)", (json.dumps(d.id),)
                    )
                db.commit()
                return d
            db.rollback()
        if not waits:
            if unavailable:
                raise RoutingError(
                    503,
                    "No eligible deployment: unavailable or denied access",
                    {"unavailable_deployments": unavailable},
                )
            raise RoutingError(503, "No deployment is available for selection")
        raise RoutingError(
            429,
            "Provider Retry-After is still active for every eligible deployment",
            {"deployments": [d.diagnostics for d in deployments]},
            min(waits),
        )

    def mark_unavailable(self, d):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO unavailable VALUES(?,?,?)",
                (d.id, "provider_model_unavailable", self.clock()),
            )

    def migrate_permission_stop(self, deployments):
        """Convert the old global permission stop to a deployment-scoped denial."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT v FROM state WHERE k='blocked'").fetchone()
            if row:
                diagnostics = json.loads(row[0])
                reason = diagnostics.get("access_error", {}).get("reason")
                match = next(
                    (
                        d
                        for d in deployments
                        if all(
                            diagnostics.get(k) == d.diagnostics.get(k)
                            for k in ("provider", "key_label", "project_id", "selected_model")
                        )
                    ),
                    None,
                )
                if match is not None and reason not in ENFORCEMENT_REASONS:
                    db.execute(
                        "INSERT OR REPLACE INTO unavailable VALUES(?,?,?)",
                        (match.id, "provider_access_denied", self.clock()),
                    )
                    db.execute(
                        "INSERT OR REPLACE INTO access_denials VALUES(?,?,?)",
                        (match.id, row[0], self.clock()),
                    )
                    db.execute("DELETE FROM state WHERE k='blocked'")
            db.commit()

    def failure(self, d, status, retry_after, diagnostics=None, stop_all=True):
        if status == 429 and retry_after is None:
            retry_after = 30
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if status in (401, 403):
                saved = {**(diagnostics or d.diagnostics), "blocked_at_ns": time.time_ns()}
                if stop_all:
                    db.execute(
                        "INSERT OR REPLACE INTO state VALUES('blocked',?)", (json.dumps(saved),)
                    )
                else:
                    db.execute(
                        "INSERT OR REPLACE INTO unavailable VALUES(?,?,?)",
                        (d.id, "provider_access_denied", self.clock()),
                    )
                    db.execute(
                        "INSERT OR REPLACE INTO access_denials VALUES(?,?,?)",
                        (d.id, json.dumps(saved), self.clock()),
                    )
            if retry_after is not None:
                try:
                    delay = max(0, float(retry_after))
                except (ValueError, TypeError):
                    try:
                        delay = max(
                            0, parsedate_to_datetime(retry_after).timestamp() - self.clock()
                        )
                    except (ValueError, TypeError, OverflowError):
                        delay = None
                if status == 429 and (delay is None or not math.isfinite(delay) or delay <= 0):
                    delay = 30
                if delay is not None and math.isfinite(delay):
                    db.execute(
                        "INSERT INTO cooldown VALUES(?,?) ON CONFLICT(bucket) DO UPDATE SET until=max(until,excluded.until)",
                        (d.id, self.clock() + delay),
                    )
            db.commit()
