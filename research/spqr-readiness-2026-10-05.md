# SPQR readiness for a spqrmigrate PoC - 2026-10-05

> Historical audit. PR #3065 resolved the blocker identified here. The current
> verification and implemented PoC are described in the
> [2026-10-09 audit](spqr-readiness-2026-10-09.md).

The audit checked upstream master of `pg-sharding/spqr` at
`c2de52df922be276715e3b6c3c64cbb95a7eaa90`. HEAD was verified through git ls-remote;
the source for that exact commit was downloaded to a temporary directory. The
local checkout contained the older `d192ae235`, so conclusions used the current
upstream source. Other unpublished branches were outside the audit's scope.

A second history check downloaded Git history from GitHub and confirmed that
`f2c09f5b4421949c4102ccf88efed3c7e385941f` was an ancestor of master. There were
**44 commits** between them. Changes were inspected in pkg/meta, qdb, coordinator,
pkg/coord, router/console, protos, and pkg/models/transaction. No later Go/proto
changes implementing AlterSystemMigration/MigrationsStr were found.

Idempotent execution logic was added later:

- [8d53ed8 - IF EXISTS, PR #3007](https://github.com/pg-sharding/spqr/commit/8d53ed88154607b74ec7a4be40ddd65ef7e4fd3c).
- [651e2f0 - IF NOT EXISTS, PR #3010](https://github.com/pg-sharding/spqr/commit/651e2f0a31943c17220b30e7bc22d8d8619f1d6b).

The subsequent commits did not add journal handlers. The public
migration-console-grammar branch (`4fe6dc0cdc5dcfaa9de6538aa67efcf80f45d2bd`) was
also checked: its tree matched f2c09f5 exactly and had no separate implementation.

**Conclusion at the time: storing PoC history inside SPQR required a journal
implementation. Idempotent metadata DDL execution already worked.**

## Results

| Requirement | Observed state |
|---|---|
| CREATE/DROP DISTRIBUTION with IF [NOT] EXISTS | Implemented; repeated execution verified |
| ATTACH/DETACH RELATION with IF [NOT] EXISTS | Implemented; repeated execution verified |
| SHOW migrations | Parser accepts it; execution returns `unknown coordinator cmd` |
| ALTER SYSTEM MIGRATION SET/RESET | Parser accepts it; execution returns `unknown coordinator cmd` |
| Migration records in QDB/etcd | No implementation found in the inspected source tree |
| BEGIN/COMMIT/ROLLBACK in Console | Execution explicitly returns `Meta transactions are not supported` |
| Migration execution lock | No dedicated API found; coordinator leadership and key range locks do not replace it |
| Previously proposed SPQRMIGRATE JSON RPC | Absent upstream; the complete protocol is not required for a minimal PoC |

EntityMgr/QDB metadata transaction mechanisms exist, but clients cannot use them
through the listed Console transaction commands. Two-phase transactions for shard
data also do not provide a transaction covering the journal and metadata DDL.

## Evidence

- [Journal command grammar](https://github.com/pg-sharding/spqr/blob/c2de52df922be276715e3b6c3c64cbb95a7eaa90/yacc/console/gram.y#L1325):
  creates AlterSystemMigration.
- [ALTER execution](https://github.com/pg-sharding/spqr/blob/c2de52df922be276715e3b6c3c64cbb95a7eaa90/pkg/meta/meta.go#L831):
  no AlterSystemMigration case; returns ErrUnknownCoordinatorCommand.
- [SHOW execution](https://github.com/pg-sharding/spqr/blob/c2de52df922be276715e3b6c3c64cbb95a7eaa90/pkg/meta/meta.go#L2257):
  no MigrationsStr handler in ordinary or extended SHOW.
- [Console transactions](https://github.com/pg-sharding/spqr/blob/c2de52df922be276715e3b6c3c64cbb95a7eaa90/pkg/meta/meta.go#L1545):
  BEGIN, COMMIT, and ROLLBACK return SPQR_NOT_IMPLEMENTED.

All `./yacc/console` tests passed. In addition,
[spqr-readiness-2026-10-05_test.go](spqr-readiness-2026-10-05_test.go) called the real
parser and ProcMetadataCommand in a temporary checkout. It confirmed errors for
the six commands above and successful repeated execution of eight DDL commands
through MemQDB. This exercised actual handlers, but was not a network test of a
running cluster or etcd.

To reproduce, copy the test into `pkg/meta/` of the specified commit and run
`go test ./pkg/meta -run TestSpqrmigratePoCAudit -v -count=1`. The test captures the
state on the audit date: once handlers are added, its missing-handler assertions
should stop passing.

## Minimum requirements for an end-to-end PoC

1. Implement execution and storage for the existing SHOW migrations and ALTER
   SYSTEM MIGRATION SET/RESET commands in the normal metadata backend. Reading,
   writing, deletion, and persistence after a coordinator restart are needed.
2. The client reads history, sorts V<version>__<description>.sql files, executes
   only pending files, and records successful versions. Value can contain JSON
   with history fields; checksums are not required for pgmigrate 1.0.13 compatibility.
3. The initial mode can explicitly require one sequential writer and
   nontransactional semantics. Retrying after a failure within a file relies on
   idempotent SQL. This does not guarantee atomicity or remove the need for a lock
   when concurrent execution is supported.
4. Key ranges and data movement remain outside client scope; commands with side
   effects on ranges must also be rejected.

Full pgmigrate compatibility additionally requires Console metadata+history
transactions, rollback/dryrun, an execution lock, and recovery verification after
failover. These are not required for a restricted nontransactional PoC, but their
absence must be explicit in the interface.

At the time of this audit, the spqrmigrate working tree contained only the original
README. The client code mentioned in an earlier response was absent; the audit
did not assume it existed.
