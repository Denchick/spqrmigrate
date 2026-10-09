"""Opt-in tests against an isolated coordinator with PR #3065.

SPQRMIGRATE_TEST_DSN='host=... port=... dbname=... user=...' pytest tests/test_live_console.py
"""

import json
import os
import uuid

import pytest

from spqrmigrate.backend import Console
from spqrmigrate.cli import main


DSN = os.environ.get("SPQRMIGRATE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="Set SPQRMIGRATE_TEST_DSN for an isolated coordinator")


@pytest.fixture
def live(tmp_path):
    suffix = uuid.uuid4().hex[:12]
    namespace = "poc-'quoted-" + suffix
    distribution = "poc_" + suffix
    (tmp_path / "migrations").mkdir()
    args = ["-c", DSN, "-d", str(tmp_path), "-m", namespace, "--disable_schema_check"]
    backend = Console(DSN, namespace)
    try:
        yield tmp_path, distribution, backend, args
    finally:
        backend.clean()
        # Names belong only to this fixture; no ranges or CASCADE are used.
        for name in (distribution, distribution + "_second"):
            if any(r[0] == name for r in backend.execute("SHOW distributions")):
                backend.execute(f"ALTER DISTRIBUTION {name} DETACH RELATION IF EXISTS {name}_table;")
            backend.execute(f"DROP DISTRIBUTION IF EXISTS {name};")
        backend.close()


def test_real_migrate_reconnect_repeat_failure_and_resume(live, capsys):
    root, ds, backend, args = live
    first = root / "migrations/V1__Create.sql"
    first.write_text(f"CREATE DISTRIBUTION IF NOT EXISTS {ds} COLUMN TYPES varchar;")
    second = root / "migrations/V2__Attach.sql"
    second.write_text(f"ALTER DISTRIBUTION {ds} ATTACH RELATION IF NOT EXISTS {ds}_table DISTRIBUTION KEY id;")
    assert main(["migrate", "-t", "latest", "--nontransactional", "-u", "O'Brien", *args]) == 0
    assert [r["version"] for r in backend.history()] == [1, 2]
    assert backend.history()[0]["installed_by"] == "O'Brien"
    # Applied files are skipped, even when their SQL would fail the allowlist now.
    first.write_text("SELECT forbidden;")
    assert main(["migrate", "-t", "latest", *args]) == 0
    assert main(["info", *args]) == 0
    assert set(json.loads(capsys.readouterr().out)) == {"1", "2"}
    third = root / "migrations/V3__Partial.sql"
    third.write_text(f"CREATE DISTRIBUTION IF NOT EXISTS {ds}_second COLUMN TYPES varchar;"
                     f"ALTER DISTRIBUTION missing_{ds} ATTACH RELATION {ds}_table DISTRIBUTION KEY id;")
    assert main(["migrate", "-t", "latest", "--nontransactional", *args]) == 1
    assert [r["version"] for r in backend.history()] == [1, 2]
    assert ds + "_second" in [r[0] for r in backend.execute("SHOW distributions")]
    third.write_text(f"CREATE DISTRIBUTION IF NOT EXISTS {ds}_second COLUMN TYPES varchar;")
    assert main(["migrate", "-t", "latest", "--nontransactional", *args]) == 0
    assert [r["version"] for r in backend.history()] == [1, 2, 3]


def test_real_baseline_clean_namespace_isolation(live):
    _, _, backend, args = live
    other = Console(DSN, backend.key + "other")
    try:
        other.save([])
        assert main(["baseline", "-b", "4", *args]) == 0
        assert backend.history()[0]["type"] == "manual"
        assert main(["baseline", "-b", "4", *args]) == 1
        assert main(["clean", *args]) == 0
        assert backend.history() == []
        assert other.execute("SHOW migrations WHERE name = %s", (other.key,))
    finally:
        other.clean()
        other.close()
