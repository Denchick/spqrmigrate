from contextlib import nullcontext
import io
import json

import pytest

from spqrmigrate.config import Config, load, parser
from spqrmigrate.errors import MigrationError
from spqrmigrate.files import discover, validate
from spqrmigrate.journal import decode, encode
from spqrmigrate.runner import run


class Store:
    user = "admin"

    def __init__(self):
        self.rows = []
        self.sql = []
        self.fail = None

    def lock(self):
        return nullcontext()

    def history(self):
        return decode(encode(self.rows))

    def save(self, rows):
        self.rows = decode(encode(rows))

    def clean(self):
        self.rows = []

    def execute(self, sql):
        if self.fail and self.fail in sql:
            raise MigrationError("injected failure")
        self.sql.append(sql)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "migrations").mkdir()
    return tmp_path


def file(project, name, text):
    path = project / "migrations" / name
    path.write_text(text)
    return path


def config(project, **values):
    return Config(base_dir=str(project), target=float("inf"), schema="public",
                  disable_schema_check=True, **values)


def execute(config, store, command="migrate"):
    output = io.StringIO()
    run(config, command, store, output)
    return output.getvalue()


def test_apply_and_repeat_skip_recorded_versions(project):
    file(project, "V0001__Initial.sql", "CREATE DISTRIBUTION IF NOT EXISTS ds COLUMN TYPES varchar;")
    store = Store()
    execute(config(project, nontransactional=True), store)
    execute(config(project), store)
    assert len(store.sql) == len(store.rows) == 1
    assert store.rows[0]["description"] == "Initial"
    assert json.loads(execute(config(project), store, "info"))["1"]["transactional"] is False
    assert json.loads(execute(config(project, show_only_unapplied=True), store, "info")) == {}


def test_default_transactional_file_requires_explicit_opt_in(project):
    file(project, "V1__Initial.sql", "CREATE DISTRIBUTION ds COLUMN TYPES varchar;")
    store = Store()
    with pytest.raises(MigrationError, match="--nontransactional"):
        execute(config(project), store)
    assert not store.sql and not store.rows


def test_failed_file_remains_unrecorded_and_applied_prefix_remains(project):
    file(project, "V1__Initial.sql", "CREATE DISTRIBUTION IF NOT EXISTS ds COLUMN TYPES varchar;")
    file(project, "V2__Second.sql", "CREATE DISTRIBUTION IF NOT EXISTS ds2 COLUMN TYPES varchar;"
         "CREATE DISTRIBUTION IF NOT EXISTS fail COLUMN TYPES varchar;")
    store = Store()
    store.fail = "fail"
    with pytest.raises(MigrationError, match="Partial"):
        execute(config(project, nontransactional=True), store)
    assert [r["version"] for r in store.rows] == [1]
    assert len(store.sql) == 2
    store.fail = None
    execute(config(project, nontransactional=True), store)
    assert [r["version"] for r in store.rows] == [1, 2]


@pytest.mark.parametrize("committed", [False, True])
def test_history_write_failure_does_not_retry_sql_automatically(project, committed):
    file(project, "V1__Initial.sql", "CREATE DISTRIBUTION IF NOT EXISTS ds COLUMN TYPES varchar;")

    class FailingStore(Store):
        def save(self, rows):
            if committed:
                super().save(rows)
            raise MigrationError("write acknowledgement lost")

    store = FailingStore()
    with pytest.raises(MigrationError, match="reconnect and inspect info"):
        execute(config(project, nontransactional=True), store)
    assert len(store.sql) == 1
    assert len(store.rows) == int(committed)


def test_bad_callback_path_is_checked_even_for_info(project):
    with pytest.raises(MigrationError, match="Callback not found"):
        execute(config(project, callbacks="beforeAll:missing.sql"), Store(), "info")


@pytest.mark.parametrize("sql", [
    "CREATE KEY RANGE kr FROM 0 ROUTE TO sh FOR DISTRIBUTION ds;",
    "DROP DISTRIBUTION ds CASCADE;", "DROP DISTRIBUTION ALL;",
    "CREATE DISTRIBUTION ds COLUMN TYPES int DEFAULT SHARD sh;",
    "ALTER DISTRIBUTION ds DEFAULT SHARD sh;", "MOVE KEY RANGE kr TO sh;",
    "DROP KEY RANGE kr;", "BEGIN;", "SELECT 1;", "ALTER SYSTEM MIGRATION RESET user_key;",
])
def test_forbidden_commands_fail_before_any_mutation(project, sql):
    file(project, "V1__Initial.sql", "CREATE DISTRIBUTION ds COLUMN TYPES varchar;")
    file(project, "V2__Bad.sql", sql)
    store = Store()
    with pytest.raises(MigrationError, match="forbidden"):
        execute(config(project, nontransactional=True), store)
    assert not store.sql and not store.rows


def test_callbacks_are_checked_before_mutations(project):
    file(project, "V1__Initial.sql", "CREATE DISTRIBUTION ds COLUMN TYPES varchar;")
    (project / "bad.sql").write_text("DROP KEY RANGE kr;")
    store = Store()
    with pytest.raises(MigrationError, match="bad.sql"):
        execute(config(project, nontransactional=True, callbacks="afterAll:bad.sql"), store)
    assert not store.sql


def test_callback_order_and_record_position(project):
    file(project, "V1__Initial.sql", "CREATE DISTRIBUTION ds COLUMN TYPES varchar;")
    for name in ("before", "after"):
        (project / f"{name}.sql").write_text(f"CREATE DISTRIBUTION {name} COLUMN TYPES varchar;")
    store = Store()
    execute(config(project, nontransactional=True, callbacks="beforeAll:before.sql,afterAll:after.sql"), store)
    assert "before" in store.sql[0] and "after" in store.sql[-1]
    assert len(store.rows) == 1


@pytest.mark.parametrize("record_after,expected", [(False, 1), (True, 0)])
def test_after_each_failure_observes_configured_record_position(project, record_after, expected):
    file(project, "V1__Initial.sql", "CREATE DISTRIBUTION ds COLUMN TYPES varchar;")
    (project / "after.sql").write_text("CREATE DISTRIBUTION fail COLUMN TYPES varchar;")
    store = Store()
    store.fail = "fail"
    with pytest.raises(MigrationError):
        execute(config(project, nontransactional=True, callbacks="afterEach:after.sql",
                       set_version_info_after_callbacks=record_after), store)
    assert len(store.rows) == expected


def test_baseline_and_clean_only_change_history(project):
    store = Store()
    store.sql = ["existing metadata"]
    execute(config(project, baseline=2), store, "baseline")
    assert store.rows[0]["type"] == "manual"
    with pytest.raises(MigrationError, match="already applied"):
        execute(config(project, baseline=2), store, "baseline")
    execute(config(project, baseline=3), store, "baseline")
    assert len(store.rows) == 1 and store.rows[0]["version"] == 3
    execute(config(project), store, "clean")
    assert store.rows == [] and store.sql == ["existing metadata"]


def test_config_preserves_reference_cli_yaml_boolean_precedence(project):
    (project / "migrations.yml").write_text("nontransactional: true\nset_version_info_after_callbacks: true\n")
    cfg = load(parser().parse_args(["info", "-d", str(project)]))
    assert cfg.nontransactional is False
    assert cfg.set_version_info_after_callbacks is True
    assert cfg.schema == "public" and cfg.disable_schema_check


@pytest.mark.parametrize("flag", ["-n", "--ungroup", "--force_mixed", "-s", "-l"])
def test_unsupported_options_are_explicit_errors(flag):
    args = ["migrate", flag]
    if flag == "-s":
        args.append("SET lock_timeout = 0")
    if flag == "-l":
        args.append("1")
    with pytest.raises(MigrationError, match="unsupported"):
        load(parser().parse_args(args))


def test_version_gaps_and_duplicate_numeric_versions(project):
    file(project, "V5__First.sql", "CREATE DISTRIBUTION ds5 COLUMN TYPES varchar;")
    file(project, "V7__Gap.sql", "CREATE DISTRIBUTION ds7 COLUMN TYPES varchar;")
    with pytest.raises(MigrationError, match="gaps"):
        execute(config(project, nontransactional=True, check_serial_versions=True), Store())
    file(project, "V6__Middle.sql", "CREATE DISTRIBUTION ds6 COLUMN TYPES varchar;")
    store = Store()
    execute(config(project, nontransactional=True, check_serial_versions=True), store)
    assert [r["version"] for r in store.rows] == [5, 6, 7]
    file(project, "V0005__Duplicate.sql", "")
    with pytest.raises(MigrationError, match="Duplicate"):
        discover(project)


def test_utf8_directive_and_zero_version(project):
    file(project, "V0__Skip.sql", "SELECT forbidden;")
    path = file(project, "V1__Initial.sql", 'CREATE DISTRIBUTION "caf\u00e9" COLUMN TYPES varchar;')
    with pytest.raises(MigrationError, match="Cannot read"):
        execute(config(project, nontransactional=True), Store())
    path.write_text('/* pgmigrate-encoding: utf-8 */\nCREATE DISTRIBUTION "caf\u00e9" COLUMN TYPES varchar;')
    store = Store()
    execute(config(project, nontransactional=True), store)
    assert len(store.rows) == 1


@pytest.mark.parametrize("value", ["invalid", '{"format":2,"history":[]}',
                                   '{"format":1,"history":[{}]}'])
def test_corrupt_or_unknown_history_is_rejected(value):
    with pytest.raises(MigrationError):
        decode(value)


def test_quoted_keywords_do_not_trigger_command_filter():
    validate('CREATE DISTRIBUTION "default" COLUMN TYPES varchar;', "test")
