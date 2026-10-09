# Compatibility with pgmigrate 1.0.13

Reference checked on 2026-10-09: the latest published
[yandex-pgmigrate package on PyPI](https://pypi.org/project/yandex-pgmigrate/1.0.13/)
is **1.0.13**. The project has no GitHub Releases; the reference uses tag `1.0.13`,
commit `a3c7250ae253e59866c380baf71fcf06b5a81cee`.
The [source](https://github.com/yandex/pgmigrate/blob/a3c7250ae253e59866c380baf71fcf06b5a81cee/pgmigrate.py),
URLs, and source/archive SHA-256 values are pinned in
[pgmigrate-reference.json](pgmigrate-reference.json). Later releases do not
change this reference automatically.

## CLI options and defaults

All reference option names, aliases, CLI value types, and argparse defaults are
preserved. The table lists configuration values after applying defaults.
Inapplicable features produce errors instead of being treated as successful no-ops.

| Option | pgmigrate default | spqrmigrate PoC |
|---|---|---|
| `cmd` | Required; info/migrate/baseline/clean | Same commands; history operations use QDB |
| `-t/--target` | None | Same; integer or `latest`; required for migrate |
| `-c/--conn` | `dbname=postgres user=postgres connect_timeout=1` | Preserved; the user must point it to the coordinator Console |
| `-d/--base_dir` | Empty string, current directory | Same; migrations.yml and migrations/ |
| `-u/--user` | None -> `SELECT CURRENT_USER` | None -> Console connection startup user; a nonempty override is preserved |
| `-b/--baseline` | 0 | Same; exclusive lower bound; baseline replaces history |
| `-a/--callbacks` | Empty string | Same four hooks, string/mapping forms, and path order; only metadata DDL is allowed |
| `-s/--session` | `['SET lock_timeout = 0']`; CLI append | **Default changed to []**; nonempty configuration is an error because PostgreSQL session SQL is inapplicable |
| `-n/--dryrun` | false; execute, then rollback | Error when true: Console rollback is not implemented |
| `-l/--termination_interval` | None | None/0 do nothing; nonzero values are errors because blocking PostgreSQL PIDs are inapplicable |
| `-m/--schema` | None -> public, schema checking disabled | public is a history namespace; an explicit name requires `--disable_schema_check`; metadata is not isolated |
| `--disable_schema_check` | false; automatically true when schema=None | Same default handling; false with an explicit schema is an error because PostgreSQL schema checking is inapplicable |
| `--check_serial_versions` | false | Same sequence check from the highest recorded version; empty history may start at any version > baseline |
| `-o/--show_only_unapplied` | false | Same for info |
| `--force_mixed` | false | Error when true: no transactional/mixed execution; the override is described below |
| `--ungroup` | false | Error when true: no transaction grouping |
| `-v/--verbose` | 0, count | Same; ERROR/WARNING/INFO/DEBUG levels, capped at 3 |
| `-h/--help` | argparse help | Same, with additional PoC limitations |
| `--nontransactional` | Absent | Extension: explicit opt-in to executing files and hooks without rollback |

## YAML and defaults without CLI options

Precedence is default -> migrations.yml -> CLI value when it is not None. The
reference's behavior is preserved: absent `store_true` arguments still yield
False, so the CLI overrides YAML true for dryrun, disable_schema_check,
check_serial_versions, show_only_unapplied, force_mixed, and ungroup. The new
nontransactional option follows the same rule; opt-in requires the CLI flag.
An absent `-d` yields an empty string and also overrides YAML base_dir. Unknown
YAML fields are ignored, as in the reference.

`set_version_info_after_callbacks` is a YAML-only option, default false. True
moves the journal write after afterEach. `callbacks` may be a mapping with four
keys and lists of paths. Invalid definitions or paths are also rejected for info,
baseline, and clean; their SQL is read and validated before migrate.

`cursor`, `conn_instance`, and `terminator_instance` are internal runtime objects
in the reference, not user YAML settings. The PoC replaces them with a Console
backend.

## File, history, and execution semantics

Preserved behavior includes recursive discovery of
`V<digits>__<nonempty description>.sql`, numeric versions, duplicate errors,
replacing underscores with spaces, skipping unmatched files, an exclusive
baseline and inclusive target, skipping everything through the highest recorded
version, the JSON info format, and five history fields (`version`, `description`,
`type`, `installed_by`, `installed_on`). Recorded versions appear regardless of
target. V0 is excluded with the default baseline=0. Baseline history contains one
manual record with description `Forced baseline`; a new baseline is rejected if
a version >= its value is already recorded. Clean deletes only history.

ASCII validation, the exact `/* pgmigrate-encoding: utf-8 */` directive, comment
removal, and SQL splitting through sqlparse are preserved. File discovery order
is stabilized by sorting; migration execution is numeric in both tools. Callback
directories are read lexicographically without recursion. A colon inside a string
callback path is preserved: the PoC does not reproduce upstream's path truncation
through split(':').

In pgmigrate, ordinary files are transactional; `NONTRANSACTIONAL` in the
description disables transactions and hooks for that file. All PoC executions
are nontransactional, so info reports transactional=false. The marker still
disables hooks. Ordinary files require the new `--nontransactional` flag, which
explicitly changes their semantics. Even marker-only files require opt-in on an
empty journal because upstream requires transactional initialization. After a
baseline, marker-only files may run without the new flag. Opt-in permits mixed
file names, but all commands run without transactions; `--force_mixed` does not
replace opt-in.

History uses one QDB key: `spqrmigrate/v1/<percent-encoded namespace>`. Its value,
`json-base64-v1:<base64url UTF-8 JSON>`, contains `{format:1, history:[...]}`.
Encoding is necessary because the Console lexer does not support PostgreSQL
apostrophe escaping (`''`). It preserves arbitrary descriptions and authors
without placing their raw values in SQL literals. Corrupt records, unknown
formats, and duplicate versions are rejected; invalid history is not overwritten
automatically. Time is the client's UTC time instead of server now(); the author
is the startup user instead of CURRENT_USER. Server-side user mapping can make
these authors differ; use `-u` to override the author explicitly.

Checksums and detection of changes to applied files are absent in both the PoC
and pgmigrate 1.0.13. Adding checksums would be a separate extension. The history
table's PostgreSQL bigint type is replaced with a JSON integer; the SQL bigint
version limit is not emulated.

## Unsupported guarantees

Transaction grouping, rollback, atomic metadata+history writes, transactional
callbacks, and dryrun require Console transactions in SPQR. Multiple writers
require a coordinator locking API with stale-owner protection or CAS; SET
currently replaces values unconditionally. Local flock only prevents simultaneous
cooperating processes on one machine using the same DSN endpoint. It is not a
distributed lock and does not protect against manual DDL.

SQL is restricted to distributions and relation attach/detach operations.
PostgreSQL DDL/DML, key ranges, transfers, default shards, CASCADE, and
administrative commands are outside the PoC's scope. The allowlist is checked for
the entire selected batch before changes; full Console syntax is checked by the
server one command at a time. A later syntax or runtime error can therefore leave
earlier commands applied.

Failure can occur between a successful file and its journal write. Files are
recorded separately: successful earlier versions remain, and errors do not cause
an automatic retry. An afterEach/afterAll failure under the default ordering can
leave the version recorded and its hook unfinished. README documents this limit
of nontransactional execution.

Success/error exit codes are 0/1; argparse uses 2. Interrupts are explicitly
handled as 130. Error wording and logging details do not reproduce upstream.

## Reference verification

`tests/test_reference.py` imports the **actual pinned source** after checking its
SHA-256. Without a database, it compares all CLI options/defaults, Config
defaults, YAML/CLI precedence, file metadata, baseline/target selection, SQL
splitting, and callback path order. All 26 differential checks passed on
2026-10-09. These checks do not establish equivalent PostgreSQL execution
semantics: the features listed above require different server support or are
inapplicable to SPQR.
