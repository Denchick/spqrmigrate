# Implementation status and production plan

Status as of 2026-10-09, based on client commit `6bc76be` and SPQR PR #3065 at
`1ada44d557ba928df5c3cc6bdc70577361a8bb28`.

The initial client is implemented and works against the coordinator Console.
The migration journal blocker is resolved in SPQR. The project is ready for
controlled evaluation with one writer and nontransactional execution; it has not
yet completed production qualification.

## Scope and release contract

Keep the coordinator mandatory and history inside SPQR. The supported domain is
distributions and relation attach/detach metadata. Key ranges, data movement,
default shards, CASCADE, PostgreSQL DDL/DML, and unrelated administrative operations
remain outside scope and must continue to produce explicit errors.

Qualify a narrow nontransactional release first. Full pgmigrate transactional
compatibility is a separate milestone requiring native SPQR support. Every
compatibility gap must remain documented or explicitly rejected by the CLI.

## Implemented

| Area | Delivered | Evidence in this repository |
|---|---|---|
| Console transport | PostgreSQL protocol connection to the coordinator; native SHOW/SET/RESET journal operations | spqrmigrate/backend.py |
| Migration discovery | Recursive V<number>__<description>.sql discovery, numeric ordering, duplicate errors, baseline/target filtering | spqrmigrate/files.py, spqrmigrate/runner.py |
| Commands | info, migrate, baseline, clean; JSON info output; pending-only view | spqrmigrate/cli.py, spqrmigrate/runner.py |
| Journal | Namespaced history, encoded JSON, five history fields, manual baseline, exact clean, corrupt-history rejection | spqrmigrate/journal.py, spqrmigrate/backend.py |
| Execution | Explicit nontransactional mode, recording after successful SQL, repeat-run skipping, no automatic retry after failure | spqrmigrate/runner.py |
| Scope enforcement | Allowlist for distribution and relation DDL; all selected SQL and callbacks checked before migration writes | spqrmigrate/files.py, tests/test_poc.py |
| Callbacks | Four hooks, YAML/string configuration, ordered paths, optional recording after afterEach, marker-based omission | spqrmigrate/files.py, spqrmigrate/runner.py |
| Configuration | pgmigrate-style CLI/default precedence, target latest, namespaces, author override, serial version checks | spqrmigrate/config.py |
| Local coordination | File lock for cooperating processes using the same endpoint on the same machine | spqrmigrate/backend.py, tests/test_console.py |
| Compatibility baseline | pgmigrate 1.0.13 source pinned by SHA-256; differential checks without PostgreSQL | tests/test_reference.py |
| Packaging and development | Console entry point, Python module entry point, wheel/sdist, editable install, Makefile, Ruff | pyproject.toml, Makefile, MANIFEST.in |
| Examples and operations | Example migrations, configuration, documented execution and recovery behavior | examples/, README.md |

The current suite has 39 local checks, 26 reference checks, and 2 coordinator
checks. All 67 passed when both opt-in inputs were provided during the initial
verification. Default runs skip reference/coordinator checks unless configured.
The previous network run used MemQDB and a temporary coordinator build with Unix
socket startup disabled for the local environment. This is useful smoke coverage,
but not a release test of an unmodified durable cluster.

## Missing or intentionally unavailable

| Capability | Current state | Required next step |
|---|---|---|
| Distributed execution lock | Local flock only; different hosts and aliases can race | Native coordinator lock with expiry, ownership validation, and fencing |
| Conditional journal updates | The client replaces the complete namespace history with SET | Native revision-aware update/CAS; reject stale updates |
| Atomic metadata and history | SQL and history are separate writes | Native SPQR metadata transaction including the journal |
| Rollback, dryrun, grouped transactions | Explicitly rejected | Integrate transactional Console execution once its guarantees are defined |
| Crash/failover recovery qualification | Partial SQL and lost journal acknowledgement are covered locally; durable cluster failure scenarios are not automated | Integration tests against coordinator + etcd, including restart and failover |
| Router propagation qualification | Current network tests have no routers/shards | Verify metadata visibility and failure behavior with registered routers and valid shard configuration |
| Automated CI | No workflow is checked in | Required lint, unit, reference, integration, and packaging jobs |
| Supported-version matrix | No automated Python/OS/SPQR matrix | Test declared Python support on Linux/macOS and pin supported SPQR builds |
| Client hardening | Configuration types and server capabilities are only partially checked; journal size is unbounded | Validate types, detect supported capabilities, and define size limits before writes |
| Durable callback recovery | A recorded version may have an unfinished callback | Define explicit recovery behavior; add interruption coverage and a tested runbook |
| Release distribution | Local build/install verified; no release workflow or checked-in license file | Choose a license, define versioning, add release notes and a tag/build/publish workflow |
| Applied-file checksums | Not implemented; also absent from pgmigrate 1.0.13 | Optional future extension, not a compatibility requirement |
| Windows | Unsupported because the client uses fcntl | Keep Linux/macOS scope unless Windows support is deliberately added |

PostgreSQL schema restriction, session SQL, and termination of blocking
PostgreSQL PIDs are inapplicable to this metadata client. They are explicit
compatibility exclusions, not pending implementation tasks. History namespaces
do not isolate distribution metadata.

## Prioritized implementation plan

### 1. Make the current behavior reproducible in CI

Owner: spqrmigrate.

- [ ] Add a required workflow for `make lint`, local tests, and package building.
- [ ] Fetch the exact pgmigrate reference, verify its pinned hash, and run the
      reference suite in a dedicated job. A skipped suite must not count as a pass.
- [ ] Add an isolated integration fixture using an unmodified, pinned SPQR build
      and durable etcd. Start/stop it automatically and capture failure logs.
- [ ] Run the coordinator suite in CI; add journal replacement/reset, Unicode
      values, malformed journal, interrupted SQL, and lost-response scenarios.
- [ ] Verify history after coordinator restart; add router metadata propagation
      coverage and ensure the shard configuration is valid.
- [ ] Install the built wheel in a fresh environment and exercise its entry point.
- [ ] Run on the minimum supported Python version and the other versions/OSes
      intended for release support.

Done when a clean checkout can reproduce the checks without a developer's local
coordinator, and required reference/integration jobs fail if their inputs are missing.

### 2. Add native coordination and conditional journal writes

Owner: SPQR for the contract and implementation; spqrmigrate for integration.

The current client only consumes SHOW migrations and ALTER SYSTEM MIGRATION
SET/RESET. In the inspected SPQR tree,
[SET uses an unconditional Put](https://github.com/pg-sharding/spqr/blob/1ada44d557ba928df5c3cc6bdc70577361a8bb28/qdb/migrations.go).
A journal row claiming ownership cannot safely replace a coordinator lock.

Proposed native SPQR guarantees, not existing client commands:

- [ ] Acquire, renew, and release one migration lock for the shared metadata
      domain. Locking only a history namespace is insufficient.
- [ ] Return an ownership token; expire abandoned owners and reject stale tokens
      on migration metadata and journal mutations, including after leadership changes.
- [ ] Define interaction with other metadata writers: reject conflicting writes
      or require them to use the same coordinator arbitration.
- [ ] Return a journal revision and support conditional updates against an
      expected revision, without changing the existing unconditional SET contract.
- [ ] Ensure lock validation and the guarded storage mutation cannot race.
- [ ] Integrate these operations in the client; abort on ownership loss and never
      automatically replay a file after an uncertain outcome.

etcd provides conditional transactions and expiring leases that can support
this server implementation; the ownership/fencing contract remains SPQR's
responsibility. [etcd API documentation](https://etcd.io/docs/v3.6/learning/api/)

Done when two clients on different machines or endpoint aliases cannot execute
conflicting migrations or lose history, and an expired owner cannot continue writing.
Do not substitute key range locks or a separate client-side history database.

### 3. Qualify recovery and finish the nontransactional release

Owner: spqrmigrate, with SPQR integration coverage.

- [ ] Validate YAML types with actionable errors while preserving documented
      CLI/default precedence and intentional compatibility differences.
- [ ] Report server capability mismatches clearly; replace a PR-number-only
      requirement with tested SPQR version/capability information when available.
- [ ] Define history-size limits and reject an impossible journal write before
      applying its SQL file; test large histories and encoded values.
- [ ] Preserve decoding compatibility when extending the journal format.
- [ ] Test interruption before SQL, within a file, between SQL and journal, and
      during each hook; distinguish an unrecorded file from an unfinished hook.
- [ ] Write and rehearse recovery procedures against durable storage, including
      an acknowledged write whose response was lost and coordinator failover.
- [ ] Verify supported Console authentication/TLS settings in the test deployment.
- [ ] Add CLI version reporting, choose a license, document tested versions, and
      prepare release notes for the exact nontransactional support contract.

Done when the published nontransactional release has required CI, native
coordination, tested persistence/recovery, installable artifacts, and an explicit
single-writer operational contract. This milestone does not claim rollback or
full pgmigrate execution compatibility.

### 4. Implement transactional compatibility

Owner: SPQR for native transactions; spqrmigrate for their use and parity tests.

In the inspected SPQR tree,
[Console BEGIN/COMMIT/ROLLBACK return an unsupported error](https://github.com/pg-sharding/spqr/blob/1ada44d557ba928df5c3cc6bdc70577361a8bb28/pkg/meta/meta.go#L1558).
If another supported SPQR build already exposes the required contract, integrate
it rather than adding a duplicate mechanism.

- [ ] Define native Console transaction boundaries for allowed metadata DDL and
      journal writes, including disconnect rollback and failover behavior.
- [ ] Commit metadata and its journal update atomically under the migration lock.
- [ ] Define when committed metadata becomes visible to registered routers and
      how propagation failures are reported and recovered.
- [ ] Restore ordinary transactional files, transaction grouping, --ungroup,
      --force_mixed, and dryrun only when their guarantees can be met.
- [ ] Preserve callbacks and set_version_info_after_callbacks ordering within
      those transaction boundaries; report accurate transactional values in info.
- [ ] Extend reference/integration coverage to execution parity and failure cases.

Done when errors and dryrun leave neither metadata nor history changes, grouped
and ungrouped execution match the documented pgmigrate behavior, and failover
cannot split a committed metadata change from its history record.

## What to do next

1. Start with the CI and durable integration fixture in step 1. It gives a
   repeatable baseline before changing the server/client contract.
2. In SPQR, agree on the lock, fencing, and journal-revision guarantees in step 2;
   implement or identify the native API and then integrate it into spqrmigrate.
3. Finish the recovery and release work in step 3 for a qualified nontransactional
   release. Use one deployment writer during any earlier controlled evaluation.
4. Pursue step 4 when full pgmigrate transactional execution is the target.
   Keep key ranges and data movement outside this roadmap.
