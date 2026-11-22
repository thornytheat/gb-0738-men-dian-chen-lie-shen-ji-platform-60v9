import json
import sqlite3
from contextlib import contextmanager

from app.errors import AuditError
from app.manifest import canonical

DDL = (
    '''CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'ACTIVE',
        superseded_by TEXT
    )''',
    '''CREATE TABLE IF NOT EXISTS appeal_events (
        seq INTEGER PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        shelf_key TEXT NOT NULL,
        version INTEGER NOT NULL CHECK (version > 0),
        request_id TEXT NOT NULL,
        payload TEXT NOT NULL,
        event TEXT NOT NULL,
        UNIQUE (run_id, shelf_key, version),
        UNIQUE (run_id, request_id)
    )''',
    '''CREATE TABLE IF NOT EXISTS run_actions (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        action TEXT NOT NULL,
        actor TEXT NOT NULL,
        note TEXT NOT NULL,
        created_at TEXT NOT NULL,
        snapshot TEXT NOT NULL
    )''',
    '''CREATE TABLE IF NOT EXISTS run_failures (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        code TEXT NOT NULL,
        message TEXT NOT NULL,
        created_at TEXT NOT NULL
    )''',
)

# Batches are never deleted. A run is either ACTIVE (handed to finance), SUPERSEDED (replaced
# by a newer batch for the same effective parameters), or ROLLED_BACK (a failed generation that
# was recovered to the prior good state). History rows persist for every transition.
MIGRATIONS = {
    'created_at': "ALTER TABLE runs ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
    'status': "ALTER TABLE runs ADD COLUMN status TEXT NOT NULL DEFAULT 'ACTIVE'",
    'superseded_by': 'ALTER TABLE runs ADD COLUMN superseded_by TEXT',
}


class Store:
    def __init__(self, database):
        self.database = database

    @contextmanager
    def connection(self, write=False):
        connection = sqlite3.connect(self.database, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('PRAGMA foreign_keys = ON')
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self.connection(write=True) as connection:
            for statement in DDL:
                connection.execute(statement)
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(runs)')}
            for column, statement in MIGRATIONS.items():
                if column not in columns:
                    connection.execute(statement)

    @staticmethod
    def read(connection, run_id, required=True):
        found = connection.execute('SELECT snapshot, status, created_at, superseded_by FROM runs WHERE run_id = ?',
                                   (run_id,)).fetchone()
        if found is None:
            if required:
                raise AuditError('RUN_NOT_FOUND', '未找到该巡检批次', 404)
            return None
        events = connection.execute('SELECT event FROM appeal_events WHERE run_id = ? ORDER BY seq', (run_id,))
        return {**json.loads(found['snapshot']), 'status': found['status'],
                'superseded_by': found['superseded_by'],
                'events': [json.loads(event['event']) for event in events]}

    def get(self, run_id, required=True):
        with self.connection() as connection:
            return self.read(connection, run_id, required)

    def save(self, snapshot):
        """Insert a complete snapshot atomically. Returns (snapshot, reused)."""
        run_id = snapshot['manifest']['run_id']
        record = canonical(snapshot)
        created = snapshot['manifest'].get('created_at', '')
        with self.connection(write=True) as connection:
            inserted = connection.execute(
                'INSERT INTO runs (run_id, snapshot, created_at, status) VALUES (?, ?, ?, ?) '
                'ON CONFLICT(run_id) DO NOTHING', (run_id, record, created, 'ACTIVE'))
            return self.read(connection, run_id), inserted.rowcount == 0

    def publish(self, snapshot):
        """Publish a batch as the current ACTIVE one and supersede older, still-active batches.

        A regenerated batch for identical effective inputs resolves to the same run_id and is
        reused untouched, so re-runs never duplicate a slot or restate an amount. Re-running the
        inputs of a superseded batch re-activates it instead of creating a second copy; a batch
        that was rolled back stays rolled back and cannot silently return to finance.
        """
        run_id = snapshot['manifest']['run_id']
        with self.connection(write=True) as connection:
            existing = self.read(connection, run_id, required=False)
            if existing is not None:
                if existing['status'] == 'ROLLED_BACK':
                    raise AuditError('BATCH_ROLLED_BACK', '该批次已回滚，不能重新发布', 409, run_id)
                if existing['status'] == 'ACTIVE':
                    return existing, True
                self._supersede_others(connection, run_id, snapshot)
                connection.execute("UPDATE runs SET status = 'ACTIVE', superseded_by = NULL WHERE run_id = ?", (run_id,))
                self._log(connection, run_id, 'REACTIVATE', 'system', '相同规则重跑，恢复该批次', snapshot)
                return self.read(connection, run_id), True
            record = canonical(snapshot)
            connection.execute(
                'INSERT INTO runs (run_id, snapshot, created_at, status) VALUES (?, ?, ?, ?)',
                (run_id, record, snapshot['manifest'].get('created_at', ''), 'ACTIVE'))
            self._supersede_others(connection, run_id, snapshot)
            self._log(connection, run_id, 'PUBLISH', 'system', '发布供财务结算', snapshot)
            return self.read(connection, run_id), False

    def _supersede_others(self, connection, run_id, snapshot):
        older = [row['run_id'] for row in connection.execute(
            "SELECT run_id FROM runs WHERE status = 'ACTIVE' AND run_id <> ? ORDER BY created_at", (run_id,))]
        for previous in older:
            connection.execute("UPDATE runs SET status = 'SUPERSEDED', superseded_by = ? WHERE run_id = ?",
                               (run_id, previous))
            self._log(connection, previous, 'SUPERSEDE', 'system', f'被批次 {run_id} 取代', snapshot)

    def _log(self, connection, run_id, action, actor, note, snapshot):
        from app.manifest import now
        connection.execute('INSERT INTO run_actions (run_id, action, actor, note, created_at, snapshot) '
                           'VALUES (?, ?, ?, ?, ?, ?)', (run_id, action, actor, note, now(), canonical(snapshot)))

    def history(self, run_id=None):
        with self.connection() as connection:
            if run_id is None:
                rows = connection.execute(
                    "SELECT run_id, created_at, status, superseded_by FROM runs ORDER BY created_at, run_id").fetchall()
                return [dict(row) for row in rows]
            rows = connection.execute(
                'SELECT seq, action, actor, note, created_at FROM run_actions WHERE run_id = ? ORDER BY seq',
                (run_id,)).fetchall()
            return [dict(row) for row in rows]

    def rollback(self, run_id, reason, replacement=None):
        """Mark a batch ROLLED_BACK; history is preserved, never erased."""
        with self.connection(write=True) as connection:
            snapshot = self.read(connection, run_id)
            if snapshot['status'] == 'ROLLED_BACK':
                return self.read(connection, run_id)
            connection.execute("UPDATE runs SET status = 'ROLLED_BACK' WHERE run_id = ?", (run_id,))
            self._log(connection, run_id, 'ROLLBACK', 'system', reason, snapshot)
            if replacement is not None:
                connection.execute("UPDATE runs SET status = 'ACTIVE', superseded_by = NULL WHERE run_id = ?",
                                   (replacement,))
                self._log(connection, replacement, 'RESTORE', 'system', f'回滚后恢复批次 {run_id}', snapshot)
            return self.read(connection, run_id)

    def record_failure(self, manifest, error):
        """Log a failed generation against the run_id without writing a partial snapshot."""
        from app.manifest import now
        code = getattr(error, 'code', type(error).__name__)
        with self.connection(write=True) as connection:
            connection.execute('INSERT INTO run_failures (run_id, code, message, created_at) VALUES (?, ?, ?, ?)',
                               (manifest['run_id'], code, str(error)[:500], now()))

    def failures(self, run_id=None):
        with self.connection() as connection:
            if run_id is None:
                rows = connection.execute(
                    'SELECT seq, run_id, code, message, created_at FROM run_failures ORDER BY seq').fetchall()
            else:
                rows = connection.execute(
                    'SELECT seq, run_id, code, message, created_at FROM run_failures WHERE run_id = ? ORDER BY seq',
                    (run_id,)).fetchall()
            return [dict(row) for row in rows]

    def append_event(self, run_id, payload, build):
        with self.connection(write=True) as connection:
            snapshot = self.read(connection, run_id)
            raw = canonical(payload)
            old = connection.execute('SELECT payload, event FROM appeal_events WHERE run_id = ? AND request_id = ?',
                                     (run_id, payload['request_id'])).fetchone()
            if old:
                if old['payload'] != raw:
                    raise AuditError('REQUEST_CONFLICT', '提交编号已用于其他内容', 409)
                return json.loads(old['event']), True
            event = build(snapshot, payload)
            connection.execute('''INSERT INTO appeal_events
                (run_id, shelf_key, version, request_id, payload, event) VALUES (?, ?, ?, ?, ?, ?)''',
                (run_id, event['key'], event['version'], payload['request_id'], raw, canonical(event)))
            return event, False
