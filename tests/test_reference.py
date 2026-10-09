"""Optional differential checks using the pinned upstream source, without a DB."""

import hashlib
import json
import os
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

from spqrmigrate.config import Config, load, parser
from spqrmigrate.files import callbacks, discover, read_statements


SOURCE = os.environ.get("PGMIGRATE_REFERENCE")
pytestmark = pytest.mark.skipif(not SOURCE, reason="Set PGMIGRATE_REFERENCE to the pinned pgmigrate.py")


@pytest.fixture
def reference():
    pin = json.loads((Path(__file__).parent.parent / "research/pgmigrate-reference.json").read_text())
    assert hashlib.sha256(Path(SOURCE).read_bytes()).hexdigest() == pin["source_sha256"]
    return SimpleNamespace(**runpy.run_path(SOURCE, run_name="pgmigrate_reference"))


@pytest.mark.parametrize("argv", [
    ["info"], ["migrate"], ["baseline"], ["clean"],
    ["info", "-t", "latest"], ["info", "--target", "4"],
    ["info", "-c", "host=coordinator"], ["info", "-d", "project"],
    ["info", "-u", "author"], ["info", "-b", "3"],
    ["info", "-a", "beforeEach:hook.sql"], ["info", "-s", "first", "-s", "second"],
    ["info", "-n"], ["info", "-l", "0.5"], ["info", "-m", "schema"],
    ["info", "--disable_schema_check"], ["info", "--check_serial_versions"],
    ["info", "-o"], ["info", "--force_mixed"], ["info", "--ungroup"],
    ["info", "-vvv"],
])
def test_cli_parsing_matches_reference(reference, monkeypatch, argv):
    captured = {}

    def capture(base_dir, args):
        captured.update(vars(args))

    globals_ = reference._main.__globals__
    monkeypatch.setitem(globals_, "get_config", capture)
    monkeypatch.setitem(globals_, "COMMANDS", {name: lambda config: None for name in reference.COMMANDS})
    monkeypatch.setattr("sys.argv", ["pgmigrate", *argv])
    reference._main()
    ours = vars(parser().parse_args(argv))
    del ours["nontransactional"]
    assert ours == captured


def test_defaults_match_except_explicit_session_change(reference):
    ours = vars(Config())
    assert ours.pop("session") == []
    assert reference.CONFIG_DEFAULTS.session == ["SET lock_timeout = 0"]
    assert ours.pop("nontransactional") is False
    for name, value in ours.items():
        assert value == getattr(reference.CONFIG_DEFAULTS, name), name


def test_yaml_and_cli_merge_matches_reference(reference, monkeypatch, tmp_path):
    (tmp_path / "migrations.yml").write_text(
        "conn: host=coordinator\ntarget: latest\nbaseline: 2\nuser: author\n"
        "check_serial_versions: true\nset_version_info_after_callbacks: true\n")
    globals_ = reference.get_config.__globals__
    monkeypatch.setitem(globals_, "_create_connection", lambda config: object())
    monkeypatch.setitem(globals_, "_init_cursor", lambda connection, session: object())
    args = parser().parse_args(["info", "-d", str(tmp_path), "-t", "4"])
    upstream = reference.get_config(str(tmp_path), args)
    ours = load(args)
    for name in ("conn", "target", "baseline", "user", "schema", "disable_schema_check",
                 "check_serial_versions", "set_version_info_after_callbacks", "base_dir"):
        assert getattr(ours, name) == getattr(upstream, name), name


def test_files_selection_and_statements_match_reference(reference, tmp_path):
    root = tmp_path / "migrations/nested"
    root.mkdir(parents=True)
    for name in ("V0__Skipped.sql", "V1__First_one.sql", "V2__NONTRANSACTIONAL_Second.sql", "V3__Third.sql"):
        (root / name).write_text("-- comment\nCREATE DISTRIBUTION ds COLUMN TYPES varchar;\n"
                                 'ALTER DISTRIBUTION ds ATTACH RELATION "semi;colon" DISTRIBUTION KEY id;')
    ours = discover(tmp_path)
    upstream = reference._get_migrations_info(str(tmp_path), 1, 2)
    assert [v for v in ours if 1 < v <= 2] == list(upstream)
    for version, migration in reference._get_migrations_info_from_dir(str(tmp_path)).items():
        expected = migration.meta.copy()
        assert ours[version].transactional == expected.pop("transactional")
        actual = ours[version].info()
        assert actual.pop("transactional") is False
        assert actual == expected
        assert read_statements(ours[version].path) == [x.decode("utf-8") for x in reference._get_statements(migration.file_path)]


@pytest.mark.parametrize("as_mapping", [False, True])
def test_callback_paths_and_order_match_reference(reference, tmp_path, as_mapping):
    root = tmp_path / "hooks"
    root.mkdir()
    for name in ("02.sql", "01.sql"):
        (root / name).write_text("")
    value = {"beforeEach": ["hooks"]} if as_mapping else "beforeEach:hooks"
    expected = reference._get_callbacks(value, str(tmp_path))
    actual = callbacks(value, tmp_path)
    assert {name: [str(path) for path in paths] for name, paths in actual.items()} == expected._asdict()
