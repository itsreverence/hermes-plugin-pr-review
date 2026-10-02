"""SQLite attempt ledger. Transactions fence concurrent and expired writers."""
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager

from .artifacts import managed_root, private_file, reject_symlinks


class StateError(RuntimeError):
    pass


class State:
    def __init__(self, root, *, clock=time.time, lease_seconds=1800):
        self.root = managed_root(root)
        self.clock = clock
        if not 1 <= lease_seconds <= 3600:
            raise StateError("lease must be between 1 and 3600 seconds")
        self.lease_seconds = lease_seconds
        self.path = self.root / "state.sqlite3"
        private_file(self.path)
        for suffix in ("-journal", "-wal", "-shm"):
            reject_symlinks(str(self.path) + suffix)
        try:
            with self.connection() as db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1):
                    raise StateError("unsupported state schema")
                db.execute("""CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, ref TEXT NOT NULL COLLATE NOCASE, stage TEXT NOT NULL,
                    status TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
                    snapshot_key TEXT, input_digest TEXT, rerun_reason TEXT,
                    previous_id TEXT, error TEXT
                )""")
                db.execute("CREATE INDEX IF NOT EXISTS identity_lookup ON attempts(ref, stage, snapshot_key, status)")
                db.execute("PRAGMA user_version=1")
        except sqlite3.Error as exc:
            raise StateError("state database unavailable or corrupt; preserve it for inspection") from exc

    @contextmanager
    def connection(self):
        reject_symlinks(self.path)
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _expire(self, db):
        db.execute("UPDATE attempts SET status='expired', error='lease_expired' WHERE status IN ('collecting','prepared') AND expires <= ?", (self.clock(),))

    def _row(self, db, attempt_id):
        if not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
            raise StateError("invalid attempt ID")
        row = db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        if row is None:
            raise StateError("unknown attempt")
        return dict(row)

    def _active(self, db, attempt_id):
        row = self._row(db, attempt_id)
        if row["status"] not in ("collecting", "prepared") or row["expires"] <= self.clock():
            raise StateError("attempt is not active or its lease expired")
        return row

    def start(self, ref, stage, *, rerun_reason=None):
        if stage not in ("triage", "review"):
            raise StateError("invalid stage")
        if rerun_reason is not None and (not rerun_reason.strip() or len(rerun_reason) > 500):
            raise StateError("rerun requires a bounded nonempty reason")
        with self.connection() as db:
            self._expire(db)
            if db.execute("SELECT 1 FROM attempts WHERE ref=? AND stage=? AND status IN ('collecting','prepared')", (ref, stage)).fetchone():
                raise StateError("PR stage already has an active attempt")
            attempt_id = uuid.uuid4().hex
            now = self.clock()
            db.execute("INSERT INTO attempts(id,ref,stage,status,created,expires,rerun_reason) VALUES (?,?,?,'collecting',?,?,?)", (attempt_id, ref, stage, now, now + self.lease_seconds, rerun_reason))
            return self._row(db, attempt_id)

    def prepared(self, attempt_id, snapshot_key, input_digest):
        with self.connection() as db:
            row = self._active(db, attempt_id)
            if row["status"] != "collecting":
                raise StateError("attempt was already prepared")
            previous = db.execute("SELECT id FROM attempts WHERE ref=? AND stage=? AND snapshot_key=? AND status='completed' ORDER BY created DESC LIMIT 1", (row["ref"], row["stage"], snapshot_key)).fetchone()
            skip = previous is not None and not row["rerun_reason"]
            db.execute("UPDATE attempts SET status=?,snapshot_key=?,input_digest=?,previous_id=? WHERE id=?", ("skipped" if skip else "prepared", snapshot_key, input_digest, previous["id"] if skip else None, attempt_id))
            return self._row(db, attempt_id)

    def finish(self, attempt_id, status, *, error=None, write=None):
        if status not in ("completed", "incomplete", "stale", "failed"):
            raise StateError("invalid terminal outcome")
        with self.connection() as db:
            row = self._active(db, attempt_id)
            if status == "completed" and row["status"] != "prepared":
                raise StateError("only a prepared attempt can complete")
            if write:
                write()
            db.execute("UPDATE attempts SET status=?,error=? WHERE id=?", (status, error, attempt_id))
            return self._row(db, attempt_id)

    def get(self, attempt_id):
        with self.connection() as db:
            self._expire(db)
            return self._row(db, attempt_id)

    def rows(self):
        with self.connection() as db:
            self._expire(db)
            return [dict(row) for row in db.execute("SELECT * FROM attempts ORDER BY created DESC LIMIT 100")]
