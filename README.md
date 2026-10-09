# spqrmigrate

A proof of concept for SPQR metadata migrations through the **coordinator
Console** using the PostgreSQL protocol. History is stored in SPQR's own QDB
through the commands introduced by [PR #3065](https://github.com/pg-sharding/spqr/pull/3065).
No separate PostgreSQL database is needed for history. File naming and CLI
behavior follow pgmigrate 1.0.13, with PoC differences described below.

## Installation and usage

Requires Python 3.10+, Linux/macOS, and a running coordinator with PR #3065.

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'

.venv/bin/spqrmigrate info -d examples
.venv/bin/spqrmigrate migrate -d examples -t latest --nontransactional -v
```

`examples/migrations.yml` points to the Console on `localhost:7002`. Change the
address in YAML or override it with `-c`:

```sh
.venv/bin/spqrmigrate migrate -d examples -t latest --nontransactional \
  -c 'host=coordinator port=7002 dbname=spqr-console user=admin connect_timeout=2'
```

You can also use `python -m spqrmigrate`. Connect to the coordinator Console,
which accepts management commands, rather than a router SQL port or a PostgreSQL
shard.

## Files and commands

```text
project/
  migrations.yml
  migrations/
    V0001__Initial_distribution.sql
    V0002__Attach_notifications.sql
```

Files named `V<number>__<description>.sql` are discovered recursively and executed
in numeric version order. Repeated runs skip recorded versions. Duplicate numeric
versions are an error. File contents must be ASCII unless they include
`/* pgmigrate-encoding: utf-8 */`.

Allowed commands are `CREATE/DROP DISTRIBUTION` and
`ALTER DISTRIBUTION ... ATTACH/DETACH RELATION` (also `TABLE`). Use `IF NOT EXISTS`
and `IF EXISTS` for repeatable execution, as shown in the examples. Key ranges,
data movement, `CASCADE`, default shards, and other commands are rejected
**before the first write**: all selected files and callbacks are checked.
SPQR performs full syntax validation of each allowed command.

| Command | Behavior |
|---|---|
| `info [-t N] [-o]` | Show history and pending versions as JSON; `-o` shows only pending versions |
| `migrate -t N\|latest --nontransactional` | Apply pending versions through the target and record each successful file |
| `baseline -b N` | Replace history with one manual version N; fail if a version >= N is already recorded |
| `clean` | Delete only the selected namespace's history; distribution and relation metadata remain |

`-b N` also sets the lower bound for file selection in `info` and `migrate`, without
creating a baseline record. `--check_serial_versions` checks for gaps from the
last recorded version through the selected target.

`-m NAME --disable_schema_check` selects a history namespace (default: `public`).
This does **not isolate metadata**: all namespaces operate on the coordinator's
shared distributions and relations.

Callbacks `beforeAll`, `beforeEach`, `afterEach`, and `afterAll` can be configured
with `-a beforeEach:path,afterAll:path` or a YAML mapping. Callback directories
are read in lexicographic order without recursion. As in pgmigrate, files whose
descriptions contain `NONTRANSACTIONAL` do not run callbacks. For ordinary files
executed with `--nontransactional`, callbacks run without a transaction.

## PoC limitations

The Console does not yet provide a transaction covering metadata and history.
Ordinary files therefore require an explicit `--nontransactional` flag. A version
is recorded after its SQL file succeeds; if a command fails, earlier changes
remain. Fix the cause, inspect `info` and the metadata, then retry an idempotent
migration. If the response to a journal write is lost, reconnect and read history
before retrying.

By default, `afterEach` runs after recording the version and `afterAll` runs after
the last version. Failure in either callback can leave a version recorded;
a repeated run does not automatically resume the unfinished callback. YAML
`set_version_info_after_callbacks: true` moves the journal write after
`afterEach`, but does not make it atomic.

Only one writer may modify the coordinator's metadata at a time, including other
metadata management tools. A local file lock prevents simultaneous spqrmigrate
processes using the same endpoint. It does not protect runs on different machines,
different addresses for the same coordinator, or manual Console commands.

`-n/--dryrun`, `--ungroup`, `--force_mixed`, nonempty `-s/--session`, nonzero
`-l/--termination_interval`, and PostgreSQL schema checking produce explicit
errors. In `info`, `transactional` is always `false`; the author is the connection's
startup user, and installation time is the client's UTC time. Checksums are not
used, matching the reference implementation.

Exit codes: 0 for success, 1 for an execution error, 2 for invalid CLI arguments,
and 130 for interruption.

## Verification

```sh
.venv/bin/python -m pytest -q
```

End-to-end tests require an **isolated** coordinator: they create and remove
their own distributions, relations, and history keys.

```sh
SPQRMIGRATE_TEST_DSN='host=localhost port=7002 dbname=spqr-console user=admin' \
  .venv/bin/python -m pytest -q tests/test_live_console.py
```

Reference comparisons run without PostgreSQL using the pinned source:

```sh
curl -fsSL https://raw.githubusercontent.com/yandex/pgmigrate/a3c7250ae253e59866c380baf71fcf06b5a81cee/pgmigrate.py \
  -o /tmp/pgmigrate-reference.py
PGMIGRATE_REFERENCE=/tmp/pgmigrate-reference.py \
  .venv/bin/python -m pytest -q tests/test_reference.py
```

The tests verify the source's SHA-256 against the pinned value in
[tests/test_reference.py](tests/test_reference.py).
