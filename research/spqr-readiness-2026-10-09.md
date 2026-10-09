# SPQR is ready for a nontransactional PoC - 2026-10-09

[PR #3065 - Implement migration journal commands](https://github.com/pg-sharding/spqr/pull/3065)
is merged. The GitHub API confirmed merge commit
`f5b7d4c0dca215e7b2ccf0ddbf546b1b9885e45b`; the inspected PR tree was
`1ada44d557ba928df5c3cc6bdc70577361a8bb28`.
The change resolves the specific blocker from the
[2026-10-05 audit](spqr-readiness-2026-10-05.md): the commands now have real
handlers and a storage backend. The described PoC needs no additional SPQR changes.

## Actual contract

```sql
SHOW migrations;
SHOW migrations WHERE name = 'name';
ALTER SYSTEM MIGRATION SET 'name' TO 'value';
ALTER SYSTEM MIGRATION RESET 'name';
```

SHOW returns name/value columns and supports ordinary extended SHOW behavior.
SET unconditionally stores or replaces a string; RESET deletes the exact name
and is idempotent. This is a string journal API, not a dedicated spqrmigrate
protocol.

- [pkg/meta/meta.go](https://github.com/pg-sharding/spqr/blob/1ada44d557ba928df5c3cc6bdc70577361a8bb28/pkg/meta/meta.go#L834):
  ALTER handler; SHOW migrations appears in the same file around L2090.
- [qdb/migrations.go](https://github.com/pg-sharding/spqr/blob/1ada44d557ba928df5c3cc6bdc70577361a8bb28/qdb/migrations.go):
  MemQDB uses DumpState and returns a copied map; etcd uses `/migrations/`.
- [coordinator/provider/migrations.go](https://github.com/pg-sharding/spqr/blob/1ada44d557ba928df5c3cc6bdc70577361a8bb28/coordinator/provider/migrations.go):
  gRPC provider; the PoC client uses the PostgreSQL Console and does not require gRPC.
- [yacc/console/lex.rl](https://github.com/pg-sharding/spqr/blob/1ada44d557ba928df5c3cc6bdc70577361a8bb28/yacc/console/lex.rl#L52):
  string literals do not support doubled apostrophes. A network test confirmed
  this. The client percent-encodes namespaces and base64url-encodes JSON while
  continuing to parameterize queries through psycopg2.

## Execution verification

The coordinator was built from a git archive of the inspected HEAD in a temporary
directory. The existing SPQR checkout was unchanged. Test setup: Console TCP on
localhost, MemQDB, and no shards, routers, etcd, or PostgreSQL. The temporary app
omitted startup of the Unix socket listener because its `/var/run/postgresql`
path was unavailable in the test environment. Query handlers, protocol processing,
and QDB were unchanged.

Network tests in `tests/test_live_console.py` verified:

1. CREATE DISTRIBUTION and ATTACH RELATION, history storage through SET, and
   reading it through SHOW from a new connection.
2. Repeating a run does not execute SQL from already recorded files.
3. A namespace containing an apostrophe and installed_by=`O'Brien` persist
   without a syntax error.
4. Failure in the second command of V3 leaves a created distribution but no
   version 3 record; after correction and an idempotent retry, history becomes 1,2,3.
5. Baseline, rejection of a repeated baseline at the same version, clean, and
   preservation of a neighboring namespace after an exact RESET.

The first test attempt revealed that ATTACH starts a background TraverseShards
operation for spqrguard. A missing `shard_data` file caused a panic in
TraverseShards after a successful attach. The isolated coordinator was given an
actual config file containing `shards: {}`. Both network tests then passed;
migration handlers needed no changes. This does not verify spqrguard behavior on
shards or propagation to routers.

[spqr-journal-2026-10-09_test.go](spqr-journal-2026-10-09_test.go) was also run in
qdb/. It verified SET -> RestoreQDB from a file -> unchanged value, independence
of the returned map, RESET -> RestoreQDB, and preservation of a similarly named
key. The test passed. The coordinator used for network tests with `--qdb-impl mem`
was ephemeral: restart persistence was checked separately with a MemQDB backup
path, not by restarting the TCP coordinator. Etcd persistence/failover and HA were
not exercised.

## Readiness boundary

The PoC with one writer and an explicit `--nontransactional` flag is **ready and
implemented**. A coordinator is required; all history stays inside SPQR. The
client rejects key ranges and transfer commands, including unsafe CASCADE and
default-shard forms.

Full transactional compatibility still lacks Console BEGIN/COMMIT/ROLLBACK:
they explicitly return `Meta transactions are not supported` in the inspected
tree. SET is not CAS and does not add a distributed lock. A future SPQR API would
need a transaction covering metadata DDL and the journal, plus an exclusive
migration lock that validates its current owner. These features are not required
for the restricted PoC; the client does not promise them and explicitly rejects
dependent options.

The complete CLI/defaults and incompatibility matrix is in
[pgmigrate-compatibility.md](pgmigrate-compatibility.md). The earlier audit is
retained as a dated result for the previous tree, not as the current conclusion.

Final verification: **67 Python checks passed** (39 local, 26 reference
comparisons, and 2 network tests). The separate Go QDB recovery check also passed.
An sdist and wheel were built; editable installation and launching the installed
wheel from another directory were verified. The temporary coordinator was stopped
after verification.
