# spqrmigrate

Versioned SQL migrations for SPQR distribution and relation metadata.

spqrmigrate connects to the **SPQR coordinator Console**, applies pending
migrations in version order, and stores their history in SPQR's own metadata
backend. It uses pgmigrate-style files and commands without requiring a separate
PostgreSQL database for migration history.

**Current version: 0.1.0.** Execution is nontransactional and requires a single
writer per coordinator. Production qualification and remaining work are tracked
in [plan.md](plan.md).

## Features

- Versioned SQL files with numeric ordering, duplicate detection, and repeatable runs.
- `info`, `migrate`, `baseline`, and `clean` commands.
- Migration history stored through native SPQR journal commands.
- JSON output for applied and pending versions.
- SQL callbacks and YAML configuration.
- Validation that rejects key ranges, data movement, and unsupported commands
  before migration writes.

## Requirements

- Python 3.10 or newer on Linux or macOS.
- An SPQR coordinator with the journal commands introduced by
  [PR #3065](https://github.com/pg-sharding/spqr/pull/3065).
- Access to the coordinator's **Console port**, not a router SQL port or a shard.
- One writer for the coordinator's metadata, including manual Console changes.

History durability depends on the coordinator's configured metadata backend.
An ephemeral MemQDB loses history on restart. Use durable storage when history
must survive a coordinator restart.

## Installation

From a checkout of this repository:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/spqrmigrate --help
```

The same CLI is available through `.venv/bin/python -m spqrmigrate`.
For development, use `make install` to install the project in editable mode with
its test, lint, and build tools.

## Quick start

The bundled [examples](examples/) create a distribution and attach a relation.
Configure the coordinator connection in `examples/migrations.yml`:

```yaml
conn: host=localhost port=7002 dbname=spqr-console user=admin connect_timeout=2
```

Inspect pending versions, apply them, then inspect the recorded history:

```sh
.venv/bin/spqrmigrate info -d examples -t latest -o
.venv/bin/spqrmigrate migrate -d examples -t latest --nontransactional --check_serial_versions -v
.venv/bin/spqrmigrate info -d examples
```

Override the connection for an individual command with `-c`:

```sh
.venv/bin/spqrmigrate info -d examples \
  -c 'host=coordinator port=7002 dbname=spqr-console user=admin connect_timeout=2'
```

`info` reports versions; it does not execute or fully validate their SQL.
`--nontransactional` explicitly selects execution without rollback.

## Migration files

```text
project/
  migrations.yml
  migrations/
    V0001__Create_distribution.sql
    V0002__Attach_notifications.sql
```

Files follow `V<number>__<description>.sql` and are discovered recursively.
Versions are compared numerically, so `V1` and `V0001` are duplicates. Underscores
in descriptions become spaces in history. Files with unmatched names are skipped.

The selected range is greater than the baseline and no greater than the target.
Recorded versions are skipped; new files below the highest recorded version are
also skipped. The default baseline is 0, so V0 is excluded. Treat applied files
as immutable: the current release does not detect changes to their contents.

Use repeatable metadata commands:

```sql
CREATE DISTRIBUTION IF NOT EXISTS ds_notify COLUMN TYPES varchar;
ALTER DISTRIBUTION ds_notify ATTACH RELATION IF NOT EXISTS notifications
    DISTRIBUTION KEY id;
```

Files are read as UTF-8 but must contain only ASCII unless they include the exact
directive `/* pgmigrate-encoding: utf-8 */`. Multiple statements are supported.

### Supported SQL

| Operation | Supported forms |
|---|---|
| Create a distribution | `CREATE DISTRIBUTION`, including `IF NOT EXISTS` |
| Drop a distribution | `DROP DISTRIBUTION`, including `IF EXISTS`, without `CASCADE` |
| Attach a relation | `ALTER DISTRIBUTION ... ATTACH RELATION`, including `IF NOT EXISTS` |
| Detach a relation | `ALTER DISTRIBUTION ... DETACH RELATION`, including `IF EXISTS` |

`TABLE` is accepted as an alias for `RELATION` in attach/detach commands.
These operations change SPQR metadata; they do not create or alter PostgreSQL
tables on shards.

Key ranges, data movement, default shards, `CASCADE`, `ALL`, other administrative
commands, and PostgreSQL DDL/DML are outside scope and rejected. All selected SQL
files and configured callbacks pass the allowlist before migration writes.
The coordinator validates complete syntax and runtime conditions one statement
at a time; a later failure can leave earlier statements applied.

## Commands

All commands accept the connection and configuration options below.

| Command | Behavior |
|---|---|
| `info [-t N] [-o]` | Print applied and pending versions as JSON; `-o` shows only pending versions |
| `migrate -t N\|latest --nontransactional` | Execute pending files through the target and record each successful version |
| `baseline -b N` | Replace history with one manual record at N; reject it if any recorded version is >= N |
| `clean` | Remove the selected namespace's history without changing distributions or relations |

`-b N` on `info` or `migrate` only sets the lower selection bound; it does not
write a baseline record. `--check_serial_versions` checks for gaps from the
highest recorded version through the selected target. On an empty history,
a consecutive sequence can start at any version greater than the baseline.

`clean` does not undo SQL. After cleaning, earlier migrations become eligible
again, so resetting history requires an intentional recovery or adoption workflow.

## Configuration

Configuration is loaded from `migrations.yml` under `-d/--base_dir`.

| Option | Default | Purpose |
|---|---|---|
| `-c`, `--conn` | `dbname=postgres user=postgres connect_timeout=1` | Coordinator Console connection string; set it explicitly for SPQR |
| `-d`, `--base_dir` | Current directory | Directory containing migrations.yml and migrations/ |
| `-t`, `--target` | Unset | Integer target or `latest`; required for migrate |
| `-b`, `--baseline` | 0 | Exclusive lower version bound, or the version written by baseline |
| `-m`, `--schema` | public | History namespace; an explicit name requires `--disable_schema_check` |
| `-u`, `--user` | Connection startup user | Author recorded in history |
| `-a`, `--callbacks` | None | Comma-separated `type:path` callbacks; YAML also accepts a mapping |
| `--check_serial_versions` | false | Reject gaps in the selected sequence |
| `-o`, `--show_only_unapplied` | false | Exclude recorded versions from info |
| `--nontransactional` | false | Explicitly permit execution without rollback |
| `-v`, `--verbose` | 0 | Increase logging verbosity; repeat up to three times |

`--schema` names a journal namespace, not an isolated metadata schema. Different
namespaces still operate on the coordinator's shared distributions and relations:

```sh
.venv/bin/spqrmigrate info -d examples -m notifications --disable_schema_check
```

Precedence is defaults, then YAML, then CLI values that are not None. Matching
pgmigrate 1.0.13, absent boolean CLI flags still provide false and override YAML
true; set `--nontransactional` and other boolean flags on the command line.
An absent `-d` similarly overrides YAML base_dir with the current directory.
Unknown YAML fields are ignored.

### Callbacks

The supported hooks are `beforeAll`, `beforeEach`, `afterEach`, and `afterAll`.
Paths are relative to the base directory unless absolute. Callback directories
are read in lexicographic order without recursion. Create the referenced files
before using a configuration such as:

```yaml
callbacks:
  beforeEach:
    - callbacks/before_each.sql
  afterAll:
    - callbacks/after_all.sql
set_version_info_after_callbacks: false
```

Callbacks use the same SQL allowlist as migrations. For files eligible for hooks,
`beforeAll` runs before the first file, `beforeEach` before each file, and
`afterAll` after the last eligible file. By default, the version is recorded before `afterEach`; YAML
`set_version_info_after_callbacks: true` moves recording after that hook.

As in pgmigrate, `NONTRANSACTIONAL` in a file's description disables its callbacks.
Ordinary files require `--nontransactional`. A new, empty history requires the
flag even for marked files; marked files can run without it once history has
been initialized, for example by baseline. All execution remains nontransactional.

## Execution and recovery

Each file's SQL and its history entry are separate writes. On SQL failure,
earlier commands remain and the failed file's version is not recorded. On
journal failure, the SQL has already succeeded and the write outcome may be
unknown. The CLI never retries the migration automatically.

After a failure:

1. Stop other writers and preserve the command's error output.
2. Reconnect, inspect `info`, and inspect the affected metadata through the Console.
3. Reconcile applied changes with the failed file and any callbacks.
4. Retry only after confirming the file is safe to repeat.

A callback failure can leave its version recorded. Repeating migrate will skip
that version and will not automatically resume the unfinished callback.

The local file lock covers cooperating processes on one machine using the same
endpoint. It does not protect different machines, endpoint aliases, or manual
Console changes. This release provides neither a distributed lock nor an atomic
metadata-and-history transaction.

Exit codes: 0 for success, 1 for an execution error, 2 for invalid arguments,
and 130 for interruption.

## pgmigrate compatibility

The reference is pgmigrate **1.0.13**, pinned to commit
`a3c7250ae253e59866c380baf71fcf06b5a81cee`. File conventions, command names, version
selection, history fields, callbacks, and CLI parsing follow that reference.
Execution guarantees differ as described above.

| Capability | Current behavior |
|---|---|
| `-n/--dryrun`, `--ungroup`, `--force_mixed` | Explicit errors; transaction rollback and grouping are unavailable |
| `-s/--session` | Nonempty configuration is rejected; default is [] instead of upstream's `SET lock_timeout = 0` |
| `-l/--termination_interval` | Nonzero values are rejected; terminating PostgreSQL blocking PIDs is inapplicable |
| PostgreSQL schema checking | Inapplicable; an explicit namespace requires `--disable_schema_check` |
| `info.transactional` | Always false, reflecting actual execution |
| Installation time and author | Client UTC time and startup user, rather than server now() and CURRENT_USER |
| Applied-file checksums | Absent, as in pgmigrate 1.0.13 |

Version values use JSON integers rather than a PostgreSQL bigint column.
Callback paths containing colons are preserved instead of reproducing upstream
path truncation. Error text and logging details are not identical to upstream.
Full transactional compatibility is a planned milestone, not a current guarantee.

## Development

```sh
make install
make lint
make test
make build
```

`make` lists all targets. `make clean` removes build and test artifacts while
keeping the virtualenv. Build output is a source archive and wheel under `dist/`.
Override `PYTHON` or `VENV` to use another interpreter or environment; use
`PYTEST_ARGS` to select tests:

```sh
make test PYTEST_ARGS='-q tests/test_console.py'
```

Local tests run by default. Reference and coordinator tests are opt-in and are
skipped unless their environment variables are set.

### Reference tests

```sh
curl -fsSL https://raw.githubusercontent.com/yandex/pgmigrate/a3c7250ae253e59866c380baf71fcf06b5a81cee/pgmigrate.py \
  -o /tmp/pgmigrate-reference.py
PGMIGRATE_REFERENCE=/tmp/pgmigrate-reference.py \
  make test PYTEST_ARGS='-q tests/test_reference.py'
```

The suite checks the source's SHA-256 before importing it, then compares CLI
parsing, defaults, configuration precedence, file selection, and callback paths.

### Coordinator tests

Use an **isolated coordinator**: these tests create and remove their own
distributions, relations, and history keys.

```sh
SPQRMIGRATE_TEST_DSN='host=localhost port=7002 dbname=spqr-console user=admin' \
  make test PYTEST_ARGS='-q tests/test_live_console.py'
```

See [plan.md](plan.md) for release criteria, missing guarantees, and the next
implementation steps.
