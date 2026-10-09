import pytest

from spqrmigrate.backend import Console
from spqrmigrate.errors import MigrationError
from spqrmigrate.journal import entry, storage_value, stored_history


class Connection:
    def __init__(self):
        self.calls = []
        self.description = True
        self.rows = []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def execute(self, sql, args):
        self.calls.append((sql, args))

    def fetchall(self):
        return self.rows

    def get_dsn_parameters(self):
        return dict(host="test-only", port="12345", dbname="console", user="admin")

    def close(self):
        pass


def test_native_queries_use_parameters_and_single_namespaced_history():
    connection = Connection()
    backend = Console("", "service's history", connect=lambda *a, **kw: connection)
    row = entry(1, "O'Brien\\caf\u00e9", "admin")
    backend.save([row])
    query, args = connection.calls[-1]
    assert query == "ALTER SYSTEM MIGRATION SET %s TO %s"
    assert args[0] == "spqrmigrate/v1/service%27s%20history"
    assert "'" not in args[1] and "\\" not in args[1]
    assert stored_history(args[1]) == [row]
    connection.rows = [(backend.key, storage_value([row]))]
    assert backend.history() == [row]
    backend.clean()
    assert connection.calls[-1] == ("ALTER SYSTEM MIGRATION RESET %s", (backend.key,))
    assert connection.autocommit is True


@pytest.mark.parametrize("value", [None, "raw JSON", "json-base64-v1:!", "json-base64-v1:/w=="])
def test_invalid_transport_encoding_fails_without_writes(value):
    connection = Connection()
    backend = Console("", "public", connect=lambda *a, **kw: connection)
    connection.rows = [(backend.key, value)]
    with pytest.raises(MigrationError, match="encoding"):
        backend.history()
    assert len(connection.calls) == 1


def test_local_lock_rejects_concurrent_clients_and_releases_after_failure():
    connection = Connection()
    first = Console("", "public", connect=lambda *a, **kw: connection)
    second = Console("", "another", connect=lambda *a, **kw: connection)
    with first.lock():
        with pytest.raises(MigrationError, match="Another local"):
            with second.lock():
                pass
    with second.lock():
        pass
