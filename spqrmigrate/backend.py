from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path
import tempfile
from urllib.parse import quote

import psycopg2

from .errors import MigrationError
from .journal import storage_value, stored_history


class Console:
    def __init__(self, dsn, namespace, connect=psycopg2.connect):
        self.connection = connect(dsn, application_name="spqrmigrate")
        self.connection.autocommit = True
        self.key = "spqrmigrate/v1/" + quote(namespace, safe="")
        self.user = self.connection.get_dsn_parameters()["user"]

    def execute(self, sql, params=None):
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchall() if cursor.description else []
        except psycopg2.Error as exc:
            message = exc.diag.message_primary or str(exc)
            raise MigrationError(f"Console: {message}") from exc

    def history(self):
        try:
            rows = self.execute("SHOW migrations WHERE name = %s", (self.key,))
        except MigrationError as exc:
            raise MigrationError(f"Cannot read migration journal; SPQR must include PR #3065. {exc}") from exc
        if not rows:
            return []
        if len(rows) != 1 or len(rows[0]) != 2 or rows[0][0] != self.key:
            raise MigrationError("Unexpected SHOW migrations response")
        return stored_history(rows[0][1])

    def save(self, history):
        self.execute("ALTER SYSTEM MIGRATION SET %s TO %s", (self.key, storage_value(history)))

    def clean(self):
        self.execute("ALTER SYSTEM MIGRATION RESET %s", (self.key,))

    @contextmanager
    def lock(self):
        # Protect cooperating local clients; this is not a cluster-wide mutex.
        params = self.connection.get_dsn_parameters()
        identity = {key: params.get(key, "") for key in ("host", "hostaddr", "port", "dbname")}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        path = Path(tempfile.gettempdir()) / f"spqrmigrate-{digest}.lock"
        with path.open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise MigrationError("Another local spqrmigrate process is using this Console") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def close(self):
        self.connection.close()
